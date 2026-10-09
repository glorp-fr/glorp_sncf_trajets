"""Tests for the Gares & Connexions board client."""

import asyncio
from datetime import datetime, timezone

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.sncf_trajets.boards import (
    BOARD_URL,
    BoardClient,
    BoardError,
    parse_board,
    uic_from_stop_area,
)

from .navitia import PARIS

# Extrait réel, La Verpillière, 2026-10-09 (champs inutiles retirés)
SAMPLE = [
    {
        "trainNumber": "17646", "trainType": "TER",
        "scheduledTime": "2026-10-09T08:56:00+00:00", "actualTime": "2026-10-09T09:46:00+00:00",
        "platform": {"track": "B", "isTrackactive": True},
        "informationStatus": {"trainStatus": "RETARD", "delay": 50},
    },
    {
        "trainNumber": "17615", "trainType": "TER",
        "scheduledTime": "2026-10-09T09:35:00+00:00", "actualTime": "2026-10-09T09:35:00+00:00",
        "platform": {"track": "A", "isTrackactive": True},
        "informationStatus": {"trainStatus": "Ontime", "delay": None},
    },
    {
        "trainNumber": "17618", "trainType": "TER",
        "scheduledTime": "2026-10-09T10:26:00+00:00", "actualTime": "2026-10-09T10:26:00+00:00",
        "platform": {"track": None, "isTrackactive": False},
        "informationStatus": {"trainStatus": "Ontime", "delay": None},
    },
]


def test_uic_from_stop_area():
    assert uic_from_stop_area("stop_area:SNCF:87723395") == "87723395"
    assert uic_from_stop_area("stop_area:A") is None
    assert uic_from_stop_area("stop_area:SNCF:123") is None
    assert uic_from_stop_area(None) is None


def test_parse_board_real_sample():
    board = parse_board(SAMPLE)
    paris_dep = datetime(2026, 10, 9, 10, 56, tzinfo=PARIS)  # 08:56 UTC
    assert board[("17646", paris_dep)] == "B"
    assert board[("17615", datetime(2026, 10, 9, 9, 35, tzinfo=timezone.utc))] == "A"
    assert len(board) == 2  # 17618 has no track


def test_parse_board_skips_malformed_entries():
    payload = [
        None,
        "x",
        {"trainNumber": "1"},
        {"trainNumber": "2", "scheduledTime": "garbage", "platform": {"track": "C"}},
        {"trainNumber": "3", "scheduledTime": "2026-10-09T09:35:00", "platform": {"track": "C"}},  # no tz
        {"trainNumber": "4", "scheduledTime": "2026-10-09T09:35:00+00:00", "platform": None},
        {"trainNumber": 5, "scheduledTime": "2026-10-09T09:35:00+00:00", "platform": {"track": " D "}},
    ]
    assert parse_board(payload) == {("5", datetime(2026, 10, 9, 9, 35, tzinfo=timezone.utc)): "D"}


def test_parse_board_rejects_non_list():
    with pytest.raises(BoardError):
        parse_board({"url": "https://geo.captcha-delivery.com/..."})


async def test_get_platforms(hass, aioclient_mock):
    aioclient_mock.get(BOARD_URL.format(uic="87723395"), json=SAMPLE)
    board = await BoardClient(async_get_clientsession(hass)).get_platforms("87723395")
    assert len(board) == 2
    assert str(aioclient_mock.mock_calls[0][1]).endswith("/Departures/0087723395")


@pytest.mark.parametrize("status", [403, 404, 500])
async def test_http_errors(hass, aioclient_mock, status):
    aioclient_mock.get(BOARD_URL.format(uic="87723395"), status=status, json={"url": "captcha"})
    with pytest.raises(BoardError):
        await BoardClient(async_get_clientsession(hass)).get_platforms("87723395")


async def test_timeout(hass, aioclient_mock):
    aioclient_mock.get(BOARD_URL.format(uic="87723395"), exc=asyncio.TimeoutError)
    with pytest.raises(BoardError):
        await BoardClient(async_get_clientsession(hass)).get_platforms("87723395")


async def test_html_body(hass, aioclient_mock):
    aioclient_mock.get(BOARD_URL.format(uic="87723395"), text="<html>captcha</html>")
    with pytest.raises(BoardError):
        await BoardClient(async_get_clientsession(hass)).get_platforms("87723395")
