import pytest
from tests.luthien_proxy.e2e_tests.mock_openai.server import MockOpenAIServer


@pytest.fixture(scope="session")
def mock_openai_server():
    server = MockOpenAIServer()
    server.start()
    yield server
    server.stop()
