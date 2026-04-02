"""Unit tests for deai_utils."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.policies.deai_utils import (
    DeAIConfig,
    build_deai_chunk_prompt,
    build_deai_prompt,
    call_deai_chunk,
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


class TestBuildDeAIPrompt:
    def test_basic_prompt_structure(self):
        prompt = build_deai_prompt("Some AI text here.")
        assert len(prompt) == 2
        assert prompt[0]["role"] == "system"
        assert prompt[1]["role"] == "user"
        assert prompt[1]["content"] == "Some AI text here."

    def test_system_prompt_contains_patterns(self):
        prompt = build_deai_prompt("text")
        system = prompt[0]["content"]
        assert "testament" in system
        assert "vibrant" in system
        assert "delve" in system
        assert "Chatbot artifacts" in system

    def test_extra_instructions_appended(self):
        prompt = build_deai_prompt("text", extra_instructions="Write in British English.")
        system = prompt[0]["content"]
        assert "Write in British English." in system

    def test_no_extra_instructions(self):
        prompt = build_deai_prompt("text", extra_instructions="")
        system = prompt[0]["content"]
        assert "Additional Instructions" not in system


class TestBuildDeAIChunkPrompt:
    def test_first_chunk_no_context(self):
        prompt = build_deai_chunk_prompt("Some chunk text.")
        assert len(prompt) == 2
        assert prompt[0]["role"] == "system"
        assert "Fragment Mode" in prompt[0]["content"]
        assert prompt[1]["content"] == "Some chunk text."
        assert "PRECEDING CONTEXT" not in prompt[1]["content"]

    def test_with_previous_context(self):
        prompt = build_deai_chunk_prompt("New chunk.", previous_context="Previous output tail.")
        user = prompt[1]["content"]
        assert "PRECEDING CONTEXT" in user
        assert "Previous output tail." in user
        assert "TEXT TO REWRITE" in user
        assert "New chunk." in user

    def test_final_chunk_flag(self):
        prompt = build_deai_chunk_prompt("Last bit.", is_final=True)
        assert "final fragment" in prompt[0]["content"]

    def test_extra_instructions(self):
        prompt = build_deai_chunk_prompt("text", extra_instructions="Be concise.")
        assert "Be concise." in prompt[0]["content"]


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
        assert chunks[0].endswith(". ")

    def test_multiple_paragraphs(self):
        text = ("para " * 12 + "\n\n") * 3  # 3 paragraphs, each ~63 chars
        chunks = split_into_chunks(text, chunk_size=50, force_chunk_size=150)
        assert len(chunks) == 3

    @pytest.mark.parametrize(
        "text",
        [
            "short",
            "a" * 60 + "\n\n" + "b" * 30,
            "x" * 200,
            ("para " * 12 + "\n\n") * 5,
            "word " * 80,
            "x" * 60 + ". " + "y" * 100,
        ],
        ids=["short", "two-paragraphs", "no-breaks", "five-paragraphs", "long-words", "sentence-boundary"],
    )
    def test_roundtrip_preserves_all_text(self, text: str):
        """Joining chunks must reproduce the original text exactly."""
        chunks = split_into_chunks(text, chunk_size=50, force_chunk_size=100)
        assert "".join(chunks) == text


class TestCallDeAIChunk:
    @pytest.mark.asyncio()
    async def test_basic_chunk_call(self):
        config = DeAIConfig(model="test-model", api_key="key", max_retries=0)

        with patch("luthien_proxy.policies.deai_utils.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _mock_response("humanized chunk")
            result = await call_deai_chunk("original chunk", config, previous_context="prev tail")

        assert result == "humanized chunk"
        call_messages = mock_llm.call_args[1]["messages"]
        assert "Fragment Mode" in call_messages[0]["content"]
        assert "prev tail" in call_messages[1]["content"]

    @pytest.mark.asyncio()
    async def test_truncation_returns_partial(self):
        """Chunk mode returns partial output on truncation instead of raising."""
        config = DeAIConfig(model="test-model", api_key="key", max_retries=0)

        with patch("luthien_proxy.policies.deai_utils.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = _mock_response("partial output", finish_reason="length")
            result = await call_deai_chunk("long chunk", config)

        assert result == "partial output"

    @pytest.mark.asyncio()
    async def test_retries_on_failure(self):
        config = DeAIConfig(model="test-model", api_key="key", max_retries=2, retry_delay=0.0)

        with patch("luthien_proxy.policies.deai_utils.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                RuntimeError("fail"),
                RuntimeError("fail again"),
                _mock_response("success"),
            ]
            result = await call_deai_chunk("text", config)

        assert result == "success"
        assert mock_llm.call_count == 3
