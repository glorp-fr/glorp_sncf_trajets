"""Tests for AlertNotifier."""

from dataclasses import replace

from homeassistant.core import ServiceCall
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_mock_service,
)

from custom_components.sncf_trajets.alerts import AlertNotifier
from custom_components.sncf_trajets.api import parse_journeys
from custom_components.sncf_trajets.const import DOMAIN, EVENT_ALERT

from .navitia import at, journey, response

DATA = {
    "api_key": "k",
    "from_id": "stop_area:A", "from_name": "La Verpillière",
    "to_id": "stop_area:B", "to_name": "Lyon Part-Dieu",
    "start": "07:30:00", "end": "09:30:00",
    "weekdays": ["mon", "tue", "wed", "thu", "fri"], "count": 3,
    "notify": ["mobile_app_pixel"], "threshold": 5,
    "quiet_start": "22:00:00", "quiet_end": "06:00:00",
}


def entry(hass, **overrides):
    e = MockConfigEntry(domain=DOMAIN, title="La Verpillière → Lyon Part-Dieu", data={**DATA, **overrides}, entry_id="e1")
    e.add_to_hass(hass)
    return e


def train(delay=0, cancelled=False, number="17716"):
    (t,) = parse_journeys(response([journey(number, at(8, 12), delay=delay)]))
    return replace(t, cancelled=cancelled)


async def test_push_and_event(hass):
    calls: list[ServiceCall] = async_mock_service(hass, "notify", "mobile_app_pixel")
    events = async_capture_events(hass, EVENT_ALERT)
    n = AlertNotifier(hass, entry(hass))
    await n.async_load()
    await n.async_process([train(cancelled=True)], at(7, 0))
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["title"] == "🚆 La Verpillière → Lyon Part-Dieu"
    assert calls[0].data["message"].startswith("❌ TER 17716 08:12 SUPPRIMÉ")
    assert calls[0].data["data"]["tag"] == "sncf_17716_20261005"
    assert calls[0].data["data"]["priority"] == "high"
    assert calls[0].data["data"]["push"] == {"interruption-level": "time-sensitive"}
    assert len(events) == 1
    assert events[0].data["type"] == "cancelled"
    assert events[0].data["train_number"] == "17716"
    assert events[0].data["entry_id"] == "e1"


async def test_no_duplicate_after_reload(hass):
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    e = entry(hass)
    n = AlertNotifier(hass, e)
    await n.async_load()
    await n.async_process([train(delay=7)], at(7, 0))
    n2 = AlertNotifier(hass, e)
    await n2.async_load()
    await n2.async_process([train(delay=7)], at(7, 2))
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_quiet_hours_then_summary(hass):
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    events = async_capture_events(hass, EVENT_ALERT)
    n = AlertNotifier(hass, entry(hass))
    await n.async_load()
    await n.async_process([train(delay=7)], at(23, 0, day=4))
    await hass.async_block_till_done()
    assert calls == []
    assert len(events) == 1  # event still fired
    await n.async_process([train(delay=12)], at(6, 2))
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["message"] == "⚠️ TER 17716 08:12 +12 min"


async def test_quiet_hours_back_to_normal_no_summary(hass):
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    n = AlertNotifier(hass, entry(hass))
    await n.async_load()
    await n.async_process([train(delay=7)], at(23, 0, day=4))
    await n.async_process([train(delay=0)], at(5, 0))
    await n.async_process([train(delay=0)], at(6, 2))
    await hass.async_block_till_done()
    assert calls == []


async def test_no_notify_targets(hass):
    events = async_capture_events(hass, EVENT_ALERT)
    n = AlertNotifier(hass, entry(hass, notify=[]))
    await n.async_load()
    await n.async_process([train(delay=7)], at(7, 0))
    await hass.async_block_till_done()
    assert len(events) == 1


async def test_missing_notify_service_does_not_crash(hass, caplog):
    events = async_capture_events(hass, EVENT_ALERT)
    n = AlertNotifier(hass, entry(hass, notify=["mobile_app_gone"]))
    await n.async_load()
    await n.async_process([train(delay=7)], at(7, 0))
    await hass.async_block_till_done()
    assert len(events) == 1
    assert "mobile_app_gone" in caplog.text


async def test_stored_state_without_pending_loads(hass, hass_storage):
    hass_storage["sncf_trajets.e1"] = {"version": 1, "key": "sncf_trajets.e1", "data": {"trains": {}}}
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    n = AlertNotifier(hass, entry(hass))
    await n.async_load()
    await n.async_process([train(delay=7)], at(23, 0, day=4))  # quiet hours -> uses pending
    await n.async_process([train(delay=12)], at(6, 2))
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_state_saved_even_if_push_fails(hass, hass_storage):
    n = AlertNotifier(hass, entry(hass, notify=["mobile_app_gone"]))
    await n.async_load()
    await n.async_process([train(delay=7)], at(7, 0))
    await hass.async_block_till_done()
    assert "17716_20261005T0812" in hass_storage["sncf_trajets.e1"]["data"]["trains"]
