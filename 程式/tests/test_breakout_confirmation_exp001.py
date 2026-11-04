from datetime import datetime
from zoneinfo import ZoneInfo

from hybrid.core.breakout_confirmation_exp001 import evaluate_confirmation

TAIPEI = ZoneInfo("Asia/Taipei")


def stamp(hour: int, minute: int) -> int:
    return int(datetime(2023, 5, 8, hour, minute, tzinfo=TAIPEI).timestamp())


def test_confirmation_requires_next_full_bar_and_next_fill_bar():
    result = evaluate_confirmation(
        setup_timestamp=stamp(10, 0), confirmation_timestamp=stamp(10, 5),
        entry_timestamp=stamp(10, 10), breakout_line=50, confirmation_close=50.1,
    )
    assert result.status == "CONFIRMED_NEXT_BAR_ENTRY_AVAILABLE"


def test_close_equal_to_breakout_line_is_blocked():
    result = evaluate_confirmation(
        setup_timestamp=stamp(10, 0), confirmation_timestamp=stamp(10, 5),
        entry_timestamp=stamp(10, 10), breakout_line=50, confirmation_close=50,
    )
    assert result.status == "BLOCKED_CLOSE_NOT_ABOVE_BREAKOUT_LINE"


def test_later_confirmation_bar_is_not_substituted_for_missing_next_bar():
    result = evaluate_confirmation(
        setup_timestamp=stamp(10, 0), confirmation_timestamp=stamp(10, 10),
        entry_timestamp=stamp(10, 15), breakout_line=50, confirmation_close=51,
    )
    assert result.status == "NONCONSECUTIVE_CONFIRMATION_BAR"


def test_confirmation_cannot_cross_sessions():
    next_day = int(datetime(2023, 5, 9, 9, 0, tzinfo=TAIPEI).timestamp())
    result = evaluate_confirmation(
        setup_timestamp=stamp(13, 25), confirmation_timestamp=next_day,
        entry_timestamp=next_day + 300, breakout_line=50, confirmation_close=51,
    )
    assert result.status == "NONCONSECUTIVE_CONFIRMATION_BAR"


def test_confirmed_close_without_actual_next_bar_cannot_fill():
    result = evaluate_confirmation(
        setup_timestamp=stamp(13, 15), confirmation_timestamp=stamp(13, 20),
        entry_timestamp=None, breakout_line=50, confirmation_close=51,
    )
    assert result.status == "NO_NEXT_ENTRY_BAR"
