"""Unit tests for preset policy classes."""

import importlib
import inspect

import pytest

from luthien_proxy.policies.presets.block_dangerous_commands import BlockDangerousCommandsPolicy
from luthien_proxy.policies.presets.block_sensitive_file_writes import BlockSensitiveFileWritesPolicy
from luthien_proxy.policies.presets.block_web_requests import BlockWebRequestsPolicy
from luthien_proxy.policies.presets.no_apologies import NoApologiesPolicy
from luthien_proxy.policies.presets.no_yapping import NoYappingPolicy
from luthien_proxy.policies.presets.plain_dashes import PlainDashesPolicy
from luthien_proxy.policies.presets.prefer_uv import PreferUvPolicy

ALL_PRESETS = [
    PreferUvPolicy,
    PlainDashesPolicy,
    BlockDangerousCommandsPolicy,
    NoApologiesPolicy,
    NoYappingPolicy,
    BlockWebRequestsPolicy,
    BlockSensitiveFileWritesPolicy,
]


@pytest.mark.parametrize("policy_class", ALL_PRESETS, ids=lambda c: c.__name__)
def test_preset_instantiates(policy_class):
    """Each preset can be instantiated with no arguments."""
    policy = policy_class()
    assert policy._config is not None
    assert policy._config.instructions


@pytest.mark.parametrize("policy_class", ALL_PRESETS, ids=lambda c: c.__name__)
def test_preset_has_docstring(policy_class):
    """Each preset has a docstring (used as description in the UI)."""
    assert policy_class.__doc__
    assert len(policy_class.__doc__.strip()) > 10


@pytest.mark.parametrize("policy_class", ALL_PRESETS, ids=lambda c: c.__name__)
def test_preset_no_constructor_params(policy_class):
    """Presets have no required constructor parameters."""
    sig = inspect.signature(policy_class.__init__)
    params = [p for name, p in sig.parameters.items() if name != "self"]
    assert all(p.default is not inspect.Parameter.empty for p in params), (
        f"{policy_class.__name__} has required constructor parameters"
    )


def test_preset_class_refs_are_importable():
    """The preset classes can be imported via their module paths."""
    preset_modules = [
        ("luthien_proxy.policies.presets.prefer_uv", "PreferUvPolicy"),
        ("luthien_proxy.policies.presets.plain_dashes", "PlainDashesPolicy"),
        ("luthien_proxy.policies.presets.block_dangerous_commands", "BlockDangerousCommandsPolicy"),
        ("luthien_proxy.policies.presets.no_apologies", "NoApologiesPolicy"),
        ("luthien_proxy.policies.presets.no_yapping", "NoYappingPolicy"),
        ("luthien_proxy.policies.presets.block_web_requests", "BlockWebRequestsPolicy"),
        ("luthien_proxy.policies.presets.block_sensitive_file_writes", "BlockSensitiveFileWritesPolicy"),
    ]
    for module_path, class_name in preset_modules:
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        assert cls is not None
