"""Tests for billing/quota error detection, enrichment, and response formatting."""

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from luthien_proxy.exceptions import (
    BackendAPIError,
    enrich_billing_message,
    enrich_rate_limit_message,
    is_billing_error,
    is_rate_limit_error,
)
from luthien_proxy.pipeline.client_format import ClientFormat


class TestIsBillingError:
    """Tests for is_billing_error detection logic."""

    def test_402_is_always_billing(self):
        """HTTP 402 Payment Required is always a billing error."""
        assert is_billing_error(402, "api_error", "something") is True

    def test_billing_error_type_detected(self):
        """error_type 'billing_error' is detected regardless of status/message."""
        assert is_billing_error(403, "billing_error", "no keywords here") is True

    @pytest.mark.parametrize(
        "message",
        [
            "You exceeded your current quota",
            "Insufficient quota remaining",
            "Your account has been suspended",
            "Account deactivated due to non-payment",
            "Billing limit reached for this month",
            "Credit balance is zero",
            "Plan limit exceeded",
            "Usage limit reached",
            "Spending limit exceeded for organization",
        ],
    )
    def test_keyword_detection(self, message):
        """Messages containing billing keywords are detected."""
        assert is_billing_error(403, "permission_error", message) is True

    def test_case_insensitive_matching(self):
        """Keyword matching is case-insensitive."""
        assert is_billing_error(403, "permission_error", "QUOTA EXCEEDED") is True
        assert is_billing_error(403, "permission_error", "Billing Issue") is True

    def test_non_billing_error(self):
        """Regular errors are not detected as billing errors."""
        assert is_billing_error(401, "authentication_error", "invalid api key") is False
        assert is_billing_error(500, "api_error", "internal server error") is False
        assert is_billing_error(429, "rate_limit_error", "too many requests") is False

    def test_429_with_quota_message_is_billing(self):
        """Rate limit 429 with quota keywords is detected as billing."""
        assert is_billing_error(429, "rate_limit_error", "You exceeded your current quota") is True


class TestEnrichBillingMessage:
    """Tests for enrich_billing_message wrapping."""

    def test_wraps_original_message(self):
        """Original upstream message is preserved in enriched output."""
        original = "You exceeded your current quota"
        enriched = enrich_billing_message(original)
        assert original in enriched

    def test_includes_actionable_guidance(self):
        """Enriched message includes guidance about what to do."""
        enriched = enrich_billing_message("quota exceeded")
        assert "billing" in enriched.lower()
        assert "check" in enriched.lower()

    def test_handles_empty_message(self):
        """Gracefully handles empty upstream message."""
        enriched = enrich_billing_message("")
        assert len(enriched) > 0


