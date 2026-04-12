"""Tests for supply_chain_feed_utils — GCS client parsing, regex, blocklist, substitution.

Mandatory test categories from OBJECTIVE.md:
1. Captured-real-response fixture tests
2. Real-network smoke test (osv_live marker)
3. Subprocess execution tests for substitute builder
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from luthien_proxy.policies.supply_chain_feed_utils import (
    VulnEntry,
    build_blocklist_index,
    build_substitute_command,
    build_substrate_strings,
    check_blocklist,
    extract_install_specs,
    listing_api_url,
    parse_bulk_zip,
    parse_listing_page,
    parse_vuln_json,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "osv"


# =====================================================================
# Category 1: Captured-real-response fixture tests
# =====================================================================


class TestParseBulkZipFixture:
    """Parse the pinned pypi_sample.zip through the cold-start code path."""

    def test_parse_pypi_sample_zip(self):
        zip_bytes = (FIXTURES / "pypi_sample.zip").read_bytes()
        entries = parse_bulk_zip(zip_bytes)

        # The fixture has 2 CRITICAL advisories with versions.
        # HIGH and MODERATE are filtered out.
        assert len(entries) > 0

        # All entries should be CRITICAL
        for entry in entries:
            assert entry.severity == "CRITICAL"

        # Verify specific entries from GHSA-8ppf-x4gr-2x7g (calibreweb, 6 versions)
        # Plus GHSA-xp7p-3gx7-j6wx (also calibreweb, 3 versions) = 9 total
        calibreweb_entries = [e for e in entries if e.name == "calibreweb"]
        assert len(calibreweb_entries) == 9
        versions = {e.version for e in calibreweb_entries}
        assert "0.6.17" in versions
        assert "0.6.12" in versions

        # Both CRITICAL calibreweb CVEs should be represented
        cve_ids = {e.cve_id for e in calibreweb_entries}
        assert "GHSA-8ppf-x4gr-2x7g" in cve_ids
        assert "GHSA-xp7p-3gx7-j6wx" in cve_ids

    def test_parse_pypi_sample_zip_builds_correct_index(self):
        """The full cold-start path: zip -> entries -> index."""
        zip_bytes = (FIXTURES / "pypi_sample.zip").read_bytes()
        entries = parse_bulk_zip(zip_bytes)
        index = build_blocklist_index(entries)

        # Should find calibreweb 0.6.17 under pypi ecosystem
        key = ("pypi", "calibreweb", "0.6.17")
        assert key in index
        assert "GHSA-8ppf-x4gr-2x7g" in index[key]


class TestParseListingPageFixture:
    """Parse the pinned pypi_sample_listing.json through the incremental code path."""

    def test_parse_listing_page(self):
        data = json.loads((FIXTURES / "pypi_sample_listing.json").read_text())
        items, next_token = parse_listing_page(data)

        assert len(items) > 0
        # All items should have name and updated fields
        for item in items:
            assert item.name.startswith("PyPI/")
            assert item.updated is not None

        # Should have a next page token (we requested maxResults=10)
        assert next_token is not None

    def test_listing_page_skips_all_zip(self):
        """The all.zip entry in the listing should be skipped."""
        data = {
            "items": [
                {"name": "PyPI/all.zip", "updated": "2025-01-01T00:00:00Z"},
                {"name": "PyPI/GHSA-test.json", "updated": "2025-01-01T00:00:00Z"},
            ]
        }
        items, _ = parse_listing_page(data)
        assert len(items) == 1
        assert items[0].name == "PyPI/GHSA-test.json"


class TestParseVulnEntryFixture:
    """Parse pinned individual vuln JSONs through parse_vuln_json."""

    def test_parse_ghsa_8ppf(self):
        data = json.loads((FIXTURES / "GHSA-8ppf-x4gr-2x7g.json").read_text())
        entries = parse_vuln_json(data)

        assert len(entries) == 6  # 6 affected versions
        assert all(e.cve_id == "GHSA-8ppf-x4gr-2x7g" for e in entries)
        assert all(e.severity == "CRITICAL" for e in entries)
        assert all(e.ecosystem == "PyPI" for e in entries)
        assert all(e.name == "calibreweb" for e in entries)

        # Verify published/modified timestamps parsed
        assert entries[0].published_at is not None
        assert entries[0].modified_at is not None

    def test_parse_ghsa_xp7p(self):
        data = json.loads((FIXTURES / "GHSA-xp7p-3gx7-j6wx.json").read_text())
        entries = parse_vuln_json(data)

        assert len(entries) == 3  # 3 affected versions
        assert all(e.cve_id == "GHSA-xp7p-3gx7-j6wx" for e in entries)
        assert all(e.severity == "CRITICAL" for e in entries)


class TestParseVulnJsonEdgeCases:
    """Edge cases for parse_vuln_json."""

    def test_non_critical_skipped(self):
        data = {
            "id": "TEST-001",
            "affected": [{"package": {"ecosystem": "PyPI", "name": "foo"}, "versions": ["1.0"]}],
            "database_specific": {"severity": "HIGH"},
        }
        assert parse_vuln_json(data) == []

    def test_missing_severity_skipped(self):
        data = {
            "id": "TEST-002",
            "affected": [{"package": {"ecosystem": "PyPI", "name": "foo"}, "versions": ["1.0"]}],
            "database_specific": {},
        }
        assert parse_vuln_json(data) == []

    def test_ranges_only_skipped(self):
        """Advisory with ranges but no expanded versions is skipped."""
        data = {
            "id": "TEST-003",
            "affected": [
                {
                    "package": {"ecosystem": "PyPI", "name": "foo"},
                    "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.0"}]}],
                }
            ],
            "database_specific": {"severity": "CRITICAL"},
        }
        assert parse_vuln_json(data) == []

    def test_empty_id_skipped(self):
        data = {
            "id": "",
            "affected": [{"package": {"ecosystem": "PyPI", "name": "foo"}, "versions": ["1.0"]}],
            "database_specific": {"severity": "CRITICAL"},
        }
        assert parse_vuln_json(data) == []


# =====================================================================
# Category 2: Real-network smoke test
# =====================================================================


@pytest.mark.osv_live
class TestOsvLiveSmoke:
    """Hit real OSV GCS endpoints. Excluded from default pytest run."""

    def test_bulk_zip_head(self):
        """HEAD the real bulk zip URL, assert HTTP 200 + content-type."""
        resp = httpx.head("https://storage.googleapis.com/osv-vulnerabilities/PyPI/all.zip", timeout=10)
        assert resp.status_code == 200
        assert "application/zip" in resp.headers.get("content-type", "")

    def test_listing_api_get(self):
        """GET a real listing page, assert JSON with items[]."""
        url = listing_api_url("PyPI", max_results=3)
        resp = httpx.get(url, timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) > 0


# =====================================================================
# Blocklist index and lookup tests
# =====================================================================


class TestBlocklistIndex:
    """Build and query the in-memory index."""

    @pytest.fixture
    def sample_entries(self):
        return [
            VulnEntry("PyPI", "calibreweb", "0.6.17", "GHSA-8ppf", "CRITICAL", None, None),
            VulnEntry("PyPI", "calibreweb", "0.6.16", "GHSA-8ppf", "CRITICAL", None, None),
            VulnEntry("npm", "axios", "1.6.8", "CVE-2024-39338", "CRITICAL", None, None),
        ]

    def test_build_index(self, sample_entries):
        index = build_blocklist_index(sample_entries)
        assert ("pypi", "calibreweb", "0.6.17") in index
        assert ("npm", "axios", "1.6.8") in index
        assert len(index) == 3

    def test_build_substrate_strings(self, sample_entries):
        index = build_blocklist_index(sample_entries)
        substrates = build_substrate_strings(index)
        assert "calibreweb==0.6.17" in substrates
        assert "calibreweb==0.6.16" in substrates
        assert "axios@1.6.8" in substrates

    def test_pypi_name_normalization(self):
        """PEP 503: Pillow -> pillow, under_scores -> under-scores."""
        entries = [
            VulnEntry("PyPI", "Pillow", "10.0.0", "CVE-TEST", "CRITICAL", None, None),
        ]
        index = build_blocklist_index(entries)
        assert ("pypi", "pillow", "10.0.0") in index

    def test_multiple_cves_same_version(self):
        entries = [
            VulnEntry("PyPI", "foo", "1.0", "CVE-A", "CRITICAL", None, None),
            VulnEntry("PyPI", "foo", "1.0", "CVE-B", "CRITICAL", None, None),
        ]
        index = build_blocklist_index(entries)
        assert sorted(index[("pypi", "foo", "1.0")]) == ["CVE-A", "CVE-B"]


# =====================================================================
# Regex extraction tests
# =====================================================================


class TestExtractInstallSpecs:
    def test_pip_install(self):
        specs = extract_install_specs("pip install calibreweb==0.6.17")
        assert ("calibreweb", "0.6.17") in specs

    def test_npm_install(self):
        specs = extract_install_specs("npm install axios@1.6.8")
        assert ("axios", "1.6.8") in specs

    def test_multiple_packages(self):
        specs = extract_install_specs("pip install foo==1.0 bar==2.0")
        assert ("foo", "1.0") in specs
        assert ("bar", "2.0") in specs

    def test_no_install_command(self):
        specs = extract_install_specs("echo hello world")
        assert specs == []

    def test_version_with_prerelease(self):
        specs = extract_install_specs("pip install pkg==1.0.0rc1")
        assert ("pkg", "1.0.0rc1") in specs


# =====================================================================
# check_blocklist integration tests
# =====================================================================


class TestCheckBlocklist:
    @pytest.fixture
    def blocklist(self):
        entries = [
            VulnEntry("PyPI", "calibreweb", "0.6.17", "GHSA-8ppf", "CRITICAL", None, None),
            VulnEntry("npm", "axios", "1.6.8", "CVE-2024-39338", "CRITICAL", None, None),
        ]
        index = build_blocklist_index(entries)
        substrates = build_substrate_strings(index)
        return index, substrates

    def test_regex_hit(self, blocklist):
        index, substrates = blocklist
        result = check_blocklist("pip install calibreweb==0.6.17", index, substrates)
        assert result is not None
        name, version, cve_ids = result
        assert name == "calibreweb"
        assert version == "0.6.17"

    def test_npm_regex_hit(self, blocklist):
        index, substrates = blocklist
        result = check_blocklist("npm install axios@1.6.8", index, substrates)
        assert result is not None

    def test_no_match(self, blocklist):
        index, substrates = blocklist
        result = check_blocklist("pip install requests==2.31.0", index, substrates)
        assert result is None

    def test_substring_backstop(self, blocklist):
        """Backstop fires when the literal appears but regex doesn't match."""
        index, substrates = blocklist
        # This string contains the literal but not in a form the regex catches
        result = check_blocklist('echo "axios@1.6.8 is bad"', index, substrates)
        assert result is not None
        _, _, cve_ids = result
        assert "CVE-2024-39338" in cve_ids

    def test_substring_backstop_case_insensitive(self, blocklist):
        index, substrates = blocklist
        result = check_blocklist("echo CALIBREWEB==0.6.17", index, substrates)
        assert result is not None


