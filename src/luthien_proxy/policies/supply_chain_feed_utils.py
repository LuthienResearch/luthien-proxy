"""Supply chain feed utilities: GCS client, vuln parser, blocklist index, regex extraction.

This module handles:
- Downloading and parsing OSV bulk zips (cold start)
- Paginating the GCS listing API (incremental updates)
- Parsing individual vulnerability JSONs
- Building and querying the in-memory blocklist index
- Regex extraction of install commands from bash strings
- Building substitution commands for blocked installs
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Ecosystems supported in v1
SUPPORTED_ECOSYSTEMS: tuple[str, ...] = ("PyPI", "npm")

# Severity levels that trigger blocking
BLOCKING_SEVERITIES: frozenset[str] = frozenset({"CRITICAL"})

# GCS base URLs
GCS_BUCKET_URL = "https://storage.googleapis.com/osv-vulnerabilities"
GCS_API_URL = "https://storage.googleapis.com/storage/v1/b/osv-vulnerabilities/o"

# Regex for extracting package==version (pip) and package@version (npm) from commands
_INSTALL_PATTERN = re.compile(
    r"""
    (?:^|[\s;|&])            # preceded by start, whitespace, or shell operator
    ([\w][\w.\-]*)           # package name: starts with word char
    (?:==|@)                 # separator: == for pip, @ for npm
    ([\w][\w.\-+]*)          # version string
    """,
    re.VERBOSE,
)


# ---------------------------------------------------------------------------
# Vuln entry parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VulnEntry:
    """A single (ecosystem, name, version, cve_id) tuple from a parsed advisory."""

    ecosystem: str
    name: str
    version: str
    cve_id: str
    severity: str
    published_at: datetime | None
    modified_at: datetime | None


def parse_vuln_json(data: dict[str, Any]) -> list[VulnEntry]:
    """Parse an OSV advisory JSON into VulnEntry tuples.

    Filters to BLOCKING_SEVERITIES. Skips advisories without
    pre-expanded version lists (ranges-only).
    """
    severity_label = _extract_severity(data)
    if severity_label is None:
        return []
    if severity_label not in BLOCKING_SEVERITIES:
        return []

    cve_id = str(data.get("id", ""))
    if not cve_id:
        return []

    published_at = _parse_iso(data.get("published"))
    modified_at = _parse_iso(data.get("modified"))

    entries: list[VulnEntry] = []
    for affected in data.get("affected", []):
        pkg = affected.get("package", {})
        ecosystem = str(pkg.get("ecosystem", ""))
        name = str(pkg.get("name", ""))
        if not ecosystem or not name:
            continue

        versions = affected.get("versions", [])
        if not versions:
            # ranges-only advisory — skip in v1
            logger.info(
                "Skipping %s for %s/%s: no pre-expanded versions (ranges-only)",
                cve_id,
                ecosystem,
                name,
            )
            continue

        for version in versions:
            entries.append(
                VulnEntry(
                    ecosystem=ecosystem,
                    name=name,
                    version=str(version),
                    cve_id=cve_id,
                    severity=severity_label,
                    published_at=published_at,
                    modified_at=modified_at,
                )
            )

    return entries


def _extract_severity(data: dict[str, Any]) -> str | None:
    """Extract the pre-computed severity label from database_specific."""
    db_specific = data.get("database_specific", {})
    severity = db_specific.get("severity")
    if not isinstance(severity, str):
        logger.info("Skipping %s: no database_specific.severity", data.get("id", "?"))
        return None
    return severity.upper()


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string to datetime, or return None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Bulk zip parsing (cold start)
# ---------------------------------------------------------------------------


def parse_bulk_zip(zip_bytes: bytes) -> list[VulnEntry]:
    """Parse all advisories from a bulk zip downloaded from OSV.

    Returns all VulnEntry tuples matching BLOCKING_SEVERITIES with
    pre-expanded version lists.
    """
    entries: list[VulnEntry] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            if not name.endswith(".json"):
                continue
            try:
                data = json.loads(zf.read(name))
                entries.extend(parse_vuln_json(data))
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Failed to parse %s in bulk zip: %s", name, exc)
    return entries


# ---------------------------------------------------------------------------
# GCS listing API parsing (incremental updates)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListingItem:
    """A single item from the GCS listing API response."""

    name: str
    updated: datetime


def parse_listing_page(data: dict[str, Any]) -> tuple[list[ListingItem], str | None]:
    """Parse a GCS listing API response page.

    Returns (items, next_page_token). next_page_token is None if no more pages.
    """
    items: list[ListingItem] = []
    for item in data.get("items", []):
        name = item.get("name", "")
        updated_str = item.get("updated", "")
        if not name or not updated_str:
            continue
        # Skip the all.zip entry
        if name.endswith("/all.zip"):
            continue
        updated = _parse_iso(updated_str)
        if updated is None:
            continue
        items.append(ListingItem(name=name, updated=updated))

    next_token = data.get("nextPageToken")
    return items, next_token if isinstance(next_token, str) else None


# ---------------------------------------------------------------------------
# In-memory blocklist index
# ---------------------------------------------------------------------------


def build_blocklist_index(
    entries: "Sequence[VulnEntry]",
) -> dict[tuple[str, str, str], list[str]]:
    """Build in-memory index from VulnEntry list.

    Key: (canonical_ecosystem, canonical_name, version)
    Value: list of CVE IDs
    """
    index: dict[tuple[str, str, str], list[str]] = {}
    for entry in entries:
        key = (_canonical_ecosystem(entry.ecosystem), _canonical_name(entry.ecosystem, entry.name), entry.version)
        if key not in index:
            index[key] = []
        if entry.cve_id not in index[key]:
            index[key].append(entry.cve_id)
    return index


def build_substrate_strings(
    index: dict[tuple[str, str, str], list[str]],
) -> frozenset[str]:
    """Build the set of literal substrate strings for the substring backstop.

    For PyPI: "name==version"
    For npm: "name@version"
    Uses canonical names so backstop matching is case-insensitive.
    """
    strings: set[str] = set()
    for (ecosystem, name, version), _cve_ids in index.items():
        if ecosystem == "pypi":
            strings.add(f"{name}=={version}")
        elif ecosystem == "npm":
            strings.add(f"{name}@{version}")
    return frozenset(strings)


def _canonical_ecosystem(ecosystem: str) -> str:
    """Canonicalize ecosystem name to lowercase."""
    return ecosystem.lower()


def _canonical_name(ecosystem: str, name: str) -> str:
    """Canonicalize package name.

    PyPI: PEP 503 normalization (lowercase, replace [-_.] with -)
    npm: lowercase
    """
    eco = ecosystem.lower()
    if eco == "pypi":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


# ---------------------------------------------------------------------------
# Regex extraction from bash commands
# ---------------------------------------------------------------------------


def extract_install_specs(command: str) -> list[tuple[str, str]]:
    """Extract (name, version) pairs from a bash command string.

    Finds pip-style name==version and npm-style name@version literals.
    This is a loose regex — not a bash parser. It catches common forms
    and the substring backstop covers the rest.
    """
    results: list[tuple[str, str]] = []
    for match in _INSTALL_PATTERN.finditer(command):
        name = match.group(1)
        version = match.group(2)
        results.append((name, version))
    return results


def check_blocklist(
    command: str,
    index: dict[tuple[str, str, str], list[str]],
    substrate_strings: frozenset[str],
) -> tuple[str, str, list[str]] | None:
    """Check a bash command against the blocklist.

    Returns (name, version, [cve_ids]) on first hit, or None.
    Tries regex extraction first, then falls back to substring backstop.
    """
    # Phase 1: regex extraction
    specs = extract_install_specs(command)
    for name, version in specs:
        for ecosystem in ("pypi", "npm"):
            canon_name = _canonical_name(ecosystem, name)
            key = (ecosystem, canon_name, version)
            if key in index:
                return (name, version, index[key])

    # Phase 2: substring backstop
    command_lower = command.lower()
    for substrate in substrate_strings:
        if substrate in command_lower:
            # Parse the substrate back to extract name/version
            if "==" in substrate:
                s_name, s_version = substrate.split("==", 1)
                key = ("pypi", s_name, s_version)
            elif "@" in substrate:
                s_name, s_version = substrate.rsplit("@", 1)
                key = ("npm", s_name, s_version)
            else:
                continue
            cve_ids = index.get(key, [])
            if cve_ids:
                return (s_name, s_version, cve_ids)

    return None


# ---------------------------------------------------------------------------
# Substitution command builder
# ---------------------------------------------------------------------------


def build_substitute_command(name: str, version: str, cve_ids: list[str]) -> str:
    """Build a shell command that prints a blocked message and exits 42.

    The message is constructed entirely from controlled strings (CVE IDs,
    package name, version). No untrusted OSV text reaches the output.
    """
    # Sanitize inputs to prevent shell injection
    safe_name = _shell_safe(name)
    safe_version = _shell_safe(version)
    safe_cves = ", ".join(_shell_safe(c) for c in cve_ids)
    first_cve = _shell_safe(cve_ids[0]) if cve_ids else "UNKNOWN"

    msg = (
        f"LUTHIEN BLOCKED: {safe_name} {safe_version} matches {safe_cves} (CRITICAL). "
        f"See https://osv.dev/vulnerability/{first_cve}"
    )

    return f"sh -c 'printf \"%s\\n\" \"{msg}\" >&2; exit 42'"


def _shell_safe(s: str) -> str:
    """Remove characters that could break out of shell single/double quotes.

    Keeps alphanumerics, hyphens, dots, underscores, commas, spaces, colons,
    slashes, and the equals sign. Everything else is stripped.
    """
    return re.sub(r"[^a-zA-Z0-9\-._,: /=]", "", s)


# ---------------------------------------------------------------------------
# GCS HTTP helpers (used by the background task in the policy module)
# ---------------------------------------------------------------------------


def bulk_zip_url(ecosystem: str) -> str:
    """Return the URL for an ecosystem's bulk zip."""
    return f"{GCS_BUCKET_URL}/{ecosystem}/all.zip"


def individual_vuln_url(ecosystem: str, vuln_id: str) -> str:
    """Return the URL for an individual vulnerability JSON."""
    return f"{GCS_BUCKET_URL}/{ecosystem}/{vuln_id}.json"


def listing_api_url(ecosystem: str, max_results: int = 500, page_token: str | None = None) -> str:
    """Return the GCS listing API URL for an ecosystem."""
    url = f"{GCS_API_URL}?prefix={ecosystem}/&maxResults={max_results}"
    if page_token:
        url += f"&pageToken={page_token}"
    return url


__all__ = [
    "BLOCKING_SEVERITIES",
    "SUPPORTED_ECOSYSTEMS",
    "ListingItem",
    "VulnEntry",
    "build_blocklist_index",
    "build_substitute_command",
    "build_substrate_strings",
    "bulk_zip_url",
    "check_blocklist",
    "extract_install_specs",
    "individual_vuln_url",
    "listing_api_url",
    "parse_bulk_zip",
    "parse_listing_page",
    "parse_vuln_json",
]