class TestBillingErrorHandlerIntegration:
    """Tests for billing errors flowing through the exception handler in main.py."""

    @pytest.fixture
    def app_with_billing_handler(self):
        """Create a minimal FastAPI app with the billing-aware error handler."""
        from luthien_proxy.main import (
            enrich_billing_message,
            enrich_rate_limit_message,
            is_billing_error,
            is_rate_limit_error,
        )

        app = FastAPI()

        @app.exception_handler(BackendAPIError)
        async def handler(request: Request, exc: BackendAPIError) -> JSONResponse:
            message = exc.message
            error_type = exc.error_type
            if is_billing_error(exc.status_code, exc.error_type, exc.message):
                message = enrich_billing_message(exc.message)
                error_type = "billing_error"
            elif is_rate_limit_error(exc.status_code, exc.error_type):
                message = enrich_rate_limit_message(exc.message)

            if exc.client_format == ClientFormat.ANTHROPIC:
                content = {
                    "type": "error",
                    "error": {"type": error_type, "message": message},
                }
            else:
                content = {
                    "error": {
                        "message": message,
                        "type": error_type,
                        "param": None,
                        "code": None,
                    },
                }
            return JSONResponse(status_code=exc.status_code, content=content)

        @app.get("/trigger-402-anthropic")
        async def trigger_402_anthropic():
            raise BackendAPIError(
                status_code=402,
                message="Payment required",
                error_type="api_error",
                client_format=ClientFormat.ANTHROPIC,
                provider="anthropic",
            )

        @app.get("/trigger-402-openai")
        async def trigger_402_openai():
            raise BackendAPIError(
                status_code=402,
                message="Payment required",
                error_type="api_error",
                client_format=ClientFormat.OPENAI,
                provider="openai",
            )

        @app.get("/trigger-quota-exceeded")
        async def trigger_quota_exceeded():
            raise BackendAPIError(
                status_code=403,
                message="You exceeded your current quota, please check your plan and billing details.",
                error_type="permission_error",
                client_format=ClientFormat.OPENAI,
                provider="openai",
            )

        @app.get("/trigger-normal-429")
        async def trigger_normal_429():
            raise BackendAPIError(
                status_code=429,
                message="Rate limit exceeded, try again in 30s",
                error_type="rate_limit_error",
                client_format=ClientFormat.OPENAI,
            )

        @app.get("/trigger-429-quota")
        async def trigger_429_quota():
            raise BackendAPIError(
                status_code=429,
                message="You exceeded your current quota",
                error_type="rate_limit_error",
                client_format=ClientFormat.OPENAI,
                provider="openai",
            )

        @app.get("/trigger-account-suspended")
        async def trigger_account_suspended():
            raise BackendAPIError(
                status_code=403,
                message="Your account has been suspended. Please contact support.",
                error_type="permission_error",
                client_format=ClientFormat.ANTHROPIC,
                provider="anthropic",
            )

        return app

    @pytest.fixture
    def client(self, app_with_billing_handler):
        return TestClient(app_with_billing_handler)

    def test_402_anthropic_gets_billing_error_type(self, client):
        """402 errors return billing_error type in Anthropic format."""
        response = client.get("/trigger-402-anthropic")
        assert response.status_code == 402
        data = response.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "billing_error"

    def test_402_anthropic_gets_enriched_message(self, client):
        """402 errors return an enriched message with guidance."""
        response = client.get("/trigger-402-anthropic")
        data = response.json()
        msg = data["error"]["message"]
        assert "Payment required" in msg
        assert "billing" in msg.lower()

    def test_402_openai_gets_billing_error_type(self, client):
        """402 errors return billing_error type in OpenAI format."""
        response = client.get("/trigger-402-openai")
        assert response.status_code == 402
        data = response.json()
        assert data["error"]["type"] == "billing_error"

    def test_402_openai_gets_enriched_message(self, client):
        """402 errors return an enriched message in OpenAI format."""
        response = client.get("/trigger-402-openai")
        data = response.json()
        msg = data["error"]["message"]
        assert "Payment required" in msg
        assert "billing" in msg.lower()

    def test_quota_exceeded_403_gets_billing_type(self, client):
        """403 with quota keywords gets billing_error type instead of permission_error."""
        response = client.get("/trigger-quota-exceeded")
        assert response.status_code == 403
        data = response.json()
        assert data["error"]["type"] == "billing_error"
        assert "quota" in data["error"]["message"].lower()

    def test_normal_429_gets_rate_limit_enrichment(self, client):
        """Regular rate limit 429 gets rate limit guidance (not billing)."""
        response = client.get("/trigger-normal-429")
        assert response.status_code == 429
        data = response.json()
        assert data["error"]["type"] == "rate_limit_error"
        msg = data["error"]["message"]
        assert "Rate limit exceeded, try again in 30s" in msg
        assert "rate limiting" in msg.lower()
        assert "wait" in msg.lower()

    def test_429_with_quota_message_is_enriched(self, client):
        """429 with quota keywords in message IS enriched as billing error."""
        response = client.get("/trigger-429-quota")
        assert response.status_code == 429
        data = response.json()
        assert data["error"]["type"] == "billing_error"
        assert "exceeded your current quota" in data["error"]["message"]

    def test_account_suspended_anthropic_format(self, client):
        """Account suspended message gets billing enrichment in Anthropic format."""
        response = client.get("/trigger-account-suspended")
        assert response.status_code == 403
        data = response.json()
        assert data["type"] == "error"
        assert data["error"]["type"] == "billing_error"
        assert "suspended" in data["error"]["message"].lower()


class TestIsRateLimitError:
    """Tests for is_rate_limit_error detection logic."""

    def test_429_is_rate_limit(self):
        assert is_rate_limit_error(429, "rate_limit_error") is True

    def test_429_with_other_type_still_detected(self):
        assert is_rate_limit_error(429, "api_error") is True

    def test_rate_limit_type_without_429(self):
        """error_type alone can trigger rate limit detection."""
        assert is_rate_limit_error(500, "rate_limit_error") is True

    def test_non_rate_limit(self):
        assert is_rate_limit_error(401, "authentication_error") is False
        assert is_rate_limit_error(500, "api_error") is False


class TestEnrichRateLimitMessage:
    """Tests for enrich_rate_limit_message wrapping."""

    def test_wraps_original_message(self):
        original = "Rate limit exceeded"
        enriched = enrich_rate_limit_message(original)
        assert original in enriched

    def test_includes_retry_guidance(self):
        enriched = enrich_rate_limit_message("too many requests")
        assert "wait" in enriched.lower()
        assert "rate limit" in enriched.lower()


