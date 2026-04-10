"""Utilities for SupplyChainGuardPolicy.

This module contains the pure helpers the policy relies on:

- Data types for packages, vulnerabilities, and check results.
- A shell command parser that recognises install commands for several
  package ecosystems and extracts their package names.
- An OSV.dev client that queries for known vulnerabilities.
- A severity filter that decides whether a set of vulns is blocking.
- Formatters that render human-readable blocked / warning messages.

Keeping these helpers separate from the policy makes them easy to unit
test without having to drive the streaming pipeline.
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Final

import httpx
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# =============================================================================
# Types
# =============================================================================


class Severity(IntEnum):
    """Ordered severity levels. Higher is worse."""

    UNKNOWN = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @classmethod
    def from_label(cls, label: str | None) -> "Severity":
        """Parse a qualitative severity label like ``"HIGH"``."""
        if not label:
            return cls.UNKNOWN
        upper = label.strip().upper()
        if upper in cls.__members__:
            return cls[upper]
        return cls.UNKNOWN

    @classmethod
    def from_cvss_score(cls, score: float) -> "Severity":
        """Convert a CVSS numeric score into a qualitative bucket.

        Thresholds match the CVSS v3 qualitative rating scale.
        """
        if score >= 9.0:
            return cls.CRITICAL
        if score >= 7.0:
            return cls.HIGH
        if score >= 4.0:
            return cls.MEDIUM
        if score > 0.0:
            return cls.LOW
        return cls.UNKNOWN

    @property
    def label(self) -> str:
        """Human-readable label (``"HIGH"``, ``"CRITICAL"``, ...)."""
        return self.name


# OSV uses ecosystem identifiers that differ from the CLI name. This map
# converts a CLI install command to the OSV ecosystem string.
ECOSYSTEM_LABELS: Final[dict[str, str]] = {
    "pip": "PyPI",
    "npm": "npm",
    "cargo": "crates.io",
    "go": "Go",
    "gem": "RubyGems",
    "composer": "Packagist",
}


@dataclass(frozen=True)
class PackageRef:
    """A reference to a single package in a specific ecosystem."""

    ecosystem: str  # OSV ecosystem label, e.g. "PyPI"
    name: str
    version: str | None = None

    def cache_key(self) -> str:
        """Key used for caching this package's OSV lookup result.

        OSV returns version-specific results when a version is supplied in
        the query body, so the cache key must also be version-specific to
        avoid cross-version contamination.
        """
        version_part = self.version or "*"
        return f"osv:{self.ecosystem}:{self.name}:{version_part}"


@dataclass(frozen=True)
class VulnInfo:
    """Summary of a single OSV vulnerability."""

    id: str
    summary: str
    severity: Severity

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain JSON-safe dict for caching."""
        return {"id": self.id, "summary": self.summary, "severity": int(self.severity)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VulnInfo":
        """Rehydrate from a dict previously produced by :meth:`to_dict`."""
        return cls(
            id=str(data.get("id", "")),
            summary=str(data.get("summary", "")),
            severity=Severity(int(data.get("severity", 0))),
        )


@dataclass
class PackageCheckResult:
    """Result of checking one package against OSV."""

    package: PackageRef
    vulns: list[VulnInfo] = field(default_factory=list)
    error: str | None = None  # set when the lookup itself failed

    @property
    def max_severity(self) -> Severity:
        """Highest severity among this package's known vulnerabilities."""
        if not self.vulns:
            return Severity.UNKNOWN
        return max((v.severity for v in self.vulns), default=Severity.UNKNOWN)

    def has_blocking(self, threshold: Severity) -> bool:
        """Whether at least one vuln reaches ``threshold`` severity."""
        return any(v.severity >= threshold for v in self.vulns)

    def blocking_vulns(self, threshold: Severity) -> list[VulnInfo]:
        """Return the subset of vulns at or above ``threshold``."""
        return [v for v in self.vulns if v.severity >= threshold]


# =============================================================================
# Configuration
# =============================================================================


class SupplyChainGuardConfig(BaseModel):
    """Configuration for SupplyChainGuardPolicy."""

    osv_api_url: str = Field(
        default="https://api.osv.dev/v1/query",
        description="OSV.dev query endpoint.",
    )
    osv_timeout_seconds: float = Field(
        default=5.0,
        description="HTTP timeout for each OSV lookup.",
        gt=0.0,
    )
    cache_ttl_seconds: int = Field(
        default=86400,
        description=(
            "How long to cache a successful OSV lookup result. Longer TTLs "
            "reduce load on OSV but increase the lag before a new advisory "
            "becomes visible to the guard. The default (24h) trades a "
            "one-day worst-case staleness window for ~1 query per "
            "package-version per day."
        ),
        gt=0,
    )
    error_cache_ttl_seconds: int = Field(
        default=60,
        description=(
            "How long to cache a *failed* OSV lookup. Short by design so an "
            "outage recovers quickly, but long enough that an outage doesn't "
            "degenerate into `osv_timeout_seconds * N` per request under "
            "fail_closed. Set to 0 to disable negative caching entirely."
        ),
        ge=0,
    )
    severity_threshold: str = Field(
        default="HIGH",
        description="Block packages whose max vulnerability severity is at or above this level.",
    )
    allowlist: list[str] = Field(
        default_factory=list,
        description="Packages to always allow, in the form 'ecosystem:name' (e.g. 'PyPI:requests').",
    )
    fail_closed: bool = Field(
        default=True,
        description="If true, block installs when the OSV lookup fails. If false, allow on lookup failure.",
    )
    bash_tool_names: tuple[str, ...] = Field(
        default=("Bash",),
        description=(
            "Tool names that represent shell execution and should be "
            "inspected for install commands. Claude Code uses `Bash`; MCP "
            "servers may expose shell access under other names (e.g. "
            "`execute_command`, `Terminal`). Add those names here to "
            "extend coverage to non–Claude-Code clients."
        ),
    )

    @field_validator("bash_tool_names", mode="before")
    @classmethod
    def _coerce_bash_tool_names(cls, value: Any) -> tuple[str, ...]:
        """Accept list input (from YAML) and normalise to a tuple."""
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            return tuple(str(v) for v in value)
        return value

    @field_validator("severity_threshold")
    @classmethod
    def _validate_severity_threshold(cls, value: str) -> str:
        """Reject unknown severity labels at config load.

        Without this, a typo like ``severity_threshold: "HIH"`` would parse
        to ``Severity.UNKNOWN`` and the policy would silently over-block
        (any vuln satisfies ``severity >= UNKNOWN``).
        """
        if Severity.from_label(value) is Severity.UNKNOWN and value.strip().upper() != "UNKNOWN":
            valid = ", ".join(s.label for s in Severity if s is not Severity.UNKNOWN)
            raise ValueError(f"unknown severity threshold {value!r}; expected one of: {valid}")
        return value

    @property
    def severity_threshold_enum(self) -> Severity:
        """Parsed severity threshold enum."""
        return Severity.from_label(self.severity_threshold)


# =============================================================================
# Command parser
# =============================================================================


# Flags that take an argument value — skip the next token after these.
_PIP_VALUE_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "-r",
        "--requirement",
        "-c",
        "--constraint",
        "-e",
        "--editable",
        "--index-url",
        "-i",
        "--extra-index-url",
        "--trusted-host",
        "--proxy",
        "--retries",
        "--timeout",
        "--cert",
        "--client-cert",
        "--root",
        "--prefix",
        "--target",
        "-t",
        "--upgrade-strategy",
        "--python-version",
        "--platform",
        "--implementation",
        "--abi",
        "--progress-bar",
        "--no-build-isolation",
        "--find-links",
        "-f",
    }
)

