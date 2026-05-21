import pytest
from tests.luthien_proxy.e2e_tests.mock_gemini.server import MockGeminiServer


@pytest.fixture(scope="session")
def mock_gemini_server():
    server = MockGeminiServer()
    server.start()
    yield server
    server.stop()
