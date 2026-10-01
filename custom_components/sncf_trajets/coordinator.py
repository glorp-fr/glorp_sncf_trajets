"""Per-trajet data coordinator."""

from __future__ import annotations

from dataclasses import replace
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .alerts import AlertNotifier
from .api import SncfApiClient, SncfApiError, SncfAuthError
from .const import (
    CONF_COUNT,
    CONF_END,
    CONF_FROM_ID,
    CONF_START,
    CONF_TO_ID,
    CONF_WEEKDAYS,
    DEFAULT_COUNT,
    DOMAIN,
    INTERVAL_ACTIVE,
    INTERVAL_MAX,
    LOOKBACK,
    WEEKDAYS,
)
from .models import TrajetData
from .schedule import next_window, parse_time, poll_interval

_LOGGER = logging.getLogger(__name__)


class SncfTrajetCoordinator(DataUpdateCoordinator[TrajetData]):
    """Fetch trains for one trajet and trigger alerts."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: SncfApiClient,
        notifier: AlertNotifier,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=INTERVAL_ACTIVE,
        )
        self.client = client
        self.notifier = notifier
        self._failures = 0

    @property
    def opts(self) -> dict:
        """Entry data merged with options."""
        return {**self.config_entry.data, **self.config_entry.options}

    async def _async_update_data(self) -> TrajetData:
        opts = self.opts
        now = dt_util.now()
        window = next_window(
            now,
            parse_time(opts[CONF_START]),
            parse_time(opts[CONF_END]),
            {WEEKDAYS.index(d) for d in opts[CONF_WEEKDAYS]},
        )
        count = opts.get(CONF_COUNT, DEFAULT_COUNT)
        still_ahead = lambda t: (t.departure >= now if not t.cancelled else t.base_departure >= now)  # noqa: E731
        since = max(window.start, now - LOOKBACK)
        try:
            trains = await self.client.get_trains(opts[CONF_FROM_ID], opts[CONF_TO_ID], since, count + 3)
        except SncfAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SncfApiError as err:
            self._failures += 1
            self.update_interval = min(INTERVAL_ACTIVE * 2**self._failures, INTERVAL_MAX)
            if self.data is None:
                raise UpdateFailed(str(err)) from err
            _LOGGER.warning("API SNCF indisponible (%s), données conservées", err)
            kept = [t for t in self.data.trains if still_ahead(t)]
            return replace(
                self.data,
                trains=kept,
                disruptions=list(dict.fromkeys(t.cause for t in kept if t.cause)),
                stale=True,
            )
        self._failures = 0

        selected = [
            t
            for t in trains
            if window.start <= t.base_departure <= window.end
            and still_ahead(t)
        ][:count]
        causes = list(dict.fromkeys(t.cause for t in selected if t.cause))

        try:
            await self.notifier.async_process(selected, now)
        except Exception:  # noqa: BLE001 - alerts must never break the entities
            _LOGGER.exception("Échec du traitement des alertes")
        self.update_interval = poll_interval(now, window)
        return TrajetData(
            window_start=window.start,
            window_end=window.end,
            trains=selected,
            disruptions=causes,
            last_update=now,
            is_future_window=now < window.start,
        )
