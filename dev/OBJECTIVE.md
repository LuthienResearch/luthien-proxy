# Objective: Demo Web Interface

Build a web interface for the AI Control demo that showcases the system preventing harmful SQL operations.

## Requirements

1. **Demo UI Page** at `/ui/demo` that:
   - Displays a side-by-side comparison of "Without AI Control" vs "With AI Control"
   - Shows the user's benign prompt ("Show me customer 123")
   - Displays the AI's response in each scenario (harmful SQL vs blocked)
   - Has visual indicators for blocked vs allowed operations
   - Includes live updates when running the demo

2. **Integration with existing demo script**:
   - Leverage the `/api/hooks/conversation` SSE endpoint for live updates
   - Show conversation flow in real-time as demo runs
   - Display policy decisions and their reasoning

3. **Clean, demo-ready presentation**:
   - Clear visual distinction between harmful and protected scenarios
   - Professional styling suitable for presentations
   - Responsive layout that works on projector/large screens

## Acceptance Criteria

- [ ] Navigate to `http://localhost:8081/ui/demo` and see the demo interface
- [ ] Run `uv run python scripts/run_demo.py` and watch live updates in the UI
- [ ] See clear visual indicators showing blocked vs allowed operations
- [ ] Interface is polished enough for live demonstrations
