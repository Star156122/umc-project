"""Pure helpers for the preregistered one-bar breakout confirmation experiment."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

BAR_SECONDS = 300
TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class ConfirmationResult:
    status: str
    setup_timestamp: int
    confirmation_timestamp: int | None
    entry_timestamp: int | None
    breakout_line: float
    confirmation_close: float | None


def _same_session_day(first: int, second: int) -> bool:
    return datetime.fromtimestamp(int(first), TAIPEI).date() == datetime.fromtimestamp(int(second), TAIPEI).date()


def evaluate_confirmation(
    *, setup_timestamp: int, confirmation_timestamp: int | None,
    entry_timestamp: int | None, breakout_line: float,
    confirmation_close: float | None,
) -> ConfirmationResult:
    """Evaluate only the next completed bar and its immediately following fill bar.

    No future row is searched or substituted. Timestamps are the stored 5-minute
    bar-start timestamps; the entry timestamp must be the actual next bar open.
    """
    setup = int(setup_timestamp)
    line = float(breakout_line)
    if confirmation_timestamp is None:
        return ConfirmationResult("NO_CONFIRMATION_BAR", setup, None, entry_timestamp, line, confirmation_close)
    confirm = int(confirmation_timestamp)
    if confirm - setup != BAR_SECONDS or not _same_session_day(setup, confirm):
        return ConfirmationResult("NONCONSECUTIVE_CONFIRMATION_BAR", setup, confirm, entry_timestamp, line, confirmation_close)
    if confirmation_close is None:
        return ConfirmationResult("INVALID_CONFIRMATION_CLOSE", setup, confirm, entry_timestamp, line, None)
    close = float(confirmation_close)
    if not close > line:
        return ConfirmationResult("BLOCKED_CLOSE_NOT_ABOVE_BREAKOUT_LINE", setup, confirm, entry_timestamp, line, close)
    if entry_timestamp is None:
        return ConfirmationResult("NO_NEXT_ENTRY_BAR", setup, confirm, None, line, close)
    entry = int(entry_timestamp)
    if entry - confirm != BAR_SECONDS or not _same_session_day(confirm, entry):
        return ConfirmationResult("NONCONSECUTIVE_ENTRY_BAR", setup, confirm, entry, line, close)
    return ConfirmationResult("CONFIRMED_NEXT_BAR_ENTRY_AVAILABLE", setup, confirm, entry, line, close)
