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


_ECOSYSTEM_WIRE_FORM: Final[dict[str, str]] = {"pypi": "PyPI", "npm": "npm"}


def _canonical_ecosystem(ecosystem: str) -> str:
    """Lowercased, normalized ecosystem slug used for all internal comparisons."""
    return ecosystem.strip().lower()


def _canonicalize_package(ecosystem: str, name: str) -> str:
    """Normalize a package name per ecosystem rules for matching.

    PyPI uses PEP 503 (case-insensitive, ``[-_.]+`` collapsed to ``-``).
    npm is case-insensitive for matching (scopes included) and preserves the
    ``@scope/name`` shape. Unknown ecosystems fall back to ``.lower()``.
    """
    eco = _canonical_ecosystem(ecosystem)
    stripped = name.strip()
    if eco == "pypi":
        return re.sub(r"[-_.]+", "-", stripped).lower()
    return stripped.lower()


def _osv_wire_ecosystem(ecosystem: str) -> str:
    """Translate a canonical ecosystem slug to OSV's wire-form string."""
    return _ECOSYSTEM_WIRE_FORM.get(_canonical_ecosystem(ecosystem), ecosystem)


@dataclass(frozen=True)
class PackageRef:
    """A reference to a single package in a specific ecosystem.

    ``ecosystem`` and ``name`` are auto-canonicalized on construction:
    ecosystem is lowercased; ``name`` is normalized per PEP 503 for PyPI or
    lowercased for npm. The original ``display_name`` is preserved for error
    messages. Equality is over the canonical fields, so case-variant
    duplicates collapse.
    """

    ecosystem: str
    name: str
    version: str | None = None
    display_name: str = field(default="", compare=False)

    def __post_init__(self) -> None:
        """Auto-canonicalize ecosystem/name and fall back display_name to the input."""
        canon_eco = _canonical_ecosystem(self.ecosystem)
        canon_name = _canonicalize_package(canon_eco, self.name)
        original_display = self.display_name or self.name
        object.__setattr__(self, "ecosystem", canon_eco)
        object.__setattr__(self, "name", canon_name)
        object.__setattr__(self, "display_name", original_display)

    @property
    def osv_ecosystem(self) -> str:
        """OSV wire-form ecosystem (e.g., ``PyPI``) for this package."""
        return _osv_wire_ecosystem(self.ecosystem)

    def cache_key(self) -> str:
        """Key used for caching this package's OSV lookup result."""
        return f"osv:{self.osv_ecosystem}:{self.name}:{self.version or '*'}"

    def blocklist_key(self) -> str | None:
        """Canonical key (lowercased ecosystem) for matching against the blocklist."""
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
            "Versions to block unconditionally. Format: '<ecosystem>:<name>:<version>' "
            "(e.g. 'PyPI:litellm:1.59.0'). Matching is case-insensitive and PEP 503 "
            "normalized for PyPI names; both the blocklist entry and the command's "
            "package name are canonicalized before comparison."
        ),
    )
    bash_tool_names: tuple[str, ...] = Field(default=("Bash",))

    @field_validator("bash_tool_names", mode="before")
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

    @field_validator("explicit_blocklist", mode="before")
    @classmethod
    def _normalize_blocklist(cls, value: Any) -> tuple[str, ...]:
        """Canonicalize each blocklist entry to lowercase ecosystem + PEP 503 name.

        Input tolerates any casing of ecosystem (``PyPI``/``pypi``) and any
        casing/punctuation of name. The stored form uses the canonical
        ecosystem slug and canonical package name, so membership tests just
        call :meth:`PackageRef.blocklist_key` on the extracted package.
        """
        if value is None:
            return ()
        if isinstance(value, str):
            raw_entries: list[str] = [value]
        elif isinstance(value, (list, tuple)):
            raw_entries = [str(v) for v in value]
        else:
            return value
        canonical: list[str] = []
        for entry in raw_entries:
            parts = entry.split(":")
            if len(parts) != 3:
                # Malformed — keep as-is so the user sees it in logs rather
                # than silently dropping.
                canonical.append(entry)
                continue
            eco_raw, name_raw, ver_raw = parts
            canon_eco = _canonical_ecosystem(eco_raw)
            canon_name = _canonicalize_package(canon_eco, name_raw)
            canonical.append(f"{canon_eco}:{canon_name}:{ver_raw.strip()}")
        return tuple(canonical)

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
_LINE_CONTINUATION_RE: Final[re.Pattern[str]] = re.compile(r"\\\n[ \t]*")
_PYPI_MGRS: Final[frozenset[str]] = frozenset({"pip", "pip3", "uv", "poetry", "pipenv", "conda"})
_NPM_MGRS: Final[frozenset[str]] = frozenset({"npm", "yarn", "pnpm", "bun"})
_LOCKFILE_FLAGS: Final[frozenset[str]] = frozenset({"--frozen-lockfile", "--ci", "--no-lockfile-update", "--immutable"})

