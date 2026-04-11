"""Utilities for SupplyChainAdvisoryPolicy.

This module contains the pure helpers the policy relies on:

- Data types for packages, vulnerabilities, and check results.
- A deliberately loose regex-based extractor for package install commands.
- An OSV.dev client that queries for known vulnerabilities.
- A severity parser that understands CVSS v3 vectors, numeric scores, and
  qualitative labels, and deliberately fails-safe on CVSS v4 vectors.
- Credential redaction and prompt-injection-safe summary formatting.

The extractor is best-effort: it recognises the common case of
``pip install <pkg>==<ver>``/``npm install <pkg>@<ver>``/etc. without
trying to defeat adversarial obfuscation. See the policy docstring for
the full threat model.
"""

from __future__ import annotations

import logging
import re
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
        """Convert a CVSS numeric score into a qualitative bucket."""
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


@dataclass(frozen=True)
class PackageRef:
    """A reference to a single package in a specific ecosystem."""

    ecosystem: str  # OSV ecosystem label, e.g. "PyPI"
    name: str
    version: str | None = None

    def cache_key(self) -> str:
        """Key used for caching this package's OSV lookup result."""
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
    error: str | None = None

    @property
    def max_severity(self) -> Severity:
        """Highest severity among this package's known vulnerabilities."""
        if not self.vulns:
            return Severity.UNKNOWN
        return max((v.severity for v in self.vulns), default=Severity.UNKNOWN)

    def has_advisory(self, threshold: Severity) -> bool:
        """Whether at least one vuln reaches ``threshold`` severity."""
        return any(v.severity >= threshold for v in self.vulns)

    def advisory_vulns(self, threshold: Severity) -> list[VulnInfo]:
        """Return the subset of vulns at or above ``threshold``."""
        return [v for v in self.vulns if v.severity >= threshold]


# =============================================================================
# Configuration
# =============================================================================


class SupplyChainAdvisoryConfig(BaseModel):
    """Configuration for SupplyChainAdvisoryPolicy."""

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
            "becomes visible to the advisory policy."
        ),
        gt=0,
    )
    error_cache_ttl_seconds: int = Field(
        default=60,
        description=(
            "How long to cache a *failed* OSV lookup. Short by design so an "
            "outage recovers quickly. Set to 0 to disable negative caching."
        ),
        ge=0,
    )
    max_concurrent_lookups: int = Field(
        default=10,
        description=(
            "Upper bound on concurrent OSV lookups per request. Bounds fan-out on commands that mention many packages."
        ),
        ge=1,
    )
    advisory_severity_threshold: str = Field(
        default="HIGH",
        description=(
            "Surface advisories whose max vulnerability severity is at or "
            "above this level. One of LOW, MEDIUM, HIGH, CRITICAL."
        ),
    )
    hard_block_versions: tuple[str, ...] = Field(
        default=(),
        description=(
            "Reserved for future hard-block support. In v1 this list must "
            "be empty — the policy only warns, never blocks."
        ),
    )
    warn_on_osv_error: bool = Field(
        default=True,
        description=(
            "If true, inject an 'OSV unavailable' advisory when the lookup "
            "fails. If false, silently skip. Default: warn (fail-closed-ish)."
        ),
    )
    bash_tool_names: tuple[str, ...] = Field(
        default=("Bash",),
        description=(
            "Tool names that represent shell execution and should be "
            "inspected for install commands. Claude Code uses `Bash`; MCP "
            "servers may expose shell access under other names."
        ),
    )

    @field_validator("bash_tool_names", "hard_block_versions", mode="before")
    @classmethod
    def _coerce_tuple(cls, value: Any) -> tuple[str, ...]:
        """Accept list input (from YAML) and normalise to a tuple."""
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            return tuple(str(v) for v in value)
        return value

    @field_validator("advisory_severity_threshold")
    @classmethod
    def _validate_severity(cls, value: str) -> str:
        if Severity.from_label(value) is Severity.UNKNOWN and value.strip().upper() != "UNKNOWN":
            valid = ", ".join(s.label for s in Severity if s is not Severity.UNKNOWN)
            raise ValueError(f"unknown severity threshold {value!r}; expected one of: {valid}")
        return value

    @field_validator("hard_block_versions")
    @classmethod
    def _reject_hard_blocks_in_v1(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """v1 does not implement hard-block; error loudly if the field is used.

        The field exists in the schema as a forward-compatibility marker so
        that a future release can populate it without a migration. Silently
        accepting values now would give operators a false sense that the
        policy is enforcing them.
        """
        if value:
            raise ValueError(
                "hard_block_versions is reserved for a future release. "
                "v1 of SupplyChainAdvisoryPolicy only warns; it does not block."
            )
        return value

    @property
    def severity_threshold_enum(self) -> Severity:
        """Parsed severity threshold enum."""
        return Severity.from_label(self.advisory_severity_threshold)


# =============================================================================
# Regex-based install-command extractor
# =============================================================================


# Package managers we recognise. Keep the list short — each entry adds a
# maintenance burden and false-positive risk. `uv pip install …` is handled
# by the optional `pip\s+` prefix after the outer manager token.
_INSTALL_CMD_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<mgr>pip|pip3|uv|poetry|pipenv|conda|npm|yarn|pnpm|bun)\s+"
    r"(?:pip\s+)?"
    r"(?P<verb>install|add|i|update|upgrade)\b"
    r"(?P<args>[^&|;><\n]*)",
    re.IGNORECASE,
)


