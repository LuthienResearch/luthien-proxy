from __future__ import annotations

from unittest.mock import patch

from luthien_proxy.observability.sentry import init_sentry
from luthien_proxy.settings import Settings


def test_init_sentry_is_noop_when_dsn_is_missing() -> None:
    settings = Settings(sentry_enabled=True, sentry_dsn="")

    with (
        patch("luthien_proxy.observability.sentry.sentry_sdk.init") as mock_init,
        patch("luthien_proxy.observability.sentry.ignore_logger") as mock_ignore_logger,
    ):
        init_sentry(settings)

    mock_init.assert_not_called()
    mock_ignore_logger.assert_not_called()


def test_init_sentry_uses_environment_and_traces_sample_rate_without_profiling() -> None:
    settings = Settings(
        sentry_enabled=True,
        sentry_dsn="https://fake@sentry.io/0",
        sentry_traces_sample_rate=0.05,
        environment="prod",
    )

    with patch("luthien_proxy.observability.sentry.sentry_sdk.init") as mock_init:
        init_sentry(settings)

    mock_init.assert_called_once()
    kwargs = mock_init.call_args.kwargs
    assert kwargs["environment"] == "prod"
    assert kwargs["traces_sample_rate"] == 0.05
    assert "profiles_sample_rate" not in kwargs
