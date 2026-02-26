"""Unit tests for the policy generator module."""

from __future__ import annotations

from luthien_proxy.admin.policy_generator import SYSTEM_PROMPT, extract_code_from_response


class TestExtractCodeFromResponse:
    """Tests for extracting Python code from LLM responses."""

    def test_plain_code(self) -> None:
        code = "class MyPolicy:\n    pass"
        assert extract_code_from_response(code) == code

    def test_markdown_python_fence(self) -> None:
        text = "Here's the code:\n```python\nclass MyPolicy:\n    pass\n```\nDone."
        assert extract_code_from_response(text) == "class MyPolicy:\n    pass"

    def test_markdown_bare_fence(self) -> None:
        text = "```\nclass MyPolicy:\n    pass\n```"
        assert extract_code_from_response(text) == "class MyPolicy:\n    pass"

    def test_whitespace_stripped(self) -> None:
        text = "\n\n  class MyPolicy:\n    pass  \n\n"
        result = extract_code_from_response(text)
        assert result == "class MyPolicy:\n    pass"

    def test_multiple_fences_uses_first(self) -> None:
        text = "```python\nfirst_code\n```\n\n```python\nsecond_code\n```"
        assert extract_code_from_response(text) == "first_code"


class TestSystemPrompt:
    """Basic checks on the system prompt content."""

    def test_contains_basepolicy(self) -> None:
        assert "BasePolicy" in SYSTEM_PROMPT

    def test_contains_openai_interface(self) -> None:
        assert "OpenAIPolicyInterface" in SYSTEM_PROMPT

    def test_contains_streaming_context(self) -> None:
        assert "StreamingPolicyContext" in SYSTEM_PROMPT

    def test_contains_push_chunk(self) -> None:
        assert "push_chunk" in SYSTEM_PROMPT

    def test_contains_noop_example(self) -> None:
        assert "NoOpPolicy" in SYSTEM_PROMPT

    def test_contains_allcaps_example(self) -> None:
        assert "AllCapsPolicy" in SYSTEM_PROMPT