_NPM_VALUE_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "--prefix",
        "--registry",
        "--workspace",
        "-w",
        "--tag",
        "--access",
    }
)

_CARGO_VALUE_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "--version",
        "--vers",
        "--registry",
        "--index",
        "--path",
        "--git",
        "--branch",
        "--tag",
        "--rev",
        "--features",
        "--target",
        "--target-dir",
        "--manifest-path",
        "--bin",
        "--example",
        "--jobs",
        "-j",
    }
)

_GEM_VALUE_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "-v",
        "--version",
        "-s",
        "--source",
        "--clear-sources",
        "-i",
        "--install-dir",
        "-n",
        "--bindir",
        "--platform",
    }
)

_COMPOSER_VALUE_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "--working-dir",
        "-d",
        "--prefer-install",
        "--repository",
    }
)

_GO_VALUE_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "-C",
        "-tags",
        "-mod",
        "-modfile",
        "-ldflags",
        "-gcflags",
        "-asmflags",
        "-overlay",
        "-pkgdir",
        "-p",
    }
)


def _strip_flags(
    tokens: list[str],
    value_flags: frozenset[str],
) -> list[str]:
    """Drop all flag tokens. Flags in ``value_flags`` also consume the next token."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            # `--flag=value` — single token; skip it.
            if "=" in tok:
                i += 1
                continue
            if tok in value_flags:
                i += 2  # skip flag and its value
                continue
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def _parse_pip_packages(tokens: list[str]) -> list[PackageRef]:
    """Parse pip/pip3/uv-pip install positional args into PackageRefs."""
    positional = _strip_flags(tokens, _PIP_VALUE_FLAGS)
    refs: list[PackageRef] = []
    for raw in positional:
        if raw.startswith((".", "/")) or "://" in raw:
            continue  # local path or URL — can't check via OSV by name alone
        if raw.endswith((".tar.gz", ".whl", ".zip")):
            continue
        name, version = _split_pip_specifier(raw)
        if not name:
            continue
        refs.append(PackageRef(ecosystem="PyPI", name=name, version=version))
    return refs


def _split_pip_specifier(raw: str) -> tuple[str, str | None]:
    """Split e.g. ``requests==2.31.0`` into ("requests", "2.31.0")."""
    # Strip extras: ``pkg[extra]==1.0`` -> ``pkg==1.0``
    bracket_start = raw.find("[")
    bracket_end = raw.find("]") if bracket_start != -1 else -1
    if bracket_start != -1 and bracket_end != -1:
        raw = raw[:bracket_start] + raw[bracket_end + 1 :]
    # Version specifiers
    for op in ("===", "==", ">=", "<=", "!=", "~=", ">", "<"):
        if op in raw:
            name, _, version = raw.partition(op)
            return name.strip(), version.strip() or None
    return raw.strip(), None


def _parse_npm_packages(tokens: list[str]) -> list[PackageRef]:
    """Parse npm/yarn/pnpm install positional args into PackageRefs."""
    positional = _strip_flags(tokens, _NPM_VALUE_FLAGS)
    refs: list[PackageRef] = []
    for raw in positional:
        if raw.startswith((".", "/")) or "://" in raw or raw.endswith(".tgz"):
            continue
        if raw.startswith("git+") or raw.startswith("github:"):
            continue
        name, version = _split_npm_specifier(raw)
        if not name:
            continue
        refs.append(PackageRef(ecosystem="npm", name=name, version=version))
    return refs


def _split_npm_specifier(raw: str) -> tuple[str, str | None]:
    """Split ``left-pad@1.3.0`` or ``@scope/pkg@1.0`` into name and version."""
    if raw.startswith("@"):
        # scoped package: @scope/name[@version]
        at = raw.find("@", 1)
        if at == -1:
            return raw, None
        return raw[:at], _normalise_npm_version(raw[at + 1 :])
    if "@" in raw:
        name, _, version = raw.partition("@")
        return name, _normalise_npm_version(version)
    return raw, None


def _normalise_npm_version(version: str) -> str | None:
    """Drop version ranges and tags that OSV's exact-version lookup can't match.

    npm accepts ``^1.3.0``, ``~1.3.0``, ``>=1.0.0 <2.0.0``, ``latest``, ``next``,
    etc. OSV's single-package query expects an exact version string, so we
    treat anything non-exact as "no version known" to avoid silent false
    negatives where OSV returns no matches for ``^1.3.0`` but does have
    advisories for ``1.3.0`` itself.
    """
    version = version.strip()
    if not version:
        return None
    # Tags like "latest", "next", "beta" — not useful for OSV.
    if version in {"latest", "next", "beta", "alpha", "rc", "canary"}:
        return None
    # Range operators at the start: ^, ~, >=, <=, >, <, =, !, ||, space, ...
    if version[0] in "^~<>=!|*x" or " " in version:
        return None
    return version


def _parse_cargo_packages(tokens: list[str]) -> list[PackageRef]:
    """Parse ``cargo install`` / ``cargo add`` args into crates.io PackageRefs."""
    positional = _strip_flags(tokens, _CARGO_VALUE_FLAGS)
    refs: list[PackageRef] = []
    for raw in positional:
        if "@" in raw:
            name, _, version = raw.partition("@")
            refs.append(PackageRef(ecosystem="crates.io", name=name, version=version or None))
        else:
            refs.append(PackageRef(ecosystem="crates.io", name=raw))
    return refs


def _parse_go_packages(tokens: list[str]) -> list[PackageRef]:
    """Parse ``go install`` / ``go get`` args into Go PackageRefs."""
    positional = _strip_flags(tokens, _GO_VALUE_FLAGS)
    refs: list[PackageRef] = []
    for raw in positional:
        # Go modules are paths like github.com/foo/bar[@version]
        if "@" in raw:
            name, _, version = raw.partition("@")
            # Go tags like "latest" / "upgrade" don't round-trip through OSV's
            # exact-version query; treat as unknown version.
            clean_version: str | None = version.strip() or None
            if clean_version in {"latest", "upgrade", "patch", "none"}:
                clean_version = None
            refs.append(PackageRef(ecosystem="Go", name=name, version=clean_version))
        else:
            refs.append(PackageRef(ecosystem="Go", name=raw))
    return refs


def _parse_gem_packages(tokens: list[str]) -> list[PackageRef]:
    """Parse ``gem install`` positional args into RubyGems PackageRefs."""
    positional = _strip_flags(tokens, _GEM_VALUE_FLAGS)
    return [PackageRef(ecosystem="RubyGems", name=raw) for raw in positional if raw]


def _parse_composer_packages(tokens: list[str]) -> list[PackageRef]:
    """Parse ``composer require`` args into Packagist PackageRefs.

    Composer accepts both ``vendor/name:^1.0`` (single token) and ``vendor/name
    "^1.0"`` (two tokens). The two-token form previously produced a phantom
    ``^1.0`` PackageRef. Packagist package names always contain ``/``, so any
    following token without ``/`` that looks like a version specifier is
    attached to the preceding package.
    """
    positional = _strip_flags(tokens, _COMPOSER_VALUE_FLAGS)
    refs: list[PackageRef] = []
    i = 0
    while i < len(positional):
        raw = positional[i]
        if ":" in raw:
            name, _, version = raw.partition(":")
            refs.append(PackageRef(ecosystem="Packagist", name=name, version=version or None))
            i += 1
            continue
        if "/" in raw:
            name = raw
            version: str | None = None
            # Next token is a candidate version if it's a specifier shape
            # and isn't itself a package name.
            if i + 1 < len(positional):
                next_tok = positional[i + 1]
                if "/" not in next_tok and _looks_like_version_specifier(next_tok):
                    version = _normalise_npm_version(next_tok)
                    i += 2
                    refs.append(PackageRef(ecosystem="Packagist", name=name, version=version))
                    continue
            refs.append(PackageRef(ecosystem="Packagist", name=name, version=version))
            i += 1
            continue
        # Token without `/` and not preceded by a package — skip rather than
        # emit a phantom ref.
        i += 1
    return refs


def _looks_like_version_specifier(tok: str) -> bool:
    """Heuristic: does ``tok`` look like a package version / range specifier?"""
    if not tok:
        return False
    return tok[0] in "0123456789^~<>=*!" or tok in {"latest", "next", "beta", "alpha"}


def _extract_install_segment(tokens: list[str]) -> list[list[str]] | None:
    """Peel off a single install invocation from a leading token list.

    Returns a list of (ecosystem_parser_input) segments, one per recognised
    install command, or ``None`` if no install command is present.
    Each returned segment is the tail tokens after the ``install`` subcommand.
    """
    if not tokens:
        return None

    head = tokens[0]
    tail = tokens[1:]

    # python -m pip install X  (the PEP-recommended form)
    if (
        head in {"python", "python3", "py"}
        and len(tail) >= 3
        and tail[0] == "-m"
        and tail[1] == "pip"
        and tail[2] == "install"
    ):
        return [["__pip__", *tail[3:]]]

    # uv pip install X  (uv is a pip frontend)
    if head == "uv" and len(tail) >= 2 and tail[0] == "pip" and tail[1] == "install":
        return [["__pip__", *tail[2:]]]

    # pip / pip3 / pipx install X
    if head in {"pip", "pip3", "pipx"} and tail and tail[0] == "install":
        return [["__pip__", *tail[1:]]]

    # npm install / npm i / npm add
    if head == "npm" and tail and tail[0] in {"install", "i", "add"}:
        return [["__npm__", *tail[1:]]]

    # yarn add / yarn global add
    if head == "yarn" and tail:
        if tail[0] == "add":
            return [["__npm__", *tail[1:]]]
        if len(tail) >= 2 and tail[0] == "global" and tail[1] == "add":
            return [["__npm__", *tail[2:]]]

    # pnpm add / pnpm install / pnpm i
    if head == "pnpm" and tail and tail[0] in {"add", "install", "i"}:
        return [["__npm__", *tail[1:]]]

    # cargo install / cargo add
    if head == "cargo" and tail and tail[0] in {"install", "add"}:
        return [["__cargo__", *tail[1:]]]

    # go install / go get
    if head == "go" and tail and tail[0] in {"install", "get"}:
        return [["__go__", *tail[1:]]]

    # gem install
    if head == "gem" and tail and tail[0] == "install":
        return [["__gem__", *tail[1:]]]

    # composer require / composer global require
    if head == "composer" and tail:
        if tail[0] == "require":
            return [["__composer__", *tail[1:]]]
        if len(tail) >= 2 and tail[0] == "global" and tail[1] == "require":
            return [["__composer__", *tail[2:]]]

    return None


_PARSERS: Final[dict[str, Any]] = {
    "__pip__": _parse_pip_packages,
    "__npm__": _parse_npm_packages,
    "__cargo__": _parse_cargo_packages,
    "__go__": _parse_go_packages,
    "__gem__": _parse_gem_packages,
    "__composer__": _parse_composer_packages,
}


_CHAIN_PATTERN: Final[re.Pattern[str]] = re.compile(r"(\|\||&&|;|\|)")


def _normalise_chain_operators(command: str) -> str:
    """Insert whitespace around shell chaining operators outside quoted regions.

    Doing this in a simple pre-pass lets us reuse ``shlex.split`` without
    writing a bespoke tokeniser.
    """
    out: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(command):
        ch = command[i]
        if quote is not None:
            out.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in {'"', "'"}:
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < len(command):
            out.append(ch)
            out.append(command[i + 1])
            i += 2
            continue
        m = _CHAIN_PATTERN.match(command, i)
        if m is not None:
            out.append(" ")
            out.append(m.group(0))
            out.append(" ")
            i = m.end()
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _split_on_chaining(tokens: list[str]) -> list[list[str]]:
    """Split a token list on shell chaining operators (``&&``, ``||``, ``;``, ``|``)."""
    segments: list[list[str]] = []
    current: list[str] = []
    for tok in tokens:
        if tok in {"&&", "||", ";", "|"}:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(tok)
    if current:
        segments.append(current)
    return segments


# Prefixes like `sudo pip install foo` or `env FOO=bar pip install foo` wrap a
# real install command. We strip them before trying to match an installer head.
_WRAPPER_WORDS: Final[frozenset[str]] = frozenset({"sudo", "exec", "time", "command", "nice", "ionice"})
_ENV_ASSIGNMENT: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _strip_wrapper_prefix(segment: list[str]) -> list[str]:
    """Strip shell wrappers that precede a real command.

    Handles ``sudo pip install``, ``env FOO=bar pip install``, and bare
    ``FOO=bar pip install`` — each of which would otherwise hide the install
    from the installer-head match in ``_extract_install_segment``.
    """
    i = 0
    while i < len(segment):
        tok = segment[i]
        if tok in _WRAPPER_WORDS:
            i += 1
            continue
        if tok == "env":
            i += 1
            # env may be followed by any number of VAR=value tokens and flags.
            while i < len(segment):
                nxt = segment[i]
                if nxt in {"-i", "-u", "--"}:
                    i += 1
                    continue
                if _ENV_ASSIGNMENT.match(nxt):
                    i += 1
                    continue
                break
            continue
        if _ENV_ASSIGNMENT.match(tok):
            # Bare ``FOO=bar pip install …`` (no env keyword).
            i += 1
            continue
        break
    return segment[i:]


def _process_segment(segment: list[str], depth: int = 0) -> list[PackageRef]:
    """Extract packages from a single pipeline segment.

    Handles wrapper stripping, ``sh -c "…"``/``bash -c "…"`` recursion, and
    dispatch to the ecosystem-specific parsers. ``depth`` bounds recursion
    into ``-c`` strings so a nested-shell bomb can't hang the parser.
    """
    segment = _strip_wrapper_prefix(segment)
    if not segment:
        return []

    head = segment[0]

    # sh -c "pip install foo && npm install bar" — recurse on the inner
    # command string. Same for bash/zsh/dash -c.
    if depth < 3 and head in {"sh", "bash", "zsh", "dash"} and len(segment) >= 3 and segment[1] == "-c":
        return parse_install_commands(segment[2], _depth=depth + 1)

    parsed = _extract_install_segment(segment)
    if parsed is None:
        return []
    refs: list[PackageRef] = []
    for parser_input in parsed:
        marker = parser_input[0]
        rest = parser_input[1:]
        parser = _PARSERS.get(marker)
        if parser is None:
            continue
        refs.extend(parser(rest))
    return refs


def parse_install_commands(command: str, _depth: int = 0) -> list[PackageRef]:
    """Extract every package that the given shell command would install.

    Supports chained commands (``&&``, ``||``, ``;``, ``|``), so
    ``pip install foo && npm install bar`` yields refs for both foo and bar.

    Unknown or non-install commands are silently skipped — the caller gets
    an empty list when nothing relevant is found. Callers concerned with
    adversarial input should use :func:`analyze_command` instead, which also
    detects install-shaped commands that we can't safely parse.
    """
    # shlex keeps chaining operators attached to adjacent tokens (e.g. ``hi;``);
    # insert whitespace around them so _split_on_chaining can see them as their
    # own tokens. We only touch characters outside quoted regions.
    normalised = _normalise_chain_operators(command)
    try:
        tokens = shlex.split(normalised, posix=True)
    except ValueError:
        logger.debug("Failed to tokenize command: %r", command)
        return []

    refs: list[PackageRef] = []
    for segment in _split_on_chaining(tokens):
        refs.extend(_process_segment(segment, depth=_depth))
    return refs


# =============================================================================
# Dangerous-construct detection
# =============================================================================


# Things we can't safely parse: command substitution, variable expansion,
# and process substitution — all forms where the shell executes or
# interpolates content that we can't see at parse time.
_COMMAND_SUB_CHARS: Final[re.Pattern[str]] = re.compile(r"\$\(|`|\$\{|<\(|>\(")
_PIPE_TO_INTERP: Final[re.Pattern[str]] = re.compile(
    r"\|\s*(?:sh|bash|zsh|dash|ksh|fish|python|python3|py|node|ruby|perl|tclsh|lua)\b"
)


def _scan_unquoted(command: str, pattern: re.Pattern[str]) -> bool:
    """Return True if ``pattern`` matches in a region where bash would expand it.

    Tracks single quotes (which fully escape their contents) and backslashes,
    but does NOT treat double quotes as safe — bash still performs command
    substitution (``$(…)``, backticks) and variable expansion (``${…}``)
    inside double-quoted strings.
    """
    in_single = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if pattern.match(command, i) is not None:
            return True
        i += 1
    return False


def _detect_dangerous_construct(command: str) -> str | None:
    """Return a reason string if ``command`` has an unparseable construct.

    Detects shell command substitution (``$(…)``, backticks, ``${…}``),
    process substitution (``<(…)``, ``>(…)``), and pipe-to-interpreter at
    the current shell layer only; for wrapped ``sh -c "…"`` arguments,
    callers should use :func:`_find_hard_block_reason_recursive`.
    """
    if _scan_unquoted(command, _COMMAND_SUB_CHARS):
        return "shell command/process substitution ($(...), backticks, ${...}, <(...), >(...))"
    if _scan_unquoted(command, _PIPE_TO_INTERP):
        return "pipe to shell/interpreter (e.g. '| sh', '| bash', '| python')"
    return None


def _structural_looks_like_install(command: str) -> bool:
    """Structural check: does any pipeline segment of ``command`` look like an install?

    Uses the same tokenising/wrapper-stripping logic as the main parser
    and asks "is the head token (after wrappers) a package manager we
    recognise, followed by an install verb?". That's stricter than a
    regex-anywhere check — ``go run $(date)`` won't match because ``run``
    is not an install verb, but ``go install foo`` will.
    """
    try:
        tokens = shlex.split(_normalise_chain_operators(command), posix=True)
    except ValueError:
        return False
    for segment in _split_on_chaining(tokens):
        stripped = _strip_wrapper_prefix(segment)
        if not stripped:
            continue
        # A recognised-and-parsed install form (pip/uv/npm/cargo/go/gem/composer/
        # python -m pip, etc.) — _extract_install_segment returns non-None.
        if _extract_install_segment(stripped) is not None:
            return True
        # An install form we don't parse but do recognise (poetry/conda/apt/etc.)
        if len(stripped) < 2:
            continue
        head = stripped[0]
        verb = stripped[1]
        if head not in _UNSUPPORTED_MANAGER_MESSAGES and head not in _SYSTEM_MANAGER_MESSAGES:
            continue
        if verb in _INSTALL_VERBS or verb in _HEAD_SPECIFIC_VERBS.get(head, frozenset()):
            return True
    return False


@dataclass(frozen=True)
class CommandAnalysis:
    """Result of analysing a shell command for install safety.

    - ``packages`` is the list of packages we were able to extract and should
      look up via OSV.
    - ``hard_block_reason`` is set when the command contains a construct we
      can't safely parse (command substitution, pipe-to-interpreter) AND the
      command mentions an installer keyword. When set, callers should block
      unconditionally — no OSV lookup can meaningfully clear the command.
    """

    packages: tuple[PackageRef, ...]
    hard_block_reason: str | None


def analyze_command(command: str) -> CommandAnalysis:
    """Analyse a shell command for supply-chain safety.

    Runs dangerous-construct detection and unverifiable-form detection
    before package extraction: if the command mixes a structural install
    shape with command substitution, pipe-to-interpreter, a requirements
    file, a URL/VCS install, or a local path, the command can't be
    cleared by OSV lookup alone and is marked for hard-block. The scan
    also descends into ``sh -c "…"`` and ``bash -c "…"`` arguments so a
    wrapper can't hide an unsafe inner form.
    """
    if not command:
        return CommandAnalysis(packages=(), hard_block_reason=None)

    reason = _find_hard_block_reason_recursive(command, depth=0)
    if reason is not None:
        return CommandAnalysis(packages=(), hard_block_reason=reason)

    # Unverifiable install forms (pip -r, pip -e, git+URL, local path, wheel
    # file, etc.) are structurally pip/npm/etc commands but have arguments
    # that OSV can't verify. Without this check they silently pass through.
    unverifiable = _detect_unverifiable_install_form(command)
    if unverifiable is not None:
        return CommandAnalysis(packages=(), hard_block_reason=unverifiable)

    packages = tuple(parse_install_commands(command))

    if not packages:
        unsupported_reason = _detect_unsupported_install_form(command)
        if unsupported_reason is not None:
            return CommandAnalysis(packages=(), hard_block_reason=unsupported_reason)

    return CommandAnalysis(packages=packages, hard_block_reason=None)


# Heads that look like package managers but whose install forms we don't parse.
# When one of these appears as the first token of a segment followed by an
# install verb, the command is hard-blocked with a specific message.
_UNSUPPORTED_MANAGER_MESSAGES: Final[dict[str, str]] = {
    # Python package managers other than pip/uv/pipx.
    "poetry": "`poetry add` is not supported by this guard; use pip/uv directly or allowlist the package",
    "pipenv": "`pipenv install` is not supported by this guard; use pip/uv directly or allowlist the package",
    "rye": "`rye add` is not supported by this guard; use pip/uv directly or allowlist the package",
    "pdm": "`pdm add` is not supported by this guard; use pip/uv directly or allowlist the package",
    # Conda family.
    "conda": "`conda install` is not supported by this guard; allowlist the package explicitly if you trust it",
    "mamba": "`mamba install` is not supported by this guard; allowlist the package explicitly if you trust it",
    "micromamba": "`micromamba install` is not supported by this guard; allowlist the package explicitly if you trust it",
    # Bun (npm alternative).
    "bun": "`bun add` is not supported by this guard; use npm/yarn/pnpm or allowlist the package",
}

# System package managers — not OSV-backed, dedicated message so the
# remediation hint doesn't confusingly suggest `pip install …`.
_SYSTEM_MANAGER_MESSAGES: Final[dict[str, str]] = {
    "apt": "system package managers (`apt install …`) are not covered by this guard; allowlist explicitly if needed",
    "apt-get": "system package managers (`apt-get install …`) are not covered by this guard; allowlist explicitly if needed",
    "aptitude": "system package managers (`aptitude install …`) are not covered by this guard; allowlist explicitly if needed",
    "dpkg": "system package managers (`dpkg -i …`) are not covered by this guard; allowlist explicitly if needed",
    "brew": "system package managers (`brew install …`) are not covered by this guard; allowlist explicitly if needed",
    "snap": "system package managers (`snap install …`) are not covered by this guard; allowlist explicitly if needed",
    "easy_install": "`easy_install` is deprecated and not supported by this guard; use pip instead",
}

_INSTALL_VERBS: Final[frozenset[str]] = frozenset({"install", "add", "require", "get"})

# Some tools use a different subcommand token. Only recognised when the head
# is the matching command — never globally, to avoid e.g. `npm -i` (if that
# ever becomes a thing) over-blocking.
_HEAD_SPECIFIC_VERBS: Final[dict[str, frozenset[str]]] = {
    "dpkg": frozenset({"-i", "--install"}),
}


def _detect_unsupported_install_form(command: str) -> str | None:
    """Return a hard-block reason if ``command`` is an install form we can't parse.

    Unlike the broad keyword-anywhere check, this requires the package
    manager to appear as the *first* token of a pipeline segment followed
    by an install verb in the subcommand slot (accounting for wrapper
    prefixes). That avoids false positives like ``pip help install`` or
    ``npm view react``, where ``install`` appears as an argument and not
    a subcommand.
    """
    try:
        tokens = shlex.split(_normalise_chain_operators(command), posix=True)
    except ValueError:
        return None

    for segment in _split_on_chaining(tokens):
        stripped = _strip_wrapper_prefix(segment)
        if len(stripped) < 2:
            continue
        head = stripped[0]
        verb = stripped[1]
        head_verbs = _HEAD_SPECIFIC_VERBS.get(head, frozenset())
        if verb not in _INSTALL_VERBS and verb not in head_verbs:
            continue
        if head in _SYSTEM_MANAGER_MESSAGES:
            return _SYSTEM_MANAGER_MESSAGES[head]
        if head in _UNSUPPORTED_MANAGER_MESSAGES:
            return _UNSUPPORTED_MANAGER_MESSAGES[head]
    return None


# Pip flags whose presence means OSV can't verify the install at all.
_PIP_UNVERIFIABLE_FLAGS: Final[dict[str, str]] = {
    "-r": "`pip install -r` reads packages from a requirements file",
    "--requirement": "`pip install --requirement` reads packages from a requirements file",
    "-c": "`pip install -c` reads constraints from a file",
    "--constraint": "`pip install --constraint` reads constraints from a file",
    "-e": "`pip install -e` is an editable / local-path install",
    "--editable": "`pip install --editable` is an editable / local-path install",
}


def _detect_unverifiable_install_form(command: str) -> str | None:
    """Return a hard-block reason for supported install forms OSV can't verify.

    The previous parser silently accepted these forms as "no parsed packages"
    which then passed the guard. Examples that were bypasses:

    - ``pip install -r requirements.txt`` — reads packages from a file
    - ``pip install -e ./local`` — editable local path install
    - ``pip install git+https://…`` — VCS install
    - ``pip install ./wheel.whl`` — local wheel install
    - ``pip install https://…/pkg.whl`` — URL install
    - ``npm install git+https://…`` / ``npm install github:foo/bar`` — VCS
    - ``npm install ./local-pkg`` — local-path install

    The policy's threat model is "block installs of vulnerable packages
    by name"; all the above forms install something that isn't a named
    PyPI/npm package, so OSV can't verify them. Hard-block with a
    specific message.
    """
    try:
        tokens = shlex.split(_normalise_chain_operators(command), posix=True)
    except ValueError:
        return None

    for segment in _split_on_chaining(tokens):
        stripped = _strip_wrapper_prefix(segment)
        parsed = _extract_install_segment(stripped)
        if parsed is None:
            continue
        for parser_input in parsed:
            marker = parser_input[0]
            rest = parser_input[1:]
            reason = _check_unverifiable_args(marker, rest)
            if reason is not None:
                return reason
    return None


def _check_unverifiable_args(marker: str, tokens: list[str]) -> str | None:
    """Return a reason if any token is an unverifiable install argument."""
    if marker == "__pip__":
        for tok in tokens:
            if tok in _PIP_UNVERIFIABLE_FLAGS:
                return (
                    f"{_PIP_UNVERIFIABLE_FLAGS[tok]} — OSV can only verify "
                    "named packages, not the contents of a file or a local "
                    "path. List packages explicitly, or allowlist the source."
                )
            # URL / VCS install
            if "://" in tok or tok.startswith(("git+", "hg+", "svn+", "bzr+")):
                return (
                    "pip install from URL / VCS — OSV can only verify named "
                    "packages from PyPI. Use named packages, or allowlist "
                    "the source explicitly."
                )
            # Local path install
            if tok.startswith(("./", "/", "../")) or tok == ".":
                return (
                    "pip install from a local path — OSV can only verify "
                    "named packages from PyPI. List packages explicitly, "
                    "or allowlist the path."
                )
            # Wheel / tarball file install
            if tok.endswith((".whl", ".tar.gz", ".zip")):
                return (
                    "pip install from a wheel or tarball file — OSV can "
                    "only verify named packages from PyPI. Use named "
                    "packages, or allowlist the file explicitly."
                )
    elif marker == "__npm__":
        for tok in tokens:
            if tok.startswith(("git+", "github:", "gitlab:", "bitbucket:", "file:")):
                return (
                    "npm install from URL / VCS — OSV can only verify named "
                    "packages from the npm registry. Use named packages, "
                    "or allowlist the source explicitly."
                )
            if "://" in tok:
                return "npm install from URL — OSV can only verify named packages from the npm registry."
            if tok.startswith(("./", "/", "../")) or tok == ".":
                return "npm install from a local path — OSV can only verify named packages from the npm registry."
            if tok.endswith(".tgz"):
                return "npm install from a tarball file — OSV can only verify named packages from the npm registry."
    return None


def _find_hard_block_reason_recursive(command: str, depth: int) -> str | None:
    """Find a hard-block reason at any shell level (bounded by ``depth``).

    Scans the current layer for dangerous constructs and then descends into
    any ``sh -c``/``bash -c`` wrapper arguments so a wrapper can't hide an
    unsafe inner form.
    """
    if depth > 3:
        return None

    # Detect at this layer.
    reason = _detect_dangerous_construct(command)
    if reason is not None and _structural_looks_like_install(command):
        return reason

    # Look for wrapped inner commands and recurse on their argument strings.
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    for i in range(len(tokens) - 2):
        if tokens[i] in {"sh", "bash", "zsh", "dash"} and tokens[i + 1] == "-c":
            inner_reason = _find_hard_block_reason_recursive(tokens[i + 2], depth + 1)
            if inner_reason is not None:
                return inner_reason
    return None


# =============================================================================
# OSV client
# =============================================================================


# Module-level lazily-created httpx client shared across all OSVClient
# instances. A module singleton is the right sharing boundary here because:
# - policies are singletons and may be hot-swapped by the admin API; making
#   the client owned by the policy instance leaks connection pools on swap
#   (no `on_unload` hook exists today);
# - httpx clients are safe to share across coroutines and tasks;
# - module-level lifetime == process lifetime, which matches the implicit
#   cleanup of any long-lived connection pool.
_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_shared_http_client() -> httpx.AsyncClient:
    """Return (creating if needed) the module-level httpx.AsyncClient.

    Constructed lazily on first use so importing the module doesn't open
    any sockets. Reused for the lifetime of the process.
    """
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is None:
        _SHARED_HTTP_CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _SHARED_HTTP_CLIENT


class OSVClient:
    """Minimal async client for the OSV.dev v1 query endpoint."""

    def __init__(
        self,
        api_url: str = "https://api.osv.dev/v1/query",
        timeout_seconds: float = 5.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize with endpoint URL, timeout, and optional injected client.

        When ``http_client`` is None (the normal case), the module-level
        shared client is used on first query so every OSVClient instance
        reuses the same connection pool. Tests can still inject a mock.
        """
        self._api_url = api_url
        self._timeout = timeout_seconds
        self._client = http_client  # None → lazily resolved to shared client

    async def query(self, package: PackageRef) -> list[VulnInfo]:
        """Look up vulnerabilities for a single package.

        Returns a (possibly empty) list of VulnInfos. Raises ``httpx.HTTPError``
        on transport errors or non-2xx responses — callers decide whether to
        treat those as fail-open or fail-closed.
        """
        body: dict[str, Any] = {"package": {"name": package.name, "ecosystem": package.ecosystem}}
        if package.version:
            body["version"] = package.version

        client = self._client if self._client is not None else _get_shared_http_client()
        response = await client.post(self._api_url, json=body, timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        return _parse_osv_response(payload)


def _parse_osv_response(payload: Any) -> list[VulnInfo]:
    """Turn the OSV query JSON into VulnInfos.

    The OSV schema for a single vuln includes ``id``, ``summary`` and a list of
    ``severity`` objects. We take the worst severity seen across the scores and
    the ``database_specific.severity`` label if present.
    """
    if not isinstance(payload, dict):
        return []
    raw_vulns = payload.get("vulns") or []
    if not isinstance(raw_vulns, list):
        return []
    results: list[VulnInfo] = []
    for entry in raw_vulns:
        if not isinstance(entry, dict):
            continue
        results.append(
            VulnInfo(
                id=str(entry.get("id", "")),
                summary=str(entry.get("summary") or entry.get("details") or ""),
                severity=_extract_max_severity(entry),
            )
        )
    return results


def _extract_max_severity(entry: dict[str, Any]) -> Severity:
    """Pull the highest severity we can find in a single OSV vuln entry."""
    max_sev = Severity.UNKNOWN

    # Top-level severity list (CVSS vectors + scores).
    for sev in entry.get("severity") or []:
        if not isinstance(sev, dict):
            continue
        score = _parse_cvss_score(sev.get("score"))
        if score is not None:
            candidate = Severity.from_cvss_score(score)
            if candidate > max_sev:
                max_sev = candidate

    # database_specific.severity is usually a label like "HIGH".
    db_specific = entry.get("database_specific")
    if isinstance(db_specific, dict):
        label_sev = Severity.from_label(db_specific.get("severity"))
        if label_sev > max_sev:
            max_sev = label_sev

    # affected[].database_specific.severity fallback.
    for affected in entry.get("affected") or []:
        if not isinstance(affected, dict):
            continue
        aff_db = affected.get("database_specific")
        if isinstance(aff_db, dict):
            label_sev = Severity.from_label(aff_db.get("severity"))
            if label_sev > max_sev:
                max_sev = label_sev

    return max_sev


def _parse_cvss_score(raw: Any) -> float | None:
    """Extract the numeric score from an OSV ``severity[].score`` entry.

    OSV most commonly puts a CVSS vector string here
    (e.g. ``CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H``). Some advisories
    put a bare numeric score instead. Both forms are supported.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None

    stripped = raw.strip()
    # Bare numeric string (e.g. "7.5").
    try:
        return float(stripped)
    except ValueError:
        pass

    if stripped.upper().startswith("CVSS:3"):
        return _cvss3_base_score(stripped)
    return None


# CVSS v3 base metric values (CVSS 3.0 and 3.1 spec).
_CVSS3_AV: Final[dict[str, float]] = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_CVSS3_AC: Final[dict[str, float]] = {"L": 0.77, "H": 0.44}
_CVSS3_PR_UNCHANGED: Final[dict[str, float]] = {"N": 0.85, "L": 0.62, "H": 0.27}
_CVSS3_PR_CHANGED: Final[dict[str, float]] = {"N": 0.85, "L": 0.68, "H": 0.5}
_CVSS3_UI: Final[dict[str, float]] = {"N": 0.85, "R": 0.62}
_CVSS3_CIA: Final[dict[str, float]] = {"H": 0.56, "L": 0.22, "N": 0.0}


def _cvss3_base_score(vector: str) -> float | None:
    """Compute the CVSS v3.x base score from a vector string.

    Follows the specification at https://www.first.org/cvss/v3.1/specification-document.
    Returns ``None`` if any required base metric is missing or malformed.
    """
    metrics: dict[str, str] = {}
    for part in vector.split("/")[1:]:  # skip the "CVSS:3.x" prefix
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        metrics[key.strip().upper()] = value.strip().upper()

    required = ("AV", "AC", "PR", "UI", "S", "C", "I", "A")
    if not all(metric in metrics for metric in required):
        return None

    scope = metrics["S"]
    try:
        av = _CVSS3_AV[metrics["AV"]]
        ac = _CVSS3_AC[metrics["AC"]]
        ui = _CVSS3_UI[metrics["UI"]]
        pr_table = _CVSS3_PR_CHANGED if scope == "C" else _CVSS3_PR_UNCHANGED
        pr = pr_table[metrics["PR"]]
        conf = _CVSS3_CIA[metrics["C"]]
        integ = _CVSS3_CIA[metrics["I"]]
        avail = _CVSS3_CIA[metrics["A"]]
    except KeyError:
        return None

    isc_base = 1 - (1 - conf) * (1 - integ) * (1 - avail)
    if scope == "C":
        impact = 7.52 * (isc_base - 0.029) - 3.25 * pow(isc_base - 0.02, 15)
    else:
        impact = 6.42 * isc_base

    if impact <= 0:
        return 0.0

    exploitability = 8.22 * av * ac * pr * ui
    raw = impact + exploitability
    if scope == "C":
        raw *= 1.08
    base = min(raw, 10.0)
    # CVSS "roundup" to one decimal (always rounds up at the hundredths place).
    return _cvss_roundup(base)


def _cvss_roundup(value: float) -> float:
    """CVSS specification roundup: round up to the nearest 0.1."""
    # Spec: integer equivalent of value * 100,000, then roundup by tenths.
    int_input = round(value * 100_000)
    if int_input % 10_000 == 0:
        return int_input / 100_000
    return (int_input // 10_000 + 1) / 10


# =============================================================================
# Allowlist and severity filtering
# =============================================================================


def is_allowlisted(package: PackageRef, allowlist: frozenset[str]) -> bool:
    """Check whether a package is exempt from supply chain enforcement."""
    key = f"{package.ecosystem}:{package.name}"
    return key in allowlist or package.name in allowlist


def filter_blocking(
    results: list[PackageCheckResult],
    threshold: Severity,
) -> list[PackageCheckResult]:
    """Return only the results that contain at least one blocking vuln."""
    return [r for r in results if r.has_blocking(threshold)]


# =============================================================================
# Redaction
# =============================================================================


# Match URL-embedded credentials (scheme://user:pass@host). The credential
# portion is whatever precedes the ``@`` after the ``://``. This is deliberately
# broad so we catch any scheme (https, http, git+https, ftp, …) and anything
# the shell would pass through.
_URL_CREDENTIAL_RE: Final[re.Pattern[str]] = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@\s]+:[^/@\s]+@")

# Long hex/base64 sequences that look like API keys, especially when attached
# to flags like ``--token`` or ``--password``. We only redact when attached to
# a known sensitive flag; redacting bare hex strings would mangle git SHAs.
_SENSITIVE_FLAG_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"(--(?:token|password|api[-_]key|auth|secret|header)[=\s])(\S+)",
    re.IGNORECASE,
)


def redact_credentials(text: str) -> str:
    """Strip embedded credentials from a command or URL.

    Used before putting a command into a blocked-message (visible to the LLM)
    or an event payload (stored in conversation_events). The policy should
    not be the vector that leaks credentials to its own telemetry pipeline.
    """
    text = _URL_CREDENTIAL_RE.sub(r"\1<redacted>@", text)
    text = _SENSITIVE_FLAG_VALUE_RE.sub(r"\1<redacted>", text)
    return text


# =============================================================================
# Formatters
# =============================================================================


# Max characters of untrusted OSV-summary text to show per vuln. Longer
# summaries are a larger prompt-injection surface and rarely informative.
_UNTRUSTED_SUMMARY_MAX = 200


def _format_untrusted_summary(summary: str) -> str:
    """Render an OSV summary with a clear untrusted-content delimiter.

    OSV.dev accepts third-party advisory submissions, so the summary field
    is untrusted text that will be shown back to the LLM. Wrap it in a
    labelled quote so a malicious advisory can't cleanly impersonate
    guard instructions.
    """
    text = (summary or "no summary").strip().splitlines()[0]
    if len(text) > _UNTRUSTED_SUMMARY_MAX:
        text = text[:_UNTRUSTED_SUMMARY_MAX] + "…"
    return f"⟨untrusted OSV advisory text⟩ {text}"


def format_blocked_message(
    results: list[PackageCheckResult],
    threshold: Severity,
    command: str | None = None,
) -> str:
    """Render the message shown to the LLM/user when an install is blocked.

    Handles three result shapes:
    - Vulnerabilities at or above threshold: show the vuln list.
    - OSV lookup failure under ``fail_closed``: show a ``[LOOKUP FAILED]``
      line with the error reason instead of a bogus ``0 blocking
      vulnerabilities`` header.
    - Mixed: render both kinds in the same message.
    """
    lines: list[str] = ["⛔ Supply chain guard blocked this install.", ""]
    if command:
        lines.append(f"Command: {redact_credentials(command)}")
        lines.append("")

    vulnerable = [r for r in results if r.blocking_vulns(threshold)]
    errored = [r for r in results if r.error and not r.blocking_vulns(threshold)]

    if vulnerable:
        lines.append("Packages with known vulnerabilities:")
        for result in vulnerable:
            blocking = result.blocking_vulns(threshold)
            header = (
                f"- {result.package.name} ({result.package.ecosystem}): "
                f"{len(blocking)} blocking vulnerabilit{'y' if len(blocking) == 1 else 'ies'} "
                f"[{result.max_severity.label}]"
            )
            lines.append(header)
            for vuln in blocking[:5]:
                lines.append(f"    {vuln.id} [{vuln.severity.label}]: {_format_untrusted_summary(vuln.summary)}")
            if len(blocking) > 5:
                lines.append(f"    ... and {len(blocking) - 5} more")

    if errored:
        if vulnerable:
            lines.append("")
        lines.append("Packages where the OSV lookup failed (fail-closed):")
        for result in errored:
            lines.append(
                f"- {result.package.name} ({result.package.ecosystem}) [LOOKUP FAILED]: "
                f"{result.error or 'unknown error'}"
            )

    lines.append("")
    lines.append(
        "Remediation: pin to a patched version listed in the OSV advisory, "
        "choose an alternative package, or explicitly allowlist the package "
        "if the advisory does not apply."
    )
    return "\n".join(lines)


def format_hard_block_message(reason: str, command: str | None = None) -> str:
    """Render the message shown when a command is blocked without an OSV lookup.

    Used when the command contains a construct we can't safely parse
    (command substitution, pipe-to-interpreter) or names a package manager
    we don't understand. In either case there is no way to clear the command
    via OSV, so the guard refuses it with an explanation.
    """
    lines: list[str] = [
        "⛔ Supply chain guard blocked this command.",
        "",
        f"Reason: {reason}.",
    ]
    if command:
        lines.append("")
        lines.append(f"Command: {redact_credentials(command)}")
    lines.append("")
    lines.append(
        "This command form cannot be verified against the OSV vulnerability "
        "database. Rewrite it as a direct install with explicit package names "
        "(e.g. `pip install requests==2.31.0`) so the guard can check each "
        "package, or explicitly allowlist it if it is known safe."
    )
    return "\n".join(lines)


def format_incoming_warning(results: list[PackageCheckResult], threshold: Severity) -> str:
    """Render the system-prompt warning prepended to incoming requests.

    Used when an incoming request already contains the output of a vulnerable
    install — we cannot block it, only warn the LLM to remediate.
    """
    lines: list[str] = [
        "⚠️ SECURITY WARNING: A package install in this conversation installed vulnerable packages.",
    ]
    for result in results:
        blocking = result.blocking_vulns(threshold)
        lines.append(
            f"- {result.package.name} ({result.package.ecosystem}): "
            f"{len(blocking)} known vulnerabilit{'y' if len(blocking) == 1 else 'ies'} "
            f"[{result.max_severity.label}]"
        )
        for vuln in blocking[:3]:
            lines.append(f"    {vuln.id}: {_format_untrusted_summary(vuln.summary)}")
        action = _remediation_for(result.package)
        lines.append(f"    Action: {action}")
    return "\n".join(lines)


def _remediation_for(package: PackageRef) -> str:
    """Suggest how to remove or replace an installed vulnerable package."""
    mapping = {
        "PyPI": f"Run `pip uninstall {package.name}` or upgrade to a patched version.",
        "npm": f"Run `npm uninstall {package.name}` or upgrade to a patched version.",
        "crates.io": f"Remove `{package.name}` from Cargo.toml or upgrade to a patched version.",
        "Go": f"Remove `{package.name}` from go.mod or upgrade to a patched version.",
        "RubyGems": f"Run `gem uninstall {package.name}` or upgrade to a patched version.",
        "Packagist": f"Run `composer remove {package.name}` or upgrade to a patched version.",
    }
    return mapping.get(package.ecosystem, f"Uninstall {package.name} or upgrade to a patched version.")


__all__ = [
    "Severity",
    "ECOSYSTEM_LABELS",
    "PackageRef",
    "VulnInfo",
    "PackageCheckResult",
    "SupplyChainGuardConfig",
    "CommandAnalysis",
    "analyze_command",
    "parse_install_commands",
    "OSVClient",
    "is_allowlisted",
    "filter_blocking",
    "format_blocked_message",
    "format_hard_block_message",
    "format_incoming_warning",
    "redact_credentials",
]
