"""Tests for the coordinator."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sncf_trajets.api import SncfApiError, SncfAuthError, parse_journeys
from custom_components.sncf_trajets.const import DOMAIN
from custom_components.sncf_trajets.coordinator import SncfTrajetCoordinator

from .navitia import at, journey, response
from .test_notifier import DATA


def trains(*spec):
    return parse_journeys(response([journey(n, at(h, m), delay=d) for n, h, m, d in spec]))


@pytest.fixture
def setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="A → B", data=DATA, entry_id="e1")
    entry.add_to_hass(hass)
    client = MagicMock()
    client.get_trains = AsyncMock()
    notifier = MagicMock()
    notifier.async_process = AsyncMock()
    return SncfTrajetCoordinator(hass, entry, client, notifier), client, notifier


async def test_future_window_filtered_and_limited(setup, freezer):
    coord, client, notifier = setup
    freezer.move_to(at(14, 0, day=9))  # Friday afternoon -> Monday 12th
    client.get_trains.return_value = parse_journeys(response([
        journey(n, at(h, m, day=12))
        for n, h, m in [("A", 7, 0), ("B", 7, 42), ("C", 8, 12), ("D", 8, 42), ("E", 9, 12)]
    ]))
    data = await coord._async_update_data()
    args = client.get_trains.await_args.args
    assert args[0] == "stop_area:A" and args[1] == "stop_area:B"
    assert args[2] == at(7, 30, day=12)
    assert args[3] == 6
    assert [t.number for t in data.trains] == ["B", "C", "D"]
    assert data.is_future_window is True
    assert coord.update_interval == timedelta(minutes=15)
    notifier.async_process.assert_awaited_once()


async def test_inside_window_drops_departed_trains(setup, freezer):
    coord, client, _ = setup
    freezer.move_to(at(8, 0))
    client.get_trains.return_value = trains(("B", 7, 42, 0), ("Late", 7, 50, 15), ("C", 8, 12, 0))
    data = await coord._async_update_data()
    assert client.get_trains.await_args.args[2] == at(7, 30)
    assert [t.number for t in data.trains] == ["Late", "C"]
    assert data.is_future_window is False
    assert coord.update_interval == timedelta(minutes=2)


async def test_disruptions_deduplicated(setup, freezer):
    coord, client, _ = setup
    freezer.move_to(at(7, 0))
    ts = trains(("B", 7, 42, 0), ("C", 8, 12, 0))
    client.get_trains.return_value = [replace(t, cause="Grève") for t in ts]
    data = await coord._async_update_data()
    assert data.disruptions == ["Grève"]


async def test_auth_error(setup, freezer):
    coord, client, _ = setup
    freezer.move_to(at(7, 0))
    client.get_trains.side_effect = SncfAuthError("401")
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_api_error_first_refresh_raises(setup, freezer):
    coord, client, _ = setup
    freezer.move_to(at(7, 0))
    client.get_trains.side_effect = SncfApiError("boom")
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


async def test_api_error_keeps_stale_data_and_backs_off(setup, freezer):
    coord, client, notifier = setup
    freezer.move_to(at(7, 0))
    client.get_trains.return_value = trains(("B", 7, 42, 0))
    coord.data = await coord._async_update_data()
    client.get_trains.side_effect = SncfApiError("boom")
    data = await coord._async_update_data()
    assert data.stale is True
    assert [t.number for t in data.trains] == ["B"]
    assert coord.update_interval == timedelta(minutes=4)
    coord.data = data
    await coord._async_update_data()
    assert coord.update_interval == timedelta(minutes=8)
    assert notifier.async_process.await_count == 1  # no alerts on stale data


async def test_notifier_failure_does_not_break_update(setup, freezer, caplog):
    coord, client, notifier = setup
    freezer.move_to(at(7, 0))
    client.get_trains.return_value = trains(("B", 7, 42, 0))
    notifier.async_process.side_effect = RuntimeError("store broken")
    data = await coord._async_update_data()
    assert [t.number for t in data.trains] == ["B"]
    assert "Échec du traitement des alertes" in caplog.text


async def test_stale_data_drops_departed_trains(setup, freezer):
    coord, client, _ = setup
    freezer.move_to(at(7, 0))
    ts = trains(("B", 7, 42, 0), ("C", 8, 12, 0))
    client.get_trains.return_value = [replace(ts[0], cause="Vieille cause"), replace(ts[1], cause="Grève")]
    coord.data = await coord._async_update_data()
    assert coord.data.disruptions == ["Vieille cause", "Grève"]
    freezer.move_to(at(8, 0))
    client.get_trains.side_effect = SncfApiError("boom")
    data = await coord._async_update_data()
    assert data.stale is True
    assert [t.number for t in data.trains] == ["C"]
    assert data.disruptions == ["Grève"]
