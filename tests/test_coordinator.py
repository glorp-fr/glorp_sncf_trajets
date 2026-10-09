"""Tests for the coordinator."""

from dataclasses import replace
from datetime import timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sncf_trajets.api import SncfApiError, SncfAuthError, parse_journeys
from custom_components.sncf_trajets.boards import BoardError
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
    boards = MagicMock()
    boards.get_platforms = AsyncMock(return_value={})
    return SncfTrajetCoordinator(hass, entry, client, notifier, boards), client, notifier


@pytest.fixture
def setup_uic(hass):
    """Coordinator on a real-looking stop_area id (UIC available)."""
    entry = MockConfigEntry(
        domain=DOMAIN, title="A → B", data={**DATA, "from_id": "stop_area:SNCF:87723395"}, entry_id="e2"
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.get_trains = AsyncMock()
    notifier = MagicMock()
    notifier.async_process = AsyncMock()
    boards = MagicMock()
    boards.get_platforms = AsyncMock(return_value={})
    return SncfTrajetCoordinator(hass, entry, client, notifier, boards), client, boards, notifier


def board(*entries):
    """{(number, base departure): track} as parse_board returns it (UTC keys)."""
    return {(n, at(h, m).astimezone(timezone.utc)): track for n, h, m, track in entries}


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


async def test_no_board_call_outside_lead(setup_uic, freezer):
    coord, client, boards, _ = setup_uic
    freezer.move_to(at(7, 31))
    client.get_trains.return_value = trains(("B", 7, 42, 0))
    data = await coord._async_update_data()
    boards.get_platforms.assert_not_awaited()
    assert data.trains[0].platform is None


async def test_due_train_gets_platform(setup_uic, freezer):
    coord, client, boards, notifier = setup_uic
    freezer.move_to(at(7, 33))
    client.get_trains.return_value = trains(("B", 7, 42, 0), ("C", 8, 12, 0))
    boards.get_platforms.return_value = board(("B", 7, 42, "B"), ("C", 8, 12, "A"))
    data = await coord._async_update_data()
    boards.get_platforms.assert_awaited_once_with("87723395")
    assert [t.platform for t in data.trains] == ["B", None]  # C not due yet
    passed = notifier.async_process.await_args.args[0]
    assert passed[0].platform == "B"
    assert data.trains[0].as_dict()["platform"] == "B"


async def test_platform_change_and_kept_when_missing(setup_uic, freezer):
    coord, client, boards, _ = setup_uic
    client.get_trains.return_value = trains(("B", 7, 42, 0))
    freezer.move_to(at(7, 33))
    boards.get_platforms.return_value = board(("B", 7, 42, "B"))
    await coord._async_update_data()
    freezer.move_to(at(7, 35))
    boards.get_platforms.return_value = board(("B", 7, 42, "A"))
    assert (await coord._async_update_data()).trains[0].platform == "A"
    freezer.move_to(at(7, 37))
    boards.get_platforms.return_value = {}
    assert (await coord._async_update_data()).trains[0].platform == "A"


async def test_board_error_keeps_platform_not_stale(setup_uic, freezer):
    coord, client, boards, _ = setup_uic
    client.get_trains.return_value = trains(("B", 7, 42, 0))
    freezer.move_to(at(7, 33))
    boards.get_platforms.return_value = board(("B", 7, 42, "B"))
    await coord._async_update_data()
    freezer.move_to(at(7, 35))
    boards.get_platforms.side_effect = BoardError("HTTP 403")
    data = await coord._async_update_data()
    assert data.trains[0].platform == "B"
    assert data.stale is False
    assert coord.update_interval == timedelta(minutes=2)


async def test_window_follows_real_departure(setup_uic, freezer):
    coord, client, boards, _ = setup_uic
    # 7:42 + 20 min delay -> leaves 8:02, lead window opens 7:52
    client.get_trains.return_value = trains(("B", 7, 42, 20))
    boards.get_platforms.return_value = board(("B", 7, 42, "B"))
    freezer.move_to(at(7, 40))
    assert (await coord._async_update_data()).trains[0].platform is None
    boards.get_platforms.assert_not_awaited()
    freezer.move_to(at(7, 53))
    assert (await coord._async_update_data()).trains[0].platform == "B"


async def test_cancelled_train_not_due(setup_uic, freezer):
    coord, client, boards, _ = setup_uic
    freezer.move_to(at(7, 35))
    client.get_trains.return_value = [replace(t, cancelled=True) for t in trains(("B", 7, 42, 0))]
    await coord._async_update_data()
    boards.get_platforms.assert_not_awaited()


async def test_no_uic_no_board_call(setup, freezer):
    coord, client, _ = setup
    freezer.move_to(at(7, 35))
    client.get_trains.return_value = trains(("B", 7, 42, 0))
    await coord._async_update_data()
    coord.boards.get_platforms.assert_not_awaited()
