"""Unit tests for SupplyChainGatePolicy utility helpers."""

from __future__ import annotations

import pytest

from luthien_proxy.policies.supply_chain_gate_utils import (
    InstallMatch,
    OSVClient,
    PackageCheckResult,
    PackageRef,
    Severity,
    SupplyChainGateConfig,
    VulnInfo,
    _cvss3_base_score,
    _extract_max_severity,
    _parse_cvss_score,
    _shell_escape_single_quoted,
    build_blocked_command,
    build_lockfile_review_command,
    extract_install_commands,
    extract_install_packages,
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
        cfg = SupplyChainGateConfig()
        # Default threshold is CRITICAL to avoid spammy false-positive blocks.
        assert cfg.severity_threshold == "critical"
        assert cfg.severity_threshold_enum is Severity.CRITICAL
        assert cfg.osv_fail_mode == "warn"
        assert cfg.block_lockfile_installs is True
        assert cfg.explicit_blocklist == ()
        assert cfg.bash_tool_names == ("Bash",)

    def test_list_fields_coerced_to_tuple(self):
        cfg = SupplyChainGateConfig.model_validate(
            {
                "bash_tool_names": ["Bash", "Terminal"],
                "explicit_blocklist": ["PyPI:litellm:1.59.0", "npm:axios:1.6.8"],
            }
        )
        assert cfg.bash_tool_names == ("Bash", "Terminal")
        assert cfg.explicit_blocklist == ("PyPI:litellm:1.59.0", "npm:axios:1.6.8")

    @pytest.mark.parametrize(
        "field,value",
        [("severity_threshold", "HIH"), ("osv_fail_mode", "shrug")],
    )
    def test_literal_fields_reject_bad_values(self, field: str, value: str):
        with pytest.raises(ValueError):
            SupplyChainGateConfig.model_validate({field: value})

    def test_medium_threshold(self):
        cfg = SupplyChainGateConfig.model_validate({"severity_threshold": "medium"})
        assert cfg.severity_threshold_enum is Severity.MEDIUM


# =============================================================================
# PackageRef
# =============================================================================


class TestPackageRef:
    def test_cache_key(self):
        assert PackageRef("PyPI", "requests", "2.31.0").cache_key() == "osv:PyPI:requests:2.31.0"
        assert PackageRef("npm", "axios", None).cache_key() == "osv:npm:axios:*"

    def test_blocklist_key(self):
        assert PackageRef("PyPI", "litellm", "1.59.0").blocklist_key() == "PyPI:litellm:1.59.0"
        assert PackageRef("npm", "axios", None).blocklist_key() is None


# =============================================================================
# extract_install_packages — regex happy paths (managers)
# =============================================================================


class TestExtractInstallManagers:
    """Happy-path parsing for each supported package manager."""

    @pytest.mark.parametrize(
        "cmd,expected",
        [
            ("pip install requests==2.31.0", PackageRef("PyPI", "requests", "2.31.0")),
            ("pip install flask", PackageRef("PyPI", "flask", None)),
            ("pip3 install numpy==1.26.0", PackageRef("PyPI", "numpy", "1.26.0")),
            ("uv pip install foo==1.0", PackageRef("PyPI", "foo", "1.0")),
            ("uv add requests==2.31.0", PackageRef("PyPI", "requests", "2.31.0")),
            ("poetry add django==5.0", PackageRef("PyPI", "django", "5.0")),
            ("pipenv install flask==3.0.0", PackageRef("PyPI", "flask", "3.0.0")),
            ("conda install numpy", PackageRef("PyPI", "numpy", None)),
            ("npm install axios@1.6.8", PackageRef("npm", "axios", "1.6.8")),
            ("npm install left-pad", PackageRef("npm", "left-pad", None)),
            ("npm install @scope/pkg@1.2.3", PackageRef("npm", "@scope/pkg", "1.2.3")),
            ("yarn add react@18.2.0", PackageRef("npm", "react", "18.2.0")),
            ("pnpm add typescript@5.4.0", PackageRef("npm", "typescript", "5.4.0")),
            ("bun add react@18.2.0", PackageRef("npm", "react", "18.2.0")),
            ("npm i axios@1.6.8", PackageRef("npm", "axios", "1.6.8")),
            ("PIP INSTALL Requests==2.31.0", PackageRef("PyPI", "Requests", "2.31.0")),
        ],
    )
    def test_manager_happy_path(self, cmd: str, expected: PackageRef):
        assert extract_install_packages(cmd) == [expected]


# =============================================================================
# extract_install_packages — filtering and edge cases
# =============================================================================


class TestExtractInstallFiltering:
    def test_multiple_packages(self):
        pkgs = extract_install_packages("pip install requests==2.31.0 flask==3.0.0 numpy")
        assert pkgs == [
            PackageRef("PyPI", "requests", "2.31.0"),
            PackageRef("PyPI", "flask", "3.0.0"),
            PackageRef("PyPI", "numpy", None),
        ]

    def test_pip_extras_stripped(self):
        assert extract_install_packages("pip install requests[security]==2.31.0") == [
            PackageRef("PyPI", "requests", "2.31.0"),
        ]

    def test_flags_skipped(self):
        assert extract_install_packages("pip install --no-deps requests==2.31.0") == [
            PackageRef("PyPI", "requests", "2.31.0"),
        ]

    def test_pip_upgrade_multiple_unversioned(self):
        assert extract_install_packages("pip install -U pip setuptools wheel") == [
            PackageRef("PyPI", "pip", None),
            PackageRef("PyPI", "setuptools", None),
            PackageRef("PyPI", "wheel", None),
        ]

    def test_pip_range_stops_at_gt(self):
        # Args regex terminates at '>' so `flask>=2.0` is truncated to `flask`.
        assert extract_install_packages("pip install flask>=2.0") == [
            PackageRef("PyPI", "flask", None),
        ]

    @pytest.mark.parametrize(
        "spec,expected_version",
        [("axios@^1.6.8", None), ("axios@latest", None), ("axios@1.6.8", "1.6.8")],
    )
    def test_npm_version_normalisation(self, spec: str, expected_version: str | None):
        pkgs = extract_install_packages(f"npm install {spec}")
        assert pkgs == [PackageRef("npm", "axios", expected_version)]

    @pytest.mark.parametrize(
        "cmd",
        [
            "pip install ./local-package",
            "pip install /opt/my-package",
            "pip install https://example.com/pkg.whl",
            "pip install pkg.tar.gz",
            "pip install pkg.whl",
            "pip install pkg.zip",
            "pip install -r requirements.txt",
            "echo hello",
            "ls -la",
        ],
    )
    def test_no_packages_extracted(self, cmd: str):
        assert extract_install_packages(cmd) == []

    def test_args_stop_at_chain_operator(self):
        pkgs = extract_install_packages("pip install requests==2.31.0 && echo done")
        assert pkgs == [PackageRef("PyPI", "requests", "2.31.0")]


# =============================================================================
# Lockfile install detection
# =============================================================================


class TestLockfileDetection:
    @pytest.mark.parametrize(
        "cmd",
        [
            "npm ci",
            "yarn install --frozen-lockfile",
            "pnpm install --frozen-lockfile",
            "pip install -r requirements.txt",
            "pip install --requirement requirements.txt",
            "uv pip install -r requirements.txt",
        ],
    )
    def test_is_lockfile(self, cmd: str):
        matches = extract_install_commands(cmd)
        assert len(matches) == 1
        assert matches[0].is_lockfile is True
        assert matches[0].packages == ()

    @pytest.mark.parametrize("cmd", ["npm install express", "yarn add react@18.2.0"])
    def test_is_not_lockfile(self, cmd: str):
        matches = extract_install_commands(cmd)
        assert len(matches) == 1
        assert matches[0].is_lockfile is False

    def test_install_match_shape(self):
        matches = extract_install_commands("npm install axios@1.6.8")
        assert len(matches) == 1
        match = matches[0]
        assert isinstance(match, InstallMatch)
        assert match.manager == "npm"
        assert match.verb == "install"
        assert match.packages == (PackageRef("npm", "axios", "1.6.8"),)


# =============================================================================
# OSV severity extraction
# =============================================================================


class TestExtractMaxSeverity:
    def test_cvss_v3_critical(self):
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

    def test_cvss_v4_without_label_is_unknown(self):
        # v3 fix: do NOT promote unparseable vectors to HIGH.
        entry = {"severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N"}]}
        assert _extract_max_severity(entry) is Severity.UNKNOWN

    def test_cvss_v4_with_label_uses_label(self):
        entry = {
            "severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/AV:N/AC:L"}],
            "database_specific": {"severity": "CRITICAL"},
        }
        assert _extract_max_severity(entry) is Severity.CRITICAL

    def test_cvss_v4_with_low_label_stays_low(self):
        # A genuine LOW advisory must not accidentally become HIGH under
        # default thresholds.
        entry = {
            "severity": [{"type": "CVSS_V4", "score": "CVSS:4.0/AV:N"}],
            "database_specific": {"severity": "LOW"},
        }
        assert _extract_max_severity(entry) is Severity.LOW


