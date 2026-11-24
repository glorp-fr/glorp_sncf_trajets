"""Glorp SNCF Trajets integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .alerts import AlertNotifier
from .api import SncfApiClient, SncfApiError, SncfAuthError, legacy_uic
from .boards import BoardClient
from .card import async_register_card
from .const import CONF_FROM_ID, CONF_FROM_UIC, CONF_TO_ID
from .coordinator import SncfTrajetCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

type SncfConfigEntry = ConfigEntry[SncfTrajetCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SncfConfigEntry) -> bool:
    """Set up one trajet."""
    await async_register_card(hass)
    session = async_get_clientsession(hass)
    client = SncfApiClient(session, entry.data[CONF_API_KEY])
    await _async_migrate_station_ids(hass, entry, client)
    notifier = AlertNotifier(hass, entry)
    await notifier.async_load()
    coordinator = SncfTrajetCoordinator(hass, entry, client, notifier, BoardClient(session))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_migrate_station_ids(
    hass: HomeAssistant, entry: SncfConfigEntry, client: SncfApiClient
) -> None:
    """Rewrite pre-Nov. 2026 station ids (stop_area:SNCF:<UIC>), rejected by Navitia since."""
    updates: dict[str, str] = {}
    for key in (CONF_FROM_ID, CONF_TO_ID):
        old = entry.data.get(key)
        try:
            new = await client.resolve_legacy_stop_area(old)
        except SncfAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SncfApiError as err:
            raise ConfigEntryNotReady(f"Conversion de l'identifiant de gare {old} impossible : {err}") from err
        if new:
            updates[key] = new
            if key == CONF_FROM_ID and (uic := legacy_uic(old)):
                updates[CONF_FROM_UIC] = uic
    if updates:
        _LOGGER.info("Identifiants de gares convertis au nouveau format Navitia : %s", updates)
        hass.config_entries.async_update_entry(entry, data={**entry.data, **updates})


async def async_unload_entry(hass: HomeAssistant, entry: SncfConfigEntry) -> bool:
    """Unload a trajet."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: SncfConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
