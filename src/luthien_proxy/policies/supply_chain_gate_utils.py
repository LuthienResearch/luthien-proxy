"""Utilities for SupplyChainGatePolicy.

Pure helpers: data types, loose regex install-command extraction, an OSV.dev
client, CVSS v3 severity parsing, credential redaction, and blocked-command
builders. See supply_chain_gate_policy.py for the threat model.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Final, Literal

import httpx
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


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
        return cls[upper] if upper in cls.__members__ else cls.UNKNOWN

    @classmethod
    def from_cvss_score(cls, score: float) -> "Severity":
        """Convert a CVSS numeric base score into a qualitative bucket."""
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
        """Human-readable label."""
        return self.name


@dataclass(frozen=True)
class PackageRef:
    """A reference to a single package in a specific ecosystem."""

    ecosystem: str
    name: str
    version: str | None = None

    def cache_key(self) -> str:
        """Key used for caching this package's OSV lookup result."""
        return f"osv:{self.ecosystem}:{self.name}:{self.version or '*'}"

    def blocklist_key(self) -> str | None:
        """Canonical key for matching against the explicit_blocklist."""
        return f"{self.ecosystem}:{self.name}:{self.version}" if self.version else None


@dataclass(frozen=True)
class VulnInfo:
    """OSV vulnerability summary. Deliberately omits the untrusted ``summary`` field."""

    id: str
    severity: Severity

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for caching."""
        return {"id": self.id, "severity": int(self.severity)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VulnInfo":
        """Rehydrate from a dict produced by :meth:`to_dict`."""
        return cls(id=str(data.get("id", "")), severity=Severity(int(data.get("severity", 0))))


@dataclass
class PackageCheckResult:
    """Result of checking one package against OSV + the explicit blocklist."""

    package: PackageRef
    vulns: list[VulnInfo] = field(default_factory=list)
    error: str | None = None
    blocklisted: bool = False

    @property
    def max_severity(self) -> Severity:
        """Highest severity among known vulnerabilities."""
        return max((v.severity for v in self.vulns), default=Severity.UNKNOWN)

    def triggers(self, threshold: Severity) -> bool:
        """Whether this result should fire the gate at ``threshold``."""
        if self.blocklisted:
            return True
        return any(v.severity >= threshold for v in self.vulns)

    def triggering_vulns(self, threshold: Severity) -> list[VulnInfo]:
        """Vulns at or above ``threshold`` (may be empty)."""
        return [v for v in self.vulns if v.severity >= threshold]


_SEVERITY_LITERAL = Literal["low", "medium", "high", "critical"]
_FAIL_MODE_LITERAL = Literal["block", "allow", "warn"]


class SupplyChainGateConfig(BaseModel):
    """Configuration for SupplyChainGatePolicy."""

    severity_threshold: _SEVERITY_LITERAL = Field(
        default="critical",
        description=(
            "Minimum severity at which a matched package triggers substitution. "
            "Default CRITICAL — lower thresholds surface a growing fraction of "
            "CVSS-v4-only advisories as noise."
        ),
    )
    osv_api_url: str = Field(default="https://api.osv.dev/v1/query")
    osv_timeout_seconds: float = Field(default=5.0, gt=0.0)
    osv_fail_mode: _FAIL_MODE_LITERAL = Field(
        default="warn",
        description="Behavior when OSV is unreachable: block | allow | warn.",
    )
    max_concurrent_lookups: int = Field(default=10, ge=1)
    cache_ttl_seconds: int = Field(default=3600, ge=0)
    negative_cache_ttl_seconds: int = Field(default=300, ge=0)
    block_lockfile_installs: bool = Field(
        default=True,
        description=(
            "Substitute lockfile installs (npm ci, pip install -r requirements.txt) "
            "with a dry-run-only command so the LLM can review resolved versions."
        ),
    )
    explicit_blocklist: tuple[str, ...] = Field(
        default=(),
        description=(
            "Versions to block unconditionally. Format: '<ecosystem>:<name>:<version>' (e.g. 'PyPI:litellm:1.59.0')."
        ),
    )
    bash_tool_names: tuple[str, ...] = Field(default=("Bash",))

    @field_validator("bash_tool_names", "explicit_blocklist", mode="before")
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

    @property
    def severity_threshold_enum(self) -> Severity:
        """Parsed severity threshold enum."""
        return Severity.from_label(self.severity_threshold)


_INSTALL_CMD_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?P<mgr>pip|pip3|uv|poetry|pipenv|conda|npm|yarn|pnpm|bun)\s+"
    r"(?:pip\s+)?"  # handles `uv pip install`
    r"(?P<verb>install|add|i|ci|update|upgrade)\b"
    r"(?P<args>[^&|;><\n]*)",
    re.IGNORECASE,
)
_PYPI_MGRS: Final[frozenset[str]] = frozenset({"pip", "pip3", "uv", "poetry", "pipenv", "conda"})
_NPM_MGRS: Final[frozenset[str]] = frozenset({"npm", "yarn", "pnpm", "bun"})
_LOCKFILE_FLAGS: Final[frozenset[str]] = frozenset({"--frozen-lockfile", "--ci", "--no-lockfile-update"})


@dataclass(frozen=True)
class InstallMatch:
    """One install command detected inside a Bash tool_use string."""

    manager: str
    verb: str
    args: str
    packages: tuple[PackageRef, ...]
    is_lockfile: bool


def _ecosystem_for(manager: str) -> str | None:
    m = manager.lower()
    if m in _PYPI_MGRS:
        return "PyPI"
    if m in _NPM_MGRS:
        return "npm"
    return None


def _split_pip_specifier(raw: str) -> tuple[str, str | None]:
    """Split ``requests==2.31.0`` into ("requests", "2.31.0")."""
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
    """Return ``version`` if it looks like an exact pin, else ``None``."""
    version = version.strip()
    if not version or version in {"latest", "next", "beta", "alpha", "rc", "canary"}:
        return None
    if version[0] in "^~<>=!|*x" or " " in version:
        return None
    return version


def _looks_like_installable_token(token: str) -> bool:
    """Filter out tokens that obviously aren't package specifiers."""
    if not token or token.startswith(("-", ".", "/")) or "://" in token:
        return False
    return not token.endswith((".tar.gz", ".whl", ".zip", ".tgz", ".txt"))


