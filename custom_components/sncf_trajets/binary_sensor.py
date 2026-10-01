"""Disruption binary sensor."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SncfConfigEntry
from .const import CONF_THRESHOLD, DEFAULT_THRESHOLD
from .entity import SncfEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: SncfConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([DisruptionBinarySensor(entry.runtime_data)])


class DisruptionBinarySensor(SncfEntity, BinarySensorEntity):
    """On when a train is delayed/cancelled or a message exists."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "disruption"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "disruption")

    @property
    def is_on(self) -> bool:
        data = self.coordinator.data
        threshold = self.coordinator.opts.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)
        return bool(data.disruptions) or any(
            t.cancelled or t.delay_minutes >= threshold for t in data.trains
        )
