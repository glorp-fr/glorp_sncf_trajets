"""Config flow for Glorp SNCF Trajets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TimeSelector,
)

from .api import SncfApiClient, SncfApiError, SncfAuthError, Station
from .const import (
    API_KEY_URL,
    CONF_COUNT,
    CONF_END,
    CONF_FROM_ID,
    CONF_FROM_NAME,
    CONF_NOTIFY,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_START,
    CONF_THRESHOLD,
    CONF_TO_ID,
    CONF_TO_NAME,
    CONF_WEEKDAYS,
    DEFAULT_COUNT,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_THRESHOLD,
    DEFAULT_WEEKDAYS,
    DOMAIN,
    WEEKDAYS,
)

SCHEDULE_KEYS = (CONF_START, CONF_END, CONF_WEEKDAYS, CONF_COUNT)
NOTIFY_KEYS = (CONF_NOTIFY, CONF_THRESHOLD, CONF_QUIET_START, CONF_QUIET_END)


def _schedule_schema(d: Mapping[str, Any]) -> dict:
    return {
        vol.Required(CONF_START, default=d.get(CONF_START, "07:30:00")): TimeSelector(),
        vol.Required(CONF_END, default=d.get(CONF_END, "09:30:00")): TimeSelector(),
        vol.Required(CONF_WEEKDAYS, default=d.get(CONF_WEEKDAYS, DEFAULT_WEEKDAYS)): SelectSelector(
            SelectSelectorConfig(options=WEEKDAYS, multiple=True, translation_key="weekdays")
        ),
        vol.Required(CONF_COUNT, default=d.get(CONF_COUNT, DEFAULT_COUNT)): vol.All(
            NumberSelector(NumberSelectorConfig(min=1, max=10, step=1, mode=NumberSelectorMode.BOX)),
            vol.Coerce(int),
        ),
    }


def _notify_schema(hass: HomeAssistant, d: Mapping[str, Any]) -> dict:
    services = sorted(s for s in hass.services.async_services_for_domain("notify") if s.startswith("mobile_app_"))
    return {
        vol.Optional(CONF_NOTIFY, default=d.get(CONF_NOTIFY, [])): SelectSelector(
            SelectSelectorConfig(options=services, multiple=True, custom_value=True)
        ),
        vol.Required(CONF_THRESHOLD, default=d.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)): vol.All(
            NumberSelector(NumberSelectorConfig(min=1, max=60, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="min")),
            vol.Coerce(int),
        ),
        vol.Required(CONF_QUIET_START, default=d.get(CONF_QUIET_START, DEFAULT_QUIET_START)): TimeSelector(),
        vol.Required(CONF_QUIET_END, default=d.get(CONF_QUIET_END, DEFAULT_QUIET_END)): TimeSelector(),
    }


class SncfTrajetsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create one entry per trajet."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._choices: list[Station] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SncfTrajetsOptionsFlow()

    def _client(self, api_key: str) -> SncfApiClient:
        return SncfApiClient(async_get_clientsession(self.hass), api_key)

    async def _validate_key(self, api_key: str) -> str | None:
        try:
            await self._client(api_key).search_stations("test")
        except SncfAuthError:
            return "invalid_auth"
        except SncfApiError:
            return "cannot_connect"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is None:
            for entry in self._async_current_entries(include_ignore=False):
                if key := entry.data.get(CONF_API_KEY):
                    self._data[CONF_API_KEY] = key
                    return await self.async_step_from_station()
        else:
            if (error := await self._validate_key(user_input[CONF_API_KEY])) is None:
                self._data[CONF_API_KEY] = user_input[CONF_API_KEY]
                return await self.async_step_from_station()
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): TextSelector()}),
            description_placeholders={"api_url": API_KEY_URL},
            errors=errors,
        )

    async def _station_query(self, step_id: str, user_input, next_step) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._choices = await self._client(self._data[CONF_API_KEY]).search_stations(user_input["query"])
            except SncfApiError:
                errors["base"] = "cannot_connect"
            else:
                if self._choices:
                    return await next_step()
                errors["query"] = "no_station_found"
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema({vol.Required("query"): TextSelector()}),
            errors=errors,
        )

    def _pick_form(self, step_id: str, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        options = [SelectOptionDict(value=s.id, label=s.name) for s in self._choices]
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {vol.Required("station"): SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST))}
            ),
            errors=errors or {},
        )

    def _station(self, station_id: str) -> Station:
        return next(s for s in self._choices if s.id == station_id)

    async def async_step_from_station(self, user_input=None) -> ConfigFlowResult:
        return await self._station_query("from_station", user_input, self.async_step_from_pick)

    async def async_step_from_pick(self, user_input=None) -> ConfigFlowResult:
        if user_input is None or "station" not in user_input:
            return self._pick_form("from_pick")
        station = self._station(user_input["station"])
        self._data[CONF_FROM_ID], self._data[CONF_FROM_NAME] = station.id, station.name
        return await self.async_step_to_station()

    async def async_step_to_station(self, user_input=None) -> ConfigFlowResult:
        return await self._station_query("to_station", user_input, self.async_step_to_pick)

    async def async_step_to_pick(self, user_input=None) -> ConfigFlowResult:
        if user_input is None or "station" not in user_input:
            return self._pick_form("to_pick")
        if user_input["station"] == self._data[CONF_FROM_ID]:
            return self._pick_form("to_pick", {"station": "same_station"})
        station = self._station(user_input["station"])
        self._data[CONF_TO_ID], self._data[CONF_TO_NAME] = station.id, station.name
        return await self.async_step_schedule()

    async def async_step_schedule(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_WEEKDAYS]:
                errors[CONF_WEEKDAYS] = "no_weekday"
            else:
                self._data.update(user_input)
                await self.async_set_unique_id(
                    f"{self._data[CONF_FROM_ID]}_{self._data[CONF_TO_ID]}_{user_input[CONF_START]}_{user_input[CONF_END]}"
                )
                self._abort_if_unique_id_configured()
                return await self.async_step_notify()
        return self.async_show_form(
            step_id="schedule",
            data_schema=vol.Schema(_schedule_schema(user_input or {})),
            errors=errors,
        )

    async def async_step_notify(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update({CONF_NOTIFY: [], **user_input})
            return self.async_create_entry(
                title=f"{self._data[CONF_FROM_NAME]} → {self._data[CONF_TO_NAME]}",
                data=self._data,
            )
        return self.async_show_form(step_id="notify", data_schema=vol.Schema(_notify_schema(self.hass, {})))

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if (error := await self._validate_key(user_input[CONF_API_KEY])) is None:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): TextSelector()}),
            description_placeholders={"api_url": API_KEY_URL},
            errors=errors,
        )


class SncfTrajetsOptionsFlow(OptionsFlow):
    """Edit schedule and notifications."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            if not user_input[CONF_WEEKDAYS]:
                errors[CONF_WEEKDAYS] = "no_weekday"
            else:
                return self.async_create_entry(data={CONF_NOTIFY: [], **user_input})
            current.update(user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({**_schedule_schema(current), **_notify_schema(self.hass, current)}),
            errors=errors,
        )