# Commands that execute their arguments inside a sandboxed environment that does
# NOT affect the user's host. We deliberately refuse to recurse into the wrapped
# command — the brief says v3 is a cooperative gate, not adversarial-robust. If
# the inner install matters, the sandbox itself (or an OSV-Scanner pre-step)
# should catch it. Note: ``sudo`` and ``env`` are NOT wrappers in this sense;
# they run on the host and must still be extracted.
_WRAPPER_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "docker",
        "podman",
        "nerdctl",
        "kubectl",
        "oc",
        "ssh",
        "nsenter",
        "nix-shell",
        "tox",
        "nox",
        "vagrant",
    }
)


@dataclass(frozen=True)
class InstallMatch:
    """One install command detected inside a Bash tool_use string.

    ``requirement_file``/``constraint_file`` capture ``-r`` / ``-c`` args from
    lockfile-style pip invocations so the dry-run substitute can reference the
    original filenames rather than hardcoded ``requirements.txt``.
    """

    manager: str
    verb: str
    args: str
    packages: tuple[PackageRef, ...]
    is_lockfile: bool
    requirement_file: str | None = None
    constraint_file: str | None = None


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


def _extract_pip_requirement_files(args: str) -> tuple[str | None, str | None]:
    """Pull ``-r <FILE>`` and ``-c <FILE>`` pairs from a pip args string.

    Returns ``(requirement_file, constraint_file)``. Only the first of each is
    captured — the dry-run substitute forwards them verbatim so the LLM sees
    a preview of the exact file it asked to install.
    """
    tokens = args.split()
    req: str | None = None
    con: str | None = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"-r", "--requirement"} and i + 1 < len(tokens):
            req = req or tokens[i + 1]
            i += 2
            continue
        if token.startswith("--requirement="):
            req = req or token.split("=", 1)[1]
            i += 1
            continue
        if token in {"-c", "--constraint"} and i + 1 < len(tokens):
            con = con or tokens[i + 1]
            i += 2
            continue
        if token.startswith("--constraint="):
            con = con or token.split("=", 1)[1]
            i += 1
            continue
        i += 1
    return req, con


def _detect_lockfile(manager: str, verb: str, args: str) -> tuple[bool, str | None, str | None]:
    """Detect lockfile-style installs and capture any -r/-c pip args.

    Returns ``(is_lockfile, requirement_file, constraint_file)``. Bare
    ``pip install`` (no ``-r``/``-c``) is NOT treated as a lockfile install.
    """
    m = manager.lower()
    v = verb.lower()
    if m == "npm" and v == "ci":
        return True, None, None
    if m in {"pip", "pip3", "uv"}:
        req, con = _extract_pip_requirement_files(args)
        if req is not None or con is not None:
            return True, req, con
        return False, None, None
    if m in {"yarn", "pnpm"} and v == "install":
        if any(t in _LOCKFILE_FLAGS for t in args.split()):
            return True, None, None
        # yarn/pnpm default `install` is lockfile-driven if no package specs.
        if not any(_looks_like_installable_token(t) for t in args.split()):
            return True, None, None
        return False, None, None
    if m == "bun" and v == "install":
        if any(t in _LOCKFILE_FLAGS for t in args.split()):
            return True, None, None
    return False, None, None


def _leading_command_words(text: str) -> list[str]:
    """Return the first word of each pipeline segment in ``text``.

    Pipeline separators are ``&&``, ``||``, ``|``, ``;``. Used to detect
    wrapper commands like ``docker run`` regardless of where they appear in
    a chain (e.g. ``cd /tmp && docker run ...``).
    """
    segments = re.split(r"&&|\|\||[|;]", text)
    words: list[str] = []
    for seg in segments:
        stripped = seg.strip()
        if not stripped:
            continue
        first = stripped.split(None, 1)[0]
        words.append(first.lower())
    return words


