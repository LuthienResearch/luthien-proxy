"""Utility functions for SaaS infrastructure management."""

import re
import secrets
import string
from datetime import datetime, timedelta, timezone

# Instance name constraints
MAX_NAME_LENGTH = 63
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$|^[a-z]$")

# Project naming
PROJECT_PREFIX = "luthien-"

# Soft delete grace period
DELETION_GRACE_DAYS = 7


class NameValidationError(Exception):
    """Raised when an instance name is invalid."""

    pass


def validate_instance_name(name: str) -> None:
    """Validate that an instance name is DNS-safe.

    Raises NameValidationError if invalid.
    """
    if not name:
        raise NameValidationError("Instance name cannot be empty")

    if len(name) > MAX_NAME_LENGTH:
        raise NameValidationError(f"Instance name must be at most {MAX_NAME_LENGTH} characters")

    if not NAME_PATTERN.match(name):
        raise NameValidationError(
            "Instance name must be lowercase alphanumeric with hyphens, start with a letter, and not end with a hyphen"
        )

    if "--" in name:
        raise NameValidationError("Instance name cannot contain consecutive hyphens")


def generate_api_key(length: int = 32) -> str:
    """Generate a secure random API key."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def project_name_from_instance(instance_name: str) -> str:
    """Convert instance name to Railway project name."""
    return f"{PROJECT_PREFIX}{instance_name}"


def instance_name_from_project(project_name: str) -> str | None:
    """Extract instance name from Railway project name.

    Returns None if project name doesn't match our naming convention.
    """
    if project_name.startswith(PROJECT_PREFIX):
        return project_name[len(PROJECT_PREFIX) :]
    return None


def calculate_deletion_date() -> datetime:
    """Calculate the deletion date (now + grace period)."""
    return datetime.now(timezone.utc) + timedelta(days=DELETION_GRACE_DAYS)


def format_deletion_countdown(deletion_date: datetime) -> str:
    """Format remaining time until deletion."""
    now = datetime.now(timezone.utc)
    if deletion_date <= now:
        return "imminent"

    remaining = deletion_date - now
    days = remaining.days
    hours = remaining.seconds // 3600

    if days > 0:
        return f"{days}d {hours}h"
    return f"{hours}h"


def parse_deletion_tag(tag_value: str) -> datetime | None:
    """Parse deletion scheduled timestamp from tag value.

    Returns None if parsing fails.
    """
    try:
        return datetime.fromisoformat(tag_value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def format_datetime(dt: datetime | None) -> str:
    """Format datetime for display."""
    if dt is None:
        return "unknown"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def redact_secret(value: str, visible_chars: int = 4) -> str:
    """Redact a secret value, showing only first few characters."""
    if len(value) <= visible_chars:
        return "*" * len(value)
    return value[:visible_chars] + "*" * (len(value) - visible_chars)
