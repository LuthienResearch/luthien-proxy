"""Utilities for DeAIPolicy LLM calls.

Handles prompt construction and LiteLLM calls for transforming AI-generated
text into more natural, human-sounding writing. Based on the humanizer project
(https://github.com/blader/humanizer) which catalogs 25 categories of AI
writing patterns from Wikipedia's "Signs of AI writing" guide.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from litellm import acompletion
from litellm.types.utils import Choices, Message, ModelResponse
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


class DeAIConfig(BaseModel):
    """Configuration for DeAIPolicy."""

    model: str = Field(
        default="claude-haiku-4-5",
        description="Any LiteLLM model string, e.g. 'claude-haiku-4-5', 'gpt-4o', 'ollama/llama3'",
    )
    api_base: str | None = Field(
        default=None,
        description="Optional. Leave blank to use the model's default backend.",
    )
    api_key: str | None = Field(
        default=None,
        description="API key for authentication",
        json_schema_extra={"format": "password"},
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature. Higher values produce more varied rewrites.",
    )
    max_tokens: int = Field(
        default=8192,
        description="Maximum output tokens for the DeAI LLM call.",
    )
    extra_instructions: str = Field(
        default="",
        description="Additional instructions appended to the DeAI prompt.",
    )
    min_text_length: int = Field(
        default=40,
        ge=0,
        description="Text blocks shorter than this (in characters) are passed through unchanged.",
    )
    chunk_size: int = Field(
        default=500,
        ge=50,
        description="Minimum buffer size (chars) before looking for a paragraph split point.",
    )
    force_chunk_size: int = Field(
        default=1500,
        ge=100,
        description="Force a split even without a paragraph boundary if buffer exceeds this.",
    )
    context_overlap: int = Field(
        default=200,
        ge=0,
        description="Characters of previous humanized output to include as context for style continuity.",
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Max retry attempts on transient failures (0 = no retries).",
    )
    retry_delay: float = Field(
        default=0.5,
        ge=0.0,
        le=30.0,
        description="Seconds to wait between retries.",
    )

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _check_force_chunk_size(self) -> DeAIConfig:
        if self.force_chunk_size <= self.chunk_size:
            raise ValueError(
                f"force_chunk_size ({self.force_chunk_size}) must be greater than chunk_size ({self.chunk_size})"
            )
        return self


_DEAI_SYSTEM_PROMPT = """\
You are a text rewriter. Your job is to take AI-generated text and rewrite it \
to sound naturally human-written, while preserving meaning and technical accuracy.

# AI Writing Patterns to Eliminate

## Content Patterns
1. Undue emphasis on significance/legacy — "stands as," "testament," "crucial," "pivotal," "underscores"
2. Notability inflation — claiming broad recognition without evidence
3. Superficial -ing analyses — tacking present participle phrases for fake depth ("symbolizing," "reflecting")
4. Promotional language — "vibrant," "breathtaking," "nestled," "stunning," "renowned"
5. Vague attributions — "Experts argue," "Industry reports," "Some critics"
6. Formulaic sections — generic "Challenges and Future Prospects" outlines

## Language & Grammar
7. Overused AI vocabulary — additionally, align, crucial, delve, enhance, fostering, garner, \
highlight, interplay, intricate, landscape, pivotal, showcase, tapestry, testament, underscore, \
valuable, vibrant, multifaceted, comprehensive, innovative, leverage, streamline, utilize, \
cutting-edge, paradigm, holistic, synergy
8. Copula avoidance — "serves as," "stands as," "features," "boasts" instead of plain "is/are"
9. Negative parallelisms — "Not only...but..." or "It's not just...it's..." constructions
10. Rule of three overuse — forcing ideas into groups of three
11. Elegant variation — excessive synonym cycling to avoid repeating words
12. False ranges — "From X to Y" where X and Y aren't on a meaningful scale

## Style
13. Em dash overuse — more frequent than natural writing
14. Excessive boldface — mechanical emphasis
15. Inline-header vertical lists — bolded headers with colons and descriptions
16. Title Case In Every Heading — should be sentence case
17. Decorative emojis in headings or bullets
18. Curly quotation marks (typographic tell from ChatGPT)

## Communication
19. Chatbot artifacts — "I hope this helps," "Of course!," "Certainly!," "Let me know"
20. Knowledge-cutoff disclaimers — "As of my last training update"
21. Sycophantic tone — overly positive, people-pleasing language

## Filler & Hedging
22. Filler phrases — "In order to," "Due to the fact that," "At this point in time," \
"It is worth noting that," "It's important to note"
23. Excessive hedging — over-qualifying with "could potentially possibly"
24. Generic positive conclusions — "The future looks bright"
25. Consistent hyphenation — humans are inconsistent with compound modifiers

# How to Rewrite

- Preserve ALL technical content, code blocks, data, and factual claims exactly
- Replace AI-isms with plain, direct language
- Vary sentence rhythm — mix short and long
- Use "is/are" instead of "serves as/stands as/features"
- Cut filler phrases entirely rather than replacing them
- Remove promotional adjectives; let facts speak
- Keep the same overall structure and information ordering
- Do NOT add new information or opinions
- Do NOT wrap the output in quotes or markdown fences
- Output ONLY the rewritten text, nothing else\
"""

_CHUNK_ADDENDUM = """

