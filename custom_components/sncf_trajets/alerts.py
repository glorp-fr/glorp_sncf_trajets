"""Alert detection and dispatch."""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_NOTIFY,
    CONF_PLATFORM_NOTIFY,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_THRESHOLD,
    DEFAULT_PLATFORM_NOTIFY,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_THRESHOLD,
    DELAY_CHANGE_STEP,
    DOMAIN,
    EVENT_ALERT,
)
from .models import Train
from .schedule import in_quiet_hours, parse_time

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1

KEEP_STATE = timedelta(hours=12)


@dataclass
class Alert:
    """A state change worth notifying."""

    type: str  # delay, delay_changed, on_time, cancelled, restored, platform, platform_changed
    train: Train
    previous_platform: str | None = None


PLATFORM_ALERTS = ("platform", "platform_changed")


def _status_alert(prev: dict, train: Train, threshold: int) -> str | None:
    """Delay / cancellation transition (mutates prev)."""
    delay = train.delay_minutes
    if train.cancelled:
        if prev["cancelled"]:
            return None
        prev["cancelled"] = True
        return "cancelled"
    if prev["cancelled"]:
        prev["cancelled"] = False
        prev["notified_delay"] = delay if delay >= threshold else 0
        return "restored"
    notified = prev["notified_delay"]
    if notified == 0 and delay >= threshold:
        prev["notified_delay"] = delay
        return "delay"
    if notified > 0 and delay >= threshold and abs(delay - notified) >= DELAY_CHANGE_STEP:
        prev["notified_delay"] = delay
        return "delay_changed"
    if notified > 0 and delay < threshold:
        prev["notified_delay"] = 0
        return "on_time"
    return None


def _platform_alert(prev: dict, train: Train, eligible: bool) -> Alert | None:
    """Track announced / changed (mutates prev)."""
    if train.cancelled or not train.platform:
        return None
    seen = prev.get("platform")
    prev["platform"] = train.platform
    if seen and seen != train.platform:
        prev["platform_notified"] = True
        return Alert("platform_changed", train, previous_platform=seen)
    if eligible and not prev.get("platform_notified"):
        prev["platform_notified"] = True
        return Alert("platform", train)
    return None


def compute_alerts(
    state: dict[str, dict],
    trains: list[Train],
    threshold: int,
    platform_notify: str = DEFAULT_PLATFORM_NOTIFY,
) -> list[Alert]:
    """Diff trains against the notified state (mutated in place)."""
    nxt = min((t for t in trains if not t.cancelled), key=lambda t: t.departure, default=None)
    next_id = nxt.id if nxt else None
    alerts: list[Alert] = []
    for train in trains:
        prev = state.setdefault(train.id, {"notified_delay": 0, "cancelled": False})
        prev["base_departure"] = train.base_departure.isoformat()
        if kind := _status_alert(prev, train, threshold):
            alerts.append(Alert(kind, train))
        eligible = platform_notify == "all" or (platform_notify == "next" and train.id == next_id)
        if alert := _platform_alert(prev, train, eligible):
            alerts.append(alert)
    return alerts


def purge_state(state: dict[str, dict], now: datetime) -> bool:
    """Drop trains that left more than 12 h ago."""
    old = [
        key
        for key, value in state.items()
        if datetime.fromisoformat(value["base_departure"]) < now - KEEP_STATE
    ]
    for key in old:
        del state[key]
    return bool(old)


def train_label(train: Train) -> str:
    """'TER 17716 08:12'."""
    return " ".join(p for p in (train.mode, train.number, f"{train.base_departure:%H:%M}") if p)


def format_message(alert: Alert, trains: list[Train]) -> str:
    """Human message for one alert."""
    t = alert.train
    label = train_label(t)
    dep = f"{t.departure:%H:%M}"
    if alert.type == "delay":
        msg = f"⚠️ {label} → +{t.delay_minutes} min (départ {dep})"
        return f"{msg}. {t.cause}" if t.cause else msg
    if alert.type == "delay_changed":
        return f"⚠️ {label} → maintenant +{t.delay_minutes} min (départ {dep})"
    if alert.type == "on_time":
        return f"✅ {label} de nouveau à l'heure"
    if alert.type == "restored":
        return f"✅ {label} rétabli"
    if alert.type == "platform":
        return f"🛤️ {label} → voie {t.platform}"
    if alert.type == "platform_changed":
        return f"⚠️ {label} : changement de voie {alert.previous_platform} → {t.platform}"
    parts = [f"❌ {label} SUPPRIMÉ"]
    if t.cause:
        parts.append(t.cause)
    nxt = next(
        (o for o in trains if not o.cancelled and o.base_departure > t.base_departure),
        None,
    )
    if nxt:
        parts.append(f"Prochain train : {nxt.departure:%H:%M}")
    return ". ".join(parts)


