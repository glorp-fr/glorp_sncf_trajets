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


def train(delay=0, cancelled=False, number="17716", h=8, m=12, cause=None):
    (t,) = parse_journeys(response([journey(number, at(h, m), delay=delay)]))
    return replace(t, cancelled=cancelled, cause=cause)


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
