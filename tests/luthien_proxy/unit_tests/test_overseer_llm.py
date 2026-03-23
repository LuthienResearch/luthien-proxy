from scripts.overseer.overseer_llm import build_analysis_prompt, parse_overseer_response
from scripts.overseer.stream_parser import TurnSummary


def test_build_analysis_prompt_includes_turn_summary():
    summary = TurnSummary(
        turn_number=3,
        session_id="s1",
        is_success=True,
        tools_used=["Read", "Bash"],
        tool_call_count=2,
        tool_result_count=2,
        cost_usd=0.03,
        duration_seconds=5.0,
        result_text="I read the files.",
        anomalies=[],
        num_turns_reported=3,
    )
    prompt = build_analysis_prompt(summary, task="Build a calculator")
    assert "Turn 3" in prompt
    assert "Read" in prompt
    assert "Build a calculator" in prompt


def test_build_analysis_prompt_includes_anomalies():
    summary = TurnSummary(
        turn_number=1,
        session_id="s1",
        is_success=False,
        tools_used=[],
        tool_call_count=0,
        tool_result_count=0,
        cost_usd=0.0,
        duration_seconds=120.0,
        result_text="Error: connection refused",
        anomalies=["Slow turn: 120.0s", "Error result: connection refused"],
        num_turns_reported=0,
    )
    prompt = build_analysis_prompt(summary, task="Build a calculator")
    assert "ANOMALIES" in prompt or "anomalies" in prompt.lower()
    assert "Slow turn" in prompt


def test_parse_overseer_response_extracts_fields():
    response_text = """## Analysis
The turn completed successfully. No proxy issues detected.

## Anomalies
None

## Next Prompt
Now write unit tests for the calculator functions you just created."""

    result = parse_overseer_response(response_text)
    assert "unit tests" in result.next_prompt.lower()
    assert len(result.anomalies) == 0


def test_parse_overseer_response_with_anomalies():
    response_text = """## Analysis
The response appears truncated mid-sentence.

## Anomalies
- Response truncated: the assistant stopped mid-word
- Possible streaming issue

## Next Prompt
Can you continue where you left off? Please finish the implementation."""

    result = parse_overseer_response(response_text)
    assert len(result.anomalies) == 2
    assert "truncated" in result.anomalies[0].lower()
    assert "continue" in result.next_prompt.lower()