def _is_wrapper_command(text: str) -> bool:
    """Return True if any pipeline segment starts with a sandbox wrapper."""
    for word in _leading_command_words(text):
        if word in _WRAPPER_COMMANDS:
            return True
    return False


def _normalize_line_continuations(text: str) -> str:
    """Fold backslash-newline line continuations into single spaces."""
    return _LINE_CONTINUATION_RE.sub(" ", text)


def extract_install_commands(text: str) -> list[InstallMatch]:
    """Regex-scan ``text`` for install commands. Deliberately loose.

    Returns an empty list when ``text`` starts with a sandbox wrapper (docker,
    kubectl, ssh, ...) — the install executes inside an isolated environment
    that does not affect the host, and we intentionally do not recurse into
    the wrapped command.
    """
    normalized = _normalize_line_continuations(text)
    if _is_wrapper_command(normalized):
        return []
    matches: list[InstallMatch] = []
    for match in _INSTALL_CMD_RE.finditer(normalized):
        manager = match.group("mgr")
        ecosystem = _ecosystem_for(manager)
        if ecosystem is None:
            continue
        verb = match.group("verb")
        args = match.group("args") or ""
        lockfile, req_file, con_file = _detect_lockfile(manager, verb, args)
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
            InstallMatch(
                manager=manager,
                verb=verb,
                args=args,
                packages=tuple(packages),
                is_lockfile=lockfile,
                requirement_file=req_file,
                constraint_file=con_file,
            )
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
        body: dict[str, Any] = {"package": {"name": package.name, "ecosystem": package.osv_ecosystem}}
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


def _sh_single_quote(text: str) -> str:
    """Wrap ``text`` in single quotes, escaping embedded single quotes."""
    return "'" + text.replace("'", "'\\''") + "'"


# Managers whose native dry-run mode resolves the lockfile WITHOUT touching
# disk. These enter the lockfile-dry-run substitution path.
_REAL_DRY_RUN_MANAGERS: Final[frozenset[str]] = frozenset({"npm", "pip", "pip3", "uv", "bun"})

# Managers with no true dry-run mode. yarn's ``--mode=skip-build`` still writes
# to disk; pnpm's ``--lockfile-only`` rewrites the lockfile. These enter the
# explain-and-refuse path instead of a fake dry-run.
_NO_DRY_RUN_MANAGERS: Final[frozenset[str]] = frozenset({"yarn", "pnpm"})


def _pip_dry_run_invocation(manager: str, requirement_file: str | None, constraint_file: str | None) -> str:
    """Build a pip/uv dry-run command that threads the original -r/-c args."""
    base = "uv pip install --dry-run" if manager.lower() == "uv" else f"{manager.lower()} install --dry-run"
    parts = [base]
    if requirement_file:
        parts += ["-r", _sh_single_quote(requirement_file)]
    if constraint_file:
        parts += ["-c", _sh_single_quote(constraint_file)]
    return " ".join(parts)


def _dry_run_for(
    manager: str,
    verb: str,
    requirement_file: str | None = None,
    constraint_file: str | None = None,
) -> str:
    """Return a dry-run command for a manager with true dry-run support."""
    m = manager.lower()
    v = verb.lower()
    if m == "npm" and v == "ci":
        return "npm ci --dry-run"
    if m == "bun" and v == "install":
        return "bun install --dry-run"
    if m in {"pip", "pip3", "uv"}:
        return _pip_dry_run_invocation(m, requirement_file, constraint_file)
    # Should not happen — callers gate on _REAL_DRY_RUN_MANAGERS.
    return f"{m} --help"


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


def _build_sh_c_wrapper(inner_script: str) -> str:
    """Wrap a shell script in ``sh -c '...'``, escaping single quotes.

    ``inner_script`` is a literal shell command (or sequence of commands
    separated by ``;``) that will run when the substitute is executed. The
    outer wrapper re-escapes every single quote in ``inner_script`` so it
    survives the outer shell's quote-stripping pass. The emitted wrapper
    uses ``sh -c`` rather than ``bash -c`` because it must run on any POSIX
    shell the user's agent happens to invoke.
    """
    return f"sh -c {_sh_single_quote(inner_script)}"


