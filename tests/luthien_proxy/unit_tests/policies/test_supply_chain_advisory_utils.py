"""Unit tests for SupplyChainAdvisoryPolicy utility helpers."""

from __future__ import annotations

import pytest

from luthien_proxy.policies.supply_chain_advisory_utils import (
    OSVClient,
    PackageCheckResult,
    PackageRef,
    Severity,
    SupplyChainAdvisoryConfig,
    VulnInfo,
    _cvss3_base_score,
    _extract_max_severity,
    _parse_cvss_score,
    extract_install_packages,
    extract_tool_result_packages,
    format_advisory_message,
    format_untrusted_summary,
    redact_credentials,
)

# =============================================================================
# Severity
# =============================================================================


class TestSeverity:
    def test_from_label_known(self):
        assert Severity.from_label("HIGH") is Severity.HIGH
        assert Severity.from_label("critical") is Severity.CRITICAL
        assert Severity.from_label("low") is Severity.LOW

    def test_from_label_unknown(self):
        assert Severity.from_label(None) is Severity.UNKNOWN
        assert Severity.from_label("") is Severity.UNKNOWN
        assert Severity.from_label("bogus") is Severity.UNKNOWN

    @pytest.mark.parametrize(
        "score,expected",
        [
            (9.5, Severity.CRITICAL),
            (9.0, Severity.CRITICAL),
            (7.5, Severity.HIGH),
            (7.0, Severity.HIGH),
            (4.0, Severity.MEDIUM),
            (1.0, Severity.LOW),
            (0.0, Severity.UNKNOWN),
        ],
    )
    def test_from_cvss_score(self, score: float, expected: Severity):
        assert Severity.from_cvss_score(score) is expected

    def test_ordering(self):
        assert Severity.CRITICAL > Severity.HIGH > Severity.MEDIUM > Severity.LOW > Severity.UNKNOWN


# =============================================================================
# Config validation
# =============================================================================


class TestConfig:
    def test_defaults(self):
        cfg = SupplyChainAdvisoryConfig()
        assert cfg.advisory_severity_threshold == "HIGH"
        assert cfg.severity_threshold_enum is Severity.HIGH
        assert cfg.warn_on_osv_error is True
        assert cfg.hard_block_versions == ()
        assert cfg.bash_tool_names == ("Bash",)

    def test_bash_tool_names_from_list(self):
        # The validator coerces lists into tuples; pyright doesn't see this
        # so the type: ignore is needed.
        cfg = SupplyChainAdvisoryConfig(bash_tool_names=["Bash", "Terminal"])  # type: ignore[arg-type]
        assert cfg.bash_tool_names == ("Bash", "Terminal")

    def test_invalid_severity_threshold(self):
        with pytest.raises(ValueError, match="unknown severity threshold"):
            SupplyChainAdvisoryConfig(advisory_severity_threshold="HIH")

    def test_hard_block_reserved_in_v1(self):
        with pytest.raises(ValueError, match="reserved for a future release"):
            SupplyChainAdvisoryConfig(hard_block_versions=["PyPI:axios:1.6.8"])  # type: ignore[arg-type]

    def test_empty_hard_block_ok(self):
        cfg = SupplyChainAdvisoryConfig(hard_block_versions=[])  # type: ignore[arg-type]
        assert cfg.hard_block_versions == ()


# =============================================================================
# extract_install_packages — regex happy paths
# =============================================================================


