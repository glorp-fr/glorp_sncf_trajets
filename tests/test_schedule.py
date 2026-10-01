"""Tests for schedule helpers."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.sncf_trajets.schedule import (
    Window,
    in_quiet_hours,
    next_window,
    parse_time,
    poll_interval,
)

TZ = ZoneInfo("Europe/Paris")
WEEK = {0, 1, 2, 3, 4}
S, E = time(7, 30), time(9, 30)


def dt(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def test_parse_time():
    assert parse_time("07:30:00") == time(7, 30)
    assert parse_time("07:30") == time(7, 30)


def test_window_before_start_same_day():
    # 2026-10-05 is a Monday
    w = next_window(dt(2026, 10, 5, 6, 0), S, E, WEEK)
    assert w == Window(dt(2026, 10, 5, 7, 30), dt(2026, 10, 5, 9, 30))


def test_window_inside():
    w = next_window(dt(2026, 10, 5, 8, 0), S, E, WEEK)
    assert w.start == dt(2026, 10, 5, 7, 30)


def test_window_after_end_goes_next_day():
    w = next_window(dt(2026, 10, 5, 14, 0), S, E, WEEK)
    assert w.start == dt(2026, 10, 6, 7, 30)


def test_window_friday_afternoon_goes_monday():
    w = next_window(dt(2026, 10, 9, 14, 0), S, E, WEEK)
    assert w.start == dt(2026, 10, 12, 7, 30)


def test_window_across_midnight():
    start, end = time(23, 0), time(1, 0)
    # Monday 00:30: window started Sunday 23:00 but Sunday excluded -> Monday 23:00
    w = next_window(dt(2026, 10, 5, 0, 30), start, end, WEEK)
    assert w == Window(dt(2026, 10, 5, 23, 0), dt(2026, 10, 6, 1, 0))
    # Tuesday 00:30: window from Monday 23:00 still running
    w = next_window(dt(2026, 10, 6, 0, 30), start, end, WEEK)
    assert w == Window(dt(2026, 10, 5, 23, 0), dt(2026, 10, 6, 1, 0))


def test_window_dst_change():
    # 2026-10-25: switch from CEST (+02) to CET (+01) at 03:00
    w = next_window(dt(2026, 10, 24, 12, 0), S, E, {6})
    assert w.start.hour == 7 and w.start.minute == 30
    assert w.start.utcoffset() == timedelta(hours=1)


def test_poll_active():
    w = Window(dt(2026, 10, 5, 7, 30), dt(2026, 10, 5, 9, 30))
    assert poll_interval(dt(2026, 10, 5, 6, 45), w) == timedelta(minutes=2)
    assert poll_interval(dt(2026, 10, 5, 9, 0), w) == timedelta(minutes=2)


def test_poll_idle_and_night():
    w = Window(dt(2026, 10, 6, 7, 30), dt(2026, 10, 6, 9, 30))
    assert poll_interval(dt(2026, 10, 5, 14, 0), w) == timedelta(minutes=15)
    assert poll_interval(dt(2026, 10, 6, 2, 0), w) == timedelta(minutes=60)


def test_poll_never_overshoots_pre_window():
    w = Window(dt(2026, 10, 6, 5, 30), dt(2026, 10, 6, 7, 0))
    # 04:00 night -> would be 60 min, but pre-window starts 04:30
    assert poll_interval(dt(2026, 10, 6, 4, 0), w) == timedelta(minutes=30)
    # 04:29 -> 1 min left, floor to 2 min
    assert poll_interval(dt(2026, 10, 6, 4, 29), w) == timedelta(minutes=2)


def test_quiet_hours():
    q1, q2 = time(22, 0), time(6, 0)
    assert in_quiet_hours(time(23, 0), q1, q2)
    assert in_quiet_hours(time(5, 59), q1, q2)
    assert not in_quiet_hours(time(6, 0), q1, q2)
    assert not in_quiet_hours(time(12, 0), q1, q2)
    assert in_quiet_hours(time(13, 0), time(12, 0), time(14, 0))
    assert not in_quiet_hours(time(13, 0), time(0, 0), time(0, 0))
