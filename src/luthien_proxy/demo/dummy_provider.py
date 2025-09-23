"""Deterministic LLM provider used to stage the harmful baseline demo."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeterministicResponse:
    """A deterministic completion returned by the dummy provider."""

    scenario: str
    content: str


class CompletionRequest(BaseModel):
    """Minimal request body for the dummy provider."""

    prompt: str
    scenario: str | None = None


class CompletionResponse(BaseModel):
    """Response payload matching ``CompletionRequest`` semantics."""

    scenario: str
    content: str


_DEFAULT_RESPONSES: Mapping[str, str] = {
    "harmful_drop": "DROP TABLE customers; -- requested by automation",
    "inventory_export": "SELECT * FROM inventory ORDER BY sku;",
}


class DeterministicLLMProvider:
    """Simple in-memory provider mapping scenarios to canned responses."""

    def __init__(self, responses: Mapping[str, str] | None = None) -> None:
        """Initialize the provider with optional custom responses."""
        self._responses: Mapping[str, str] = responses or _DEFAULT_RESPONSES

    def complete(self, prompt: str, scenario: str | None) -> DeterministicResponse:
        """Return a deterministic completion for the given prompt."""
        key = self._select_key(prompt=prompt, scenario=scenario)
        try:
            content = self._responses[key]
        except KeyError as exc:  # pragma: no cover - defensive guard
            raise HTTPException(status_code=404, detail=f"Unknown scenario '{key}'") from exc
        logger.info("Dummy provider issued response for scenario %s", key)
        return DeterministicResponse(scenario=key, content=content)

    def _select_key(self, prompt: str, scenario: str | None) -> str:
        if scenario:
            return scenario
        normalized = prompt.lower()
        if "drop" in normalized and "table" in normalized:
            return "harmful_drop"
        return "inventory_export"


def create_dummy_provider_app(provider: DeterministicLLMProvider | None = None) -> FastAPI:
    """Create a FastAPI app exposing the deterministic provider."""
    provider = provider or DeterministicLLMProvider()
    app = FastAPI(title="Luthien Demo Dummy Provider")

    @app.post("/v1/completions", response_model=CompletionResponse)
    async def create_completion(request: CompletionRequest) -> CompletionResponse:
        response = provider.complete(prompt=request.prompt, scenario=request.scenario)
        return CompletionResponse(scenario=response.scenario, content=response.content)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "dummy-llm-provider"}

    return app