class TestExtractInstallPackages:
    def test_pip_pinned(self):
        pkgs = extract_install_packages("pip install requests==2.31.0")
        assert pkgs == [PackageRef("PyPI", "requests", "2.31.0")]

    def test_pip_unversioned(self):
        pkgs = extract_install_packages("pip install flask")
        assert pkgs == [PackageRef("PyPI", "flask", None)]

    def test_pip3(self):
        pkgs = extract_install_packages("pip3 install numpy==1.26.0")
        assert pkgs == [PackageRef("PyPI", "numpy", "1.26.0")]

    def test_uv_pip_install(self):
        pkgs = extract_install_packages("uv pip install litellm==1.48.0")
        assert pkgs == [PackageRef("PyPI", "litellm", "1.48.0")]

    def test_uv_add(self):
        pkgs = extract_install_packages("uv add requests==2.31.0")
        assert pkgs == [PackageRef("PyPI", "requests", "2.31.0")]

    def test_poetry_add(self):
        pkgs = extract_install_packages("poetry add django==5.0")
        assert pkgs == [PackageRef("PyPI", "django", "5.0")]

    def test_pipenv_install(self):
        pkgs = extract_install_packages("pipenv install flask==3.0.0")
        assert pkgs == [PackageRef("PyPI", "flask", "3.0.0")]

    def test_conda_install(self):
        pkgs = extract_install_packages("conda install numpy")
        assert pkgs == [PackageRef("PyPI", "numpy", None)]

    def test_npm_install_versioned(self):
        pkgs = extract_install_packages("npm install axios@1.6.8")
        assert pkgs == [PackageRef("npm", "axios", "1.6.8")]

    def test_npm_install_unversioned(self):
        pkgs = extract_install_packages("npm install left-pad")
        assert pkgs == [PackageRef("npm", "left-pad", None)]

    def test_npm_install_scoped(self):
        pkgs = extract_install_packages("npm install @scope/pkg@1.2.3")
        assert pkgs == [PackageRef("npm", "@scope/pkg", "1.2.3")]

    def test_yarn_add(self):
        pkgs = extract_install_packages("yarn add react@18.2.0")
        assert pkgs == [PackageRef("npm", "react", "18.2.0")]

    def test_pnpm_add(self):
        pkgs = extract_install_packages("pnpm add typescript@5.4.0")
        assert pkgs == [PackageRef("npm", "typescript", "5.4.0")]

    def test_bun_add(self):
        pkgs = extract_install_packages("bun add react@18.2.0")
        assert pkgs == [PackageRef("npm", "react", "18.2.0")]

    def test_npm_i_alias(self):
        pkgs = extract_install_packages("npm i axios@1.6.8")
        assert pkgs == [PackageRef("npm", "axios", "1.6.8")]

    def test_multiple_packages(self):
        pkgs = extract_install_packages("pip install requests==2.31.0 flask==3.0.0 numpy")
        assert pkgs == [
            PackageRef("PyPI", "requests", "2.31.0"),
            PackageRef("PyPI", "flask", "3.0.0"),
            PackageRef("PyPI", "numpy", None),
        ]

    def test_pip_extras_stripped(self):
        pkgs = extract_install_packages("pip install requests[security]==2.31.0")
        assert pkgs == [PackageRef("PyPI", "requests", "2.31.0")]

    def test_flags_skipped(self):
        pkgs = extract_install_packages("pip install --no-deps requests==2.31.0")
        assert pkgs == [PackageRef("PyPI", "requests", "2.31.0")]

    def test_pip_range_stops_at_gt(self):
        # The regex args group stops at `>`, so `flask>=2.0` is truncated
        # to `flask` — emitted as an unversioned ref. Intentional: the loose
        # regex trades precision for simplicity.
        pkgs = extract_install_packages("pip install flask>=2.0")
        assert pkgs == [PackageRef("PyPI", "flask", None)]

    def test_npm_range_prefix_dropped(self):
        pkgs = extract_install_packages("npm install axios@^1.6.8")
        assert pkgs == [PackageRef("npm", "axios", None)]

    def test_npm_latest_tag_dropped(self):
        pkgs = extract_install_packages("npm install axios@latest")
        assert pkgs == [PackageRef("npm", "axios", None)]

    def test_local_path_skipped(self):
        pkgs = extract_install_packages("pip install ./local-package")
        assert pkgs == []

    def test_url_skipped(self):
        pkgs = extract_install_packages("pip install https://example.com/pkg.whl")
        assert pkgs == []

    def test_case_insensitive(self):
        pkgs = extract_install_packages("PIP INSTALL Requests==2.31.0")
        # The manager/verb regex is case-insensitive, name is taken verbatim.
        assert pkgs == [PackageRef("PyPI", "Requests", "2.31.0")]

    def test_no_install_command(self):
        assert extract_install_packages("echo hello") == []
        assert extract_install_packages("ls -la") == []

    def test_args_stop_at_chain_operator(self):
        # We deliberately don't follow chain operators — the brief calls this
        # out as explicitly out of scope. But packages before the operator
        # should still be caught.
        pkgs = extract_install_packages("pip install requests==2.31.0 && echo done")
        assert pkgs == [PackageRef("PyPI", "requests", "2.31.0")]

    def test_sh_c_is_best_effort(self):
        # Documented non-goal: we do not parse sh -c wrappers or quoting.
        # The regex may either find the inner command (with garbled version
        # tokens from surrounding quotes) or miss it entirely; either is
        # acceptable. Just assert the call doesn't crash.
        pkgs = extract_install_packages("sh -c 'pip install requests==2.31.0'")
        # Name should be recognised even if the version token gets mangled.
        assert any(p.name == "requests" for p in pkgs)


