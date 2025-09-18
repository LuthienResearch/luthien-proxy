import pytest

from luthien_proxy.policies.engine import PolicyEngine


@pytest.mark.asyncio
async def test_engine_minimal_paths():
    eng = PolicyEngine()
    # Default policy is available and copy-able
    pol = await eng.get_policy()
    assert isinstance(pol, dict) and pol.get("name") == "default"

    # Logging/audit should no-op without configured backends
    await eng.log_decision(None, None, "test", 0.1, 0.2, {"k": 1})
    await eng.trigger_audit("ep", "step", "reason", 0.9, {"x": True})

    # Episode state returns {} without Redis
    state = await eng.get_episode_state("id1")
    assert state == {}
    await eng.update_episode_state("id1", {"step_count": 1})