_PYPI_MGRS: Final[frozenset[str]] = frozenset({"pip", "pip3", "uv", "poetry", "pipenv", "conda"})
_NPM_MGRS: Final[frozenset[str]] = frozenset({"npm", "yarn", "pnpm", "bun"})


def _ecosystem_for(manager: str) -> str | None:
    """Map a recognised manager token to an OSV ecosystem label."""
    m = manager.lower()
    if m in _PYPI_MGRS:
        return "PyPI"
    if m in _NPM_MGRS:
        return "npm"
    return None


# Match `pkg==1.2.3` (PyPI exact-pin) — used by the tool_result scanner.
_PYPI_PIN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.-])(?P<name>[A-Za-z0-9][A-Za-z0-9._-]{0,80})==(?P<version>[0-9][0-9A-Za-z.+!-]*)",
)

# Match `"pkg": "1.2.3"` (package.json dependency line).
_NPM_JSON_DEP_RE: Final[re.Pattern[str]] = re.compile(
    r'"(?P<name>@?[A-Za-z0-9][A-Za-z0-9._/-]{0,80})"\s*:\s*'
    r'"[~^>=<]*(?P<version>[0-9][0-9A-Za-z.+!-]*)"',
)


def _split_pip_specifier(raw: str) -> tuple[str, str | None]:
    """Split ``requests==2.31.0`` into ("requests", "2.31.0")."""
    # Strip extras: ``pkg[extra]==1.0`` -> ``pkg==1.0``
    bracket_start = raw.find("[")
    bracket_end = raw.find("]") if bracket_start != -1 else -1
    if bracket_start != -1 and bracket_end != -1:
        raw = raw[:bracket_start] + raw[bracket_end + 1 :]
    for op in ("===", "==", ">=", "<=", "!=", "~=", ">", "<"):
        if op in raw:
            name, _, version = raw.partition(op)
            return name.strip(), version.strip() or None
    return raw.strip(), None


def _split_npm_specifier(raw: str) -> tuple[str, str | None]:
    """Split ``left-pad@1.3.0`` / ``@scope/pkg@1.0`` into name and version."""
    if raw.startswith("@"):
        at = raw.find("@", 1)
        if at == -1:
            return raw, None
        return raw[:at], _normalise_exact_version(raw[at + 1 :])
    if "@" in raw:
        name, _, version = raw.partition("@")
        return name, _normalise_exact_version(version)
    return raw, None


def _normalise_exact_version(version: str) -> str | None:
    """Return ``version`` if it looks like an exact pin, else ``None``.

    OSV's single-package query expects an exact version string. Ranges,
    tags (``latest``), and prefixes like ``^`` or ``~`` all yield no
    matches even when the underlying version is known-vulnerable, so we
    drop them and let OSV return all-version advisories.
    """
    version = version.strip()
    if not version:
        return None
    if version in {"latest", "next", "beta", "alpha", "rc", "canary"}:
        return None
    if version[0] in "^~<>=!|*x" or " " in version:
        return None
    return version


def _looks_like_installable_token(token: str) -> bool:
    """Filter out tokens that obviously aren't package specifiers."""
    if not token:
        return False
    if token.startswith("-"):
        return False  # flag
    if token.startswith((".", "/")):
        return False  # local path
    if "://" in token:
        return False  # URL
    if token.endswith((".tar.gz", ".whl", ".zip", ".tgz")):
        return False  # archive file
    return True


