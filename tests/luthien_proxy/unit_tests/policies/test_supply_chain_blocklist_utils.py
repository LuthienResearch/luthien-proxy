"""Unit tests for :mod:`supply_chain_blocklist_utils`.

Covers pure helpers: version range matching across PEP 440 / semver edges,
loose regex extraction of package literals, OSV response parsing, and the
``sh -c`` substitute builder (including real ``bash -c`` execution tests for
every code path that emits a substitute).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import UTC, datetime

import pytest

from luthien_proxy.policies.supply_chain_blocklist_utils import (
    ECOSYSTEM_NPM,
    ECOSYSTEM_PYPI,
    AffectedRange,
    BlocklistEntry,
    BlocklistIndex,
    OSVClient,
    build_substitute_command,
    canonicalize_name,
    extract_literals,
    version_matches,
)

# =============================================================================
# Name canonicalisation
# =============================================================================


class TestCanonicalizeName:
    def test_pypi_pep503(self) -> None:
        assert canonicalize_name(ECOSYSTEM_PYPI, "Flask-SQL_Alchemy") == "flask-sql-alchemy"

    def test_pypi_mixed_separators(self) -> None:
        assert canonicalize_name(ECOSYSTEM_PYPI, "a.b_c-d") == "a-b-c-d"

    def test_npm_lowercase(self) -> None:
        assert canonicalize_name(ECOSYSTEM_NPM, "Axios") == "axios"

    def test_npm_scoped(self) -> None:
        assert canonicalize_name(ECOSYSTEM_NPM, "@MyOrg/My-Pkg") == "@myorg/my-pkg"


# =============================================================================
# Range matching
# =============================================================================


class TestPypiRangeMatching:
    def test_within_introduced_fixed(self) -> None:
        r = AffectedRange(introduced="1.0.0", fixed="2.0.0", last_affected=None)
        assert version_matches(ECOSYSTEM_PYPI, "1.5.0", r) is True

    def test_at_introduced(self) -> None:
        r = AffectedRange(introduced="1.0.0", fixed="2.0.0", last_affected=None)
        assert version_matches(ECOSYSTEM_PYPI, "1.0.0", r) is True

    def test_at_fixed_excluded(self) -> None:
        r = AffectedRange(introduced="1.0.0", fixed="2.0.0", last_affected=None)
        assert version_matches(ECOSYSTEM_PYPI, "2.0.0", r) is False

    def test_below_introduced(self) -> None:
        r = AffectedRange(introduced="1.6.0", fixed="1.6.9", last_affected=None)
        assert version_matches(ECOSYSTEM_PYPI, "1.5.9", r) is False

    def test_last_affected_inclusive(self) -> None:
        r = AffectedRange(introduced=None, fixed=None, last_affected="1.6.8")
        assert version_matches(ECOSYSTEM_PYPI, "1.6.8", r) is True
        assert version_matches(ECOSYSTEM_PYPI, "1.6.9", r) is False

    def test_litellm_regression_critical_bound(self) -> None:
        # The canonical motivating example: SpecifierSet("<1.6.9") matches 1.6.8.
        r = AffectedRange(introduced=None, fixed="1.6.9", last_affected=None)
        assert version_matches(ECOSYSTEM_PYPI, "1.6.8", r) is True
        assert version_matches(ECOSYSTEM_PYPI, "1.6.9", r) is False

    def test_pre_release_excluded_by_fixed(self) -> None:
        r = AffectedRange(introduced="1.0.0", fixed="2.0.0", last_affected=None)
        assert version_matches(ECOSYSTEM_PYPI, "1.9.9a1", r) is True

    def test_exact_version_range(self) -> None:
        r = AffectedRange(introduced="1.2.3", fixed=None, last_affected="1.2.3")
        assert version_matches(ECOSYSTEM_PYPI, "1.2.3", r) is True
        assert version_matches(ECOSYSTEM_PYPI, "1.2.4", r) is False
        assert version_matches(ECOSYSTEM_PYPI, "1.2.2", r) is False

    def test_unparseable_candidate_fails_closed(self) -> None:
        r = AffectedRange(introduced="1.0.0", fixed="2.0.0", last_affected=None)
        assert version_matches(ECOSYSTEM_PYPI, "not-a-version", r) is False

    def test_all_none_range_matches_any_version(self) -> None:
        r = AffectedRange(introduced=None, fixed=None, last_affected=None)
        assert version_matches(ECOSYSTEM_PYPI, "0.0.1", r) is True
        assert version_matches(ECOSYSTEM_PYPI, "99.99.99", r) is True


class TestNpmRangeMatching:
    def test_basic_range(self) -> None:
        r = AffectedRange(introduced="1.6.0", fixed="1.6.9", last_affected=None)
        assert version_matches(ECOSYSTEM_NPM, "1.6.8", r) is True
        assert version_matches(ECOSYSTEM_NPM, "1.6.9", r) is False
        assert version_matches(ECOSYSTEM_NPM, "1.5.9", r) is False

    def test_pre_release_ranks_below_release(self) -> None:
        r = AffectedRange(introduced="1.0.0", fixed="2.0.0", last_affected=None)
        assert version_matches(ECOSYSTEM_NPM, "1.5.0-alpha.1", r) is True

    def test_exact_version(self) -> None:
        r = AffectedRange(introduced="1.2.3", fixed=None, last_affected="1.2.3")
        assert version_matches(ECOSYSTEM_NPM, "1.2.3", r) is True
        assert version_matches(ECOSYSTEM_NPM, "1.2.4", r) is False

    def test_garbage_fails_closed(self) -> None:
        r = AffectedRange(introduced="1.0.0", fixed="2.0.0", last_affected=None)
        assert version_matches(ECOSYSTEM_NPM, "latest", r) is False

    def test_pre_release_ordering_within_core(self) -> None:
        # 1.0.0 > 1.0.0-rc.1 > 1.0.0-beta > 1.0.0-alpha
        r = AffectedRange(introduced="1.0.0-alpha", fixed="1.0.0", last_affected=None)
        assert version_matches(ECOSYSTEM_NPM, "1.0.0-beta", r) is True
        assert version_matches(ECOSYSTEM_NPM, "1.0.0", r) is False


# =============================================================================
# Command extraction
# =============================================================================


class TestExtractLiterals:
    def test_pypi_double_equals(self) -> None:
        out = extract_literals("pip install litellm==1.59.0")
        assert [(e.ecosystem, e.name, e.version) for e in out] == [("PyPI", "litellm", "1.59.0")]

    def test_npm_at_sign(self) -> None:
        out = extract_literals("npm install axios@1.6.8")
        assert [(e.ecosystem, e.name, e.version) for e in out] == [("npm", "axios", "1.6.8")]

    def test_npm_scoped(self) -> None:
        out = extract_literals("npm install @babel/core@7.22.0")
        assert any(e.name == "@babel/core" and e.version == "7.22.0" for e in out)

    def test_mixed(self) -> None:
        out = extract_literals("pip install litellm==1.59.0 && npm install axios@1.6.8")
        eco = {e.ecosystem for e in out}
        assert eco == {ECOSYSTEM_NPM, ECOSYSTEM_PYPI}

    def test_latest_tag_not_matched(self) -> None:
        # Version must start with a digit — "latest" is not a version.
        out = extract_literals("npm install foo@latest")
        assert out == []

    def test_email_like_not_matched_as_npm(self) -> None:
        out = extract_literals("mail user@example.com")
        assert all(e.ecosystem != ECOSYSTEM_NPM or e.version[0].isdigit() for e in out)

    def test_dedupe(self) -> None:
        out = extract_literals("pip install litellm==1.59.0; pip install litellm==1.59.0")
        assert len(out) == 1

    def test_empty_command(self) -> None:
        assert extract_literals("") == []


# =============================================================================
# BlocklistIndex
# =============================================================================


class TestBlocklistIndex:
    def test_lookup_miss(self) -> None:
        idx = BlocklistIndex([])
        out = extract_literals("pip install safe==1.0.0")
        assert idx.lookup(out[0]) is None

    def test_lookup_hit_pypi(self) -> None:
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_PYPI,
            canonical_name="litellm",
            cve_id="CVE-2024-0001",
            severity="CRITICAL",
            range=AffectedRange(introduced=None, fixed="1.6.9", last_affected=None),
        )
        idx = BlocklistIndex([entry])
        lit = extract_literals("pip install litellm==1.6.8")[0]
        hit = idx.lookup(lit)
        assert hit is not None
        assert hit.cve_id == "CVE-2024-0001"

    def test_substring_backstop_exact_pin(self) -> None:
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_NPM,
            canonical_name="axios",
            cve_id="CVE-2024-0002",
            severity="CRITICAL",
            range=AffectedRange(introduced="1.6.8", fixed=None, last_affected="1.6.8"),
        )
        idx = BlocklistIndex([entry])
        hit = idx.substring_backstop('echo "axios@1.6.8 is bad"')
        assert hit is not None
        assert hit.cve_id == "CVE-2024-0002"

    def test_substring_backstop_ignores_open_range(self) -> None:
        # Open ranges must NOT emit a substring backstop, or we'd false-hit
        # every unrelated version string.
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_NPM,
            canonical_name="axios",
            cve_id="CVE-2024-0002",
            severity="CRITICAL",
            range=AffectedRange(introduced="1.6.0", fixed="1.7.0", last_affected=None),
        )
        idx = BlocklistIndex([entry])
        assert idx.substring_backstop("axios@1.6.8") is None

    def test_case_insensitive_pypi(self) -> None:
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_PYPI,
            canonical_name="flask-sql-alchemy",
            cve_id="CVE-X",
            severity="CRITICAL",
            range=AffectedRange(introduced=None, fixed="9.9.9", last_affected=None),
        )
        idx = BlocklistIndex([entry])
        lit = extract_literals("pip install Flask_SQL.Alchemy==1.0.0")[0]
        assert idx.lookup(lit) is not None


# =============================================================================
# Substitute builder — including subprocess tests against real bash
# =============================================================================


def _run_bash(substitute: str, cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", substitute],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestBuildSubstituteSubprocess:
    """Run every generated substitute through real ``bash -c``.

    Every code path that emits a ``sh -c '...'`` substitute is exercised
    here (currently one: :func:`build_substitute_command`). For each, we
    assert exit code 42, stderr content, and absence of side effects in
    the cwd.
    """

    def test_clean_command_substitute(self) -> None:
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_PYPI,
            canonical_name="litellm",
            cve_id="CVE-2024-0001",
            severity="CRITICAL",
            range=AffectedRange(introduced=None, fixed="1.6.9", last_affected=None),
        )
        sub = build_substitute_command("pip install litellm==1.6.8", entry)
        with tempfile.TemporaryDirectory() as tmp:
            before = set(os.listdir(tmp))
            result = _run_bash(sub, tmp)
            after = set(os.listdir(tmp))
        assert result.returncode == 42
        assert "LUTHIEN BLOCKED" in result.stderr
        assert "litellm" in result.stderr
        assert result.stdout == ""
        assert after == before, f"unexpected files: {after - before}"

    def test_attacker_quote_original_does_not_escape(self) -> None:
        # Classic attacker quote: single quote + shell metacharacters +
        # unterminated quote. If our _sh_single_quote is wrong, the outer
        # sh -c string will re-interpret this and run `touch /tmp/PWN_*`.
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_PYPI,
            canonical_name="litellm",
            cve_id="CVE-2024-0001",
            severity="CRITICAL",
            range=AffectedRange(introduced=None, fixed="1.6.9", last_affected=None),
        )
        hostile = "pip install 'litellm==1.6.8'; touch PWN_ATTACK; echo 'hi"
        sub = build_substitute_command(hostile, entry)
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_bash(sub, tmp)
            files = os.listdir(tmp)
        assert result.returncode == 42
        assert "LUTHIEN BLOCKED" in result.stderr
        assert "PWN_ATTACK" not in files
        assert files == []

    def test_backtick_command_substitution_does_not_fire(self) -> None:
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_PYPI,
            canonical_name="litellm",
            cve_id="CVE-X",
            severity="CRITICAL",
            range=AffectedRange(introduced=None, fixed="1.6.9", last_affected=None),
        )
        hostile = "`touch PWN_BACKTICK`"
        sub = build_substitute_command(hostile, entry)
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_bash(sub, tmp)
            files = os.listdir(tmp)
        assert result.returncode == 42
        assert "PWN_BACKTICK" not in files
        assert files == []

    def test_dollar_paren_substitution_does_not_fire(self) -> None:
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_PYPI,
            canonical_name="litellm",
            cve_id="CVE-X",
            severity="CRITICAL",
            range=AffectedRange(introduced=None, fixed="1.6.9", last_affected=None),
        )
        hostile = "$(touch PWN_PAREN)"
        sub = build_substitute_command(hostile, entry)
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_bash(sub, tmp)
            files = os.listdir(tmp)
        assert result.returncode == 42
        assert "PWN_PAREN" not in files
        assert files == []


# =============================================================================
# OSV response parsing
# =============================================================================


def _vuln(
    vuln_id: str,
    ecosystem: str,
    name: str,
    events: list[dict],
    *,
    severity: str = "CRITICAL",
    published: str = "2026-04-01T00:00:00Z",
) -> dict:
    return {
        "id": vuln_id,
        "published": published,
        "database_specific": {"severity": severity},
        "affected": [
            {"package": {"ecosystem": ecosystem, "name": name}, "ranges": [{"events": events}]},
        ],
    }


class TestOSVResponseParsing:
    def test_parse_introduced_fixed(self) -> None:
        raw = [_vuln("CVE-1", ECOSYSTEM_PYPI, "litellm", [{"introduced": "1.0.0"}, {"fixed": "1.6.9"}])]
        out = OSVClient._parse_response(ECOSYSTEM_PYPI, raw, None, "CRITICAL", 100)
        assert len(out.entries) == 1
        row = out.entries[0]
        assert row.canonical_name == "litellm"
        assert row.cve_id == "CVE-1"
        parsed_range = AffectedRange.from_json(row.affected_range)
        assert parsed_range.introduced == "1.0.0"
        assert parsed_range.fixed == "1.6.9"

    def test_severity_floor_excludes(self) -> None:
        raw = [_vuln("CVE-LOW", ECOSYSTEM_PYPI, "foo", [{"introduced": "1.0"}], severity="MEDIUM")]
        out = OSVClient._parse_response(ECOSYSTEM_PYPI, raw, None, "CRITICAL", 100)
        assert out.entries == []

    def test_since_filter_excludes_old(self) -> None:
        # Two vulns, one before `since`, one after.
        raw = [
            _vuln("CVE-OLD", ECOSYSTEM_PYPI, "a", [{"introduced": "1.0"}], published="2020-01-01T00:00:00Z"),
            _vuln("CVE-NEW", ECOSYSTEM_PYPI, "a", [{"introduced": "1.0"}], published="2026-04-05T00:00:00Z"),
        ]
        since = datetime(2026, 1, 1, tzinfo=UTC)
        out = OSVClient._parse_response(ECOSYSTEM_PYPI, raw, since, "CRITICAL", 100)
        assert len(out.entries) == 1
        assert out.entries[0].cve_id == "CVE-NEW"

    def test_introduced_zero_sentinel(self) -> None:
        raw = [_vuln("CVE-Z", ECOSYSTEM_PYPI, "foo", [{"introduced": "0"}, {"fixed": "2.0.0"}])]
        out = OSVClient._parse_response(ECOSYSTEM_PYPI, raw, None, "CRITICAL", 100)
        parsed = AffectedRange.from_json(out.entries[0].affected_range)
        assert parsed.introduced is None  # "0" is the OSV "from the beginning" sentinel
        assert parsed.fixed == "2.0.0"

    def test_limit_caps_entries(self) -> None:
        # Each vuln emits one range; limit is enforced across the loop.
        raw = [
            _vuln(f"CVE-{i}", ECOSYSTEM_PYPI, f"pkg{i}", [{"introduced": "1.0"}, {"fixed": "2.0"}]) for i in range(10)
        ]
        out = OSVClient._parse_response(ECOSYSTEM_PYPI, raw, None, "CRITICAL", 3)
        assert len(out.entries) == 3

    def test_ecosystem_filter(self) -> None:
        raw = [
            {
                "id": "CVE-MIX",
                "published": "2026-04-01T00:00:00Z",
                "database_specific": {"severity": "CRITICAL"},
                "affected": [
                    {"package": {"ecosystem": "Go", "name": "some"}, "ranges": [{"events": [{"introduced": "1.0"}]}]},
                    {
                        "package": {"ecosystem": ECOSYSTEM_PYPI, "name": "p"},
                        "ranges": [{"events": [{"introduced": "1.0"}]}],
                    },
                ],
            },
        ]
        out = OSVClient._parse_response(ECOSYSTEM_PYPI, raw, None, "CRITICAL", 10)
        assert len(out.entries) == 1
        assert out.entries[0].canonical_name == "p"

    def test_latest_published_at_tracks_max(self) -> None:
        raw = [
            _vuln("A", ECOSYSTEM_PYPI, "x", [{"introduced": "1.0"}], published="2026-04-01T00:00:00Z"),
            _vuln("B", ECOSYSTEM_PYPI, "y", [{"introduced": "1.0"}], published="2026-04-05T12:00:00Z"),
        ]
        out = OSVClient._parse_response(ECOSYSTEM_PYPI, raw, None, "CRITICAL", 100)
        assert out.latest_published_at == datetime(2026, 4, 5, 12, 0, tzinfo=UTC)


# =============================================================================
# Full substitute text shape
# =============================================================================


class TestSubstituteShape:
    def test_contains_header_and_exit(self) -> None:
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_PYPI,
            canonical_name="litellm",
            cve_id="CVE-2024-0001",
            severity="CRITICAL",
            range=AffectedRange(introduced=None, fixed="1.6.9", last_affected=None),
        )
        sub = build_substitute_command("pip install litellm==1.6.8", entry)
        assert sub.startswith("sh -c ")
        assert "exit 42" in sub
        assert "LUTHIEN BLOCKED" in sub

    def test_clips_long_original(self) -> None:
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_PYPI,
            canonical_name="litellm",
            cve_id="CVE-X",
            severity="CRITICAL",
            range=AffectedRange(introduced=None, fixed="9.9.9", last_affected=None),
        )
        huge = "pip install " + ("x" * 1000)
        sub = build_substitute_command(huge, entry)
        # The clipped original should appear inside, but not 1000 x's.
        assert "xxxxxxxxx" in sub
        assert sub.count("x") < 500

    def test_does_not_include_summary_field(self) -> None:
        # We deliberately never include untrusted OSV "summary" text in the
        # substitute. The build_substitute_command API doesn't even accept
        # such a parameter — this test pins that property.
        entry = BlocklistEntry(
            ecosystem=ECOSYSTEM_PYPI,
            canonical_name="litellm",
            cve_id="CVE-X",
            severity="CRITICAL",
            range=AffectedRange(introduced=None, fixed="9.9.9", last_affected=None),
        )
        sub = build_substitute_command("pip install litellm==1.0.0", entry)
        # No free-form untrusted fields — just our own constant strings and
        # structured metadata.
        assert "summary" not in sub.lower()


@pytest.mark.parametrize(
    "candidate,introduced,fixed,expected",
    [
        ("1.6.8", None, "1.6.9", True),
        ("1.6.9", None, "1.6.9", False),
        ("1.6.8", "1.6.0", "1.7.0", True),
        ("1.5.9", "1.6.0", "1.7.0", False),
        ("2.0.0", "1.6.0", "1.7.0", False),
    ],
)
def test_pypi_bounds_parametrized(candidate: str, introduced: str | None, fixed: str | None, expected: bool) -> None:
    r = AffectedRange(introduced=introduced, fixed=fixed, last_affected=None)
    assert version_matches(ECOSYSTEM_PYPI, candidate, r) is expected