# =============================================================================
# extract_tool_result_packages — tool output scanning
# =============================================================================


class TestExtractToolResultPackages:
    def test_pip_freeze_output(self):
        text = "requests==2.31.0\nflask==3.0.0\nurllib3==2.0.7"
        pkgs = extract_tool_result_packages(text)
        assert PackageRef("PyPI", "requests", "2.31.0") in pkgs
        assert PackageRef("PyPI", "flask", "3.0.0") in pkgs
        assert PackageRef("PyPI", "urllib3", "2.0.7") in pkgs

    def test_package_json_format(self):
        text = '{"dependencies": {"axios": "1.6.8", "left-pad": "1.3.0"}}'
        pkgs = extract_tool_result_packages(text)
        assert PackageRef("npm", "axios", "1.6.8") in pkgs
        assert PackageRef("npm", "left-pad", "1.3.0") in pkgs

    def test_package_json_caret_prefix_stripped(self):
        text = '{"axios": "^1.6.8"}'
        pkgs = extract_tool_result_packages(text)
        assert PackageRef("npm", "axios", "1.6.8") in pkgs

    def test_scoped_npm_in_json(self):
        text = '{"@scope/pkg": "1.2.3"}'
        pkgs = extract_tool_result_packages(text)
        assert PackageRef("npm", "@scope/pkg", "1.2.3") in pkgs

    def test_skips_non_package_json_keys(self):
        text = '{"name": "my-app", "version": "1.0.0", "axios": "1.6.8"}'
        pkgs = extract_tool_result_packages(text)
        names = {p.name for p in pkgs}
        assert "axios" in names
        assert "name" not in names
        assert "version" not in names

    def test_pip_show_output(self):
        text = "Name: requests\nVersion: 2.31.0\n"
        # Plain "Version: 2.31.0" has no name==version pattern; the simple
        # scanner shouldn't hallucinate. Documented best-effort miss.
        assert extract_tool_result_packages(text) == []

    def test_empty_text(self):
        assert extract_tool_result_packages("") == []

    def test_no_matches(self):
        assert extract_tool_result_packages("hello world") == []


# =============================================================================
# OSV severity extraction
# =============================================================================


class TestExtractMaxSeverity:
    def test_cvss_v3_high(self):
        entry = {
            "severity": [
                {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"},
            ],
        }
        assert _extract_max_severity(entry) is Severity.CRITICAL

    def test_cvss_v3_medium(self):
        entry = {
            "severity": [
                {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N"},
            ],
        }
        assert _extract_max_severity(entry) is Severity.MEDIUM

    def test_numeric_score(self):
        entry = {"severity": [{"type": "CVSS_V3", "score": "7.5"}]}
        assert _extract_max_severity(entry) is Severity.HIGH

    def test_label_fallback(self):
        entry = {"database_specific": {"severity": "CRITICAL"}}
        assert _extract_max_severity(entry) is Severity.CRITICAL

    def test_affected_db_specific(self):
        entry = {"affected": [{"database_specific": {"severity": "HIGH"}}]}
        assert _extract_max_severity(entry) is Severity.HIGH

    def test_max_across_signals(self):
        entry = {
            "severity": [{"type": "CVSS_V3", "score": "4.0"}],
            "database_specific": {"severity": "CRITICAL"},
        }
        assert _extract_max_severity(entry) is Severity.CRITICAL

    def test_empty_entry(self):
        assert _extract_max_severity({}) is Severity.UNKNOWN

    def test_cvss_v4_falls_back_to_high(self):
        # CVSS v4 vectors we can't parse must NOT silently downgrade to UNKNOWN;
        # OSV only publishes these for real advisories, so fail-safe is HIGH.
        entry = {"severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N"}]}
        assert _extract_max_severity(entry) is Severity.HIGH

    def test_cvss_v4_with_label_prefers_label(self):
        entry = {
            "severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/AV:N/AC:L"}],
            "database_specific": {"severity": "CRITICAL"},
        }
        assert _extract_max_severity(entry) is Severity.CRITICAL


class TestParseCvssScore:
    def test_numeric(self):
        assert _parse_cvss_score(7.5) == 7.5
        assert _parse_cvss_score(10) == 10.0

    def test_bare_numeric_string(self):
        assert _parse_cvss_score("7.5") == 7.5

    def test_cvss_v3_vector(self):
        score = _parse_cvss_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
        assert score is not None
        assert score >= 9.0  # critical

    def test_cvss_v4_returns_none(self):
        assert _parse_cvss_score("CVSS:4.0/AV:N") is None

    def test_malformed(self):
        assert _parse_cvss_score("nonsense") is None

    def test_none(self):
        assert _parse_cvss_score(None) is None


