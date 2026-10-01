"""Serve the Lovelace card and register it as a resource."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import CARD_URL, DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)
CARD_FILE = Path(__file__).parent / "www" / "sncf-trajets-card.js"
_DONE = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Expose the card JS and add it to Lovelace resources (storage mode).

    Never raises: a failure here must not block the setup of a trajet.
    """
    if hass.data.get(_DONE) or hass.http is None:
        return
    try:
        await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_FILE), True)])
    except Exception:  # noqa: BLE001
        _LOGGER.warning("Impossible de servir la carte %s", CARD_URL, exc_info=True)
        return
    hass.data[_DONE] = True

    try:
        await _async_register_resource(hass)
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "Enregistrement automatique de la ressource Lovelace impossible, ajoutez %s manuellement",
            CARD_URL,
            exc_info=True,
        )


async def _async_register_resource(hass: HomeAssistant) -> None:
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None or lovelace.resource_mode != "storage":
        _LOGGER.info("Ajoutez la ressource %s manuellement (mode YAML)", CARD_URL)
        return
    resources = lovelace.resources
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True
    url = f"{CARD_URL}?v={VERSION}"
    for item in resources.async_items():
        if item["url"].split("?")[0] == CARD_URL:
            if item["url"] != url:
                await resources.async_update_item(item["id"], {"res_type": "module", "url": url})
            return
    await resources.async_create_item({"res_type": "module", "url": url})
