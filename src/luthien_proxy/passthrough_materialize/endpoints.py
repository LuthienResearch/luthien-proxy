"""Endpoint eligibility classification for passthrough materialization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Provider(StrEnum):
    """Provider families supported by passthrough materialization."""

    OPENAI = "openai"
    GEMINI = "gemini"


class EndpointKind(StrEnum):
    """Materializable provider endpoint variants."""

    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    OPENAI_RESPONSES = "openai_responses"
    GEMINI_GENERATE_CONTENT = "gemini_generate_content"
    GEMINI_STREAM_GENERATE_CONTENT = "gemini_stream_generate_content"


@dataclass(frozen=True, slots=True)
class EligibleEndpoint:
    """Endpoint that can become canonical conversation history."""

    path: str
    provider: Provider
    kind: EndpointKind


@dataclass(frozen=True, slots=True)
class ExcludedEndpoint:
    """Endpoint intentionally skipped by passthrough materialization."""

    path: str


type EndpointClassification = EligibleEndpoint | ExcludedEndpoint


def classify_endpoint(path: str) -> EndpointClassification:
    """Classify a captured request path for materialization eligibility."""
    match path:
        case "/openai/v1/chat/completions":
            return EligibleEndpoint(
                path=path,
                provider=Provider.OPENAI,
                kind=EndpointKind.OPENAI_CHAT_COMPLETIONS,
            )
        case "/openai/v1/responses":
            return EligibleEndpoint(
                path=path,
                provider=Provider.OPENAI,
                kind=EndpointKind.OPENAI_RESPONSES,
            )
        case gemini_path if _is_gemini_generate_content(gemini_path):
            return EligibleEndpoint(
                path=path,
                provider=Provider.GEMINI,
                kind=EndpointKind.GEMINI_GENERATE_CONTENT,
            )
        case gemini_path if _is_gemini_stream_generate_content(gemini_path):
            return EligibleEndpoint(
                path=path,
                provider=Provider.GEMINI,
                kind=EndpointKind.GEMINI_STREAM_GENERATE_CONTENT,
            )
        case _:
            return ExcludedEndpoint(path=path)


def _is_gemini_generate_content(path: str) -> bool:
    return path.startswith("/gemini/") and path.endswith(":generateContent")


def _is_gemini_stream_generate_content(path: str) -> bool:
    return path.startswith("/gemini/") and path.endswith(":streamGenerateContent")


__all__ = [
    "EligibleEndpoint",
    "EndpointClassification",
    "EndpointKind",
    "ExcludedEndpoint",
    "Provider",
    "classify_endpoint",
]
