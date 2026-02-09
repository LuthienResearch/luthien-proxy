"""Demo policy: Block pip install, require uv instead."""

from __future__ import annotations

from typing import ClassVar

from luthien_proxy.policies.simple_judge_policy import SimpleJudgePolicy


class PipBlockPolicy(SimpleJudgePolicy):
    """Blocks pip install commands — use uv instead."""

    RULES: ClassVar[list[str]] = [
        "Block any 'pip install' or 'pip3 install' commands. Users should use 'uv add' or 'uv pip install' instead.",
        "Block 'python -m pip install' commands.",
        "Allow all other tool calls.",
    ]


__all__ = ["PipBlockPolicy"]