# =====================================================================
# Category 4: subprocess.run execution tests for substitute builder
# =====================================================================


class TestBuildSubstituteCommand:
    def test_basic_output(self):
        cmd = build_substitute_command("calibreweb", "0.6.17", ["GHSA-8ppf"])
        assert "LUTHIEN BLOCKED" in cmd
        assert "calibreweb" in cmd
        assert "0.6.17" in cmd
        assert "GHSA-8ppf" in cmd
        assert "exit 42" in cmd

    def test_osv_url_included(self):
        cmd = build_substitute_command("axios", "1.6.8", ["CVE-2024-39338"])
        assert "https://osv.dev/vulnerability/CVE-2024-39338" in cmd

    def test_subprocess_exit_42(self):
        """Run the generated command through real bash and check exit code."""
        cmd = build_substitute_command("calibreweb", "0.6.17", ["GHSA-8ppf"])
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 42
        assert "LUTHIEN BLOCKED" in result.stderr

    def test_subprocess_clean_cwd(self, tmp_path):
        """Running the substitute should not create files."""
        cmd = build_substitute_command("calibreweb", "0.6.17", ["GHSA-8ppf"])
        subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(tmp_path),
        )
        # No files created
        assert list(tmp_path.iterdir()) == []

    def test_subprocess_attacker_quote(self):
        """Attacker-crafted original command with quotes should not escape."""
        cmd = build_substitute_command("'; touch /tmp/PWN; '", "1.0", ["CVE-TEST"])
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 42
        assert "LUTHIEN BLOCKED" in result.stderr
        import os

        assert not os.path.exists("/tmp/PWN")

    def test_subprocess_backtick_injection(self):
        """Backtick injection attempt should not execute."""
        cmd = build_substitute_command("`touch /tmp/PWN`", "1.0", ["CVE-TEST"])
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 42
        import os

        assert not os.path.exists("/tmp/PWN")

    def test_subprocess_dollar_paren_injection(self):
        """$(cmd) injection attempt should not execute."""
        cmd = build_substitute_command("$(touch /tmp/PWN)", "1.0", ["CVE-TEST"])
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 42
        import os

        assert not os.path.exists("/tmp/PWN")


# =====================================================================
# URL builder tests
# =====================================================================


class TestUrlBuilders:
    def test_listing_api_url_basic(self):
        url = listing_api_url("PyPI")
        assert "prefix=PyPI/" in url
        assert "maxResults=500" in url

    def test_listing_api_url_with_token(self):
        url = listing_api_url("PyPI", page_token="abc123")
        assert "pageToken=abc123" in url