class TestCvss3BaseScore:
    def test_critical(self):
        score = _cvss3_base_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
        assert score == 9.8

    def test_missing_metric_returns_none(self):
        assert _cvss3_base_score("CVSS:3.1/AV:N/AC:L/PR:N") is None

    def test_malformed_metric_returns_none(self):
        assert _cvss3_base_score("CVSS:3.1/AV:X/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") is None


# =============================================================================
# Credential redaction
# =============================================================================


class TestRedactCredentials:
    def test_url_credentials(self):
        text = "pip install --index-url https://user:secret@example.com/simple/ pkg"
        redacted = redact_credentials(text)
        assert "secret" not in redacted
        assert "<redacted>@" in redacted

    def test_token_flag(self):
        text = "pip install --token abc123xyz pkg"
        redacted = redact_credentials(text)
        assert "abc123xyz" not in redacted
        assert "<redacted>" in redacted

    def test_password_flag_equals(self):
        text = "tool --password=topsecret"
        redacted = redact_credentials(text)
        assert "topsecret" not in redacted

    def test_no_credentials(self):
        text = "pip install requests==2.31.0"
        assert redact_credentials(text) == text


# =============================================================================
# Formatting
# =============================================================================


class TestFormatting:
    def test_untrusted_summary_adds_delimiter(self):
        out = format_untrusted_summary("malicious SQL injection")
        assert out.startswith("<untrusted OSV advisory text>")

    def test_untrusted_summary_truncates(self):
        long = "x" * 500
        out = format_untrusted_summary(long)
        assert len(out) < 500

    def test_untrusted_summary_first_line_only(self):
        out = format_untrusted_summary("line1\nline2")
        assert "line1" in out
        assert "line2" not in out

    def test_format_advisory_flagged(self):
        result = PackageCheckResult(
            package=PackageRef("PyPI", "litellm", "1.48.0"),
            vulns=[VulnInfo("CVE-2025-12345", "remote code execution", Severity.CRITICAL)],
        )
        msg = format_advisory_message([result], Severity.HIGH, command="pip install litellm==1.48.0")
        assert "SUPPLY CHAIN ADVISORY" in msg
        assert "litellm@1.48.0" in msg
        assert "CVE-2025-12345" in msg
        assert "CRITICAL" in msg
        # Confirms the "not blocked" framing.
        assert "NOT blocked" in msg

    def test_format_advisory_errored(self):
        result = PackageCheckResult(
            package=PackageRef("PyPI", "requests", "2.31.0"),
            error="connection refused",
        )
        msg = format_advisory_message([result], Severity.HIGH)
        assert "OSV lookup failed" in msg
        assert "requests" in msg
        assert "connection refused" in msg


# =============================================================================
# OSV client (no network; uses dummy httpx client)
# =============================================================================


class _DummyResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _DummyClient:
    def __init__(self, payload: dict):
        self._payload = payload
        self.calls: list[dict] = []

    async def post(self, url: str, json: dict, timeout: float):
        self.calls.append(json)
        return _DummyResponse(self._payload)


@pytest.mark.asyncio
async def test_osv_client_versioned_query():
    client = _DummyClient({"vulns": []})
    osv = OSVClient(http_client=client)  # type: ignore[arg-type]
    await osv.query(PackageRef("PyPI", "requests", "2.31.0"))
    assert client.calls[0] == {
        "package": {"name": "requests", "ecosystem": "PyPI"},
        "version": "2.31.0",
    }


@pytest.mark.asyncio
async def test_osv_client_unversioned_query():
    client = _DummyClient({"vulns": []})
    osv = OSVClient(http_client=client)  # type: ignore[arg-type]
    await osv.query(PackageRef("npm", "axios", None))
    assert client.calls[0] == {"package": {"name": "axios", "ecosystem": "npm"}}


@pytest.mark.asyncio
async def test_osv_client_parses_vulns():
    payload = {
        "vulns": [
            {
                "id": "GHSA-xxxx",
                "summary": "bad",
                "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
            }
        ]
    }
    client = _DummyClient(payload)
    osv = OSVClient(http_client=client)  # type: ignore[arg-type]
    vulns = await osv.query(PackageRef("PyPI", "thing", "1.0"))
    assert len(vulns) == 1
    assert vulns[0].id == "GHSA-xxxx"
    assert vulns[0].severity is Severity.CRITICAL
