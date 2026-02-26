#!/usr/bin/env python3
"""Quick test script to verify redirect handlers work correctly."""

import asyncio
from fastapi.testclient import TestClient
from src.luthien_proxy.main import create_app
from src.luthien_proxy.utils.db import DatabasePool
from redis.asyncio import Redis
from unittest.mock import AsyncMock

async def test_redirects():
    """Test that redirect handlers return correct 301 redirects."""
    print("Testing redirect handlers...")
    
    # Create a minimal app for testing
    mock_db_pool = AsyncMock(spec=DatabasePool)
    mock_redis = AsyncMock(spec=Redis)
    
    app = create_app(
        api_key="test-key", 
        admin_key="admin-key",
        db_pool=mock_db_pool,
        redis_client=mock_redis
    )
    
    client = TestClient(app)
    
    # Test /debug/diff redirect
    response = client.get("/debug/diff", follow_redirects=False)
    print(f"GET /debug/diff -> Status: {response.status_code}, Location: {response.headers.get('location', 'None')}")
    assert response.status_code == 301
    assert response.headers.get('location') == '/diffs'
    
    # Test /admin/* redirects
    test_paths = [
        ('/admin/policy', '/api/admin/policy'),
        ('/admin/policy/list', '/api/admin/policy/list'),
        ('/admin/config', '/api/admin/config'),
    ]
    
    for old_path, expected_new_path in test_paths:
        response = client.get(old_path, follow_redirects=False)
        print(f"GET {old_path} -> Status: {response.status_code}, Location: {response.headers.get('location', 'None')}")
        assert response.status_code == 301
        assert response.headers.get('location') == expected_new_path
    
    print("✅ All redirect tests passed!")

if __name__ == "__main__":
    asyncio.run(test_redirects())