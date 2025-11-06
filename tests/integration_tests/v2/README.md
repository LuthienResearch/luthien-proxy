# V2 Integration Tests

These tests exercise the FastAPI gateway backed by the new queue-based `PolicyOrchestrator`.

Currently `test_gateway_streaming.py` issues requests against an in-process app created via
`luthien_proxy.v2.main.create_app` to validate streaming and non-streaming responses end-to-end.
