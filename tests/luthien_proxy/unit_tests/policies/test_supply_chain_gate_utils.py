"""Unit tests for SupplyChainGatePolicy utility helpers."""

from __future__ import annotations

import subprocess

import pytest

from luthien_proxy.policies.supply_chain_gate_utils import (
    InstallMatch,
    OSVClient,
    PackageCheckResult,
    PackageRef,
    Severity,
    SupplyChainGateConfig,
    VulnInfo,
    _canonical_ecosystem,
    _canonicalize_package,
    _cvss3_base_score,
    _extract_max_severity,
    _is_wrapper_command,
    _normalize_line_continuations,
    _parse_cvss_score,
    _shell_escape_single_quoted,
    build_blocked_command,
    build_lockfile_dry_run_command,
    build_lockfile_explain_refuse_command,
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
        # Blocklist entries are stored in canonical form (lowercase ecosystem +
        # PEP 503 name) so membership tests work regardless of user casing.
        assert cfg.explicit_blocklist == ("pypi:litellm:1.59.0", "npm:axios:1.6.8")

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
        # cache_key uses the OSV wire-form ecosystem so OSV responses cache
        # consistently regardless of how the PackageRef was spelled on input.
        assert PackageRef("PyPI", "requests", "2.31.0").cache_key() == "osv:PyPI:requests:2.31.0"
        assert PackageRef("pypi", "requests", "2.31.0").cache_key() == "osv:PyPI:requests:2.31.0"
        assert PackageRef("npm", "axios", None).cache_key() == "osv:npm:axios:*"

    def test_blocklist_key_uses_canonical_form(self):
        # blocklist_key uses lowercase ecosystem + PEP 503 name so the
        # explicit blocklist matches regardless of user casing/punctuation.
        assert PackageRef("PyPI", "litellm", "1.59.0").blocklist_key() == "pypi:litellm:1.59.0"
        assert PackageRef("pypi", "litellm", "1.59.0").blocklist_key() == "pypi:litellm:1.59.0"
        assert PackageRef("npm", "axios", None).blocklist_key() is None

    def test_canonicalization_collapses_case_variants(self):
        # Fatal #3: PyPI names collapse per PEP 503.
        a = PackageRef("PyPI", "Pillow", "10.0.0")
        b = PackageRef("pypi", "pillow", "10.0.0")
        assert a == b
        assert a.name == "pillow"
        assert a.display_name == "Pillow"
        # Punctuation variants also collapse.
        c = PackageRef("PyPI", "Pillow_Image", "1.0")
        d = PackageRef("PyPI", "pillow-image", "1.0")
        assert c == d
        assert c.name == "pillow-image"

    def test_npm_name_lowercased(self):
        # npm is case-insensitive for matching; both scoped and unscoped.
        assert PackageRef("npm", "Axios", "1.6.8") == PackageRef("npm", "axios", "1.6.8")
        assert PackageRef("npm", "@MyScope/Pkg", "1.0") == PackageRef("npm", "@myscope/pkg", "1.0")
        # Scope and name both lowercased.
        assert PackageRef("npm", "@MyScope/Pkg", "1.0").name == "@myscope/pkg"

    def test_osv_ecosystem_wire_form(self):
        assert PackageRef("pypi", "x", "1").osv_ecosystem == "PyPI"
        assert PackageRef("PyPI", "x", "1").osv_ecosystem == "PyPI"
        assert PackageRef("npm", "x", "1").osv_ecosystem == "npm"

    def test_display_name_preserved(self):
        # The original display name is preserved separately for error messages.
        ref = PackageRef("PyPI", "Pillow", "10.0.0")
        assert ref.display_name == "Pillow"


class TestCanonicalizationHelpers:
    def test_canonical_ecosystem(self):
        assert _canonical_ecosystem("PyPI") == "pypi"
        assert _canonical_ecosystem("NPM") == "npm"
        assert _canonical_ecosystem("  pypi  ") == "pypi"

    def test_canonicalize_pypi_pep503(self):
        assert _canonicalize_package("pypi", "Pillow") == "pillow"
        assert _canonicalize_package("PyPI", "Pillow_Image") == "pillow-image"
        assert _canonicalize_package("pypi", "Pillow.Image") == "pillow-image"
        assert _canonicalize_package("pypi", "requests--security") == "requests-security"

    def test_canonicalize_npm(self):
        assert _canonicalize_package("npm", "Axios") == "axios"
        assert _canonicalize_package("npm", "@MyScope/Pkg") == "@myscope/pkg"
        # Non-PEP503 punctuation stays (npm preserves ``-``/``_``).
        assert _canonicalize_package("npm", "left-pad") == "left-pad"


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

    def test_pip_captures_requirement_file(self):
        # Fatal #1: the dry-run must thread the original filename.
        matches = extract_install_commands("pip install -r dev-requirements.txt")
        assert len(matches) == 1
        assert matches[0].is_lockfile is True
        assert matches[0].requirement_file == "dev-requirements.txt"
        assert matches[0].constraint_file is None

    def test_pip_captures_constraint_file(self):
        matches = extract_install_commands("pip install -r foo.txt -c constraints.txt")
        assert len(matches) == 1
        assert matches[0].requirement_file == "foo.txt"
        assert matches[0].constraint_file == "constraints.txt"

    def test_pip_long_form_requirement(self):
        matches = extract_install_commands("pip install --requirement=bar.txt")
        assert len(matches) == 1
        assert matches[0].is_lockfile is True
        assert matches[0].requirement_file == "bar.txt"

    def test_bare_pip_install_is_not_lockfile(self):
        # Fatal #1: `pip install` with no -r/-c is NOT a lockfile install.
        # It also has no extractable packages, so it yields no flagged match.
        matches = extract_install_commands("pip install")
        # Either zero matches (no packages, no lockfile) or one match with
        # is_lockfile=False and empty packages — both are acceptable here.
        for m in matches:
            assert m.is_lockfile is False


# =============================================================================
# Fatal #4 — wrapper command suppression
# =============================================================================


class TestWrapperCommands:
    @pytest.mark.parametrize(
        "cmd",
        [
            "docker run --rm python:3.11 pip install requests==2.31.0",
            "docker exec -it container pip install requests==2.31.0",
            "podman run --rm img pip install foo",
            "nerdctl run img npm install axios@1.6.8",
            "kubectl exec deploy/foo -- pip install bar",
            "oc exec pod -- pip install bar",
            "ssh host pip install bar",
            "nsenter -t 1 -m pip install bar",
            'nix-shell -p python3Packages.requests --run "pip install bar"',
            "tox -e py311 -- pip install bar",
            "nox -s test -- pip install bar",
            "vagrant ssh -c 'pip install bar'",
        ],
    )
    def test_wrapper_suppresses_extraction(self, cmd: str):
        # Fatal #4: wrapper commands run the install in a sandboxed env
        # that does not affect the host. Return zero matches.
        assert extract_install_commands(cmd) == []
        assert extract_install_packages(cmd) == []

    @pytest.mark.parametrize(
        "cmd",
        [
            "sudo pip install requests==2.31.0",
            "env FOO=bar pip install requests==2.31.0",
            "pip install requests==2.31.0",
        ],
    )
    def test_sudo_and_env_are_not_wrappers(self, cmd: str):
        # sudo/env run the install on the host — still extracted.
        pkgs = extract_install_packages(cmd)
        assert pkgs == [PackageRef("PyPI", "requests", "2.31.0")]

    def test_wrapper_in_chained_segment(self):
        # Chain: `cd /tmp && docker run ...` — the docker segment should
        # suppress. This is a cooperative-case assumption; we're not trying
        # to be adversarial-robust.
        assert extract_install_commands("cd /tmp && docker run --rm img pip install foo") == []

    def test_is_wrapper_command_detects_leading_word(self):
        assert _is_wrapper_command("docker run img pip install foo") is True
        assert _is_wrapper_command("sudo pip install foo") is False
        assert _is_wrapper_command("pip install foo") is False


# =============================================================================
# Fatal #5 — line continuation normalization
# =============================================================================


class TestLineContinuations:
    def test_normalize_joins_lines(self):
        # Backslash-newline and any trailing whitespace on the next line
        # collapse into a single space (the space before the backslash is
        # preserved).
        assert _normalize_line_continuations("pip install \\\n  requests") == "pip install  requests"

    def test_single_continuation(self):
        # Fatal #5: `pip install \\\n  requests==2.31.0` used to capture `\`
        # as the package name. After normalization it extracts the real pkg.
        pkgs = extract_install_packages("pip install \\\n  requests==2.31.0")
        assert pkgs == [PackageRef("PyPI", "requests", "2.31.0")]

    def test_multiple_continuations(self):
        cmd = "pip install \\\n  pkg-a==1.0 \\\n  pkg-b==2.0"
        pkgs = extract_install_packages(cmd)
        assert pkgs == [
            PackageRef("PyPI", "pkg-a", "1.0"),
            PackageRef("PyPI", "pkg-b", "2.0"),
        ]

    def test_no_continuation_still_works(self):
        pkgs = extract_install_packages("pip install requests==2.31.0")
        assert pkgs == [PackageRef("PyPI", "requests", "2.31.0")]


# =============================================================================
# Fatal #3 — explicit blocklist canonicalization
# =============================================================================


class TestBlocklistCanonicalization:
    def test_config_canonicalizes_pypi_names(self):
        cfg = SupplyChainGateConfig.model_validate({"explicit_blocklist": ["PyPI:Pillow:10.0.0"]})
        # Stored canonical form: lowercase ecosystem + PEP 503 name.
        assert "pypi:pillow:10.0.0" in cfg.explicit_blocklist

    def test_config_canonicalizes_punctuation(self):
        cfg = SupplyChainGateConfig.model_validate({"explicit_blocklist": ["PyPI:pillow_image:1.0"]})
        assert "pypi:pillow-image:1.0" in cfg.explicit_blocklist

    def test_config_canonicalizes_npm_scoped(self):
        cfg = SupplyChainGateConfig.model_validate({"explicit_blocklist": ["npm:@MyScope/Pkg:1.0"]})
        assert "npm:@myscope/pkg:1.0" in cfg.explicit_blocklist

    def test_config_accepts_lowercase_ecosystem(self):
        cfg = SupplyChainGateConfig.model_validate({"explicit_blocklist": ["pypi:requests:2.0"]})
        assert "pypi:requests:2.0" in cfg.explicit_blocklist

    def test_packageref_blocklist_key_matches_canonical_entry(self):
        # End-to-end: a PyPI:Pillow:10.0.0 blocklist entry matches a
        # `pip install pillow==10.0.0` extracted package.
        cfg = SupplyChainGateConfig.model_validate({"explicit_blocklist": ["PyPI:Pillow:10.0.0"]})
        extracted = extract_install_packages("pip install pillow==10.0.0")[0]
        assert extracted.blocklist_key() in cfg.explicit_blocklist

    def test_packageref_blocklist_key_matches_punctuation_variant(self):
        cfg = SupplyChainGateConfig.model_validate({"explicit_blocklist": ["pypi:pillow-image:1.0"]})
        extracted = extract_install_packages("pip install Pillow_Image==1.0")[0]
        assert extracted.blocklist_key() in cfg.explicit_blocklist

    def test_packageref_blocklist_key_matches_npm_scoped_variant(self):
        cfg = SupplyChainGateConfig.model_validate({"explicit_blocklist": ["npm:@MyScope/Pkg:1.0"]})
        extracted = extract_install_packages("npm install @myscope/pkg@1.0")[0]
        assert extracted.blocklist_key() in cfg.explicit_blocklist

    def test_packageref_blocklist_key_matches_npm_case_variant(self):
        cfg = SupplyChainGateConfig.model_validate({"explicit_blocklist": ["npm:axios:1.6.8"]})
        extracted = extract_install_packages("npm install Axios@1.6.8")[0]
        assert extracted.blocklist_key() in cfg.explicit_blocklist


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


class TestBuildLockfileDryRunCommand:
    def test_npm_ci_shape(self):
        out = build_lockfile_dry_run_command("npm ci", "npm", "ci")
        assert out.startswith("sh -c '")
        assert "npm ci --dry-run" in out
        assert "LUTHIEN" in out
        assert "exit 42" in out

    def test_pip_defaults_to_no_requirement_file(self):
        # Fatal #1: pip install with no -r/-c must NOT produce a bogus
        # hardcoded `-r requirements.txt`. Bare pip install shouldn't reach
        # this path at all — but if a caller mis-uses the API, the output
        # should just be `pip install --dry-run`, not `... -r requirements.txt`.
        out = build_lockfile_dry_run_command("pip install", "pip", "install")
        assert "requirements.txt" not in out
        assert "pip install --dry-run" in out

    def test_pip_threads_requirement_filename(self):
        # Fatal #1: the dry-run must reference the ORIGINAL filename,
        # not a hardcoded `requirements.txt`.
        out = build_lockfile_dry_run_command(
            "pip install -r dev-requirements.txt",
            "pip",
            "install",
            requirement_file="dev-requirements.txt",
        )
        assert "dev-requirements.txt" in out
        # No spurious bare reference to `requirements.txt`.
        assert "requirements.txt" in out  # substring of dev-requirements.txt
        # More precise check: no invocation uses bare `-r requirements.txt`.
        assert "'requirements.txt'" not in out

    def test_pip_threads_constraint_filename(self):
        out = build_lockfile_dry_run_command(
            "pip install -r dev-requirements.txt -c constraints.txt",
            "pip",
            "install",
            requirement_file="dev-requirements.txt",
            constraint_file="constraints.txt",
        )
        assert "dev-requirements.txt" in out
        assert "constraints.txt" in out
        assert "-c" in out

    def test_uv_pip_prefix(self):
        out = build_lockfile_dry_run_command(
            "uv pip install -r requirements.txt",
            "uv",
            "install",
            requirement_file="requirements.txt",
        )
        assert "uv pip install --dry-run" in out

    def test_raises_on_yarn(self):
        # yarn has no real dry-run; caller must use explain-refuse instead.
        with pytest.raises(ValueError):
            build_lockfile_dry_run_command("yarn install", "yarn", "install")

    def test_raises_on_pnpm(self):
        with pytest.raises(ValueError):
            build_lockfile_dry_run_command("pnpm install", "pnpm", "install")


class TestBuildLockfileExplainRefuseCommand:
    def test_yarn_shape(self):
        out = build_lockfile_explain_refuse_command("yarn install --frozen-lockfile", "yarn", "install")
        assert out.startswith("sh -c '")
        assert "exit 42" in out

    def test_pnpm_shape(self):
        out = build_lockfile_explain_refuse_command("pnpm install --frozen-lockfile", "pnpm", "install")
        assert out.startswith("sh -c '")

    def test_raises_on_npm(self):
        with pytest.raises(ValueError):
            build_lockfile_explain_refuse_command("npm ci", "npm", "ci")


# =============================================================================
# SUBPROCESS EXECUTION TESTS — Major #2
#
# Every builder function that emits a bash substitute gets run through a real
# bash to verify it parses and behaves correctly. Substring-only tests hid
# Fatals #1 and #2 in earlier reviews.
# =============================================================================


def _run_bash(script: str, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=cwd, timeout=10)


class TestSubprocessExecution:
    def test_blocked_command_runs_and_exits_42(self):
        result = PackageCheckResult(
            package=PackageRef("PyPI", "litellm", "1.59.0"),
            vulns=[VulnInfo("GHSA-xxxx", Severity.CRITICAL)],
        )
        sub = build_blocked_command("pip install litellm==1.59.0", [result], Severity.CRITICAL)
        proc = _run_bash(sub)
        assert proc.returncode == 42
        assert "LUTHIEN BLOCKED" in proc.stderr
        assert "litellm" in proc.stderr
        assert "GHSA-xxxx" in proc.stderr
        assert proc.stdout == ""

    def test_blocked_command_ignores_attacker_metachars(self, tmp_path):
        # Classic quote-breakout injection: the original command contains
        # unbalanced quotes, shell metacharacters, and would execute arbitrary
        # commands if the builder naively interpolated the original text.
        result = PackageCheckResult(
            package=PackageRef("PyPI", "foo", "1.0"),
            vulns=[VulnInfo("GHSA-x", Severity.CRITICAL)],
        )
        marker = tmp_path / "LUTHIEN_PUNCH_TEST"
        attacker_cmd = f"pip install 'foo'; touch {marker}; echo 'hi"
        sub = build_blocked_command(attacker_cmd, [result], Severity.CRITICAL)
        proc = _run_bash(sub)
        assert proc.returncode == 42
        assert not marker.exists(), "attacker metacharacters should not execute"

    def test_blocked_command_handles_embedded_single_quotes(self):
        # Stress the shell-escape path — the builder must survive CVE IDs or
        # package names that contain single quotes (rare but possible in
        # hand-crafted blocklist entries).
        result = PackageCheckResult(
            package=PackageRef("npm", "weird'name", "1.0"),
            vulns=[VulnInfo("GHSA-y'all", Severity.CRITICAL)],
        )
        sub = build_blocked_command("npm install weird'name@1.0", [result], Severity.CRITICAL)
        proc = _run_bash(sub)
        assert proc.returncode == 42
        assert "LUTHIEN BLOCKED" in proc.stderr

    def test_npm_ci_dry_run_parses_and_exits_42(self, tmp_path):
        # Stub out the real npm invocation with ``true`` (no-op) so the test
        # doesn't need npm installed. The rest of the wrapper runs and we
        # assert the wrapper's own exit-42 semantics survive.
        sub = build_lockfile_dry_run_command("npm ci", "npm", "ci")
        stubbed = sub.replace("npm ci --dry-run", "true")
        proc = _run_bash(stubbed)
        assert proc.returncode == 42
        assert "LUTHIEN" in proc.stderr

    def test_pip_dry_run_runs_with_custom_filename(self, tmp_path):
        sub = build_lockfile_dry_run_command(
            "pip install -r dev-requirements.txt",
            "pip",
            "install",
            requirement_file="dev-requirements.txt",
        )
        # Stub out the pip call (pip may not be available). Replace the
        # entire pip invocation including its args with a no-op so quoting
        # stays balanced. We slice at ``pip install --dry-run`` and keep the
        # ``;`` terminator.
        before, _, after = sub.partition("pip install --dry-run")
        # Skip past the args until the next ``; printf`` that starts the
        # advisory printf. The inner script only has one ``; printf '\\n`` so
        # we can split on that.
        _, sep, rest = after.partition("; printf")
        stubbed = f"{before}true{sep}{rest}"
        proc = _run_bash(stubbed)
        assert proc.returncode == 42
        assert "LUTHIEN" in proc.stderr

    def test_pip_dry_run_with_constraint_filename(self):
        sub = build_lockfile_dry_run_command(
            "pip install -r dev-requirements.txt -c constraints.txt",
            "pip",
            "install",
            requirement_file="dev-requirements.txt",
            constraint_file="constraints.txt",
        )
        before, _, after = sub.partition("pip install --dry-run")
        _, sep, rest = after.partition("; printf")
        stubbed = f"{before}true{sep}{rest}"
        proc = _run_bash(stubbed)
        assert proc.returncode == 42

    def test_yarn_explain_refuse_runs_and_exits_42(self):
        sub = build_lockfile_explain_refuse_command("yarn install --frozen-lockfile", "yarn", "install")
        proc = _run_bash(sub)
        assert proc.returncode == 42
        combined = proc.stderr.lower()
        assert "yarn" in combined or "pnpm" in combined or "lockfile" in combined
        assert "cannot be safely previewed" in proc.stderr or "LUTHIEN BLOCKED" in proc.stderr

    def test_pnpm_explain_refuse_runs_and_exits_42(self):
        sub = build_lockfile_explain_refuse_command("pnpm install --frozen-lockfile", "pnpm", "install")
        proc = _run_bash(sub)
        assert proc.returncode == 42
        assert "LUTHIEN BLOCKED" in proc.stderr

    def test_yarn_explain_refuse_does_not_write_to_disk(self, tmp_path):
        # An explain-refuse must NOT execute yarn. If it did, yarn would try
        # to read package.json or write yarn.lock. Run inside an empty tmp_path
        # and verify nothing was created.
        before = sorted(p.name for p in tmp_path.iterdir())
        sub = build_lockfile_explain_refuse_command("yarn install", "yarn", "install")
        _run_bash(sub, cwd=str(tmp_path))
        after = sorted(p.name for p in tmp_path.iterdir())
        assert before == after, f"explain-refuse must not touch disk, got: {after}"


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
