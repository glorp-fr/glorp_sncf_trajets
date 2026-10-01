"""Sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SncfConfigEntry
from .const import CONF_COUNT, CONF_FROM_NAME, CONF_THRESHOLD, CONF_TO_NAME, DEFAULT_COUNT, DEFAULT_THRESHOLD
from .entity import SncfEntity
from .models import Train

STATES = ["on_time", "delayed", "cancelled"]


async def async_setup_entry(
    hass: HomeAssistant, entry: SncfConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    count = coordinator.opts.get(CONF_COUNT, DEFAULT_COUNT)
    # Drop entities of trains beyond the configured count (options lowered)
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_train_"
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        suffix = (reg_entry.unique_id or "").removeprefix(prefix)
        if reg_entry.unique_id.startswith(prefix) and suffix.isdigit() and int(suffix) > count:
            registry.async_remove(reg_entry.entity_id)
    async_add_entities(
        [NextTrainSensor(coordinator), *(TrainSensor(coordinator, i) for i in range(1, count + 1))]
    )


class NextTrainSensor(SncfEntity, SensorEntity):
    """Next non-cancelled departure; carries the full list for the card."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "next_train"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "next_train")

    @property
    def native_value(self) -> datetime | None:
        return next((t.departure for t in self.coordinator.data.trains if not t.cancelled), None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        opts = self.coordinator.opts
        return {
            "trains": [t.as_dict() for t in data.trains],
            "disruptions": data.disruptions,
            "window_start": data.window_start.isoformat(),
            "window_end": data.window_end.isoformat(),
            "is_future_window": data.is_future_window,
            "stale": data.stale,
            "last_update": data.last_update.isoformat() if data.last_update else None,
            "from_name": opts[CONF_FROM_NAME],
            "to_name": opts[CONF_TO_NAME],
            "threshold": opts.get(CONF_THRESHOLD, DEFAULT_THRESHOLD),
        }


class TrainSensor(SncfEntity, SensorEntity):
    """State of the i-th train of the window."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATES
    _attr_translation_key = "train"

    def __init__(self, coordinator, index: int) -> None:
        super().__init__(coordinator, f"train_{index}")
        self._index = index
        self._attr_translation_placeholders = {"index": str(index)}

    @property
    def _train(self) -> Train | None:
        trains = self.coordinator.data.trains
        return trains[self._index - 1] if len(trains) >= self._index else None

    @property
    def native_value(self) -> str | None:
        if (t := self._train) is None:
            return None
        if t.cancelled:
            return "cancelled"
        threshold = self.coordinator.opts.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)
        return "delayed" if t.delay_minutes >= threshold else "on_time"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self._train.as_dict() if self._train else None
