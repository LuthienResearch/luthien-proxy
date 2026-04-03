"""Unit tests for judge_completion() from judge_client module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from luthien_proxy.credentials.credential import Credential, CredentialType
from luthien_proxy.llm.judge_client import judge_completion


class TestJudgeCompletionApiKey:
    """Test judge_completion() with API_KEY credential type."""

    @pytest.mark.asyncio
    async def test_api_key_credential_passes_api_key_kwarg(self):
        """API_KEY credential passes api_key kwarg to acompletion."""
        cred = Credential(value="sk-ant-test-key", credential_type=CredentialType.API_KEY)

        mock_message = MagicMock()
        mock_message.content = "test response"

        mock_choice = MagicMock()
        mock_choice.message = mock_message

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        with patch("luthien_proxy.llm.judge_client.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            result = await judge_completion(
                cred,
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": "test"}],
            )

            assert result == "test response"
            mock_acompletion.assert_called_once()

            call_kwargs = mock_acompletion.call_args[1]
            assert call_kwargs["api_key"] == "sk-ant-test-key"
            assert "extra_headers" not in call_kwargs

    @pytest.mark.asyncio
    async def test_api_key_passes_model_and_messages(self):
        """API_KEY credential passes model and messages to acompletion."""
        cred = Credential(value="sk-ant-test", credential_type=CredentialType.API_KEY)

        mock_message = MagicMock()
        mock_message.content = "response"
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_message)]

        with patch("luthien_proxy.llm.judge_client.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            messages = [{"role": "user", "content": "hello"}]
            await judge_completion(
                cred,
                model="claude-opus",
                messages=messages,
            )

            call_kwargs = mock_acompletion.call_args[1]
            assert call_kwargs["model"] == "claude-opus"
            assert call_kwargs["messages"] == messages


class TestJudgeCompletionAuthToken:
    """Test judge_completion() with AUTH_TOKEN credential type."""

    @pytest.mark.asyncio
    async def test_auth_token_credential_passes_bearer_header(self):
        """AUTH_TOKEN credential passes extra_headers with Bearer authorization."""
        cred = Credential(value="sk-ant-oat-test-token", credential_type=CredentialType.AUTH_TOKEN)

        mock_message = MagicMock()
        mock_message.content = "token response"
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_message)]

        with patch("luthien_proxy.llm.judge_client.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            result = await judge_completion(
                cred,
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": "test"}],
            )

            assert result == "token response"

            call_kwargs = mock_acompletion.call_args[1]
            assert call_kwargs["extra_headers"]["Authorization"] == "Bearer sk-ant-oat-test-token"
            assert call_kwargs["api_key"] == "placeholder"

    @pytest.mark.asyncio
    async def test_auth_token_uses_placeholder_api_key(self):
        """AUTH_TOKEN credential uses placeholder for api_key kwarg."""
        cred = Credential(value="sk-ant-oat-oauth-token", credential_type=CredentialType.AUTH_TOKEN)

        mock_message = MagicMock()
        mock_message.content = "response"
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_message)]

        with patch("luthien_proxy.llm.judge_client.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            await judge_completion(
                cred,
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": "test"}],
            )

            call_kwargs = mock_acompletion.call_args[1]
            # LiteLLM requires a non-None api_key even for bearer auth
            assert call_kwargs["api_key"] == "placeholder"


class TestJudgeCompletionOptionalParams:
    """Test judge_completion() with optional parameters."""

    @pytest.mark.asyncio
    async def test_api_base_passed_through(self):
        """api_base parameter is passed through to acompletion."""
        cred = Credential(value="sk-ant-test", credential_type=CredentialType.API_KEY)

        mock_message = MagicMock()
        mock_message.content = "response"
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_message)]

        with patch("luthien_proxy.llm.judge_client.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            await judge_completion(
                cred,
                model="claude-haiku-4-5",
                messages=[],
                api_base="https://custom.api.com",
            )

            call_kwargs = mock_acompletion.call_args[1]
            assert call_kwargs["api_base"] == "https://custom.api.com"

    @pytest.mark.asyncio
    async def test_response_format_passed_through(self):
        """response_format parameter is passed through to acompletion."""
        cred = Credential(value="sk-ant-test", credential_type=CredentialType.API_KEY)

        mock_message = MagicMock()
        mock_message.content = "response"
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_message)]

        with patch("luthien_proxy.llm.judge_client.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            response_format = {"type": "json_object"}
            await judge_completion(
                cred,
                model="claude-haiku-4-5",
                messages=[],
                response_format=response_format,
            )

            call_kwargs = mock_acompletion.call_args[1]
            assert call_kwargs["response_format"] == response_format

    @pytest.mark.asyncio
    async def test_temperature_and_max_tokens_passed(self):
        """temperature and max_tokens parameters are passed through."""
        cred = Credential(value="sk-ant-test", credential_type=CredentialType.API_KEY)

        mock_message = MagicMock()
        mock_message.content = "response"
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_message)]

        with patch("luthien_proxy.llm.judge_client.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            await judge_completion(
                cred,
                model="claude-haiku-4-5",
                messages=[],
                temperature=0.8,
                max_tokens=512,
            )

            call_kwargs = mock_acompletion.call_args[1]
            assert call_kwargs["temperature"] == 0.8
            assert call_kwargs["max_tokens"] == 512


class TestJudgeCompletionErrorHandling:
    """Test judge_completion() error handling."""

    @pytest.mark.asyncio
    async def test_raises_when_response_content_is_none(self):
        """judge_completion raises ValueError when response content is None."""
        cred = Credential(value="sk-ant-test", credential_type=CredentialType.API_KEY)

        mock_message = MagicMock()
        mock_message.content = None  # None content
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_message)]

        with patch("luthien_proxy.llm.judge_client.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            with pytest.raises(ValueError, match="LLM response content is None"):
                await judge_completion(
                    cred,
                    model="claude-haiku-4-5",
                    messages=[{"role": "user", "content": "test"}],
                )

    @pytest.mark.asyncio
    async def test_returns_string_content(self):
        """judge_completion returns content as string."""
        cred = Credential(value="sk-ant-test", credential_type=CredentialType.API_KEY)

        mock_message = MagicMock()
        mock_message.content = "final response string"
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=mock_message)]

        with patch("luthien_proxy.llm.judge_client.acompletion", new_callable=AsyncMock) as mock_acompletion:
            mock_acompletion.return_value = mock_response

            result = await judge_completion(
                cred,
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": "test"}],
            )

            assert isinstance(result, str)
            assert result == "final response string"
