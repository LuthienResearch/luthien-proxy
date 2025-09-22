import pytest

from luthien_proxy.policies.engine import PolicyEngine


class _AcquireCM:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, sql: str, *params):
        self.pool.executed.append((sql.strip(), params))
        return "OK"


class FakePool:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    def acquire(self):
        return _AcquireCM(self)


@pytest.mark.asyncio
async def test_engine_writes_to_db_pool():
    eng = PolicyEngine(database_url=None, redis_url="redis://localhost:6379/0")
    pool = FakePool()
    eng.db_pool = pool
    eng.redis_client = None

    await eng.log_decision("ep", "s", "t", 0.1, 0.2, {"x": 1})
    await eng.trigger_audit("ep", "s", "why", 0.9, {"y": True})

    # One insert per call
    assert any("INSERT INTO control_decisions" in sql for sql, _ in pool.executed)
    assert any("INSERT INTO audit_requests" in sql for sql, _ in pool.executed)
