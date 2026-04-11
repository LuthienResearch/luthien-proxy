"""Utilities for :mod:`supply_chain_blocklist_policy`.

Pure helpers (no I/O other than the OSV HTTP client): blocklist data
structures, PEP 440 / semver range matching, loose-regex extraction of package
literals from shell commands, and the ``sh -c`` substitute builder. Kept
separate from the policy so each concern is unit-testable in isolation.

The policy is cooperative-LLM only. We never attempt to parse arbitrary bash;
the regex extraction is a best-effort surface, backed by a literal-substring
backstop that catches exact ``name@version`` strings anywhere in the command.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Iterable

import httpx
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field

from luthien_proxy.policies.supply_chain_blocklist_db import BlocklistRow

logger = logging.getLogger(__name__)


ECOSYSTEM_PYPI: Final[str] = "PyPI"
ECOSYSTEM_NPM: Final[str] = "npm"
SUPPORTED_ECOSYSTEMS: Final[tuple[str, ...]] = (ECOSYSTEM_PYPI, ECOSYSTEM_NPM)


class SupplyChainBlocklistConfig(BaseModel):
    """Policy configuration (all fields defaulted for drop-in YAML use)."""

    bash_tool_names: tuple[str, ...] = Field(default=("Bash",))
    osv_api_url: str = Field(default="https://api.osv.dev/v1/query")
    osv_fetch_url: str = Field(default="https://api.osv.dev/v1/vulns/")
    osv_timeout_seconds: float = Field(default=15.0)
    poll_interval_seconds: float = Field(default=300.0)
    poll_jitter_seconds: float = Field(default=60.0)
    min_severity: str = Field(default="CRITICAL")
    # On first run, ingest advisories published in the last N days.
    initial_lookback_days: int = Field(default=30)
    # Soft cap on entries returned per ecosystem per tick.
    max_entries_per_tick: int = Field(default=500)

    model_config = ConfigDict(frozen=True)


@dataclass(frozen=True)
class AffectedRange:
    """A structured version range from an OSV advisory.

    OSV emits ``ranges`` as ordered events (``introduced``, ``fixed``,
    ``last_affected``). We normalize to closed/half-open bounds:

    - ``introduced``: lower inclusive bound (``None`` for "all versions below fixed").
    - ``fixed``: upper exclusive bound (``None`` for "no fix yet").
    - ``last_affected``: upper inclusive bound (mutually exclusive with ``fixed``).
    """

    introduced: str | None
    fixed: str | None
    last_affected: str | None

    def to_json(self) -> str:
        """Serialize to the shape stored in the ``affected_range`` column."""
        return json.dumps(
            {"introduced": self.introduced, "fixed": self.fixed, "last_affected": self.last_affected},
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> "AffectedRange":
        """Parse a range serialized by :meth:`to_json`."""
        data = json.loads(raw)
        return cls(
            introduced=data.get("introduced"),
            fixed=data.get("fixed"),
            last_affected=data.get("last_affected"),
        )


@dataclass(frozen=True)
class BlocklistEntry:
    """An in-memory blocklist entry for a single (ecosystem, package, range, cve)."""

    ecosystem: str
    canonical_name: str
    cve_id: str
    severity: str
    range: AffectedRange

    @classmethod
    def from_row(cls, row: BlocklistRow) -> "BlocklistEntry":
        """Build an in-memory entry from a DB row."""
        return cls(
            ecosystem=row.ecosystem,
            canonical_name=row.canonical_name,
            cve_id=row.cve_id,
            severity=row.severity,
            range=AffectedRange.from_json(row.affected_range),
        )


def canonicalize_name(ecosystem: str, name: str) -> str:
    """Lowercase name and apply PEP 503 collapse for PyPI.

    PyPI: case-insensitive, ``[-_.]+`` collapses to ``-`` (PEP 503).
    npm: case-insensitive with scope preserved (``@scope/name``).
    """
    stripped = name.strip()
    if ecosystem == ECOSYSTEM_PYPI:
        return re.sub(r"[-_.]+", "-", stripped).lower()
    return stripped.lower()


# =============================================================================
# RANGE MATCHING
# =============================================================================


def _parse_pypi_version(v: str) -> Version | None:
    try:
        return Version(v)
    except InvalidVersion:
        return None


# Strict numeric semver (X.Y.Z, optional -pre.N). Enough for our OSV range
# endpoints; the lookup literals we match also have to be numeric semver or
# we fail closed (no match). Hand-rolled because ``python-semver`` isn't a
# dependency and this is easier to audit than importing a package.
_SEMVER_RE: Final[re.Pattern[str]] = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class _SemverTuple:
    major: int
    minor: int
    patch: int
    prerelease: tuple[object, ...] | None  # None ranks higher than any prerelease

    def __lt__(self, other: "_SemverTuple") -> bool:
        if (self.major, self.minor, self.patch) != (other.major, other.minor, other.patch):
            return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)
        # Same core: the one WITH a prerelease ranks lower than the one without.
        if self.prerelease is None and other.prerelease is None:
            return False
        if self.prerelease is None:
            return False
        if other.prerelease is None:
            return True
        return self.prerelease < other.prerelease

    def __le__(self, other: "_SemverTuple") -> bool:
        return self == other or self < other


def _parse_semver(v: str) -> _SemverTuple | None:
    m = _SEMVER_RE.match(v.strip())
    if not m:
        return None
    major, minor, patch, pre = m.group(1), m.group(2), m.group(3), m.group(4)
    if pre is None:
        return _SemverTuple(int(major), int(minor), int(patch), None)
    pre_parts: tuple[object, ...] = tuple(int(p) if p.isdigit() else p for p in pre.split("."))
    return _SemverTuple(int(major), int(minor), int(patch), pre_parts)


def version_matches(ecosystem: str, candidate: str, affected: AffectedRange) -> bool:
    """Check whether ``candidate`` falls inside ``affected`` for ``ecosystem``.

    Returns ``False`` (fail-closed) if the candidate version cannot be parsed
    — the substring backstop is still in play, and the policy caller never
    emits a substitution based on False here alone.
    """
    if ecosystem == ECOSYSTEM_PYPI:
        return _pypi_in_range(candidate, affected)
    if ecosystem == ECOSYSTEM_NPM:
        return _npm_in_range(candidate, affected)
    return False


def _pypi_in_range(candidate: str, affected: AffectedRange) -> bool:
    cand = _parse_pypi_version(candidate)
    if cand is None:
        return False
    if affected.introduced is not None:
        lower = _parse_pypi_version(affected.introduced)
        if lower is None or cand < lower:
            return False
    if affected.fixed is not None:
        upper = _parse_pypi_version(affected.fixed)
        if upper is None:
            return False
        if cand >= upper:
            return False
    if affected.last_affected is not None:
        upper = _parse_pypi_version(affected.last_affected)
        if upper is None:
            return False
        if cand > upper:
            return False
    # All-None range means "every version affected".
    return True


def _npm_in_range(candidate: str, affected: AffectedRange) -> bool:
    cand = _parse_semver(candidate)
    if cand is None:
        return False
    if affected.introduced is not None:
        lower = _parse_semver(affected.introduced)
        if lower is None or cand < lower:
            return False
    if affected.fixed is not None:
        upper = _parse_semver(affected.fixed)
        if upper is None:
            return False
        if not (cand < upper):
            return False
    if affected.last_affected is not None:
        upper = _parse_semver(affected.last_affected)
        if upper is None:
            return False
        if upper < cand:
            return False
    return True


# =============================================================================
# COMMAND EXTRACTION
# =============================================================================

# Loose regex: captures ``name@version`` or ``name==version`` literals anywhere
# in a command string. Scoped packages (``@scope/name@version``) are handled
# by the second alternation. Version = any non-whitespace run containing at
# least one digit (so ``foo@latest`` doesn't match).
_PYPI_LITERAL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*(?P<version>[0-9][A-Za-z0-9._+!-]*)"
)
_NPM_LITERAL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<name>(?:@[A-Za-z0-9][A-Za-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*)@(?P<version>[0-9][A-Za-z0-9._+-]*)"
)


@dataclass(frozen=True)
class ExtractedLiteral:
    """A (potential) ``name version`` literal extracted from a command."""

    ecosystem: str
    name: str
    version: str
    # The exact slice of the source command this literal was drawn from, for
    # substring backstop and error-reporting.
    raw: str


def extract_literals(command: str) -> list[ExtractedLiteral]:
    """Extract PyPI ``name==version`` and npm ``name@version`` literals.

    The regexes are loose on purpose: this is the happy path for cooperative
    LLMs. Adversarial obfuscation is explicitly out of scope (see policy
    docstring). The backstop substring scan catches literals the regex misses.
    """
    if not command:
        return []
    seen: set[tuple[str, str, str]] = set()
    out: list[ExtractedLiteral] = []
    for m in _PYPI_LITERAL_RE.finditer(command):
        key = (ECOSYSTEM_PYPI, m.group("name").lower(), m.group("version"))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ExtractedLiteral(
                ecosystem=ECOSYSTEM_PYPI,
                name=m.group("name"),
                version=m.group("version"),
                raw=m.group(0),
            )
        )
    for m in _NPM_LITERAL_RE.finditer(command):
        # Exclude emails and similar ``user@host`` hits: the npm regex's
        # version group requires a leading digit, so ``foo@latest`` and
        # ``foo@example.com`` both fall out automatically.
        key = (ECOSYSTEM_NPM, m.group("name").lower(), m.group("version"))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ExtractedLiteral(
                ecosystem=ECOSYSTEM_NPM,
                name=m.group("name"),
                version=m.group("version"),
                raw=m.group(0),
            )
        )
    return out


# =============================================================================
# BLOCKLIST INDEX
# =============================================================================


class BlocklistIndex:
    """In-memory lookup structure.

    Bundles the ``(ecosystem, canonical_name) -> entries`` map with the set
    of literal ``name@version`` strings used by the substring backstop. The
    index is rebuilt (not mutated in place) every time the background task
    pushes updates; per-request lookups read a snapshot.
    """

    def __init__(self, entries: Iterable[BlocklistEntry]) -> None:
        """Index the given entries."""
        by_key: dict[tuple[str, str], list[BlocklistEntry]] = {}
        literals: dict[str, BlocklistEntry] = {}
        for entry in entries:
            key = (entry.ecosystem, entry.canonical_name)
            by_key.setdefault(key, []).append(entry)
            # Best-effort literal backstop: only meaningful when the entry's
            # range pins a single version (introduced == last_affected == fixed?).
            for lit in _literal_strings_for(entry):
                literals[lit] = entry
        self._by_key = by_key
        self._literals = literals

    def __len__(self) -> int:  # noqa: D105
        return sum(len(v) for v in self._by_key.values())

    def lookup(self, extracted: ExtractedLiteral) -> BlocklistEntry | None:
        """Return the first entry whose range contains ``extracted``."""
        canon = canonicalize_name(extracted.ecosystem, extracted.name)
        candidates = self._by_key.get((extracted.ecosystem, canon), [])
        for entry in candidates:
            if version_matches(extracted.ecosystem, extracted.version, entry.range):
                return entry
        return None

    def substring_backstop(self, command: str) -> BlocklistEntry | None:
        """Return an entry whose literal ``name@version`` form appears in ``command``."""
        if not command or not self._literals:
            return None
        for literal, entry in self._literals.items():
            if literal in command:
                return entry
        return None


def _literal_strings_for(entry: BlocklistEntry) -> list[str]:
    """Return a small set of literal strings to probe with the substring backstop.

    We emit backstops only when the range pins exact versions (introduced ==
    last_affected, or fixed is the next patch of introduced). Emitting a
    backstop for an open-ended range ("<1.6.9") would cause noisy false hits
    on unrelated version literals; the primary path already handles open
    ranges via version_matches().
    """
    r = entry.range
    exact: str | None = None
    if r.introduced is not None and r.last_affected is not None and r.introduced == r.last_affected:
        exact = r.introduced
    elif r.introduced is not None and r.fixed is None and r.last_affected is None:
        # Single-version advisory encoded as introduced-only.
        exact = r.introduced
    if exact is None:
        return []
    forms: list[str] = []
    if entry.ecosystem == ECOSYSTEM_PYPI:
        forms.append(f"{entry.canonical_name}=={exact}")
    elif entry.ecosystem == ECOSYSTEM_NPM:
        forms.append(f"{entry.canonical_name}@{exact}")
    return forms


# =============================================================================
# OSV CLIENT
# =============================================================================


@dataclass(frozen=True)
class OSVFetchResult:
    """One tick of OSV data for one ecosystem."""

    ecosystem: str
    entries: list[BlocklistRow]
    latest_published_at: datetime | None


class OSVClient:
    """Minimal OSV client.

    Uses ``POST /v1/query`` with ``{"package": {"ecosystem": ...}}`` to fetch
    advisories for an ecosystem. OSV does not support a true "since"
    parameter on the query endpoint, so the caller post-filters by
    ``published_at > last_seen_at``.

    Injectable via :class:`SupplyChainBlocklistPolicy` for tests; the real
    client is ``httpx.AsyncClient``-backed.
    """

    def __init__(self, api_url: str, timeout_seconds: float) -> None:
        """Init with an OSV base URL and a timeout."""
        self._api_url = api_url
        self._timeout = timeout_seconds

    async def fetch_recent(
        self,
        ecosystem: str,
        since: datetime | None,
        min_severity: str,
        limit: int,
    ) -> OSVFetchResult:
        """Query OSV and return parsed ranges for ``ecosystem`` published after ``since``."""
        payload = {"package": {"ecosystem": ecosystem}}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._api_url, json=payload)
            response.raise_for_status()
            data = response.json()
        vulns = data.get("vulns") or []
        return self._parse_response(ecosystem, vulns, since, min_severity, limit)

    @staticmethod
    def _parse_response(
        ecosystem: str,
        raw_vulns: list[dict],
        since: datetime | None,
        min_severity: str,
        limit: int,
    ) -> OSVFetchResult:
        min_rank = _severity_rank(min_severity)
        entries: list[BlocklistRow] = []
        latest_seen: datetime | None = None
        for vuln in raw_vulns:
            if len(entries) >= limit:
                break
            published = _parse_iso(vuln.get("published"))
            if published is None:
                continue
            if since is not None and published <= since:
                continue
            if latest_seen is None or published > latest_seen:
                latest_seen = published
            severity = _extract_severity_label(vuln)
            if _severity_rank(severity) < min_rank:
                continue
            cve_id = str(vuln.get("id") or "")
            if not cve_id:
                continue
            for affected in vuln.get("affected") or []:
                pkg = affected.get("package") or {}
                if pkg.get("ecosystem") != ecosystem:
                    continue
                name = pkg.get("name")
                if not isinstance(name, str):
                    continue
                canonical = canonicalize_name(ecosystem, name)
                for range_payload in affected.get("ranges") or []:
                    for parsed in _parse_osv_range(range_payload):
                        entries.append(
                            BlocklistRow(
                                ecosystem=ecosystem,
                                canonical_name=canonical,
                                cve_id=cve_id,
                                affected_range=parsed.to_json(),
                                severity=severity,
                                published_at=published,
                            )
                        )
                        if len(entries) >= limit:
                            break
                    if len(entries) >= limit:
                        break
                if len(entries) >= limit:
                    break
        return OSVFetchResult(ecosystem=ecosystem, entries=entries, latest_published_at=latest_seen)


_SEVERITY_ORDER: Final[dict[str, int]] = {
    "UNKNOWN": 0,
    "LOW": 1,
    "MODERATE": 2,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


def _severity_rank(label: str) -> int:
    return _SEVERITY_ORDER.get(label.upper(), 0)


def _extract_severity_label(vuln: dict) -> str:
    """Pull a coarse severity label from an OSV vuln payload.

    OSV places qualitative labels in ``database_specific.severity`` when
    available. Numeric CVSS vectors live in ``severity[]``; we do not parse
    them at fetch time (non-goal).
    """
    db_spec = vuln.get("database_specific") or {}
    label = db_spec.get("severity")
    if isinstance(label, str) and label:
        return label.upper()
    return "UNKNOWN"


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _parse_osv_range(range_payload: dict) -> list[AffectedRange]:
    """Translate one OSV ``ranges[i]`` entry into ``AffectedRange`` objects.

    OSV emits ordered events; each (introduced, fixed|last_affected) pair
    becomes one range. We ignore "limit" events and type-specific details;
    the canonical OSV order is (introduced -> fixed?) repeating.
    """
    events = range_payload.get("events") or []
    ranges: list[AffectedRange] = []
    current_intro: str | None = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if "introduced" in ev:
            current_intro = str(ev["introduced"]) if ev["introduced"] is not None else None
            if current_intro == "0":
                current_intro = None  # OSV's "from the beginning" sentinel
        elif "fixed" in ev:
            ranges.append(
                AffectedRange(
                    introduced=current_intro,
                    fixed=str(ev["fixed"]),
                    last_affected=None,
                )
            )
            current_intro = None
        elif "last_affected" in ev:
            ranges.append(
                AffectedRange(
                    introduced=current_intro,
                    fixed=None,
                    last_affected=str(ev["last_affected"]),
                )
            )
            current_intro = None
    # Dangling "introduced" with no terminator == "affected from there on".
    if current_intro is not None:
        ranges.append(AffectedRange(introduced=current_intro, fixed=None, last_affected=None))
    return ranges


# =============================================================================
# SUBSTITUTION BUILDER
# =============================================================================


_ORIGINAL_CMD_CLIP: Final[int] = 200


def _clip(text: str, limit: int = _ORIGINAL_CMD_CLIP) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _sh_single_quote(text: str) -> str:
    r"""Escape ``text`` for embedding inside a single-quoted shell string.

    Inside single quotes every character is literal except the single quote
    itself. Replace each ``'`` with ``'\''`` so the outer quoted string
    terminates, inserts an escaped quote, and resumes.
    """
    return "'" + text.replace("'", "'\\''") + "'"


def build_substitute_command(original_command: str, entry: BlocklistEntry) -> str:
    """Build the ``sh -c '... exit 42'`` replacement for a flagged command.

    Every string in the output is under our control: the CVE ID, severity,
    canonical name, ecosystem, and the clipped/redacted original command.
    No free-form OSV text (no ``summary`` field) is ever included — we
    treat OSV responses as untrusted even though the background task has
    already filtered them.
    """
    redacted = _clip(original_command)
    lines = [
        f"LUTHIEN BLOCKED: {entry.canonical_name} version matches known-compromised advisory.",
        f"Ecosystem: {entry.ecosystem}",
        f"Advisory:  {entry.cve_id} ({entry.severity})",
        f"Range:     {_describe_range(entry.range)}",
        "",
        f"Original command (clipped): {redacted}",
        "",
        "This is a best-effort supply-chain gate for cooperative LLMs. To",
        "proceed intentionally, pin a patched version that does not match",
        "the advisory range.",
    ]
    body = "\n".join(lines)
    inner = f"printf '%s\\n' {_sh_single_quote(body)} >&2; exit 42"
    return f"sh -c {_sh_single_quote(inner)}"


def _describe_range(r: AffectedRange) -> str:
    parts: list[str] = []
    if r.introduced is not None:
        parts.append(f">={r.introduced}")
    if r.fixed is not None:
        parts.append(f"<{r.fixed}")
    if r.last_affected is not None:
        parts.append(f"<={r.last_affected}")
    return ",".join(parts) if parts else "(all versions)"


__all__ = [
    "AffectedRange",
    "BlocklistEntry",
    "BlocklistIndex",
    "ECOSYSTEM_NPM",
    "ECOSYSTEM_PYPI",
    "ExtractedLiteral",
    "OSVClient",
    "OSVFetchResult",
    "SUPPORTED_ECOSYSTEMS",
    "SupplyChainBlocklistConfig",
    "build_substitute_command",
    "canonicalize_name",
    "extract_literals",
    "version_matches",
]