class TestParseCvssScore:
    def test_numeric_forms(self):
        assert _parse_cvss_score(7.5) == 7.5
        assert _parse_cvss_score(10) == 10.0
        assert _parse_cvss_score("7.5") == 7.5

    def test_cvss_v3_vector(self):
        score = _parse_cvss_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
        assert score is not None
        assert score >= 9.0

    def test_cvss_v4_not_parseable(self):
        assert _parse_cvss_score("CVSS:4.0/AV:N") is None

    def test_unparseable_inputs(self):
        assert _parse_cvss_score("nonsense") is None
        assert _parse_cvss_score(None) is None


class TestCvss3BaseScore:
    def test_critical_exact(self):
        assert _cvss3_base_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8

    def test_missing_or_malformed_metric_returns_none(self):
        assert _cvss3_base_score("CVSS:3.1/AV:N/AC:L/PR:N") is None
        assert _cvss3_base_score("CVSS:3.1/AV:X/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") is None


# =============================================================================
# Redaction
# =============================================================================


class TestRedactCredentials:
    def test_url_credentials(self):
        redacted = redact_credentials("pip install --index-url https://user:secret@example.com/simple/ pkg")
        assert "secret" not in redacted
        assert "<redacted>@" in redacted

    @pytest.mark.parametrize(
        "text,secret",
        [
            ("pip install --token abc123xyz pkg", "abc123xyz"),
            ("tool --password=topsecret", "topsecret"),
        ],
    )
    def test_flag_values_redacted(self, text: str, secret: str):
        redacted = redact_credentials(text)
        assert secret not in redacted
        assert "<redacted>" in redacted

    def test_no_credentials(self):
        text = "pip install requests==2.31.0"
        assert redact_credentials(text) == text


