"""Tests for the alert diff engine."""

from dataclasses import replace
from datetime import timedelta

from custom_components.sncf_trajets.alerts import (
    Alert,
    compute_alerts,
    format_message,
    format_summary,
    purge_state,
)
from custom_components.sncf_trajets.api import parse_journeys

from .navitia import at, journey, response


def train(delay=0, cancelled=False, number="17716", h=8, m=12, cause=None, platform=None):
    (t,) = parse_journeys(response([journey(number, at(h, m), delay=delay)]))
    return replace(t, cancelled=cancelled, cause=cause, platform=platform)


def types(alerts):
    return [a.type for a in alerts]


def test_first_seen_on_time_no_alert():
    state = {}
    assert compute_alerts(state, [train()], 5) == []
    assert state["17716_20261005T0812"]["notified_delay"] == 0


def test_first_seen_already_delayed():
    assert types(compute_alerts({}, [train(delay=7)], 5)) == ["delay"]


def test_below_threshold_no_alert():
    assert compute_alerts({}, [train(delay=4)], 5) == []


def test_delay_then_changed_then_on_time():
    state = {}
    assert types(compute_alerts(state, [train(delay=7)], 5)) == ["delay"]
    assert compute_alerts(state, [train(delay=9)], 5) == []  # +2 < step
    assert types(compute_alerts(state, [train(delay=15)], 5)) == ["delay_changed"]
    assert types(compute_alerts(state, [train(delay=10)], 5)) == ["delay_changed"]
    assert types(compute_alerts(state, [train(delay=2)], 5)) == ["on_time"]
    assert compute_alerts(state, [train(delay=0)], 5) == []


def test_cancel_then_restore():
    state = {}
    assert types(compute_alerts(state, [train(cancelled=True)], 5)) == ["cancelled"]
    assert compute_alerts(state, [train(cancelled=True)], 5) == []
    assert types(compute_alerts(state, [train(delay=8)], 5)) == ["restored"]
    assert compute_alerts(state, [train(delay=8)], 5) == []
    assert types(compute_alerts(state, [train(delay=0)], 5)) == ["on_time"]


def test_purge_old_trains():
    state = {}
    compute_alerts(state, [train()], 5)
    assert purge_state(state, at(12, 0)) is False
    assert purge_state(state, at(21, 0)) is True
    assert state == {}


def test_messages():
    t_ok = train(number="17718", h=8, m=42)
    t = train(delay=7, cause="Panne de signalisation")
    assert format_message(Alert("delay", t), [t]) == "⚠️ TER 17716 08:12 → +7 min (départ 08:19). Panne de signalisation"
    assert format_message(Alert("delay_changed", t), [t]) == "⚠️ TER 17716 08:12 → maintenant +7 min (départ 08:19)"
    assert format_message(Alert("on_time", t), [t]) == "✅ TER 17716 08:12 de nouveau à l'heure"
    assert format_message(Alert("restored", t), [t]) == "✅ TER 17716 08:12 rétabli"
    c = train(cancelled=True, cause="Mouvement social")
    assert format_message(Alert("cancelled", c), [c, t_ok]) == "❌ TER 17716 08:12 SUPPRIMÉ. Mouvement social. Prochain train : 08:42"
    c2 = train(cancelled=True)
    assert format_message(Alert("cancelled", c2), [c2]) == "❌ TER 17716 08:12 SUPPRIMÉ"


def test_summary():
    assert format_summary([train(), train(delay=2, number="1")], 5) is None
    text = format_summary([train(delay=7), train(cancelled=True, number="17718", m=42)], 5)
    assert text == "⚠️ TER 17716 08:12 +7 min\n❌ TER 17718 08:42 supprimé"


def test_platform_first_seen_next_train_only():
    state = {}
    a, b = train(platform="B"), train(number="17718", m=20, platform="A")
    assert [(x.type, x.train.number) for x in compute_alerts(state, [a, b], 5)] == [("platform", "17716")]
    assert compute_alerts(state, [a, b], 5) == []
    # 17716 left: 17718 becomes next and gets its push now
    assert [(x.type, x.train.number) for x in compute_alerts(state, [b], 5)] == [("platform", "17718")]


def test_platform_next_skips_cancelled_first_train():
    alerts = compute_alerts({}, [train(cancelled=True, platform="B"), train(number="17718", m=20, platform="A")], 5)
    assert [(x.type, x.train.number) for x in alerts] == [("cancelled", "17716"), ("platform", "17718")]


def test_platform_modes_all_and_off():
    ts = [train(platform="B"), train(number="17718", m=20, platform="A")]
    assert types(compute_alerts({}, ts, 5, "all")) == ["platform", "platform"]
    assert compute_alerts({}, ts, 5, "off") == []


def test_platform_changed_always_emitted():
    state = {}
    compute_alerts(state, [train(platform="B")], 5, "off")
    alerts = compute_alerts(state, [train(platform="A")], 5, "off")
    assert types(alerts) == ["platform_changed"]
    assert alerts[0].previous_platform == "B"
    assert compute_alerts(state, [train(platform="A")], 5, "next") == []  # no late "platform" push after a change


def test_platform_change_on_non_next_train():
    state = {}
    first, other = train(platform="B"), train(number="17718", m=20, platform="A")
    compute_alerts(state, [first, other], 5)
    alerts = compute_alerts(state, [first, replace(other, platform="C")], 5)
    assert [(x.type, x.train.number) for x in alerts] == [("platform_changed", "17718")]


def test_platform_with_delay_both_alerts():
    assert types(compute_alerts({}, [train(delay=7, platform="B")], 5)) == ["delay", "platform"]


def test_cancelled_train_no_platform_alert():
    state = {}
    compute_alerts(state, [train(platform="B")], 5)
    assert types(compute_alerts(state, [train(cancelled=True, platform="A")], 5)) == ["cancelled"]


def test_old_state_without_platform_keys():
    state = {"17716_20261005T0812": {"notified_delay": 0, "cancelled": False, "base_departure": at(8, 12).isoformat()}}
    assert types(compute_alerts(state, [train(platform="B")], 5)) == ["platform"]


def test_platform_messages():
    t = train(platform="B")
    assert format_message(Alert("platform", t), [t]) == "🛤️ TER 17716 08:12 → voie B"
    t2 = train(platform="A")
    assert format_message(Alert("platform_changed", t2, previous_platform="B"), [t2]) == (
        "⚠️ TER 17716 08:12 : changement de voie B → A"
    )


def test_platform_next_is_earliest_real_departure():
    a = train(delay=30, number="A", h=7, m=42, platform="B")  # leaves 08:12
    b = train(number="B", h=7, m=50, platform="A")  # leaves 07:50
    alerts = compute_alerts({}, [a, b], 99)
    assert [(x.type, x.train.number) for x in alerts] == [("platform", "B")]
