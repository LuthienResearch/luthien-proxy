from __future__ import annotations

import pytest

from luthien_proxy.passthrough_materialize.endpoints import (
    EligibleEndpoint,
    EndpointKind,
    ExcludedEndpoint,
    Provider,
    classify_endpoint,
)


@pytest.mark.parametrize(
    ("path", "provider", "kind"),
    [
        ("/openai/v1/chat/completions", Provider.OPENAI, EndpointKind.OPENAI_CHAT_COMPLETIONS),
        ("/openai/v1/responses", Provider.OPENAI, EndpointKind.OPENAI_RESPONSES),
        (
            "/gemini/v1beta/models/gemini-2.5-pro:generateContent",
            Provider.GEMINI,
            EndpointKind.GEMINI_GENERATE_CONTENT,
        ),
        (
            "/gemini/v1beta/models/gemini-2.5-pro:streamGenerateContent",
            Provider.GEMINI,
            EndpointKind.GEMINI_STREAM_GENERATE_CONTENT,
        ),
    ],
)
def test_classifies_eligible_endpoint_when_path_is_materializable(
    path: str,
    provider: Provider,
    kind: EndpointKind,
) -> None:
    classified = classify_endpoint(path)

    assert classified == EligibleEndpoint(path=path, provider=provider, kind=kind)


@pytest.mark.parametrize(
    "path",
    [
        "/openai/v1/models",
        "/openai/v1/embeddings",
        "/openai/v1/chat/completions/extra",
        "/gemini/v1beta/models",
        "/gemini/v1beta/models/gemini-2.5-pro:embedContent",
        "/gemini/v1beta/models/gemini-2.5-pro:generateContent:extra",
        "/anthropic/v1/messages",
        "/unknown/v1/chat/completions",
    ],
)
def test_excludes_endpoint_when_path_is_not_materializable(path: str) -> None:
    classified = classify_endpoint(path)

    assert classified == ExcludedEndpoint(path=path)