# =============================================================================
# Blocked-command builders (shell escaping, content)
# =============================================================================


class TestShellEscape:
    def test_no_quotes(self):
        assert _shell_escape_single_quoted("hello world") == "hello world"

    def test_single_quote(self):
        # A single quote inside a single-quoted string becomes '\''
        assert _shell_escape_single_quoted("it's") == "it'\\''s"

    def test_backslash_untouched(self):
        # Inside single quotes, backslashes are literal — no double-escaping.
        assert _shell_escape_single_quoted("a\\b") == "a\\b"


class TestBuildBlockedCommand:
    def test_contains_sh_c_and_blocked_marker(self):
        result = PackageCheckResult(
            package=PackageRef("PyPI", "litellm", "1.59.0"),
            vulns=[VulnInfo("GHSA-xxxx", Severity.CRITICAL)],
        )
        out = build_blocked_command(
            "pip install litellm==1.59.0",
            [result],
            Severity.CRITICAL,
        )
        assert out.startswith("sh -c '")
        assert out.endswith("exit 42'")
        assert "LUTHIEN BLOCKED" in out

    def test_includes_package_cve_and_severity(self):
        result = PackageCheckResult(
            package=PackageRef("PyPI", "litellm", "1.59.0"),
            vulns=[VulnInfo("GHSA-abcd-1234", Severity.CRITICAL)],
        )
        out = build_blocked_command(
            "pip install litellm==1.59.0",
            [result],
            Severity.CRITICAL,
        )
        assert "litellm" in out
        assert "1.59.0" in out
        assert "GHSA-abcd-1234" in out
        assert "CRITICAL" in out
        # OSV URL link we fully control.
        assert "osv.dev/vulnerability/GHSA-abcd-1234" in out

    def test_redacts_credentials_in_original(self):
        result = PackageCheckResult(
            package=PackageRef("PyPI", "litellm", "1.59.0"),
            vulns=[VulnInfo("GHSA-x", Severity.CRITICAL)],
        )
        out = build_blocked_command(
            "pip install --token topsecret litellm==1.59.0",
            [result],
            Severity.CRITICAL,
        )
        assert "topsecret" not in out

    def test_explicit_blocklist_label(self):
        result = PackageCheckResult(
            package=PackageRef("npm", "axios", "1.6.8"),
            vulns=[],
            blocklisted=True,
        )
        out = build_blocked_command(
            "npm install axios@1.6.8",
            [result],
            Severity.CRITICAL,
        )
        assert "axios" in out
        assert "explicit blocklist" in out

    def test_does_not_leak_osv_summary(self):
        # VulnInfo no longer carries a summary field — regression guard.
        result = PackageCheckResult(
            package=PackageRef("PyPI", "litellm", "1.59.0"),
            vulns=[VulnInfo("GHSA-xxxx", Severity.CRITICAL)],
        )
        out = build_blocked_command(
            "pip install litellm==1.59.0",
            [result],
            Severity.CRITICAL,
        )
        assert "untrusted" not in out
        # No "summary:" key leak.
        assert "summary" not in out.lower()


