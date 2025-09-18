from luthien_proxy.control_plane.utils.streaming import extract_delta_text


def test_extract_delta_text_happy_path():
    chunk = {
        "choices": [
            {"delta": {"content": "Hello"}},
            {"delta": {"content": ", world"}},
        ]
    }
    assert extract_delta_text(chunk) == "Hello, world"


def test_extract_delta_text_handles_missing():
    assert extract_delta_text({}) == ""
    assert extract_delta_text({"choices": []}) == ""
    assert extract_delta_text({"choices": [{"delta": {}}]}) == ""
    # Non-dict choice and non-dict delta
    assert extract_delta_text({"choices": ["x", {"delta": 1}]}) == ""
