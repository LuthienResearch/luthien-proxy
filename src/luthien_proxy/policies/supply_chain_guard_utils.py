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
from pydantic import BaseModel, Field

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
        description="How long to cache OSV lookup results.",
        gt=0,
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
        default=False,
        description="If true, block installs when the OSV lookup fails. If false, allow on lookup failure.",
    )

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
        return raw[:at], raw[at + 1 :] or None
    if "@" in raw:
        name, _, version = raw.partition("@")
        return name, version or None
    return raw, None


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
            refs.append(PackageRef(ecosystem="Go", name=name, version=version or None))
        else:
            refs.append(PackageRef(ecosystem="Go", name=raw))
    return refs


def _parse_gem_packages(tokens: list[str]) -> list[PackageRef]:
    """Parse ``gem install`` positional args into RubyGems PackageRefs."""
    positional = _strip_flags(tokens, _GEM_VALUE_FLAGS)
    return [PackageRef(ecosystem="RubyGems", name=raw) for raw in positional if raw]


def _parse_composer_packages(tokens: list[str]) -> list[PackageRef]:
    """Parse ``composer require`` args into Packagist PackageRefs."""
    positional = _strip_flags(tokens, _COMPOSER_VALUE_FLAGS)
    refs: list[PackageRef] = []
    for raw in positional:
        if ":" in raw:
            name, _, version = raw.partition(":")
            refs.append(PackageRef(ecosystem="Packagist", name=name, version=version or None))
        else:
            refs.append(PackageRef(ecosystem="Packagist", name=raw))
    return refs


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


def parse_install_commands(command: str) -> list[PackageRef]:
    """Extract every package that the given shell command would install.

    Supports chained commands (``&&``, ``||``, ``;``, ``|``), so
    ``pip install foo && npm install bar`` yields refs for both foo and bar.

    Unknown or non-install commands are silently skipped — the caller gets
    an empty list when nothing relevant is found.
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
        parsed = _extract_install_segment(segment)
        if parsed is None:
            continue
        for parser_input in parsed:
            marker = parser_input[0]
            rest = parser_input[1:]
            parser = _PARSERS.get(marker)
            if parser is None:
                continue
            refs.extend(parser(rest))
    return refs


# =============================================================================
# OSV client
# =============================================================================


class OSVClient:
    """Minimal async client for the OSV.dev v1 query endpoint."""

    def __init__(
        self,
        api_url: str = "https://api.osv.dev/v1/query",
        timeout_seconds: float = 5.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize with endpoint URL, timeout, and optional shared client."""
        self._api_url = api_url
        self._timeout = timeout_seconds
        self._client = http_client  # when None, a new client is created per request

    async def query(self, package: PackageRef) -> list[VulnInfo]:
        """Look up vulnerabilities for a single package.

        Returns a (possibly empty) list of VulnInfos. Raises ``httpx.HTTPError``
        on transport errors or non-2xx responses — callers decide whether to
        treat those as fail-open or fail-closed.
        """
        body: dict[str, Any] = {"package": {"name": package.name, "ecosystem": package.ecosystem}}
        if package.version:
            body["version"] = package.version

        if self._client is not None:
            response = await self._client.post(self._api_url, json=body, timeout=self._timeout)
            response.raise_for_status()
            payload = response.json()
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._api_url, json=body)
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
# Formatters
# =============================================================================


def format_blocked_message(
    results: list[PackageCheckResult],
    threshold: Severity,
    command: str | None = None,
) -> str:
    """Render the message shown to the LLM/user when an install is blocked."""
    lines: list[str] = ["⛔ Supply chain guard blocked this install.", ""]
    if command:
        lines.append(f"Command: {command}")
        lines.append("")
    lines.append("Packages with known vulnerabilities:")
    for result in results:
        blocking = result.blocking_vulns(threshold)
        header = (
            f"- {result.package.name} ({result.package.ecosystem}): "
            f"{len(blocking)} blocking vulnerabilit{'y' if len(blocking) == 1 else 'ies'} "
            f"[{result.max_severity.label}]"
        )
        lines.append(header)
        for vuln in blocking[:5]:
            summary = (vuln.summary or "no summary").strip().splitlines()[0]
            lines.append(f"    {vuln.id} [{vuln.severity.label}]: {summary}")
        if len(blocking) > 5:
            lines.append(f"    ... and {len(blocking) - 5} more")
    lines.append("")
    lines.append(
        "Remediation: pin to a patched version listed in the OSV advisory, "
        "choose an alternative package, or explicitly allowlist the package "
        "if the advisory does not apply."
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
            summary = (vuln.summary or "no summary").strip().splitlines()[0]
            lines.append(f"    {vuln.id}: {summary}")
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
    "parse_install_commands",
    "OSVClient",
    "is_allowlisted",
    "filter_blocking",
    "format_blocked_message",
    "format_incoming_warning",
]