def format_summary(trains: list[Train], threshold: int) -> str | None:
    """One line per abnormal train, None if all normal."""
    lines = []
    for t in trains:
        if t.cancelled:
            lines.append(f"❌ {train_label(t)} supprimé")
        elif t.delay_minutes >= threshold:
            lines.append(f"⚠️ {train_label(t)} +{t.delay_minutes} min")
    return "\n".join(lines) or None


class AlertNotifier:
    """Persist notified state, fire events and send mobile pushes."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._state: dict = {"trains": {}, "pending": []}

    @property
    def _opts(self) -> dict:
        return {**self._entry.data, **self._entry.options}

    async def async_load(self) -> None:
        """Load persisted state."""
        data = await self._store.async_load()
        if isinstance(data, dict):
            state = {"trains": {}, "pending": [], **data}
            if not isinstance(state["trains"], dict):
                state["trains"] = {}
            if not isinstance(state["pending"], list):
                state["pending"] = []
            self._state = state

    async def async_process(self, trains: list[Train], now: datetime) -> None:
        """Diff, then notify or queue during quiet hours."""
        opts = self._opts
        threshold = opts.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)
        before = copy.deepcopy(self._state["trains"])
        purge_state(self._state["trains"], now)
        alerts = compute_alerts(
            self._state["trains"],
            trains,
            threshold,
            opts.get(CONF_PLATFORM_NOTIFY, DEFAULT_PLATFORM_NOTIFY),
        )
        changed = self._state["trains"] != before

        for alert in alerts:
            self._fire_event(alert)

        quiet = in_quiet_hours(
            now.time(),
            parse_time(opts.get(CONF_QUIET_START, DEFAULT_QUIET_START)),
            parse_time(opts.get(CONF_QUIET_END, DEFAULT_QUIET_END)),
        )
        pending: list[str] = self._state["pending"]
        sends: list[tuple[str, str, dict]] = []
        if quiet:
            for alert in alerts:
                if alert.type in PLATFORM_ALERTS:
                    continue  # obsolete once the train has left
                if alert.train.id not in pending:
                    pending.append(alert.train.id)
                    changed = True
        else:
            if pending:
                concerned = [t for t in trains if t.id in pending]
                if summary := format_summary(concerned, threshold):
                    sends.append((summary, f"sncf_summary_{now:%Y%m%d}", {}))
                alerts = [a for a in alerts if a.type in PLATFORM_ALERTS or a.train.id not in pending]
                pending.clear()
                changed = True
            sends.extend(self._alert_message(alert, trains) for alert in alerts)

        # Persist before sending so a failing push never loses state
        if changed:
            await self._store.async_save(self._state)
        for message, tag, extra in sends:
            await self._push(message, tag, **extra)

    def _fire_event(self, alert: Alert) -> None:
        t = alert.train
        self._hass.bus.async_fire(
            EVENT_ALERT,
            {
                "entry_id": self._entry.entry_id,
                "trajet": self._entry.title,
                "type": alert.type,
                "train_number": t.number,
                "base_departure": t.base_departure.isoformat(),
                "departure": t.departure.isoformat(),
                "delay_minutes": t.delay_minutes,
                "cause": t.cause,
                "platform": t.platform,
                "previous_platform": alert.previous_platform,
            },
        )

    @staticmethod
    def _alert_message(alert: Alert, trains: list[Train]) -> tuple[str, str, dict]:
        t = alert.train
        extra = {}
        if alert.type in ("cancelled", "platform_changed"):
            extra = {"priority": "high", "ttl": 0, "push": {"interruption-level": "time-sensitive"}}
        return format_message(alert, trains), f"sncf_{t.number}_{t.base_departure:%Y%m%d}", extra

    async def _push(self, message: str, tag: str, **extra) -> None:
        for service in self._opts.get(CONF_NOTIFY, []):
            try:
                await self._hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": f"🚆 {self._entry.title}",
                        "message": message,
                        "data": {"tag": tag, **extra},
                    },
                    blocking=False,  # do not block the refresh on slow pushes
                )
            except Exception as err:  # noqa: BLE001 - one bad service must not abort the others
                _LOGGER.warning("Notification via notify.%s impossible : %s", service, err)
