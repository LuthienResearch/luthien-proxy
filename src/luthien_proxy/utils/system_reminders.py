"""Strip Claude Code ``<system-reminder>`` blocks from user text."""

from __future__ import annotations

_OPEN = "<system-reminder>"
_CLOSE = "</system-reminder>"


def strip_system_reminders(text: str) -> str:
    """Remove ``<system-reminder>...</system-reminder>`` spans in linear time.

    An unterminated opening tag and everything after it are kept as-is.
    """
    out: list[str] = []
    pos = 0
    while (start := text.find(_OPEN, pos)) != -1:
        end = text.find(_CLOSE, start + len(_OPEN))
        if end == -1:
            break
        out.append(text[pos:start])
        pos = end + len(_CLOSE)
    out.append(text[pos:])
    return "".join(out)
