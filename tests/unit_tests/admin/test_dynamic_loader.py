"""Unit tests for the dynamic policy loader."""

from __future__ import annotations

import pytest

from luthien_proxy.admin.dynamic_loader import (
    PolicyLoadError,
    PolicyValidationError,
    dry_run_load,
    load_policy_from_source,
    validate_source,
)
from luthien_proxy.policy_core.base_policy import BasePolicy

# A minimal valid policy for testing
VALID_POLICY = """
from __future__ import annotations
from litellm.types.utils import ModelResponse
from luthien_proxy.policy_core.base_policy import BasePolicy
from luthien_proxy.policy_core.openai_interface import OpenAIPolicyInterface
from luthien_proxy.policy_core.policy_context import PolicyContext
from luthien_proxy.policy_core.streaming_policy_context import StreamingPolicyContext

class TestPolicy(BasePolicy, OpenAIPolicyInterface):
    @property
    def short_policy_name(self) -> str:
        return "Test"

    async def on_openai_request(self, request, context: PolicyContext):
        return request

    async def on_openai_response(self, response: ModelResponse, context: PolicyContext) -> ModelResponse:
        return response

    async def on_chunk_received(self, ctx: StreamingPolicyContext) -> None:
        ctx.push_chunk(ctx.last_chunk_received)

    async def on_content_delta(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_content_complete(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_tool_call_delta(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_tool_call_complete(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_finish_reason(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_stream_complete(self, ctx: StreamingPolicyContext) -> None:
        pass

    async def on_streaming_policy_complete(self, ctx: StreamingPolicyContext) -> None:
        pass
"""


class TestValidateSource:
    """Tests for source code validation."""

    def test_valid_policy_passes(self) -> None:
        issues = validate_source(VALID_POLICY)
        assert issues == []

    def test_syntax_error_detected(self) -> None:
        issues = validate_source("def broken(:\n  pass")
        assert len(issues) == 1
        assert "Syntax error" in issues[0]

    def test_disallowed_import_os(self) -> None:
        code = "import os\nclass Foo:\n  pass"
        issues = validate_source(code)
        assert any("os" in i for i in issues)

    def test_disallowed_import_subprocess(self) -> None:
        code = "import subprocess\nclass Foo:\n  pass"
        issues = validate_source(code)
        assert any("subprocess" in i for i in issues)

    def test_disallowed_from_import(self) -> None:
        code = "from pathlib import Path\nclass Foo:\n  pass"
        issues = validate_source(code)
        assert any("pathlib" in i for i in issues)

    def test_blocked_builtin_exec(self) -> None:
        code = "class Foo:\n  def run(self): exec('print(1)')"
        issues = validate_source(code)
        assert any("exec" in i for i in issues)

    def test_blocked_builtin_eval(self) -> None:
        code = "class Foo:\n  def run(self): eval('1+1')"
        issues = validate_source(code)
        assert any("eval" in i for i in issues)

    def test_no_class_definition(self) -> None:
        code = "def some_function(): pass"
        issues = validate_source(code)
        assert any("class" in i.lower() for i in issues)

    def test_allowed_imports_pass(self) -> None:
        code = """
import json
import re
import logging
from typing import Any
from pydantic import BaseModel
class Foo:
    pass
"""
        issues = validate_source(code)
        assert issues == []

    def test_policy_core_imports_allowed(self) -> None:
        code = """
from luthien_proxy.policy_core.base_policy import BasePolicy
class Foo:
    pass
"""
        issues = validate_source(code)
        assert issues == []

    def test_getattr_blocked(self) -> None:
        code = "class Foo:\n  def run(self): getattr(self, 'x')"
        issues = validate_source(code)
        assert any("getattr" in i for i in issues)

    def test_setattr_blocked(self) -> None:
        code = "class Foo:\n  def run(self): setattr(self, 'x', 1)"
        issues = validate_source(code)
        assert any("setattr" in i for i in issues)

    def test_dunder_class_blocked(self) -> None:
        code = "class Foo:\n  def run(self): return self.__class__"
        issues = validate_source(code)
        assert any("__class__" in i for i in issues)

    def test_dunder_bases_blocked(self) -> None:
        code = "class Foo:\n  def run(self): return Foo.__bases__"
        issues = validate_source(code)
        assert any("__bases__" in i for i in issues)

    def test_dunder_subclasses_blocked(self) -> None:
        code = "class Foo:\n  def run(self): return object.__subclasses__()"
        issues = validate_source(code)
        assert any("__subclasses__" in i for i in issues)

    def test_asyncio_import_blocked(self) -> None:
        code = "import asyncio\nclass Foo:\n  pass"
        issues = validate_source(code)
        assert any("asyncio" in i for i in issues)

    def test_chunk_builders_import_allowed(self) -> None:
        code = """
from luthien_proxy.policy_core.chunk_builders import create_text_chunk
class Foo:
    pass
"""
        issues = validate_source(code)
        assert issues == []


