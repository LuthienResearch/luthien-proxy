import pytest

from luthien_control.control_plane.app import health_check


@pytest.mark.asyncio
async def test_health_check_returns_expected_shape():
    result = await health_check()
    assert isinstance(result, dict)
    assert result.get("status") == "healthy"
    assert result.get("service") == "luthien-control-plane"
    assert "version" in result