class TestBuildLockfileReviewCommand:
    def test_npm_ci_shape(self):
        out = build_lockfile_review_command("npm ci", "npm", "ci")
        assert out.startswith("sh -c '")
        assert "npm ci --dry-run" in out
        assert "LUTHIEN" in out
        assert "exit 42" in out

    @pytest.mark.parametrize(
        "original,manager,verb,expected_dry_run",
        [
            (
                "yarn install --frozen-lockfile",
                "yarn",
                "install",
                "yarn install --mode=skip-build",
            ),
            (
                "pnpm install --frozen-lockfile",
                "pnpm",
                "install",
                "pnpm install --lockfile-only",
            ),
            (
                "pip install -r requirements.txt",
                "pip",
                "install",
                "pip install --dry-run -r requirements.txt",
            ),
        ],
    )
    def test_other_manager_dry_run(self, original: str, manager: str, verb: str, expected_dry_run: str):
        assert expected_dry_run in build_lockfile_review_command(original, manager, verb)


# =============================================================================
# OSV client (no network; uses dummy httpx client)
# =============================================================================


class _DummyResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _DummyClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    async def post(self, url: str, json: dict, timeout: float) -> _DummyResponse:
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


# =============================================================================
# PackageCheckResult
# =============================================================================


class TestPackageCheckResult:
    def test_triggers_by_severity(self):
        crit = PackageCheckResult(
            package=PackageRef("PyPI", "foo", "1.0"),
            vulns=[VulnInfo("G", Severity.CRITICAL)],
        )
        assert crit.triggers(Severity.HIGH) is True
        assert crit.triggers(Severity.CRITICAL) is True

        med = PackageCheckResult(
            package=PackageRef("PyPI", "foo", "1.0"),
            vulns=[VulnInfo("G", Severity.MEDIUM)],
        )
        assert med.triggers(Severity.HIGH) is False

    def test_blocklisted_triggers_regardless(self):
        result = PackageCheckResult(
            package=PackageRef("PyPI", "foo", "1.0"),
            vulns=[],
            blocklisted=True,
        )
        assert result.triggers(Severity.CRITICAL) is True

    def test_max_severity_empty(self):
        result = PackageCheckResult(package=PackageRef("PyPI", "foo", "1.0"))
        assert result.max_severity is Severity.UNKNOWN

    def test_triggering_vulns_filters_below_threshold(self):
        result = PackageCheckResult(
            package=PackageRef("PyPI", "foo", "1.0"),
            vulns=[VulnInfo("G1", Severity.LOW), VulnInfo("G2", Severity.CRITICAL)],
        )
        triggering = result.triggering_vulns(Severity.HIGH)
        assert len(triggering) == 1
        assert triggering[0].id == "G2"
