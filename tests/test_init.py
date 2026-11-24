"""Integration-level tests."""

from dataclasses import replace
from types import SimpleNamespace
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sncf_trajets.api import parse_journeys
from custom_components.sncf_trajets.card import _DONE, async_register_card
from custom_components.sncf_trajets.const import CARD_URL, DOMAIN, VERSION

from .navitia import at, journey, response
from .test_notifier import DATA

GET_TRAINS = "custom_components.sncf_trajets.api.SncfApiClient.get_trains"


@pytest.fixture(autouse=True)
def _no_uic_lookup():
    """Fake stop_area ids have no UIC: never hit the network for it."""
    with patch("custom_components.sncf_trajets.api.SncfApiClient.get_uic", AsyncMock(return_value=None)):
        yield


def sample():
    ts = parse_journeys(response([journey("17714", at(7, 42)), journey("17716", at(8, 12), delay=7), journey("17718", at(8, 42))]))
    ts[2] = replace(ts[2], cancelled=True, cause="Mouvement social")
    return ts


async def setup_entry(hass, trains):
    entry = MockConfigEntry(domain=DOMAIN, title="La Verpillière → Lyon Part-Dieu", data={**DATA, "notify": []}, unique_id="u", entry_id="e1")
    entry.add_to_hass(hass)
    with patch(GET_TRAINS, AsyncMock(return_value=trains)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def eid(hass, unique_id, domain="sensor"):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


async def test_entities(hass, freezer):
    freezer.move_to(at(7, 35))  # inside the 07:30-09:30 window
    await setup_entry(hass, sample())

    nxt = hass.states.get(eid(hass, "e1_next_train"))
    assert nxt.attributes["from_name"] == "La Verpillière"
    assert nxt.attributes["to_name"] == "Lyon Part-Dieu"
    assert [t["number"] for t in nxt.attributes["trains"]] == ["17714", "17716", "17718"]
    assert nxt.attributes["disruptions"] == ["Mouvement social"]
    assert nxt.attributes["is_future_window"] is False
    assert nxt.attributes["stale"] is False

    assert hass.states.get(eid(hass, "e1_train_1")).state == "on_time"
    assert hass.states.get(eid(hass, "e1_train_2")).state == "delayed"
    assert hass.states.get(eid(hass, "e1_train_3")).state == "cancelled"
    assert hass.states.get(eid(hass, "e1_train_2")).attributes["delay_minutes"] == 7
    assert hass.states.get(eid(hass, "e1_disruption", "binary_sensor")).state == "on"


async def test_next_train_timestamp(hass, freezer):
    freezer.move_to(at(7, 0))
    await setup_entry(hass, sample())
    state = hass.states.get(eid(hass, "e1_next_train")).state
    assert dt_util.parse_datetime(state) == at(7, 42)


async def test_no_train(hass, freezer):
    freezer.move_to(at(7, 0))
    await setup_entry(hass, [])
    assert hass.states.get(eid(hass, "e1_next_train")).state == "unknown"
    assert hass.states.get(eid(hass, "e1_train_1")).state == "unknown"
    assert hass.states.get(eid(hass, "e1_disruption", "binary_sensor")).state == "off"


async def test_unload(hass, freezer):
    freezer.move_to(at(7, 0))
    entry = await setup_entry(hass, sample())
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


def fake_lovelace(hass, items):
    resources = SimpleNamespace(
        loaded=True,
        async_items=lambda: items,
        async_create_item=AsyncMock(),
        async_update_item=AsyncMock(),
    )
    hass.data[LOVELACE_DATA] = SimpleNamespace(resource_mode="storage", resources=resources)
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    return resources


async def test_card_resource_created_updated_idempotent(hass):
    url = f"{CARD_URL}?v={VERSION}"
    res = fake_lovelace(hass, [])
    await async_register_card(hass)
    res.async_create_item.assert_awaited_once_with({"res_type": "module", "url": url})
    await async_register_card(hass)
    res.async_create_item.assert_awaited_once()

    hass.data.pop(_DONE)
    res = fake_lovelace(hass, [{"id": "r1", "url": f"{CARD_URL}?v=0.0.1"}])
    await async_register_card(hass)
    res.async_update_item.assert_awaited_once_with("r1", {"res_type": "module", "url": url})
    res.async_create_item.assert_not_awaited()


async def test_card_failure_does_not_block_setup(hass, freezer):
    freezer.move_to(at(7, 35))
    res = fake_lovelace(hass, [])
    res.async_create_item.side_effect = RuntimeError("boom")
    await async_register_card(hass)  # does not raise
    hass.data.pop(_DONE)
    await setup_entry(hass, sample())
    assert hass.states.get(eid(hass, "e1_next_train")) is not None


async def test_static_path_failure_does_not_set_flag(hass):
    fake_lovelace(hass, [])
    hass.http.async_register_static_paths.side_effect = RuntimeError("boom")
    await async_register_card(hass)
    assert _DONE not in hass.data


async def test_options_update_reloads(hass, freezer):
    freezer.move_to(at(7, 35))
    entry = await setup_entry(hass, sample())
    assert eid(hass, "e1_train_3") is not None
    with patch(GET_TRAINS, AsyncMock(return_value=sample())):
        hass.config_entries.async_update_entry(entry, options={"count": 2})
        await hass.async_block_till_done()
    assert hass.states.get(eid(hass, "e1_train_2")).state != "unavailable"
    assert eid(hass, "e1_train_3") is None  # orphaned registry entry removed
    assert hass.states.get("sensor.la_verpilliere_lyon_part_dieu_train_3") is None
    assert hass.states.get(eid(hass, "e1_train_1")).state != "unavailable"


RESOLVE = "custom_components.sncf_trajets.api.SncfApiClient.resolve_legacy_stop_area"
NEW_A = "stop_area:SNC:FR::LMU:aaaa:"
NEW_B = "stop_area:SNC:FR::LMU:bbbb:"


async def test_setup_migrates_legacy_station_ids(hass, freezer):
    """Ids stop_area:SNCF:<UIC> stopped working in Nov. 2026: rewrite them at setup."""
    freezer.move_to(at(7, 35))
    entry = MockConfigEntry(
        domain=DOMAIN, title="t", unique_id="u", entry_id="e1",
        data={**DATA, "notify": [], "from_id": "stop_area:SNCF:87723395", "to_id": "stop_area:SNCF:87723197"},
    )
    entry.add_to_hass(hass)
    mapping = {"stop_area:SNCF:87723395": NEW_A, "stop_area:SNCF:87723197": NEW_B}
    get_trains = AsyncMock(return_value=sample())
    with patch(RESOLVE, AsyncMock(side_effect=lambda sid: mapping[sid])), patch(GET_TRAINS, get_trains):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.data["from_id"] == NEW_A
    assert entry.data["to_id"] == NEW_B
    assert entry.data["from_uic"] == "87723395"
    assert get_trains.await_args.args[:2] == (NEW_A, NEW_B)
    assert hass.states.get(eid(hass, "e1_train_1")).state == "on_time"


async def test_setup_legacy_resolution_failure_retries(hass, freezer):
    from custom_components.sncf_trajets.api import SncfApiError

    freezer.move_to(at(7, 35))
    entry = MockConfigEntry(
        domain=DOMAIN, title="t", unique_id="u", entry_id="e1",
        data={**DATA, "notify": [], "from_id": "stop_area:SNCF:87723395"},
    )
    entry.add_to_hass(hass)
    with patch(RESOLVE, AsyncMock(side_effect=SncfApiError("HTTP 500"))), patch(GET_TRAINS, AsyncMock(return_value=[])):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.data["from_id"] == "stop_area:SNCF:87723395"


async def test_setup_current_ids_untouched(hass, freezer):
    freezer.move_to(at(7, 35))
    resolve = AsyncMock(return_value=None)
    with patch(RESOLVE, resolve):
        entry = await setup_entry(hass, sample())
    assert entry.data["from_id"] == "stop_area:A"
    assert "from_uic" not in entry.data