def extract_install_packages(text: str) -> list[PackageRef]:
    """Best-effort regex scan of ``text`` for package install commands.

    Returns a list of (ecosystem, name, version) refs. Duplicates are
    preserved — callers that care about uniqueness should dedupe. If the
    regex misses a command shape (``sh -c ...``, chained commands, eval,
    base64, unusual managers), the package is simply not emitted. This
    is intentional: the policy is an advisory, not a security boundary.
    """
    refs: list[PackageRef] = []
    for match in _INSTALL_CMD_RE.finditer(text):
        manager = match.group("mgr")
        ecosystem = _ecosystem_for(manager)
        if ecosystem is None:
            continue
        args = match.group("args") or ""
        for raw in args.split():
            if not _looks_like_installable_token(raw):
                continue
            if ecosystem == "PyPI":
                name, version = _split_pip_specifier(raw)
            else:
                name, version = _split_npm_specifier(raw)
            if not name:
                continue
            refs.append(PackageRef(ecosystem=ecosystem, name=name, version=version))
    return refs


def extract_tool_result_packages(text: str) -> list[PackageRef]:
    """Best-effort scan for 'currently installed' version mentions.

    Matches ``pkg==1.2.3`` (pip freeze / pip show output) and
    ``"pkg": "1.2.3"`` (package.json / npm ls output). Designed for the
    common happy path — deliberately not exhaustive.
    """
    refs: list[PackageRef] = []
    for match in _PYPI_PIN_RE.finditer(text):
        name = match.group("name")
        version = match.group("version")
        # Reject obvious non-packages: bare numbers or versions without a name.
        if name and (name[0].isalpha() or name[0] == "_"):
            refs.append(PackageRef(ecosystem="PyPI", name=name, version=version))
    for match in _NPM_JSON_DEP_RE.finditer(text):
        name = match.group("name")
        version = match.group("version")
        if not name:
            continue
        # Skip known non-package JSON keys that accidentally look like deps.
        if name in {"version", "name", "main", "license", "description"}:
            continue
        refs.append(PackageRef(ecosystem="npm", name=name, version=version))
    return refs


# =============================================================================
# OSV client
# =============================================================================


_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_shared_http_client() -> httpx.AsyncClient:
    """Return (creating lazily) the module-level httpx.AsyncClient."""
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
        """Initialize with endpoint URL, timeout, and optional injected client."""
        self._api_url = api_url
        self._timeout = timeout_seconds
        self._client = http_client

    async def query(self, package: PackageRef) -> list[VulnInfo]:
        """Look up vulnerabilities for a single package.

        Returns a (possibly empty) list of VulnInfos. Raises ``httpx.HTTPError``
        on transport errors or non-2xx responses — callers decide whether to
        treat those as warn-on-error or silently skip.
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
    """Turn the OSV query JSON into VulnInfos."""
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
    """Pull the highest severity we can find in one OSV vuln entry."""
    max_sev = Severity.UNKNOWN
    saw_unparseable_vector = False

    for sev in entry.get("severity") or []:
        if not isinstance(sev, dict):
            continue
        raw_score = sev.get("score")
        score = _parse_cvss_score(raw_score)
        if score is not None:
            candidate = Severity.from_cvss_score(score)
            if candidate > max_sev:
                max_sev = candidate
        elif isinstance(raw_score, str) and raw_score.strip().upper().startswith("CVSS:"):
            # CVSS v4 (or any future vector we can't parse). We deliberately
            # don't silently downgrade to UNKNOWN — that would hide real HIGH
            # advisories just because OSV started publishing v4 vectors.
            saw_unparseable_vector = True

    db_specific = entry.get("database_specific")
    if isinstance(db_specific, dict):
        label_sev = Severity.from_label(db_specific.get("severity"))
        if label_sev > max_sev:
            max_sev = label_sev

    for affected in entry.get("affected") or []:
        if not isinstance(affected, dict):
            continue
        aff_db = affected.get("database_specific")
        if isinstance(aff_db, dict):
            label_sev = Severity.from_label(aff_db.get("severity"))
            if label_sev > max_sev:
                max_sev = label_sev

    if saw_unparseable_vector and max_sev is Severity.UNKNOWN:
        logger.info("OSV advisory %s has unparseable CVSS vector; treating as HIGH", entry.get("id"))
        max_sev = Severity.HIGH

    return max_sev


def _parse_cvss_score(raw: Any) -> float | None:
    """Extract the numeric score from an OSV ``severity[].score`` entry."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
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
    """Compute the CVSS v3.x base score from a vector string."""
    metrics: dict[str, str] = {}
    for part in vector.split("/")[1:]:
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
    return _cvss_roundup(base)


