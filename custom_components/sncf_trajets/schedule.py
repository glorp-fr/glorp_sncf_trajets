"""Pure time helpers: travel window and polling cadence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .const import (
    INTERVAL_ACTIVE,
    INTERVAL_IDLE,
    INTERVAL_NIGHT,
    PRE_WINDOW,
)


@dataclass(frozen=True)
class Window:
    """A concrete occurrence of a travel window."""

    start: datetime
    end: datetime


def parse_time(value: str) -> time:
    """Parse 'HH:MM' or 'HH:MM:SS'."""
    return time.fromisoformat(value)


def next_window(
    now: datetime, start: time, end: time, weekdays: set[int]
) -> Window:
    """Return the running window, or the next one on an allowed weekday."""
    if not weekdays:
        raise ValueError("no weekday selected")
    tz = now.tzinfo
    today = now.date()
    for offset in range(-1, 9):
        day = today + timedelta(days=offset)
        if day.weekday() not in weekdays:
            continue
        w_start = datetime.combine(day, start, tz)
        end_day = day + timedelta(days=1) if end <= start else day
        w_end = datetime.combine(end_day, end, tz)
        if w_end > now:
            return Window(w_start, w_end)
    raise ValueError("no window found")


def poll_interval(now: datetime, window: Window) -> timedelta:
    """Return how long to wait before the next API refresh."""
    active_from = window.start - PRE_WINDOW
    if active_from <= now <= window.end:
        return INTERVAL_ACTIVE
    interval = INTERVAL_NIGHT if now.hour < 5 else INTERVAL_IDLE
    if now < active_from:
        interval = min(interval, active_from - now)
    return max(interval, INTERVAL_ACTIVE)


def in_quiet_hours(t: time, start: time, end: time) -> bool:
    """Return True if t is within [start, end), handling midnight wrap."""
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end
