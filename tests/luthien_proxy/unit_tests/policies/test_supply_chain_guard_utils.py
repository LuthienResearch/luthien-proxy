"""Unit tests for supply_chain_guard_utils.

Covers:
- Command parsing across ecosystems (pip, npm, cargo, go, gem, composer).
- Allowlist behaviour.
- Severity parsing / filtering / formatters.
- OSV response parsing (payload shape) and OSVClient HTTP interaction via
  a mocked httpx.AsyncClient.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from luthien_proxy.policies.supply_chain_guard_utils import (
    OSVClient,
    PackageCheckResult,
    PackageRef,
    Severity,
    SupplyChainGuardConfig,
    VulnInfo,
    _cvss3_base_score,
    _extract_max_severity,
    _parse_cvss_score,
    _parse_osv_response,
    filter_blocking,
    format_blocked_message,
    format_incoming_warning,
    is_allowlisted,
    parse_install_commands,
)

# =============================================================================
# Command parsing
# =============================================================================


class TestParsePip:
    def test_simple_install(self):
        refs = parse_install_commands("pip install requests")
        assert refs == [PackageRef(ecosystem="PyPI", name="requests")]

    def test_version_pinned(self):
        refs = parse_install_commands("pip install requests==2.31.0")
        assert refs == [PackageRef(ecosystem="PyPI", name="requests", version="2.31.0")]

    def test_multiple_packages(self):
        refs = parse_install_commands("pip install requests flask django")
        names = [r.name for r in refs]
        assert names == ["requests", "flask", "django"]

    def test_extras_stripped(self):
        refs = parse_install_commands("pip install requests[security]==2.31.0")
        assert refs == [PackageRef(ecosystem="PyPI", name="requests", version="2.31.0")]

    def test_requirements_file_ignored(self):
        refs = parse_install_commands("pip install -r requirements.txt")
        assert refs == []

    def test_editable_ignored(self):
        refs = parse_install_commands("pip install -e ./mylib")
        assert refs == []

    def test_extra_index_url_skipped(self):
        refs = parse_install_commands("pip install --extra-index-url https://foo flask")
        assert refs == [PackageRef(ecosystem="PyPI", name="flask")]

    def test_equals_flag(self):
        refs = parse_install_commands("pip install --timeout=30 flask")
        assert refs == [PackageRef(ecosystem="PyPI", name="flask")]

    def test_pip3_alias(self):
        refs = parse_install_commands("pip3 install requests")
        assert refs == [PackageRef(ecosystem="PyPI", name="requests")]

    def test_uv_pip(self):
        refs = parse_install_commands("uv pip install requests")
        assert refs == [PackageRef(ecosystem="PyPI", name="requests")]

    def test_wheel_ignored(self):
        refs = parse_install_commands("pip install foo.whl")
        assert refs == []

    def test_url_ignored(self):
        refs = parse_install_commands("pip install https://example.com/foo.tar.gz")
        assert refs == []

    @pytest.mark.parametrize(
        ("op", "value"),
        [("==", "1.0"), (">=", "1.0"), ("~=", "2.0"), ("!=", "3.0"), (">", "4.0")],
    )
    def test_version_operators(self, op: str, value: str):
        refs = parse_install_commands(f"pip install foo{op}{value}")
        assert refs == [PackageRef(ecosystem="PyPI", name="foo", version=value)]


class TestParseNpm:
    def test_simple(self):
        refs = parse_install_commands("npm install left-pad")
        assert refs == [PackageRef(ecosystem="npm", name="left-pad")]

    def test_alias_i(self):
        refs = parse_install_commands("npm i left-pad")
        assert refs == [PackageRef(ecosystem="npm", name="left-pad")]

    def test_versioned(self):
        refs = parse_install_commands("npm add left-pad@1.3.0")
        assert refs == [PackageRef(ecosystem="npm", name="left-pad", version="1.3.0")]

    def test_scoped(self):
        refs = parse_install_commands("npm install @types/node")
        assert refs == [PackageRef(ecosystem="npm", name="@types/node")]

    def test_scoped_versioned(self):
        refs = parse_install_commands("npm install @scope/pkg@2.0.0")
        assert refs == [PackageRef(ecosystem="npm", name="@scope/pkg", version="2.0.0")]

    def test_yarn_add(self):
        refs = parse_install_commands("yarn add left-pad")
        assert refs == [PackageRef(ecosystem="npm", name="left-pad")]

    def test_yarn_global_add(self):
        refs = parse_install_commands("yarn global add typescript")
        assert refs == [PackageRef(ecosystem="npm", name="typescript")]

    def test_pnpm_add(self):
        refs = parse_install_commands("pnpm add react")
        assert refs == [PackageRef(ecosystem="npm", name="react")]

    def test_git_ignored(self):
        refs = parse_install_commands("npm install git+https://github.com/foo/bar.git")
        assert refs == []

    def test_tgz_ignored(self):
        refs = parse_install_commands("npm install ./my-pkg.tgz")
        assert refs == []


class TestParseCargo:
    def test_install(self):
        refs = parse_install_commands("cargo install ripgrep")
        assert refs == [PackageRef(ecosystem="crates.io", name="ripgrep")]

    def test_add_with_version(self):
        refs = parse_install_commands("cargo add serde@1.0")
        assert refs == [PackageRef(ecosystem="crates.io", name="serde", version="1.0")]

    def test_version_flag_skipped(self):
        refs = parse_install_commands("cargo install --version 1.0 ripgrep")
        assert refs == [PackageRef(ecosystem="crates.io", name="ripgrep")]


class TestParseGo:
    def test_install(self):
        refs = parse_install_commands("go install github.com/foo/bar@latest")
        assert refs == [PackageRef(ecosystem="Go", name="github.com/foo/bar", version="latest")]

    def test_get(self):
        refs = parse_install_commands("go get golang.org/x/tools")
        assert refs == [PackageRef(ecosystem="Go", name="golang.org/x/tools")]


class TestParseGem:
    def test_install(self):
        refs = parse_install_commands("gem install rails")
        assert refs == [PackageRef(ecosystem="RubyGems", name="rails")]

    def test_with_version_flag(self):
        refs = parse_install_commands("gem install rails -v 7.0")
        # -v consumes its next token (7.0), leaving only rails.
        assert refs == [PackageRef(ecosystem="RubyGems", name="rails")]


class TestParseComposer:
    def test_require(self):
        refs = parse_install_commands("composer require guzzlehttp/guzzle")
        assert refs == [PackageRef(ecosystem="Packagist", name="guzzlehttp/guzzle")]

    def test_require_with_version(self):
        refs = parse_install_commands("composer require guzzlehttp/guzzle:^7.0")
        assert refs == [PackageRef(ecosystem="Packagist", name="guzzlehttp/guzzle", version="^7.0")]

    def test_global_require(self):
        refs = parse_install_commands("composer global require phpunit/phpunit")
        assert refs == [PackageRef(ecosystem="Packagist", name="phpunit/phpunit")]


class TestChaining:
    def test_and_chain(self):
        refs = parse_install_commands("pip install foo && npm add bar")
        assert [r.name for r in refs] == ["foo", "bar"]

    def test_or_chain(self):
        refs = parse_install_commands("pip install foo || pip install baz")
        assert [r.name for r in refs] == ["foo", "baz"]

    def test_semicolon(self):
        refs = parse_install_commands("echo hi; pip install foo")
        assert [r.name for r in refs] == ["foo"]

    def test_pipe(self):
        refs = parse_install_commands("echo y | gem install rails")
        assert [r.name for r in refs] == ["rails"]


class TestNonInstall:
    def test_unrecognised_command(self):
        assert parse_install_commands("echo hello") == []

    def test_empty(self):
        assert parse_install_commands("") == []

    def test_pip_without_install(self):
        assert parse_install_commands("pip list") == []

    def test_bad_shell_syntax(self):
        # Unclosed quote -> shlex raises; parser returns [].
        assert parse_install_commands("pip install 'foo") == []


# =============================================================================
# Allowlist
# =============================================================================


class TestAllowlist:
    def test_exact_ecosystem_name(self):
        assert is_allowlisted(PackageRef("PyPI", "requests"), frozenset({"PyPI:requests"}))

    def test_bare_name(self):
        assert is_allowlisted(PackageRef("PyPI", "requests"), frozenset({"requests"}))

    def test_not_in_list(self):
        assert not is_allowlisted(PackageRef("PyPI", "flask"), frozenset({"PyPI:requests"}))

    def test_wrong_ecosystem(self):
        assert not is_allowlisted(PackageRef("npm", "requests"), frozenset({"PyPI:requests"}))


# =============================================================================
# Severity
# =============================================================================


class TestSeverity:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (9.5, Severity.CRITICAL),
            (9.0, Severity.CRITICAL),
            (7.5, Severity.HIGH),
            (7.0, Severity.HIGH),
            (5.0, Severity.MEDIUM),
            (4.0, Severity.MEDIUM),
            (2.0, Severity.LOW),
            (0.1, Severity.LOW),
            (0.0, Severity.UNKNOWN),
        ],
    )
    def test_from_cvss_score(self, score: float, expected: Severity):
        assert Severity.from_cvss_score(score) is expected

    @pytest.mark.parametrize(
        ("label", "expected"),
        [
            ("HIGH", Severity.HIGH),
            ("high", Severity.HIGH),
            ("critical", Severity.CRITICAL),
            ("bogus", Severity.UNKNOWN),
            (None, Severity.UNKNOWN),
        ],
    )
    def test_from_label(self, label: str | None, expected: Severity):
        assert Severity.from_label(label) is expected

    def test_ordering(self):
        assert Severity.HIGH > Severity.MEDIUM > Severity.LOW


class TestCvssVectorParsing:
    """CVSS v3 base score computation from vector strings.

    Reference scores below come from NVD / FIRST CVSS calculators for well-known CVEs.
    """

    @pytest.mark.parametrize(
        ("vector", "expected"),
        [
            # CVE-2021-44228 (Log4Shell) — 10.0
            ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),
            # "C:H / I:N / A:N" unprivileged network — 7.5
            ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N", 7.5),
            # "C:L / I:N / A:N" unprivileged network — 5.3
            ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:N/A:N", 5.3),
            # Local, high complexity, required privs + UI, low conf impact only — 1.8
            ("CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:N/A:N", 1.8),
        ],
    )
    def test_known_vectors(self, vector: str, expected: float):
        assert _cvss3_base_score(vector) == pytest.approx(expected, abs=0.05)

    def test_cvss_v3_0_prefix(self):
        # 3.0 vectors use the same metric set and formula.
        assert _cvss3_base_score("CVSS:3.0/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == pytest.approx(9.8, abs=0.1)

    def test_missing_required_metric_returns_none(self):
        assert _cvss3_base_score("CVSS:3.1/AV:N/AC:L") is None

    def test_unknown_metric_value_returns_none(self):
        assert _cvss3_base_score("CVSS:3.1/AV:Z/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (8.1, 8.1),
            ("8.1", 8.1),
            ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H", 10.0),
            ("bogus", None),
            (None, None),
        ],
    )
    def test_parse_cvss_score_variants(self, raw: object, expected: float | None):
        result = _parse_cvss_score(raw)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected, abs=0.05)

    def test_real_vector_through_extract_max_severity(self):
        # A log4shell-style entry should come out CRITICAL.
        entry = {
            "severity": [
                {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H"},
            ]
        }
        assert _extract_max_severity(entry) is Severity.CRITICAL


class TestExtractMaxSeverity:
    def test_cvss_score_as_float(self):
        entry = {"severity": [{"type": "CVSS_V3", "score": 9.5}]}
        assert _extract_max_severity(entry) is Severity.CRITICAL

    def test_database_specific_label(self):
        entry = {"database_specific": {"severity": "HIGH"}}
        assert _extract_max_severity(entry) is Severity.HIGH

    def test_affected_database_specific(self):
        entry = {"affected": [{"database_specific": {"severity": "MEDIUM"}}]}
        assert _extract_max_severity(entry) is Severity.MEDIUM

    def test_multiple_sources_takes_max(self):
        entry = {
            "severity": [{"score": 4.0}],  # MEDIUM
            "database_specific": {"severity": "CRITICAL"},
        }
        assert _extract_max_severity(entry) is Severity.CRITICAL

    def test_empty(self):
        assert _extract_max_severity({}) is Severity.UNKNOWN


class TestParseOsvResponse:
    def test_no_vulns(self):
        assert _parse_osv_response({}) == []
        assert _parse_osv_response({"vulns": []}) == []

    def test_not_a_dict(self):
        assert _parse_osv_response("garbage") == []
        assert _parse_osv_response(None) == []

    def test_single_vuln(self):
        payload = {
            "vulns": [
                {
                    "id": "GHSA-xxxx",
                    "summary": "Remote code execution",
                    "database_specific": {"severity": "CRITICAL"},
                }
            ]
        }
        result = _parse_osv_response(payload)
        assert len(result) == 1
        assert result[0].id == "GHSA-xxxx"
        assert result[0].severity is Severity.CRITICAL

    def test_falls_back_to_details(self):
        payload = {"vulns": [{"id": "X", "details": "Long details"}]}
        assert _parse_osv_response(payload)[0].summary == "Long details"


# =============================================================================
# Filtering
# =============================================================================


def _result(*severities: Severity) -> PackageCheckResult:
    return PackageCheckResult(
        package=PackageRef("PyPI", "foo"),
        vulns=[VulnInfo(id=f"v{i}", summary="", severity=s) for i, s in enumerate(severities)],
    )


class TestFilterBlocking:
    def test_passes_through_blocking(self):
        results = [_result(Severity.HIGH), _result(Severity.LOW)]
        assert filter_blocking(results, Severity.HIGH) == [results[0]]

    def test_empty_returns_empty(self):
        assert filter_blocking([], Severity.HIGH) == []

    def test_threshold_inclusive(self):
        results = [_result(Severity.HIGH)]
        assert filter_blocking(results, Severity.HIGH) == results


class TestPackageCheckResult:
    def test_max_severity_empty(self):
        r = PackageCheckResult(package=PackageRef("PyPI", "foo"))
        assert r.max_severity is Severity.UNKNOWN

    def test_max_severity_picks_worst(self):
        r = _result(Severity.LOW, Severity.CRITICAL, Severity.MEDIUM)
        assert r.max_severity is Severity.CRITICAL

    def test_blocking_vulns(self):
        r = _result(Severity.LOW, Severity.HIGH)
        assert len(r.blocking_vulns(Severity.HIGH)) == 1


# =============================================================================
# Formatters
# =============================================================================


class TestFormatters:
    def test_blocked_message_includes_package_and_cve(self):
        result = PackageCheckResult(
            package=PackageRef("PyPI", "evil"),
            vulns=[VulnInfo(id="CVE-2024-1", summary="RCE bug", severity=Severity.CRITICAL)],
        )
        msg = format_blocked_message([result], Severity.HIGH, command="pip install evil")
        assert "evil" in msg
        assert "CVE-2024-1" in msg
        assert "CRITICAL" in msg
        assert "pip install evil" in msg
        assert "Remediation" in msg

    def test_blocked_message_truncates_long_list(self):
        vulns = [VulnInfo(id=f"CVE-{i}", summary="bad", severity=Severity.HIGH) for i in range(10)]
        result = PackageCheckResult(package=PackageRef("PyPI", "foo"), vulns=vulns)
        msg = format_blocked_message([result], Severity.HIGH)
        assert "5 more" in msg  # shows truncation

    def test_incoming_warning_contains_action(self):
        result = PackageCheckResult(
            package=PackageRef("PyPI", "evil"),
            vulns=[VulnInfo(id="CVE-2024-1", summary="RCE", severity=Severity.HIGH)],
        )
        msg = format_incoming_warning([result], Severity.HIGH)
        assert "pip uninstall evil" in msg
        assert "SECURITY WARNING" in msg

    def test_incoming_warning_npm_action(self):
        result = PackageCheckResult(
            package=PackageRef("npm", "evil"),
            vulns=[VulnInfo(id="x", summary="", severity=Severity.HIGH)],
        )
        msg = format_incoming_warning([result], Severity.HIGH)
        assert "npm uninstall evil" in msg


# =============================================================================
# Config
# =============================================================================


class TestConfig:
    def test_defaults(self):
        cfg = SupplyChainGuardConfig()
        assert cfg.severity_threshold == "HIGH"
        assert cfg.severity_threshold_enum is Severity.HIGH
        assert cfg.fail_closed is False
        assert cfg.allowlist == []

    def test_threshold_enum_case_insensitive(self):
        cfg = SupplyChainGuardConfig(severity_threshold="critical")
        assert cfg.severity_threshold_enum is Severity.CRITICAL


# =============================================================================
# OSVClient (HTTP client mocked)
# =============================================================================


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class TestOSVClient:
    @pytest.mark.asyncio
    async def test_query_posts_correct_body(self):
        captured: dict[str, Any] = {}

        async def fake_post(url: str, json: dict[str, Any], timeout: float | None = None) -> _FakeResponse:
            captured["url"] = url
            captured["body"] = json
            captured["timeout"] = timeout
            return _FakeResponse({"vulns": []})

        fake_client = MagicMock()
        fake_client.post = AsyncMock(side_effect=fake_post)

        osv = OSVClient(api_url="https://example/osv", timeout_seconds=2.5, http_client=fake_client)
        result = await osv.query(PackageRef("PyPI", "requests", version="2.31.0"))

        assert result == []
        assert captured["url"] == "https://example/osv"
        assert captured["body"] == {
            "package": {"name": "requests", "ecosystem": "PyPI"},
            "version": "2.31.0",
        }
        assert captured["timeout"] == 2.5

    @pytest.mark.asyncio
    async def test_query_returns_parsed_vulns(self):
        payload = {
            "vulns": [
                {
                    "id": "GHSA-x",
                    "summary": "bad",
                    "database_specific": {"severity": "HIGH"},
                }
            ]
        }
        fake_client = MagicMock()
        fake_client.post = AsyncMock(return_value=_FakeResponse(payload))
        osv = OSVClient(http_client=fake_client)
        result = await osv.query(PackageRef("PyPI", "foo"))
        assert result == [VulnInfo(id="GHSA-x", summary="bad", severity=Severity.HIGH)]

    @pytest.mark.asyncio
    async def test_query_raises_on_http_error(self):
        fake_client = MagicMock()
        fake_client.post = AsyncMock(return_value=_FakeResponse({}, status=500))
        osv = OSVClient(http_client=fake_client)
        with pytest.raises(RuntimeError):
            await osv.query(PackageRef("PyPI", "foo"))

    @pytest.mark.asyncio
    async def test_query_omits_version_when_absent(self):
        captured: dict[str, Any] = {}

        async def fake_post(url: str, json: dict[str, Any], timeout: float | None = None) -> _FakeResponse:
            captured.update(json)
            return _FakeResponse({"vulns": []})

        fake_client = MagicMock()
        fake_client.post = AsyncMock(side_effect=fake_post)
        osv = OSVClient(http_client=fake_client)
        await osv.query(PackageRef("PyPI", "foo"))
        assert "version" not in captured


# =============================================================================
# VulnInfo round-trip
# =============================================================================


class TestVulnInfoRoundtrip:
    def test_to_dict_and_back(self):
        vuln = VulnInfo(id="CVE-1", summary="bad", severity=Severity.CRITICAL)
        clone = VulnInfo.from_dict(json.loads(json.dumps(vuln.to_dict())))
        assert clone == vuln
