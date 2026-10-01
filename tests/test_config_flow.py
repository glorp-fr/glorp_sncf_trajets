"""Tests for the config flow."""

from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.sncf_trajets.api import SncfAuthError, Station
from custom_components.sncf_trajets.const import DOMAIN

from .test_notifier import DATA

SEARCH = "custom_components.sncf_trajets.config_flow.SncfApiClient.search_stations"
SETUP = "custom_components.sncf_trajets.async_setup_entry"


def stations(query):
    return {
        "verp": [Station("stop_area:A", "La Verpillière")],
        "part": [Station("stop_area:B", "Lyon Part-Dieu"), Station("stop_area:C", "Lyon Perrache")],
        "test": [Station("stop_area:X", "Paris")],
        "zzz": [],
    }[query]


async def test_full_flow(hass):
    async_mock_service(hass, "notify", "mobile_app_pixel")
    with patch(SEARCH, AsyncMock(side_effect=stations)), patch(SETUP, create=True, return_value=True):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        assert r["step_id"] == "user"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"api_key": "k"})
        assert r["step_id"] == "from_station"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"query": "zzz"})
        assert r["errors"] == {"query": "no_station_found"}
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"query": "verp"})
        assert r["step_id"] == "from_pick"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"station": "stop_area:A"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"query": "part"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"station": "stop_area:B"})
        assert r["step_id"] == "schedule"
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"],
            {"start": "07:30:00", "end": "09:30:00", "weekdays": ["mon", "tue", "wed", "thu", "fri"], "count": 3},
        )
        assert r["step_id"] == "notify"
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"],
            {"notify": ["mobile_app_pixel"], "threshold": 5, "quiet_start": "22:00:00", "quiet_end": "06:00:00"},
        )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "La Verpillière → Lyon Part-Dieu"
    assert r["data"] == DATA
    assert r["result"].unique_id == "stop_area:A_stop_area:B_07:30:00_09:30:00"


async def test_invalid_key(hass):
    with patch(SEARCH, AsyncMock(side_effect=SncfAuthError)):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"api_key": "bad"})
    assert r["errors"] == {"base": "invalid_auth"}


async def test_key_reused_from_existing_entry(hass):
    MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="other").add_to_hass(hass)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["step_id"] == "from_station"


async def test_same_station_rejected(hass):
    with patch(SEARCH, AsyncMock(side_effect=stations)):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"api_key": "k"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"query": "verp"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"station": "stop_area:A"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"query": "verp"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"station": "stop_area:A"})
    assert r["step_id"] == "to_pick"
    assert r["errors"] == {"station": "same_station"}


async def test_schedule_requires_weekday(hass):
    with patch(SEARCH, AsyncMock(side_effect=stations)):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"api_key": "k"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"query": "verp"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"station": "stop_area:A"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"query": "part"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"station": "stop_area:B"})
        r = await hass.config_entries.flow.async_configure(
            r["flow_id"], {"start": "07:30:00", "end": "09:30:00", "weekdays": [], "count": 3}
        )
    assert r["errors"] == {"weekdays": "no_weekday"}


async def test_options_flow(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="u")
    entry.add_to_hass(hass)
    with patch(SETUP, create=True, return_value=True):
        r = await hass.config_entries.options.async_init(entry.entry_id)
        assert r["step_id"] == "init"
        r = await hass.config_entries.options.async_configure(
            r["flow_id"],
            {"start": "17:00:00", "end": "19:00:00", "weekdays": ["mon"], "count": 2,
             "notify": [], "threshold": 10, "quiet_start": "23:00:00", "quiet_end": "06:00:00"},
        )
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["start"] == "17:00:00"
    assert entry.options["count"] == 2


async def test_reauth(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, unique_id="u")
    entry.add_to_hass(hass)
    with patch(SEARCH, AsyncMock(side_effect=stations)), patch(SETUP, create=True, return_value=True):
        r = await entry.start_reauth_flow(hass)
        assert r["step_id"] == "reauth_confirm"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"api_key": "new"})
    assert r["type"] is FlowResultType.ABORT
    assert r["reason"] == "reauth_successful"
    assert entry.data["api_key"] == "new"