def _is_lockfile_install(manager: str, verb: str, args: str) -> bool:
    """Detect lockfile-style installs with no argv-named packages."""
    m = manager.lower()
    v = verb.lower()
    if m == "npm" and v == "ci":
        return True
    if m in {"pip", "pip3", "uv"}:
        tokens = args.split()
        for i, token in enumerate(tokens):
            if token in {"-r", "--requirement"} or token.startswith("--requirement="):
                return True
            if i == 0 and token.endswith(".txt"):
                return True
    if m in {"yarn", "pnpm", "bun"} and v == "install":
        if any(t in _LOCKFILE_FLAGS for t in args.split()):
            return True
    return False


def extract_install_commands(text: str) -> list[InstallMatch]:
    """Regex-scan ``text`` for install commands. Deliberately loose."""
    matches: list[InstallMatch] = []
    for match in _INSTALL_CMD_RE.finditer(text):
        manager = match.group("mgr")
        ecosystem = _ecosystem_for(manager)
        if ecosystem is None:
            continue
        verb = match.group("verb")
        args = match.group("args") or ""
        lockfile = _is_lockfile_install(manager, verb, args)
        packages: list[PackageRef] = []
        if not lockfile:
            for raw in args.split():
                if not _looks_like_installable_token(raw):
                    continue
                if ecosystem == "PyPI":
                    name, version = _split_pip_specifier(raw)
                else:
                    name, version = _split_npm_specifier(raw)
                if name:
                    packages.append(PackageRef(ecosystem=ecosystem, name=name, version=version))
        matches.append(
            InstallMatch(manager=manager, verb=verb, args=args, packages=tuple(packages), is_lockfile=lockfile)
        )
    return matches