def build_blocked_command(original_command: str, results: list[PackageCheckResult], threshold: Severity) -> str:
    """Build the bash replacement command emitted when a package matches.

    Every string we emit is under our control: OSV vuln IDs, package names,
    ecosystems, CVE/severity labels, and an osv.dev URL. We never include
    the OSV ``summary`` field (untrusted text).
    """
    body = "\n".join(_format_blocked_lines(original_command, results, threshold))
    inner = f"printf '%s\\n' {_sh_single_quote(body)} >&2; exit 42"
    return _build_sh_c_wrapper(inner)


_LOCKFILE_ADVISORY = (
    "LUTHIEN: lockfile installs are held by the supply-chain gate. The dry-run "
    "output above lists every resolved package version. Please review it, then "
    "re-run by naming the specific packages explicitly (e.g. 'npm install "
    "pkg@1.2.3 other@4.5.6') so each version can be checked against OSV."
)

_LOCKFILE_EXPLAIN_REFUSE_ADVISORY = (
    "LUTHIEN BLOCKED: yarn/pnpm lockfile installs cannot be safely previewed "
    "by Luthien because these tools do not support a true dry-run mode that "
    "resolves the lockfile without writing to disk. To proceed safely:\n"
    "  1. Review the lockfile diff manually,\n"
    "  2. Or convert to explicit per-package installs that Luthien can vet,\n"
    "  3. Or set block_lockfile_installs=false in the policy config to opt out "
    "(NOT recommended)."
)


def build_lockfile_dry_run_command(
    original_command: str,
    manager: str,
    verb: str,
    requirement_file: str | None = None,
    constraint_file: str | None = None,
) -> str:
    """Build a lockfile dry-run substitute for managers with real dry-run mode.

    Only npm, pip/pip3, uv, and bun have a native dry-run that resolves the
    lockfile without touching disk. For pip the ``-r``/``-c`` file names are
    threaded through verbatim so the LLM previews the exact file it asked to
    install, not a hardcoded ``requirements.txt``.
    """
    if manager.lower() not in _REAL_DRY_RUN_MANAGERS:
        raise ValueError(f"build_lockfile_dry_run_command does not support manager={manager!r}")
    dry_run_cmd = _dry_run_for(manager, verb, requirement_file, constraint_file)
    redacted = _clip(redact_credentials(original_command), _ORIGINAL_CMD_CLIP)
    header = f"LUTHIEN lockfile review (substituting dry-run for: {redacted})"
    inner = (
        f"printf '%s\\n' {_sh_single_quote(header)} >&2; "
        f"{dry_run_cmd} 2>&1; "
        f"printf '\\n%s\\n' {_sh_single_quote(_LOCKFILE_ADVISORY)} >&2; "
        "exit 42"
    )
    return _build_sh_c_wrapper(inner)


def build_lockfile_explain_refuse_command(original_command: str, manager: str, verb: str) -> str:
    """Build an explain-and-refuse substitute for yarn/pnpm lockfile installs.

    These managers have no real dry-run mode — ``yarn install --mode=skip-build``
    still writes to disk, ``pnpm install --lockfile-only`` rewrites the
    lockfile. Rather than pretend, we print an explanation, list remediation
    options, and exit 42 without running anything.
    """
    if manager.lower() not in _NO_DRY_RUN_MANAGERS:
        raise ValueError(f"build_lockfile_explain_refuse_command does not support manager={manager!r}")
    redacted = _clip(redact_credentials(original_command), _ORIGINAL_CMD_CLIP)
    # Assemble the body once and let _sh_single_quote handle all escaping.
    body = f"{_LOCKFILE_EXPLAIN_REFUSE_ADVISORY}\nOriginal command: {redacted}"
    inner = f"printf '%s\\n' {_sh_single_quote(body)} >&2; exit 42"
    return _build_sh_c_wrapper(inner)


def build_lockfile_substitute(match: InstallMatch, original_command: str) -> str:
    """Dispatch to the correct lockfile substitute for ``match``'s manager."""
    mgr = match.manager.lower()
    if mgr in _NO_DRY_RUN_MANAGERS:
        return build_lockfile_explain_refuse_command(original_command, match.manager, match.verb)
    return build_lockfile_dry_run_command(
        original_command,
        match.manager,
        match.verb,
        requirement_file=match.requirement_file,
        constraint_file=match.constraint_file,
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
    "build_lockfile_dry_run_command",
    "build_lockfile_explain_refuse_command",
    "build_lockfile_substitute",
    "extract_install_commands",
    "extract_install_packages",
    "redact_credentials",
]
