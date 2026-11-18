import time

from luthien_proxy.utils.system_reminders import strip_system_reminders

OPEN = "<system-reminder>"
CLOSE = "</system-reminder>"


def test_text_without_tags_is_unchanged():
    assert strip_system_reminders("just a question") == "just a question"


def test_removes_one_block():
    assert strip_system_reminders(f"{OPEN}noise{CLOSE}real question") == "real question"


def test_removes_several_blocks():
    text = f"{OPEN}a{CLOSE}first {OPEN}b\nc{CLOSE}second{OPEN}{CLOSE} third"
    assert strip_system_reminders(text) == "first second third"


def test_keeps_unterminated_tag_and_the_rest():
    text = f"{OPEN}a{CLOSE}kept {OPEN}never closed"
    assert strip_system_reminders(text) == f"kept {OPEN}never closed"


def test_close_tag_without_open_is_kept():
    assert strip_system_reminders(f"text {CLOSE} more") == f"text {CLOSE} more"


def test_many_opening_tags_stay_fast():
    text = OPEN * 20_000 + "tail"
    started = time.perf_counter()
    assert strip_system_reminders(text) == text
    assert time.perf_counter() - started < 1.0
