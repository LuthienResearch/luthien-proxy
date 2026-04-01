"""Unit tests for humanizer_utils."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.policies.humanizer_utils import (
    HumanizerConfig,
    HumanizerTruncatedError,
    build_humanizer_chunk_prompt,
    build_humanizer_prompt,
    call_humanizer,
    call_humanizer_chunk,
    extract_code_blocks,
    restore_code_blocks,
    split_into_chunks,
)


def _mock_response(content: str, finish_reason: str = "stop") -> MagicMock:
    """Build a mock LiteLLM ModelResponse."""
    mock_message = MagicMock()
    mock_message.content = content
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_choice.finish_reason = finish_reason
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    return mock_resp


class TestBuildHumanizerPrompt:
    def test_basic_prompt_structure(self):
        prompt = build_humanizer_prompt("Some AI text here.")
        assert len(prompt) == 2
        assert prompt[0]["role"] == "system"
        assert prompt[1]["role"] == "user"
        assert prompt[1]["content"] == "Some AI text here."

    def test_system_prompt_contains_patterns(self):
        prompt = build_humanizer_prompt("text")
        system = prompt[0]["content"]
        assert "testament" in system
        assert "vibrant" in system
        assert "delve" in system
        assert "Chatbot artifacts" in system

    def test_system_prompt_mentions_code_placeholders(self):
        prompt = build_humanizer_prompt("text")
        system = prompt[0]["content"]
        assert "CODE_BLOCK_" in system

    def test_extra_instructions_appended(self):
        prompt = build_humanizer_prompt("text", extra_instructions="Write in British English.")
        system = prompt[0]["content"]
        assert "Write in British English." in system

    def test_no_extra_instructions(self):
        prompt = build_humanizer_prompt("text", extra_instructions="")
        system = prompt[0]["content"]
        assert "Additional Instructions" not in system


class TestBuildHumanizerChunkPrompt:
    def test_first_chunk_no_context(self):
        prompt = build_humanizer_chunk_prompt("Some chunk text.")
        assert len(prompt) == 2
        assert prompt[0]["role"] == "system"
        assert "Fragment Mode" in prompt[0]["content"]
        assert prompt[1]["content"] == "Some chunk text."
        assert "PRECEDING CONTEXT" not in prompt[1]["content"]

    def test_with_previous_context(self):
        prompt = build_humanizer_chunk_prompt("New chunk.", previous_context="Previous output tail.")
        user = prompt[1]["content"]
        assert "PRECEDING CONTEXT" in user
        assert "Previous output tail." in user
        assert "TEXT TO REWRITE" in user
        assert "New chunk." in user

    def test_final_chunk_flag(self):
        prompt = build_humanizer_chunk_prompt("Last bit.", is_final=True)
        assert "final fragment" in prompt[0]["content"]

    def test_extra_instructions(self):
        prompt = build_humanizer_chunk_prompt("text", extra_instructions="Be concise.")
        assert "Be concise." in prompt[0]["content"]


class TestCodeBlockExtraction:
    def test_fenced_code_block(self):
        text = "Before\n```python\ndef foo():\n    pass\n```\nAfter"
        masked, blocks = extract_code_blocks(text)
        assert "```python" not in masked
        assert "def foo" not in masked
        assert len(blocks) == 1
        restored = restore_code_blocks(masked, blocks)
        assert restored == text

    def test_inline_code(self):
        text = "Use `foo()` to call it."
        masked, blocks = extract_code_blocks(text)
        assert "`foo()`" not in masked
        assert len(blocks) == 1
        restored = restore_code_blocks(masked, blocks)
        assert restored == text

    def test_multiple_code_blocks(self):
        text = "First `a`, then ```\nb\n```, then `c`."
        masked, blocks = extract_code_blocks(text)
        assert len(blocks) == 3
        restored = restore_code_blocks(masked, blocks)
        assert restored == text

    def test_no_code_blocks(self):
        text = "Just plain text with no code."
        masked, blocks = extract_code_blocks(text)
        assert masked == text
        assert blocks == {}


class TestSplitIntoChunks:
    def test_short_text_single_chunk(self):
        assert split_into_chunks("Hello world.", chunk_size=50) == ["Hello world."]

    def test_splits_at_paragraph_boundary(self):
        text = "a" * 60 + "\n\n" + "b" * 30
        chunks = split_into_chunks(text, chunk_size=50, force_chunk_size=150)
        assert len(chunks) == 2
        assert chunks[0] == "a" * 60 + "\n\n"
        assert chunks[1] == "b" * 30

    def test_force_split_no_paragraph(self):
        text = "x" * 200
        chunks = split_into_chunks(text, chunk_size=50, force_chunk_size=100)
        assert len(chunks) >= 2
        assert "".join(chunks) == text

    def test_sentence_boundary_preferred(self):
        text = "x" * 60 + ". " + "y" * 100
        chunks = split_into_chunks(text, chunk_size=50, force_chunk_size=100)
        assert len(chunks) >= 2
        # First chunk should end at the sentence boundary
        assert chunks[0].endswith(". ")

    def test_multiple_paragraphs(self):
        text = ("para " * 12 + "\n\n") * 3  # 3 paragraphs, each ~63 chars
        chunks = split_into_chunks(text, chunk_size=50, force_chunk_size=150)
        assert len(chunks) == 3


class TestCallHumanizer:
    @pytest.mark.asyncio()
    async def test_successful_call(self):
        config = HumanizerConfig(model="test-model", api_key="test-key", max_retries=0)

        with patch("luthien_proxy.policies.humanizer_utils.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _mock_response("Humanized text output.")
            result = await call_humanizer("Original AI text.", config)

        assert result == "Humanized text output."

    @pytest.mark.asyncio()
    async def test_truncation_raises(self):
        config = HumanizerConfig(model="test-model", api_key="key", max_retries=0)

        with patch("luthien_proxy.policies.humanizer_utils.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _mock_response("partial...", finish_reason="length")
            with pytest.raises(HumanizerTruncatedError, match="truncated"):
                await call_humanizer("text", config)

    @pytest.mark.asyncio()
    async def test_retries_on_failure(self):
        config = HumanizerConfig(model="test-model", api_key="key", max_retries=2, retry_delay=0.0)

        with patch("luthien_proxy.policies.humanizer_utils.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                RuntimeError("fail"),
                RuntimeError("fail again"),
                _mock_response("success"),
            ]
            result = await call_humanizer("text", config)

        assert result == "success"
        assert mock_llm.call_count == 3

    @pytest.mark.asyncio()
    async def test_code_blocks_protected(self):
        config = HumanizerConfig(model="test-model", api_key="key", max_retries=0)
        text = "This is vibrant. ```python\ndef foo(): pass\n``` End."

        with patch("luthien_proxy.policies.humanizer_utils.acompletion", new_callable=AsyncMock) as mock_llm:

            def capture_and_respond(**kwargs: object) -> MagicMock:
                messages = kwargs["messages"]
                user_content = messages[1]["content"]  # type: ignore[index]
                assert "def foo" not in user_content
                assert "CODE_BLOCK_" in user_content
                rewritten = user_content.replace("This is vibrant.", "This works well.")
                return _mock_response(rewritten)

            mock_llm.side_effect = capture_and_respond
            result = await call_humanizer(text, config)

        assert "```python\ndef foo(): pass\n```" in result
        assert "This works well." in result


class TestCallHumanizerChunk:
    @pytest.mark.asyncio()
    async def test_basic_chunk_call(self):
        config = HumanizerConfig(model="test-model", api_key="key", max_retries=0)

        with patch("luthien_proxy.policies.humanizer_utils.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _mock_response("humanized chunk")
            result = await call_humanizer_chunk("original chunk", config, previous_context="prev tail")

        assert result == "humanized chunk"
        # Verify chunk prompt was used (has PRECEDING CONTEXT)
        call_messages = mock_llm.call_args[1]["messages"]
        assert "Fragment Mode" in call_messages[0]["content"]
        assert "prev tail" in call_messages[1]["content"]

    @pytest.mark.asyncio()
    async def test_truncation_returns_partial(self):
        """Chunk mode returns partial output on truncation instead of raising."""
        config = HumanizerConfig(model="test-model", api_key="key", max_retries=0)

        with patch("luthien_proxy.policies.humanizer_utils.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _mock_response("partial output", finish_reason="length")
            result = await call_humanizer_chunk("long chunk", config)

        assert result == "partial output"

    @pytest.mark.asyncio()
    async def test_chunk_code_blocks_protected(self):
        config = HumanizerConfig(model="test-model", api_key="key", max_retries=0)

        with patch("luthien_proxy.policies.humanizer_utils.acompletion", new_callable=AsyncMock) as mock_llm:

            def capture(**kwargs: object) -> MagicMock:
                messages = kwargs["messages"]
                user = messages[1]["content"]  # type: ignore[index]
                # Extract just the TEXT TO REWRITE part if present
                if "[TEXT TO REWRITE]" in user:
                    text_part = user.split("[TEXT TO REWRITE]\n")[1]
                else:
                    text_part = user
                assert "`code`" not in text_part
                assert "CODE_BLOCK_" in text_part
                return _mock_response(text_part)

            mock_llm.side_effect = capture
            result = await call_humanizer_chunk("Use `code` here.", config)

        assert "`code`" in result