def extract_install_packages(text: str) -> list[PackageRef]:
    """Flatten ``extract_install_commands`` to a list of package refs."""
    return [pkg for m in extract_install_commands(text) for pkg in m.packages]


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

        Raises ``httpx.HTTPError`` on transport errors or non-2xx responses.
        """
        body: dict[str, Any] = {"package": {"name": package.name, "ecosystem": package.ecosystem}}
        if package.version:
            body["version"] = package.version
        client = self._client or _get_shared_http_client()
        response = await client.post(self._api_url, json=body, timeout=self._timeout)
        response.raise_for_status()
        return _parse_osv_response(response.json())


def _parse_osv_response(payload: Any) -> list[VulnInfo]:
    """Turn the OSV query JSON into ``VulnInfo`` records."""
    if not isinstance(payload, dict):
        return []
    raw_vulns = payload.get("vulns") or []
    if not isinstance(raw_vulns, list):
        return []
    return [
        VulnInfo(id=str(entry.get("id", "")), severity=_extract_max_severity(entry))
        for entry in raw_vulns
        if isinstance(entry, dict)
    ]


def _extract_max_severity(entry: dict[str, Any]) -> Severity:
    """Pull the highest severity from one OSV vuln entry.

    Unparseable vectors (CVSS v4, future formats) are NOT promoted; we fall
    through to any qualitative label. With the default threshold at CRITICAL,
    this biases toward occasional false-negatives over noisy false-positives
    that would train the LLM to ignore advisories.
    """
    max_sev = Severity.UNKNOWN
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
            logger.info(
                "unparseable CVSS vector for OSV entry %s: %s",
                entry.get("id", "<unknown>"),
                raw_score,
            )
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
        if ":" in part:
            key, _, value = part.partition(":")
            metrics[key.strip().upper()] = value.strip().upper()
    required = ("AV", "AC", "PR", "UI", "S", "C", "I", "A")
    if not all(m in metrics for m in required):
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
    return _cvss_roundup(min(raw, 10.0))


def _cvss_roundup(value: float) -> float:
    """CVSS specification roundup: round up to the nearest 0.1."""
    int_input = round(value * 100_000)
    if int_input % 10_000 == 0:
        return int_input / 100_000
    return (int_input // 10_000 + 1) / 10


_URL_CREDENTIAL_RE: Final[re.Pattern[str]] = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/@\s]+:[^/@\s]+@")
_SENSITIVE_FLAG_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"(--(?:token|password|api[-_]key|auth|secret|header)[=\s])(\S+)",
    re.IGNORECASE,
)


def redact_credentials(text: str) -> str:
    """Strip embedded credentials from a command or URL."""
    text = _URL_CREDENTIAL_RE.sub(r"\1<redacted>@", text)
    return _SENSITIVE_FLAG_VALUE_RE.sub(r"\1<redacted>", text)


_ORIGINAL_CMD_CLIP = 200
_OSV_URL_TEMPLATE: Final[str] = "https://osv.dev/vulnerability/{vuln_id}"


def _clip(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars with an ellipsis marker."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _shell_escape_single_quoted(text: str) -> str:
    r"""Escape ``text`` for embedding inside a bash single-quoted string.

    Inside single quotes bash treats every character literally except the
    single quote itself. Replace each ``'`` with ``'\''``.
    """
    return text.replace("'", "'\\''")


_DRY_RUN_COMMANDS: Final[dict[tuple[str, str], str]] = {
    ("npm", "ci"): "npm ci --dry-run",
    ("yarn", "install"): "yarn install --mode=skip-build",
    ("pnpm", "install"): "pnpm install --lockfile-only",
    ("bun", "install"): "bun install --dry-run",
    ("pip", "install"): "pip install --dry-run -r requirements.txt",
    ("pip3", "install"): "pip install --dry-run -r requirements.txt",
    ("uv", "install"): "uv pip install --dry-run -r requirements.txt",
}


def _dry_run_for(manager: str, verb: str) -> str:
    """Return a dry-run command string for a recognised lockfile manager."""
    return _DRY_RUN_COMMANDS.get((manager.lower(), verb.lower()), f"{manager.lower()} --help")


def _format_blocked_lines(original_command: str, results: list[PackageCheckResult], threshold: Severity) -> list[str]:
    """Build the human-readable body of a blocked-command error message."""
    redacted = _clip(redact_credentials(original_command), _ORIGINAL_CMD_CLIP)
    lines = [
        "LUTHIEN BLOCKED: supply-chain gate refused to run this command.",
        f"Original command: {redacted}",
        "",
        "Reason: one or more packages in this install match a known advisory at "
        f"severity {threshold.label} or higher, or appear on the explicit blocklist.",
    ]
    triggered = [r for r in results if r.triggers(threshold)]
    if triggered:
        lines += ["", "Flagged packages:"]
        for result in triggered:
            ver = f"@{result.package.version}" if result.package.version else ""
            if result.blocklisted:
                lines.append(f"- {result.package.name}{ver} ({result.package.ecosystem}): explicit blocklist match")
                continue
            lines.append(f"- {result.package.name}{ver} ({result.package.ecosystem}) [{result.max_severity.label}]")
            for vuln in result.triggering_vulns(threshold)[:3]:
                lines.append(f"    {vuln.id} [{vuln.severity.label}] {_OSV_URL_TEMPLATE.format(vuln_id=vuln.id)}")
    lines += [
        "",
        "This is a best-effort gate for cooperative LLMs. To proceed intentionally, "
        "pin a patched version or name a package with no matching advisory.",
    ]
    return lines


def build_blocked_command(original_command: str, results: list[PackageCheckResult], threshold: Severity) -> str:
    """Build the ``sh -c`` replacement command emitted when a package matches.

    Every string we emit is under our control: OSV vuln IDs, package names,
    ecosystems, CVE/severity labels, and an osv.dev URL. We never include
    the OSV ``summary`` field (untrusted text).
    """
    body = "\n".join(_format_blocked_lines(original_command, results, threshold))
    escaped = _shell_escape_single_quoted(body)
    return f"sh -c 'printf \"%s\\n\" '\"'\"'{escaped}'\"'\"' >&2; exit 42'"


_LOCKFILE_ADVISORY = (
    "LUTHIEN: lockfile installs are held by the supply-chain gate. The dry-run "
    "output above lists every resolved package version. Please review it, then "
    "re-run by naming the specific packages explicitly (e.g. 'npm install "
    "pkg@1.2.3 other@4.5.6') so each version can be checked against OSV."
)


def build_lockfile_review_command(original_command: str, manager: str, verb: str) -> str:
    """Build the lockfile dry-run substitute command (Option B)."""
    dry_run_cmd = _dry_run_for(manager, verb)
    redacted = _clip(redact_credentials(original_command), _ORIGINAL_CMD_CLIP)
    escaped_original = _shell_escape_single_quoted(redacted)
    escaped_advisory = _shell_escape_single_quoted(_LOCKFILE_ADVISORY)
    return (
        "sh -c '"
        f'printf "LUTHIEN lockfile review (substituting dry-run for: %s)\\n" '
        f"'\"'\"'{escaped_original}'\"'\"' >&2; "
        f"{dry_run_cmd} 2>&1; "
        f"printf \"\\n%s\\n\" '\"'\"'{escaped_advisory}'\"'\"' >&2; "
        "exit 42'"
    )


__all__ = [
    "InstallMatch",
    "OSVClient",
    "PackageCheckResult",
    "PackageRef",
    "Severity",
    "SupplyChainGateConfig",
    "VulnInfo",
    "build_blocked_command",
    "build_lockfile_review_command",
    "extract_install_commands",
    "extract_install_packages",
    "redact_credentials",
]
