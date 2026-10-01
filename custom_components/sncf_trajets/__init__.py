"""SNCF Trajets integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .alerts import AlertNotifier
from .api import SncfApiClient
from .card import async_register_card
from .coordinator import SncfTrajetCoordinator

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

type SncfConfigEntry = ConfigEntry[SncfTrajetCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SncfConfigEntry) -> bool:
    """Set up one trajet."""
    await async_register_card(hass)
    client = SncfApiClient(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    notifier = AlertNotifier(hass, entry)
    await notifier.async_load()
    coordinator = SncfTrajetCoordinator(hass, entry, client, notifier)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SncfConfigEntry) -> bool:
    """Unload a trajet."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: SncfConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