# Fragment Mode

You are rewriting a fragment of a larger text, not a complete document. \
Do NOT add introductions, conclusions, or transitional phrases that weren't \
in the original. Maintain consistent style with the preceding context if provided.\
"""


def build_deai_chunk_prompt(
    chunk: str,
    previous_context: str = "",
    extra_instructions: str = "",
    is_final: bool = False,
) -> list[dict[str, str]]:
    """Build the message list for a chunk-mode DeAI call.

    Includes the preceding humanized output as style context so the
    rewriter maintains consistency across chunks.
    """
    system = _DEAI_SYSTEM_PROMPT + _CHUNK_ADDENDUM
    if is_final:
        system += "\nThis is the final fragment of the text."
    if extra_instructions:
        system += f"\n\n# Additional Instructions\n{extra_instructions}"

    if previous_context:
        user_content = (
            f"[PRECEDING CONTEXT \u2014 do not rewrite, for style reference only]\n"
            f"{previous_context}\n\n"
            f"[TEXT TO REWRITE]\n{chunk}"
        )
    else:
        user_content = chunk

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


class DeAITruncatedError(Exception):
    """Raised when the DeAI LLM output appears truncated."""


async def _call_litellm(
    messages: list[dict[str, str]],
    config: DeAIConfig,
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
    raise_on_truncation: bool = True,
) -> str:
    """Shared LiteLLM call with retry logic.

    When raise_on_truncation is False (chunk mode), truncated output is
    returned as-is instead of raising.
    """
    resolved_key = api_key if api_key is not None else config.api_key
    kwargs: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    if config.api_base:
        kwargs["api_base"] = config.api_base
    if resolved_key:
        kwargs["api_key"] = resolved_key
    if extra_headers:
        kwargs["extra_headers"] = extra_headers

    max_attempts = 1 + config.max_retries
    last_exc: Exception | None = None

    for attempt in range(max_attempts):
        try:
            response = await acompletion(**kwargs)
            response = cast(ModelResponse, response)

            first_choice: Choices = cast(Choices, response.choices[0])
            finish_reason = first_choice.finish_reason
            if finish_reason == "length" and raise_on_truncation:
                raise DeAITruncatedError(
                    f"DeAI output truncated (hit max_tokens={config.max_tokens}). "
                    "The original text is too long for the configured max_tokens."
                )

            message: Message = first_choice.message
            if message.content is None:
                raise ValueError("DeAI response content is None")

            return message.content
        except DeAITruncatedError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            is_last_attempt = attempt == max_attempts - 1
            if is_last_attempt:
                break
            logger.warning(
                "DeAI attempt %d/%d failed: %r — retrying in %.1fs",
                attempt + 1,
                max_attempts,
                exc,
                config.retry_delay,
            )
            if config.retry_delay > 0:
                await asyncio.sleep(config.retry_delay)

    assert last_exc is not None
    raise last_exc


async def call_deai_chunk(
    chunk: str,
    config: DeAIConfig,
    previous_context: str = "",
    is_final: bool = False,
    api_key: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """Call the DeAI LLM to rewrite a text chunk.

    Uses chunk-mode prompting with context overlap. On truncation, returns
    partial output instead of raising (graceful degradation for streaming).
    """
    prompt = build_deai_chunk_prompt(
        chunk,
        previous_context=previous_context,
        extra_instructions=config.extra_instructions,
        is_final=is_final,
    )
    return await _call_litellm(prompt, config, api_key, extra_headers, raise_on_truncation=False)


def find_chunk_boundary(
    buf: str,
    chunk_size: int,
    force_chunk_size: int,
) -> int | None:
    """Find the split position in buf, or None if not enough text yet.

    Shared by both the streaming and non-streaming paths.
    Returns the index at which to split (the chunk is buf[:index]).
    """
    if len(buf) < chunk_size:
        return None

    # Prefer paragraph boundary (\n\n) after chunk_size
    split_pos = buf.find("\n\n", chunk_size)
    if split_pos != -1 and split_pos < force_chunk_size:
        return split_pos + 2

    if len(buf) < force_chunk_size:
        return None

    # Force split — prefer sentence boundary
    force_region = buf[:force_chunk_size]
    sentence_end = max(
        force_region.rfind(". "),
        force_region.rfind("! "),
        force_region.rfind("? "),
    )
    if sentence_end > chunk_size:
        return sentence_end + 2
    return force_chunk_size


def split_into_chunks(
    text: str,
    chunk_size: int = 500,
    force_chunk_size: int = 1500,
) -> list[str]:
    """Split text into paragraph-aligned chunks for independent humanization.

    Used by the non-streaming path to break a complete text block into
    chunks that can each be humanized without hitting token limits.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= chunk_size:
            chunks.append(remaining)
            break

        boundary = find_chunk_boundary(remaining, chunk_size, force_chunk_size)
        if boundary is None:
            chunks.append(remaining)
            break

        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:]

    return chunks


__all__ = [
    "DeAIConfig",
    "DeAITruncatedError",
    "build_deai_chunk_prompt",
    "call_deai_chunk",
    "find_chunk_boundary",
    "split_into_chunks",
]