class TestLoadPolicyFromSource:
    """Tests for loading and instantiating policies from source."""

    def test_load_valid_policy(self) -> None:
        policy = load_policy_from_source(VALID_POLICY, policy_name="test")
        assert isinstance(policy, BasePolicy)
        assert policy.short_policy_name == "Test"

    def test_load_invalid_syntax_raises(self) -> None:
        with pytest.raises(PolicyValidationError, match="Syntax error"):
            load_policy_from_source("def broken(:\n  pass")

    def test_load_disallowed_import_raises(self) -> None:
        code = "import os\nclass Foo:\n  pass"
        with pytest.raises(PolicyValidationError, match="Disallowed import"):
            load_policy_from_source(code)

    def test_load_no_basepolicy_subclass_raises(self) -> None:
        code = """
class NotAPolicy:
    pass
"""
        with pytest.raises(PolicyLoadError, match="No BasePolicy subclass"):
            load_policy_from_source(code)

    def test_load_multiple_policy_classes_uses_first(self) -> None:
        code = """
from luthien_proxy.policy_core.base_policy import BasePolicy

class FirstPolicy(BasePolicy):
    @property
    def short_policy_name(self):
        return "First"

class SecondPolicy(BasePolicy):
    @property
    def short_policy_name(self):
        return "Second"
"""
        policy = load_policy_from_source(code, policy_name="multi")
        assert isinstance(policy, BasePolicy)
        assert policy.short_policy_name == "First"

    def test_load_with_config(self) -> None:
        code = """
from luthien_proxy.policy_core.base_policy import BasePolicy

class ConfigPolicy(BasePolicy):
    def __init__(self, config: dict = None):
        self._config = config or {}

    @property
    def short_policy_name(self):
        return "Config"
"""
        policy = load_policy_from_source(code, config={"key": "value"}, policy_name="config")
        assert isinstance(policy, BasePolicy)

    def test_load_minimal_basepolicy(self) -> None:
        code = """
from luthien_proxy.policy_core.base_policy import BasePolicy

class MinimalPolicy(BasePolicy):
    pass
"""
        policy = load_policy_from_source(code, policy_name="minimal")
        assert isinstance(policy, BasePolicy)
        assert policy.short_policy_name == "MinimalPolicy"


class TestDryRunLoad:
    """Tests for dry-run validation."""

    def test_valid_policy(self) -> None:
        result = dry_run_load(VALID_POLICY)
        assert result["valid"] is True
        assert result["class_name"] == "TestPolicy"
        assert result["short_name"] == "Test"
        assert result["issues"] == []

    def test_invalid_syntax(self) -> None:
        result = dry_run_load("def broken(:\n  pass")
        assert result["valid"] is False
        assert len(result["issues"]) > 0

    def test_disallowed_import(self) -> None:
        result = dry_run_load("import os\nclass Foo:\n  pass")
        assert result["valid"] is False
        assert any("os" in i for i in result["issues"])

    def test_no_basepolicy(self) -> None:
        code = "class NotAPolicy:\n  pass"
        result = dry_run_load(code)
        assert result["valid"] is False
