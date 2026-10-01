"""Tests for the Navitia client."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.sncf_trajets.api import (
    SncfApiClient,
    SncfApiError,
    SncfAuthError,
    SncfQuotaError,
    confirm_suspected,
    merge_trains,
    parse_journeys,
)
from custom_components.sncf_trajets.const import API_BASE

from .navitia import at, departure, departures_response, disruption, journey, response


def test_parse_on_time_and_delayed():
    trains = parse_journeys(response([journey("17714", at(7, 42)), journey("17716", at(8, 12), delay=7)]))
    assert [t.number for t in trains] == ["17714", "17716"]
    assert trains[0].delay_minutes == 0
    assert trains[1].delay_minutes == 7
    assert trains[1].departure == at(8, 19)
    assert trains[0].id == "17714_20261005T0742"
    assert trains[0].mode == "TER"


def test_parse_linked_disruption_gives_cause_and_cancel():
    data = response(
        [journey("17718", at(8, 42), links=["d1"])],
        [disruption("d1", "NO_SERVICE", "Mouvement social")],
    )
    (train,) = parse_journeys(data)
    assert train.cancelled is True
    assert train.cause == "Mouvement social"


def test_parse_disruption_matched_by_number():
    data = response(
        [journey("17716", at(8, 12), delay=10)],
        [disruption("d2", "SIGNIFICANT_DELAYS", "Panne de signalisation", number="17716")],
    )
    (train,) = parse_journeys(data)
    assert train.cancelled is False
    assert train.cause == "Panne de signalisation"


def test_parse_skips_journeys_with_transfer():
    j = journey("1", at(7, 0))
    j["sections"].append(dict(j["sections"][1]))
    assert parse_journeys(response([j])) == []


def test_parse_empty_and_error_payload():
    assert parse_journeys({}) == []
    assert parse_journeys({"error": {"id": "no_solution"}}) == []


def test_merge_marks_missing_train_suspected_only():
    base = parse_journeys(response([journey("A", at(7, 42)), journey("B", at(8, 12)), journey("C", at(8, 42))]))
    rt = parse_journeys(response([journey("A", at(7, 42)), journey("C", at(8, 42), delay=3)]))
    merged = merge_trains(base, rt)
    assert [(t.number, t.cancelled, t.suspected_cancelled, t.delay_minutes) for t in merged] == [
        ("A", False, False, 0),
        ("B", False, True, 0),
        ("C", False, False, 3),
    ]


def test_merge_keeps_linked_no_service_cancelled():
    base = parse_journeys(response(
        [journey("A", at(7, 42), links=["d1"]), journey("B", at(8, 12))],
        [disruption("d1", "NO_SERVICE", "Grève")],
    ))
    rt = parse_journeys(response([journey("B", at(8, 12))]))
    (a, _) = merge_trains(base, rt)
    assert a.cancelled is True and a.suspected_cancelled is False


def test_merge_keeps_last_missing_train_without_proof():
    base = parse_journeys(response([journey("A", at(7, 42)), journey("B", at(8, 12))]))
    rt = parse_journeys(response([journey("A", at(7, 42))]))
    merged = merge_trains(base, rt)
    assert [(t.number, t.cancelled) for t in merged] == [("A", False), ("B", False)]


def test_merge_keeps_realtime_only_trains():
    base = parse_journeys(response([journey("A", at(7, 42))]))
    rt = parse_journeys(response([journey("A", at(7, 42)), journey("X", at(7, 50))]))
    assert [t.number for t in merge_trains(base, rt)] == ["A", "X"]


def _suspect():
    base = parse_journeys(response([journey("A", at(7, 42)), journey("B", at(8, 12)), journey("C", at(8, 42))]))
    rt = parse_journeys(response([journey("A", at(7, 42), delay=6), journey("C", at(8, 42))]))
    return base, rt


def _client(hass, base_rt, departures=None, error=None):
    client = SncfApiClient(async_get_clientsession(hass), "key")
    base, rt = base_rt

    async def fake_get(path, params):
        p = dict(params)
        if path == "journeys":
            assert p["from"] == "stop_area:A" and p["to"] == "stop_area:B"
            assert p["datetime"] == "20261005T073000"
            assert p["max_nb_transfers"] == "0"
            return base if p["data_freshness"] == "base_schedule" else rt
        assert path == "stop_areas/stop_area:A/departures"
        if error:
            raise error
        return departures

    client._get = AsyncMock(side_effect=fake_get)
    return client


BASE_PAYLOAD = response([journey("A", at(7, 42)), journey("B", at(8, 12)), journey("C", at(8, 42))])
RT_PAYLOAD = response([journey("A", at(7, 42), delay=6), journey("C", at(8, 42))])


async def _run(hass, departures=None, error=None):
    client = _client(hass, (BASE_PAYLOAD, RT_PAYLOAD), departures, error)
    trains = await client.get_trains("stop_area:A", "stop_area:B", at(7, 30), 6)
    return client, trains


async def test_get_trains_confirms_late_train_running(hass):
    deps = departures_response([departure("B", at(8, 12), delay=100)])
    client, trains = await _run(hass, deps)
    assert [(t.number, t.cancelled, t.delay_minutes) for t in trains] == [
        ("A", False, 6), ("B", False, 100), ("C", False, 0)
    ]
    assert trains[1].arrival == at(8, 35) + timedelta(minutes=100)
    assert client._get.await_count == 3
    path, params = client._get.await_args.args
    assert dict(params) == {
        "from_datetime": "20261005T081200",
        "data_freshness": "realtime",
        "duration": "21600",
        "count": "200",
    }


async def test_get_trains_absent_from_departures_is_cancelled(hass):
    _, trains = await _run(hass, departures_response([departure("X", at(8, 12))]))
    assert [(t.number, t.cancelled) for t in trains] == [("A", False), ("B", True), ("C", False)]


async def test_get_trains_no_service_link_is_cancelled(hass):
    deps = departures_response(
        [departure("B", at(8, 12), links=["d1"])],
        [disruption("d1", "NO_SERVICE", "Mouvement social")],
    )
    _, trains = await _run(hass, deps)
    assert trains[1].cancelled is True
    assert trains[1].cause == "Mouvement social"


async def test_get_trains_confirmation_error_raises(hass):
    with pytest.raises(SncfApiError):
        await _run(hass, error=SncfApiError("boom"))


def _padded(n, last_at):
    deps = [departure(f"P{i}", at(7, 0)) for i in range(n - 1)]
    deps.append(departure("Z", last_at))
    return departures_response(deps)


async def test_get_trains_truncated_page_is_inconclusive(hass):
    with pytest.raises(SncfApiError):
        await _run(hass, _padded(200, at(8, 30)))


async def test_get_trains_full_page_but_late_last_departure_cancels(hass):
    _, trains = await _run(hass, _padded(200, at(11, 30)))
    assert [(t.number, t.cancelled) for t in trains] == [("A", False), ("B", True), ("C", False)]


async def test_get_trains_missing_departures_key_raises(hass):
    with pytest.raises(SncfApiError):
        await _run(hass, {"disruptions": []})


async def test_get_trains_no_suspect_skips_departures(hass):
    rt = response([journey("A", at(7, 42)), journey("B", at(8, 12)), journey("C", at(8, 42))])
    client = _client(hass, (BASE_PAYLOAD, rt))
    await client.get_trains("stop_area:A", "stop_area:B", at(7, 30), 6)
    assert client._get.await_count == 2


def test_confirm_suspected_ignores_other_dates_and_cause_from_disruption():
    (b,) = parse_journeys(response([journey("B", at(8, 12))]))
    suspect = replace(b, suspected_cancelled=True)
    other_day = departures_response([departure("B", at(8, 12, day=6))])
    assert confirm_suspected([suspect], other_day)[0].cancelled is True
    delayed = departures_response(
        [departure("B", at(8, 12), delay=20, links=["d"])],
        [disruption("d", "SIGNIFICANT_DELAYS", "Panne")],
    )
    (res,) = confirm_suspected([suspect], delayed)
    assert (res.cancelled, res.delay_minutes, res.cause) == (False, 20, "Panne")


async def test_unknown_404_is_error(hass, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/journeys", status=404, json={"error": {"id": "unknown_object"}})
    client = SncfApiClient(async_get_clientsession(hass), "key")
    with pytest.raises(SncfApiError):
        await client.get_trains("a", "b", at(7, 30), 3)


async def test_search_stations(hass, aioclient_mock):
    aioclient_mock.get(
        f"{API_BASE}/places",
        json={"places": [
            {"id": "stop_area:SNCF:87721175", "name": "La Verpillière (La Verpillière)", "embedded_type": "stop_area"},
            {"id": "admin:fr:38537", "name": "La Verpillière", "embedded_type": "administrative_region"},
        ]},
    )
    client = SncfApiClient(async_get_clientsession(hass), "key")
    stations = await client.search_stations("verpilliere")
    assert [(s.id, s.name) for s in stations] == [("stop_area:SNCF:87721175", "La Verpillière (La Verpillière)")]


@pytest.mark.parametrize(
    ("status", "exc"),
    [(401, SncfAuthError), (403, SncfAuthError), (429, SncfQuotaError), (500, SncfApiError)],
)
async def test_http_errors(hass, aioclient_mock, status, exc):
    aioclient_mock.get(f"{API_BASE}/places", status=status)
    client = SncfApiClient(async_get_clientsession(hass), "key")
    with pytest.raises(exc):
        await client.search_stations("x")


async def test_timeout_is_api_error(hass, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/places", exc=asyncio.TimeoutError)
    client = SncfApiClient(async_get_clientsession(hass), "key")
    with pytest.raises(SncfApiError):
        await client.search_stations("x")


async def test_no_solution_is_empty(hass, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/journeys", status=404, json={"error": {"id": "no_solution"}})
    client = SncfApiClient(async_get_clientsession(hass), "key")
    assert await client.get_trains("a", "b", at(7, 30), 3) == []


def test_parse_regionaura_mode_becomes_ter():
    """Test that REGIONAURA mode is converted to TER."""
    trains = parse_journeys(response([journey("100", at(9, 0), mode="REGIONAURA")]))
    assert len(trains) == 1
    assert trains[0].mode == "TER"


def test_parse_region_prefix_mode_becomes_ter():
    """Test that any REGION* mode is converted to TER."""
    trains = parse_journeys(response([journey("100", at(9, 0), mode="REGIONPACA")]))
    assert len(trains) == 1
    assert trains[0].mode == "TER"


def test_parse_disruption_empty_number_never_matches():
    """Test that empty train number never matches a disruption."""
    data = response(
        [journey("", at(8, 12))],
        [disruption("d1", "SIGNIFICANT_DELAYS", "Panne", number="")],
    )
    (train,) = parse_journeys(data)
    assert train.cancelled is False
    assert train.cause is None


def test_parse_disruption_partial_number_no_match():
    """Test that partial number match (1771 vs 17716) doesn't match."""
    data = response(
        [journey("17716", at(8, 12))],
        [disruption("d1", "SIGNIFICANT_DELAYS", "Panne", number="1771")],
    )
    (train,) = parse_journeys(data)
    assert train.cancelled is False
    assert train.cause is None


def test_parse_disruption_different_date_no_match():
    """Test that disruption on different date (10-06) doesn't cancel train (10-05)."""
    data = response(
        [journey("17716", at(8, 12))],
        [disruption("d1", "NO_SERVICE", "Panne", number="17716")],
    )
    # Modify disruption to have 10-06 date
    data["disruptions"][0]["impacted_objects"] = [
        {
            "pt_object": {
                "embedded_type": "trip",
                "id": "SNCF:2026-10-06:17716:1187:Train",
                "name": "SNCF:2026-10-06:17716:1187:Train"
            }
        }
    ]
    (train,) = parse_journeys(data)
    assert train.cancelled is False
    assert train.cause is None


async def test_invalid_json_is_api_error(hass, aioclient_mock):
    """Test that invalid JSON response raises SncfApiError."""
    aioclient_mock.get(f"{API_BASE}/places", status=200, text="not json")
    client = SncfApiClient(async_get_clientsession(hass), "key")
    with pytest.raises(SncfApiError):
        await client.search_stations("x")