class TestAnthropicStreamingBillingError:
    """Tests for billing error enrichment in Anthropic mid-stream errors."""

    def test_build_error_event_enriches_402(self):
        """_build_error_event enriches 402 errors with billing guidance."""
        from unittest.mock import MagicMock

        from luthien_proxy.pipeline.anthropic_processor import _build_error_event

        # Create a mock AnthropicStatusError with status_code 402
        mock_error = MagicMock()
        mock_error.__class__ = type("APIStatusError", (), {})
        # We need to use a real AnthropicStatusError to pass isinstance check
        from anthropic import APIStatusError as AnthropicStatusError
        from httpx import Request as HttpxRequest
        from httpx import Response as HttpxResponse

        request = HttpxRequest(method="POST", url="https://api.anthropic.com/v1/messages")
        response = HttpxResponse(status_code=402, request=request, text="Payment required")
        error = AnthropicStatusError(
            message="Payment required",
            response=response,
            body={"error": {"type": "billing_error", "message": "Payment required"}},
        )

        event = _build_error_event(error, "test-call-id")
        assert event["error"]["type"] == "billing_error"
        assert "Payment required" in event["error"]["message"]
        assert "billing" in event["error"]["message"].lower()

    def test_build_error_event_enriches_quota_message(self):
        """_build_error_event enriches 403 with quota keywords."""
        from anthropic import APIStatusError as AnthropicStatusError
        from httpx import Request as HttpxRequest
        from httpx import Response as HttpxResponse

        from luthien_proxy.pipeline.anthropic_processor import _build_error_event

        request = HttpxRequest(method="POST", url="https://api.anthropic.com/v1/messages")
        response = HttpxResponse(status_code=403, request=request, text="quota exceeded")
        error = AnthropicStatusError(
            message="You exceeded your current quota",
            response=response,
            body={"error": {"type": "permission_error", "message": "You exceeded your current quota"}},
        )

        event = _build_error_event(error, "test-call-id")
        assert event["error"]["type"] == "billing_error"
        assert "exceeded your current quota" in event["error"]["message"]

    def test_build_error_event_enriches_429_rate_limit(self):
        """_build_error_event enriches 429 rate limit with retry guidance."""
        from anthropic import APIStatusError as AnthropicStatusError
        from httpx import Request as HttpxRequest
        from httpx import Response as HttpxResponse

        from luthien_proxy.pipeline.anthropic_processor import _build_error_event

        request = HttpxRequest(method="POST", url="https://api.anthropic.com/v1/messages")
        response = HttpxResponse(status_code=429, request=request, text="rate limited")
        error = AnthropicStatusError(
            message="Rate limit exceeded",
            response=response,
            body={"error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}},
        )

        event = _build_error_event(error, "test-call-id")
        assert event["error"]["type"] == "rate_limit_error"
        assert "Rate limit exceeded" in event["error"]["message"]
        assert "wait" in event["error"]["message"].lower()

    def test_build_error_event_does_not_enrich_normal_errors(self):
        """_build_error_event does not enrich non-billing, non-rate-limit errors."""
        from anthropic import APIStatusError as AnthropicStatusError
        from httpx import Request as HttpxRequest
        from httpx import Response as HttpxResponse

        from luthien_proxy.pipeline.anthropic_processor import _build_error_event

        request = HttpxRequest(method="POST", url="https://api.anthropic.com/v1/messages")
        response = HttpxResponse(status_code=401, request=request, text="unauthorized")
        error = AnthropicStatusError(
            message="invalid x-api-key",
            response=response,
            body={"error": {"type": "authentication_error", "message": "invalid x-api-key"}},
        )

        event = _build_error_event(error, "test-call-id")
        assert event["error"]["type"] == "authentication_error"
        assert event["error"]["message"] == "invalid x-api-key"


class TestStatusCodeMappings:
    """Tests that 402 is mapped to billing_error in all status code maps."""

    def test_anthropic_processor_maps_402(self):
        from luthien_proxy.pipeline.anthropic_processor import _ANTHROPIC_STATUS_ERROR_TYPE_MAP

        assert _ANTHROPIC_STATUS_ERROR_TYPE_MAP[402] == "billing_error"

    def test_main_maps_402(self):
        from luthien_proxy.main import http_status_to_anthropic_error_type

        assert http_status_to_anthropic_error_type(402) == "billing_error"