def _cvss_roundup(value: float) -> float:
    """CVSS specification roundup: round up to the nearest 0.1."""
    int_input = round(value * 100_000)
    if int_input % 10_000 == 0:
        return int_input / 100_000
    return (int_input // 10_000 + 1) / 10


# =============================================================================
# Redaction and formatting
# =============================================================================


_URL_CREDENTIAL_RE: Final[re.Pattern[str]] = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@\s]+:[^/@\s]+@")
_SENSITIVE_FLAG_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"(--(?:token|password|api[-_]key|auth|secret|header)[=\s])(\S+)",
    re.IGNORECASE,
)


def redact_credentials(text: str) -> str:
    """Strip embedded credentials from a command or URL.

    Used before putting a command into an advisory message (visible to the
    LLM) or an event payload. The policy should not be the vector that
    leaks credentials to its own telemetry pipeline.
    """
    text = _URL_CREDENTIAL_RE.sub(r"\1<redacted>@", text)
    text = _SENSITIVE_FLAG_VALUE_RE.sub(r"\1<redacted>", text)
    return text


# Max chars of untrusted OSV-summary text to show per vuln. Longer summaries
# are a larger prompt-injection surface and rarely informative.
_UNTRUSTED_SUMMARY_MAX = 200


def format_untrusted_summary(summary: str) -> str:
    """Render an OSV summary with a clear untrusted-content delimiter.

    OSV.dev accepts third-party advisory submissions, so the summary field
    is untrusted text that will be shown back to the LLM. Wrap it in a
    labelled quote so a malicious advisory can't cleanly impersonate
    policy instructions.
    """
    text = (summary or "no summary").strip().splitlines()[0]
    if len(text) > _UNTRUSTED_SUMMARY_MAX:
        text = text[:_UNTRUSTED_SUMMARY_MAX] + "…"
    return f"<untrusted OSV advisory text> {text}"


def format_advisory_message(
    results: list[PackageCheckResult],
    threshold: Severity,
    command: str | None = None,
) -> str:
    """Render an advisory message the LLM can relay to the user."""
    lines: list[str] = [
        "SUPPLY CHAIN ADVISORY: one or more referenced packages have known advisories "
        f"at severity {threshold.label} or higher. This is informational — the "
        "command is NOT blocked. Please surface this to the user before proceeding.",
        "",
    ]
    if command:
        lines.append(f"Command: {redact_credentials(command)}")
        lines.append("")

    flagged = [r for r in results if r.advisory_vulns(threshold)]
    errored = [r for r in results if r.error]

    if flagged:
        lines.append("Packages with known advisories:")
        for result in flagged:
            advisories = result.advisory_vulns(threshold)
            version_part = f"@{result.package.version}" if result.package.version else ""
            header = (
                f"- {result.package.name}{version_part} ({result.package.ecosystem}): "
                f"{len(advisories)} advisor{'y' if len(advisories) == 1 else 'ies'} "
                f"[{result.max_severity.label}]"
            )
            lines.append(header)
            for vuln in advisories[:5]:
                lines.append(f"    {vuln.id} [{vuln.severity.label}]: {format_untrusted_summary(vuln.summary)}")
            if len(advisories) > 5:
                lines.append(f"    ... and {len(advisories) - 5} more")

    if errored:
        if flagged:
            lines.append("")
        lines.append("Packages where the OSV lookup failed (advisory status unknown):")
        for result in errored:
            lines.append(f"- {result.package.name} ({result.package.ecosystem}): {result.error or 'unknown error'}")

    lines.append("")
    lines.append(
        "Recommendation: verify the advisory at https://osv.dev, pin to a patched "
        "version if one exists, or choose an alternative package. This policy is "
        "best-effort and may miss obfuscated commands; run OSV-Scanner inside the "
        "sandbox for hardened supply-chain coverage."
    )
    return "\n".join(lines)


__all__ = [
    "OSVClient",
    "PackageCheckResult",
    "PackageRef",
    "Severity",
    "SupplyChainAdvisoryConfig",
    "VulnInfo",
    "extract_install_packages",
    "extract_tool_result_packages",
    "format_advisory_message",
    "format_untrusted_summary",
    "redact_credentials",
]
