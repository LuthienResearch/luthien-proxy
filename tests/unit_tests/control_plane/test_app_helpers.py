from collections import Counter

import pytest

from luthien_proxy.control_plane.app import get_hook_counters, list_endpoints


@pytest.mark.asyncio
async def test_list_endpoints_shape():
    data = await list_endpoints()
    assert "hooks" in data and "health" in data


@pytest.mark.asyncio
async def test_get_hook_counters_is_dict():
    data = await get_hook_counters(counters=Counter({"a": 1}))
    assert isinstance(data, dict)
