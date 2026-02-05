"""Tests for saas-infra utility functions."""

import sys
from pathlib import Path

# Add repo root to path for saas_infra import
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from datetime import datetime, timedelta, timezone

import pytest
from saas_infra.utils import (
    NameValidationError,
    calculate_deletion_date,
    format_datetime,
    format_deletion_countdown,
    generate_api_key,
    instance_name_from_project,
    parse_deletion_tag,
    project_name_from_instance,
    redact_secret,
    validate_instance_name,
)


class TestValidateInstanceName:
    def test_valid_simple_name(self):
        validate_instance_name("myinstance")

    def test_valid_name_with_hyphens(self):
        validate_instance_name("my-test-instance")

    def test_valid_single_char(self):
        validate_instance_name("a")

    def test_valid_name_with_numbers(self):
        validate_instance_name("instance123")
        validate_instance_name("my2nd-instance")

    def test_empty_name_raises(self):
        with pytest.raises(NameValidationError, match="cannot be empty"):
            validate_instance_name("")

    def test_too_long_name_raises(self):
        long_name = "a" * 64
        with pytest.raises(NameValidationError, match="at most 63 characters"):
            validate_instance_name(long_name)

    def test_uppercase_raises(self):
        with pytest.raises(NameValidationError, match="lowercase"):
            validate_instance_name("MyInstance")

    def test_starts_with_number_raises(self):
        with pytest.raises(NameValidationError, match="start with a letter"):
            validate_instance_name("1instance")

    def test_ends_with_hyphen_raises(self):
        with pytest.raises(NameValidationError, match="not end with a hyphen"):
            validate_instance_name("instance-")

    def test_consecutive_hyphens_raises(self):
        with pytest.raises(NameValidationError, match="consecutive hyphens"):
            validate_instance_name("my--instance")

    def test_special_chars_raises(self):
        with pytest.raises(NameValidationError):
            validate_instance_name("my_instance")
        with pytest.raises(NameValidationError):
            validate_instance_name("my.instance")


class TestGenerateApiKey:
    def test_default_length(self):
        key = generate_api_key()
        assert len(key) == 32

    def test_custom_length(self):
        key = generate_api_key(16)
        assert len(key) == 16

    def test_alphanumeric_only(self):
        key = generate_api_key(100)
        assert key.isalnum()

    def test_keys_are_unique(self):
        keys = {generate_api_key() for _ in range(100)}
        assert len(keys) == 100


class TestProjectNaming:
    def test_project_name_from_instance(self):
        assert project_name_from_instance("myinstance") == "luthien-myinstance"
        assert project_name_from_instance("test") == "luthien-test"

    def test_instance_name_from_project(self):
        assert instance_name_from_project("luthien-myinstance") == "myinstance"
        assert instance_name_from_project("luthien-test") == "test"

    def test_instance_name_from_non_luthien_project(self):
        assert instance_name_from_project("other-project") is None
        assert instance_name_from_project("myproject") is None


class TestDeletionDate:
    def test_calculate_deletion_date(self):
        before = datetime.now(timezone.utc)
        deletion_date = calculate_deletion_date()
        after = datetime.now(timezone.utc)

        expected_min = before + timedelta(days=7)
        expected_max = after + timedelta(days=7)

        assert expected_min <= deletion_date <= expected_max

    def test_format_deletion_countdown_days(self):
        future = datetime.now(timezone.utc) + timedelta(days=3, hours=12)
        result = format_deletion_countdown(future)
        assert "3d" in result
        # Don't check exact hours due to timing
        assert "h" in result

    def test_format_deletion_countdown_hours_only(self):
        future = datetime.now(timezone.utc) + timedelta(hours=12)
        result = format_deletion_countdown(future)
        assert "d" not in result
        # Should show hours (between 11h and 12h depending on timing)
        assert "h" in result

    def test_format_deletion_countdown_past(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        result = format_deletion_countdown(past)
        assert result == "imminent"


class TestParseDeletionTag:
    def test_valid_iso_format(self):
        dt = parse_deletion_tag("2024-01-15T10:30:00+00:00")
        assert dt is not None
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_valid_iso_format_with_z(self):
        dt = parse_deletion_tag("2024-01-15T10:30:00Z")
        assert dt is not None

    def test_invalid_format_returns_none(self):
        assert parse_deletion_tag("not-a-date") is None
        assert parse_deletion_tag("") is None


class TestFormatDatetime:
    def test_format_datetime(self):
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = format_datetime(dt)
        assert "2024-01-15" in result
        assert "10:30:00" in result

    def test_format_datetime_none(self):
        assert format_datetime(None) == "unknown"


class TestRedactSecret:
    def test_redact_secret(self):
        assert redact_secret("mysecretkey") == "myse*******"
        assert redact_secret("abc") == "***"

    def test_redact_custom_visible(self):
        assert redact_secret("mysecretkey", visible_chars=6) == "mysecr*****"
