# SNCF Trajets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Intégration Home Assistant installable via HACS qui suit des trajets SNCF (N trains directs dans une plage horaire/jours), expose capteurs + carte Lovelace et pousse des notifications mobiles à chaque changement d'état d'un train.

**Architecture:** Une config entry par trajet. `api.py` interroge Navitia (2 appels base/realtime fusionnés), `schedule.py` calcule la plage et l'intervalle de polling (fonctions pures), `coordinator.py` orchestre, `alerts.py` diffe les états persistés et envoie push + événement, `sensor.py`/`binary_sensor.py` exposent les données, `card.py` sert et enregistre la carte JS.

**Tech Stack:** Python 3.13, Home Assistant ≥ 2025.1 (tests sur 2026.2.3 via `pytest-homeassistant-custom-component` 0.13.316), aiohttp, JS vanilla (Web Components) pour la carte.

**Spec:** `docs/superpowers/specs/2026-10-01-sncf-trajets-design.md`

## Global Constraints

- Domaine `sncf_trajets`, dossier `custom_components/sncf_trajets/`, version `0.1.0`.
- API : `https://api.sncf.com/v1/coverage/sncf`, HTTP Basic (clé = login, mot de passe vide), heures Navitia en `Europe/Paris` au format `YYYYMMDDTHHMMSS`.
- Uniquement trains directs (`max_nb_transfers=0`, `direct_path=none`).
- Défauts : 3 trains (1–10), seuil 5 min, jours lun–ven, silence 22:00–06:00, palier « retard modifié » 5 min.
- Polling : 2 min de `début−60 min` à `fin` ; 60 min entre 00:00 et 05:00 ; 15 min sinon ; jamais plus tard que `début−60 min` ; backoff erreur `min(2 min × 2^n, 60 min)`.
- Événement `sncf_trajets_alert` ; types `delay`, `delay_changed`, `on_time`, `cancelled`, `restored`.
- Push : titre `🚆 <Départ> → <Arrivée>`, `tag` `sncf_<num>_<YYYYMMDD>`, suppression en priorité haute / time-sensitive.
- Aucune clé API dans le dépôt.
- Textes UI en français (fr) et anglais (en).

## Review Focus

1. Train supprimé absent de la réponse temps réel → doit apparaître « Supprimé » et déclencher `cancelled` (test : `test_merge_marks_missing_train_cancelled` dans Task 2).
2. Redémarrage de HA en pleine plage → aucune notification déjà envoyée n'est renvoyée (test : `test_no_duplicate_after_reload` dans Task 4).
3. Plage traversant minuit et changement d'heure (fin octobre) → la plage reste correcte en heure locale (tests `test_window_across_midnight`, `test_window_dst_change` dans Task 1).
4. Navitia renvoie 404 `no_solution` (aucun train, ex. dimanche) → liste vide, pas d'erreur ni de passage en `stale` (test `test_no_solution_is_empty` dans Task 2).
5. Service `notify.mobile_app_*` supprimé/renommé → avertissement dans le log, pas d'exception, l'événement HA est quand même émis (test `test_missing_notify_service_does_not_crash` dans Task 4).

---

## File Structure

```
custom_components/sncf_trajets/
  __init__.py          setup/unload/reload d'une entry
  manifest.json
  const.py             constantes
  schedule.py          Window, parse_time, next_window, poll_interval, in_quiet_hours
  models.py            Train, TrajetData
  api.py               SncfApiClient, Station, exceptions, parse_journeys, merge_trains
  alerts.py            Alert, compute_alerts, purge_state, format_message, AlertNotifier
  coordinator.py       SncfTrajetCoordinator
  config_flow.py       ConfigFlow, OptionsFlow, reauth
  sensor.py            NextTrainSensor, TrainSensor
  binary_sensor.py     DisruptionBinarySensor
  card.py              async_register_card
  strings.json
  translations/fr.json, translations/en.json
  www/sncf-trajets-card.js
tests/
  __init__.py, conftest.py, navitia.py (builder de réponses)
  test_schedule.py, test_api.py, test_alerts.py, test_notifier.py,
  test_coordinator.py, test_config_flow.py, test_init.py
hacs.json, README.md, LICENSE, pyproject.toml, requirements_test.txt, .gitignore
.github/workflows/tests.yml, .github/workflows/validate.yml
```

Commandes : `.venv/bin/pytest` depuis la racine du dépôt (venv déjà créé avec `pytest-homeassistant-custom-component`).

---

### Task 1: Scaffolding + schedule

**Files:**
- Create: `.gitignore`, `pyproject.toml`, `requirements_test.txt`, `custom_components/sncf_trajets/__init__.py` (vide pour l'instant, docstring), `custom_components/sncf_trajets/manifest.json`, `custom_components/sncf_trajets/const.py`, `custom_components/sncf_trajets/schedule.py`, `tests/__init__.py`, `tests/conftest.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Produces: `Window(start: datetime, end: datetime)` (frozen dataclass), `parse_time(value: str) -> time`, `next_window(now, start: time, end: time, weekdays: set[int]) -> Window`, `poll_interval(now, window) -> timedelta`, `in_quiet_hours(t: time, start: time, end: time) -> bool`; constantes de `const.py`.

- [ ] **Step 1: Scaffolding**

`.gitignore` :
```
.venv/
__pycache__/
.pytest_cache/
*.pyc
.coverage
```

`requirements_test.txt` :
```
pytest-homeassistant-custom-component==0.13.316
```

`pyproject.toml` :
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
```

`custom_components/sncf_trajets/manifest.json` :
```json
{
  "domain": "sncf_trajets",
  "name": "SNCF Trajets",
  "after_dependencies": ["lovelace"],
  "codeowners": ["@glorp-fr"],
  "config_flow": true,
  "dependencies": ["http"],
  "documentation": "https://github.com/glorp-fr/ha-sncf-trajets",
  "integration_type": "service",
  "iot_class": "cloud_polling",
  "issue_tracker": "https://github.com/glorp-fr/ha-sncf-trajets/issues",
  "requirements": [],
  "version": "0.1.0"
}
```

`custom_components/sncf_trajets/__init__.py` :
```python
"""SNCF Trajets integration."""
```

`custom_components/sncf_trajets/const.py` :
```python
"""Constants for SNCF Trajets."""

from datetime import timedelta
from zoneinfo import ZoneInfo

DOMAIN = "sncf_trajets"
VERSION = "0.1.0"

API_BASE = "https://api.sncf.com/v1/coverage/sncf"
NAVITIA_TZ = ZoneInfo("Europe/Paris")

CONF_FROM_ID = "from_id"
CONF_FROM_NAME = "from_name"
CONF_TO_ID = "to_id"
CONF_TO_NAME = "to_name"
CONF_START = "start"
CONF_END = "end"
CONF_WEEKDAYS = "weekdays"
CONF_COUNT = "count"
CONF_NOTIFY = "notify"
CONF_THRESHOLD = "threshold"
CONF_QUIET_START = "quiet_start"
CONF_QUIET_END = "quiet_end"

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULT_COUNT = 3
DEFAULT_THRESHOLD = 5
DEFAULT_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]
DEFAULT_QUIET_START = "22:00:00"
DEFAULT_QUIET_END = "06:00:00"

DELAY_CHANGE_STEP = 5
LOOKBACK = timedelta(minutes=30)

INTERVAL_ACTIVE = timedelta(minutes=2)
INTERVAL_IDLE = timedelta(minutes=15)
INTERVAL_NIGHT = timedelta(minutes=60)
INTERVAL_MAX = timedelta(minutes=60)
PRE_WINDOW = timedelta(minutes=60)

EVENT_ALERT = "sncf_trajets_alert"
CARD_URL = "/sncf_trajets/sncf-trajets-card.js"
```

`tests/__init__.py` : `"""Tests for SNCF Trajets."""`

`tests/conftest.py` :
```python
"""Common fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


@pytest.fixture(autouse=True)
async def paris_timezone(hass):
    """Run every test with Europe/Paris as HA time zone."""
    await hass.config.async_set_time_zone("Europe/Paris")
    yield
```

- [ ] **Step 2: Write the failing tests** — `tests/test_schedule.py`

```python
"""Tests for schedule helpers."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.sncf_trajets.schedule import (
    Window,
    in_quiet_hours,
    next_window,
    parse_time,
    poll_interval,
)

TZ = ZoneInfo("Europe/Paris")
WEEK = {0, 1, 2, 3, 4}
S, E = time(7, 30), time(9, 30)


def dt(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def test_parse_time():
    assert parse_time("07:30:00") == time(7, 30)
    assert parse_time("07:30") == time(7, 30)


def test_window_before_start_same_day():
    # 2026-10-05 is a Monday
    w = next_window(dt(2026, 10, 5, 6, 0), S, E, WEEK)
    assert w == Window(dt(2026, 10, 5, 7, 30), dt(2026, 10, 5, 9, 30))


def test_window_inside():
    w = next_window(dt(2026, 10, 5, 8, 0), S, E, WEEK)
    assert w.start == dt(2026, 10, 5, 7, 30)


def test_window_after_end_goes_next_day():
    w = next_window(dt(2026, 10, 5, 14, 0), S, E, WEEK)
    assert w.start == dt(2026, 10, 6, 7, 30)


def test_window_friday_afternoon_goes_monday():
    w = next_window(dt(2026, 10, 9, 14, 0), S, E, WEEK)
    assert w.start == dt(2026, 10, 12, 7, 30)


def test_window_across_midnight():
    start, end = time(23, 0), time(1, 0)
    # Monday 00:30: window started Sunday 23:00 but Sunday excluded -> Monday 23:00
    w = next_window(dt(2026, 10, 5, 0, 30), start, end, WEEK)
    assert w == Window(dt(2026, 10, 5, 23, 0), dt(2026, 10, 6, 1, 0))
    # Tuesday 00:30: window from Monday 23:00 still running
    w = next_window(dt(2026, 10, 6, 0, 30), start, end, WEEK)
    assert w == Window(dt(2026, 10, 5, 23, 0), dt(2026, 10, 6, 1, 0))


def test_window_dst_change():
    # 2026-10-25: switch from CEST (+02) to CET (+01) at 03:00
    w = next_window(dt(2026, 10, 24, 12, 0), S, E, {6})
    assert w.start.hour == 7 and w.start.minute == 30
    assert w.start.utcoffset() == timedelta(hours=1)


def test_poll_active():
    w = Window(dt(2026, 10, 5, 7, 30), dt(2026, 10, 5, 9, 30))
    assert poll_interval(dt(2026, 10, 5, 6, 45), w) == timedelta(minutes=2)
    assert poll_interval(dt(2026, 10, 5, 9, 0), w) == timedelta(minutes=2)


def test_poll_idle_and_night():
    w = Window(dt(2026, 10, 6, 7, 30), dt(2026, 10, 6, 9, 30))
    assert poll_interval(dt(2026, 10, 5, 14, 0), w) == timedelta(minutes=15)
    assert poll_interval(dt(2026, 10, 6, 2, 0), w) == timedelta(minutes=60)


def test_poll_never_overshoots_pre_window():
    w = Window(dt(2026, 10, 6, 5, 30), dt(2026, 10, 6, 7, 0))
    # 04:00 night -> would be 60 min, but pre-window starts 04:30
    assert poll_interval(dt(2026, 10, 6, 4, 0), w) == timedelta(minutes=30)
    # 04:29 -> 1 min left, floor to 2 min
    assert poll_interval(dt(2026, 10, 6, 4, 29), w) == timedelta(minutes=2)


def test_quiet_hours():
    q1, q2 = time(22, 0), time(6, 0)
    assert in_quiet_hours(time(23, 0), q1, q2)
    assert in_quiet_hours(time(5, 59), q1, q2)
    assert not in_quiet_hours(time(6, 0), q1, q2)
    assert not in_quiet_hours(time(12, 0), q1, q2)
    assert in_quiet_hours(time(13, 0), time(12, 0), time(14, 0))
    assert not in_quiet_hours(time(13, 0), time(0, 0), time(0, 0))
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_schedule.py -q`
Expected: FAIL — `ModuleNotFoundError: ... schedule`

- [ ] **Step 4: Implement** — `custom_components/sncf_trajets/schedule.py`

```python
"""Pure time helpers: travel window and polling cadence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from .const import (
    INTERVAL_ACTIVE,
    INTERVAL_IDLE,
    INTERVAL_NIGHT,
    PRE_WINDOW,
)


@dataclass(frozen=True)
class Window:
    """A concrete occurrence of a travel window."""

    start: datetime
    end: datetime


def parse_time(value: str) -> time:
    """Parse 'HH:MM' or 'HH:MM:SS'."""
    return time.fromisoformat(value)


def next_window(
    now: datetime, start: time, end: time, weekdays: set[int]
) -> Window:
    """Return the running window, or the next one on an allowed weekday."""
    if not weekdays:
        raise ValueError("no weekday selected")
    tz = now.tzinfo
    today = now.date()
    for offset in range(-1, 9):
        day = today + timedelta(days=offset)
        if day.weekday() not in weekdays:
            continue
        w_start = datetime.combine(day, start, tz)
        end_day = day + timedelta(days=1) if end <= start else day
        w_end = datetime.combine(end_day, end, tz)
        if w_end > now:
            return Window(w_start, w_end)
    raise ValueError("no window found")


def poll_interval(now: datetime, window: Window) -> timedelta:
    """Return how long to wait before the next API refresh."""
    active_from = window.start - PRE_WINDOW
    if active_from <= now <= window.end:
        return INTERVAL_ACTIVE
    interval = INTERVAL_NIGHT if now.hour < 5 else INTERVAL_IDLE
    if now < active_from:
        interval = min(interval, active_from - now)
    return max(interval, INTERVAL_ACTIVE)


def in_quiet_hours(t: time, start: time, end: time) -> bool:
    """Return True if t is within [start, end), handling midnight wrap."""
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end
```

- [ ] **Step 5: Run tests** — `.venv/bin/pytest tests/test_schedule.py -q` → all PASS

- [ ] **Step 6: Commit**

```bash
git add .gitignore pyproject.toml requirements_test.txt custom_components tests
git commit -m "feat: scaffolding et calcul des plages horaires"
```

---

### Task 2: Models + API client

**Files:**
- Create: `custom_components/sncf_trajets/models.py`, `custom_components/sncf_trajets/api.py`, `tests/navitia.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `API_BASE`, `NAVITIA_TZ` (Task 1)
- Produces:
  - `Train(number: str, mode: str, base_departure: datetime, departure: datetime, base_arrival: datetime, arrival: datetime, cancelled: bool = False, cause: str | None = None)` with properties `id -> str` (`f"{number}_{base_departure:%Y%m%dT%H%M}"`), `delay_minutes -> int`, method `as_dict() -> dict`.
  - `TrajetData(window_start, window_end, trains: list[Train], disruptions: list[str], last_update: datetime, is_future_window: bool, stale: bool = False)`.
  - `Station(id: str, name: str)`; exceptions `SncfApiError`, `SncfAuthError(SncfApiError)`, `SncfQuotaError(SncfApiError)`.
  - `parse_journeys(data: dict) -> list[Train]`, `merge_trains(base: list[Train], realtime: list[Train]) -> list[Train]`.
  - `SncfApiClient(session, api_key)` with `async search_stations(query) -> list[Station]` and `async get_trains(from_id, to_id, since: datetime, count: int) -> list[Train]`.

- [ ] **Step 1: Test builder** — `tests/navitia.py`

```python
"""Builders for fake Navitia responses."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")


def nav(value: datetime) -> str:
    return value.astimezone(PARIS).strftime("%Y%m%dT%H%M%S")


def at(h: int, m: int = 0, day: int = 5) -> datetime:
    """2026-10-<day> h:m Paris (default Monday 5th)."""
    return datetime(2026, 10, day, h, m, tzinfo=PARIS)


def journey(number, base_dep, minutes=23, delay=0, links=(), mode="TER"):
    base_arr = base_dep + timedelta(minutes=minutes)
    dep = base_dep + timedelta(minutes=delay)
    arr = base_arr + timedelta(minutes=delay)
    section = {
        "type": "public_transport",
        "base_departure_date_time": nav(base_dep),
        "departure_date_time": nav(dep),
        "base_arrival_date_time": nav(base_arr),
        "arrival_date_time": nav(arr),
        "display_informations": {
            "headsign": number,
            "commercial_mode": mode,
            "links": [{"type": "disruption", "id": i} for i in links],
        },
    }
    return {
        "departure_date_time": nav(dep),
        "arrival_date_time": nav(arr),
        "sections": [{"type": "waiting"}, section],
    }


def disruption(id_, effect, text, number=None):
    impacted = []
    if number:
        impacted = [{"pt_object": {"embedded_type": "trip", "id": f"OCE:SN{number}F", "name": number}}]
    return {
        "id": id_,
        "severity": {"effect": effect},
        "messages": [{"text": f"<p>{text}</p>"}],
        "impacted_objects": impacted,
    }


def response(journeys=(), disruptions=()):
    return {"journeys": list(journeys), "disruptions": list(disruptions)}
```

- [ ] **Step 2: Write failing tests** — `tests/test_api.py`

```python
"""Tests for the Navitia client."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.sncf_trajets.api import (
    SncfApiClient,
    SncfApiError,
    SncfAuthError,
    SncfQuotaError,
    merge_trains,
    parse_journeys,
)
from custom_components.sncf_trajets.const import API_BASE

from .navitia import at, disruption, journey, response


def test_parse_on_time_and_delayed():
    trains = parse_journeys(response([journey("17714", at(7, 42)), journey("17716", at(8, 12), delay=7)]))
    assert [t.number for t in trains] == ["17714", "17716"]
    assert trains[0].delay_minutes == 0
    assert trains[1].delay_minutes == 7
    assert trains[1].departure == at(8, 19)
    assert trains[0].id == "17714_20261005T0742"
    assert trains[0].mode == "TER"


def test_parse_linked_disruption_gives_cause_and_cancel():
    data = response(
        [journey("17718", at(8, 42), links=["d1"])],
        [disruption("d1", "NO_SERVICE", "Mouvement social")],
    )
    (train,) = parse_journeys(data)
    assert train.cancelled is True
    assert train.cause == "Mouvement social"


def test_parse_disruption_matched_by_number():
    data = response(
        [journey("17716", at(8, 12), delay=10)],
        [disruption("d2", "SIGNIFICANT_DELAYS", "Panne de signalisation", number="17716")],
    )
    (train,) = parse_journeys(data)
    assert train.cancelled is False
    assert train.cause == "Panne de signalisation"


def test_parse_skips_journeys_with_transfer():
    j = journey("1", at(7, 0))
    j["sections"].append(dict(j["sections"][1]))
    assert parse_journeys(response([j])) == []


def test_parse_empty_and_error_payload():
    assert parse_journeys({}) == []
    assert parse_journeys({"error": {"id": "no_solution"}}) == []


def test_merge_marks_missing_train_cancelled():
    base = parse_journeys(response([journey("A", at(7, 42)), journey("B", at(8, 12)), journey("C", at(8, 42))]))
    rt = parse_journeys(response([journey("A", at(7, 42)), journey("C", at(8, 42), delay=3)]))
    merged = merge_trains(base, rt)
    assert [(t.number, t.cancelled, t.delay_minutes) for t in merged] == [
        ("A", False, 0),
        ("B", True, 0),
        ("C", False, 3),
    ]


def test_merge_keeps_last_missing_train_without_proof():
    base = parse_journeys(response([journey("A", at(7, 42)), journey("B", at(8, 12))]))
    rt = parse_journeys(response([journey("A", at(7, 42))]))
    merged = merge_trains(base, rt)
    assert [(t.number, t.cancelled) for t in merged] == [("A", False), ("B", False)]


def test_merge_keeps_realtime_only_trains():
    base = parse_journeys(response([journey("A", at(7, 42))]))
    rt = parse_journeys(response([journey("A", at(7, 42)), journey("X", at(7, 50))]))
    assert [t.number for t in merge_trains(base, rt)] == ["A", "X"]


async def test_get_trains_calls_base_and_realtime(hass):
    client = SncfApiClient(async_get_clientsession(hass), "key")
    base = response([journey("A", at(7, 42)), journey("B", at(8, 12)), journey("C", at(8, 42))])
    rt = response([journey("A", at(7, 42), delay=6), journey("C", at(8, 42))])

    async def fake_get(path, params):
        assert path == "journeys"
        p = dict(params)
        assert p["from"] == "stop_area:A" and p["to"] == "stop_area:B"
        assert p["datetime"] == "20261005T073000"
        assert p["max_nb_transfers"] == "0"
        return base if p["data_freshness"] == "base_schedule" else rt

    client._get = AsyncMock(side_effect=fake_get)
    trains = await client.get_trains("stop_area:A", "stop_area:B", at(7, 30), 6)
    assert [(t.number, t.cancelled, t.delay_minutes) for t in trains] == [
        ("A", False, 6), ("B", True, 0), ("C", False, 0)
    ]
    assert client._get.await_count == 2


async def test_search_stations(hass, aioclient_mock):
    aioclient_mock.get(
        f"{API_BASE}/places",
        json={"places": [
            {"id": "stop_area:SNCF:87721175", "name": "La Verpillière (La Verpillière)", "embedded_type": "stop_area"},
            {"id": "admin:fr:38537", "name": "La Verpillière", "embedded_type": "administrative_region"},
        ]},
    )
    client = SncfApiClient(async_get_clientsession(hass), "key")
    stations = await client.search_stations("verpilliere")
    assert [(s.id, s.name) for s in stations] == [("stop_area:SNCF:87721175", "La Verpillière (La Verpillière)")]


@pytest.mark.parametrize(
    ("status", "exc"),
    [(401, SncfAuthError), (403, SncfAuthError), (429, SncfQuotaError), (500, SncfApiError)],
)
async def test_http_errors(hass, aioclient_mock, status, exc):
    aioclient_mock.get(f"{API_BASE}/places", status=status)
    client = SncfApiClient(async_get_clientsession(hass), "key")
    with pytest.raises(exc):
        await client.search_stations("x")


async def test_timeout_is_api_error(hass, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/places", exc=asyncio.TimeoutError)
    client = SncfApiClient(async_get_clientsession(hass), "key")
    with pytest.raises(SncfApiError):
        await client.search_stations("x")


async def test_no_solution_is_empty(hass, aioclient_mock):
    aioclient_mock.get(f"{API_BASE}/journeys", status=404, json={"error": {"id": "no_solution"}})
    client = SncfApiClient(async_get_clientsession(hass), "key")
    assert await client.get_trains("a", "b", at(7, 30), 3) == []
```

- [ ] **Step 3: Run** — `.venv/bin/pytest tests/test_api.py -q` → FAIL (module missing)

- [ ] **Step 4: Implement** — `custom_components/sncf_trajets/models.py`

```python
"""Data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Train:
    """A direct train between the two stations."""

    number: str
    mode: str
    base_departure: datetime
    departure: datetime
    base_arrival: datetime
    arrival: datetime
    cancelled: bool = False
    cause: str | None = None

    @property
    def id(self) -> str:
        """Stable id: train number + theoretical departure."""
        return f"{self.number}_{self.base_departure:%Y%m%dT%H%M}"

    @property
    def delay_minutes(self) -> int:
        """Departure delay in whole minutes (never negative)."""
        seconds = (self.departure - self.base_departure).total_seconds()
        return max(0, round(seconds / 60))

    def as_dict(self) -> dict:
        """Serialize for entity attributes."""
        return {
            "number": self.number,
            "mode": self.mode,
            "base_departure": self.base_departure.isoformat(),
            "departure": self.departure.isoformat(),
            "base_arrival": self.base_arrival.isoformat(),
            "arrival": self.arrival.isoformat(),
            "delay_minutes": self.delay_minutes,
            "cancelled": self.cancelled,
            "cause": self.cause,
        }


@dataclass
class TrajetData:
    """Snapshot of one trajet."""

    window_start: datetime
    window_end: datetime
    trains: list[Train] = field(default_factory=list)
    disruptions: list[str] = field(default_factory=list)
    last_update: datetime | None = None
    is_future_window: bool = False
    stale: bool = False
```

`custom_components/sncf_trajets/api.py` :

```python
"""Navitia (API SNCF) client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime
import re
from typing import Any

import aiohttp

from .const import API_BASE, NAVITIA_TZ
from .models import Train

_TAG_RE = re.compile(r"<[^>]+>")


class SncfApiError(Exception):
    """Generic API error."""


class SncfAuthError(SncfApiError):
    """Invalid API key."""


class SncfQuotaError(SncfApiError):
    """Quota exceeded."""


@dataclass(frozen=True)
class Station:
    """A stop_area."""

    id: str
    name: str


def _parse_dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=NAVITIA_TZ)


def _message(disruption: dict) -> str | None:
    for msg in disruption.get("messages", []):
        text = _TAG_RE.sub("", msg.get("text", "")).strip()
        if text:
            return text
    return None


def _impacted_numbers(disruption: dict) -> set[str]:
    numbers = set()
    for obj in disruption.get("impacted_objects", []):
        pt = obj.get("pt_object", {})
        if pt.get("embedded_type") in ("trip", "vehicle_journey") and pt.get("name"):
            numbers.add(pt["name"])
    return numbers


def parse_journeys(data: dict[str, Any]) -> list[Train]:
    """Convert a /journeys payload into direct trains."""
    disruptions = data.get("disruptions", [])
    by_id = {d.get("id"): d for d in disruptions}
    trains: list[Train] = []
    for journey in data.get("journeys", []):
        sections = [s for s in journey.get("sections", []) if s.get("type") == "public_transport"]
        if len(sections) != 1:
            continue
        sec = sections[0]
        info = sec.get("display_informations", {})
        number = info.get("headsign") or info.get("trip_short_name") or ""
        linked = [
            by_id[link["id"]]
            for link in info.get("links", [])
            if link.get("type") == "disruption" and link.get("id") in by_id
        ]
        linked += [d for d in disruptions if d not in linked and number in _impacted_numbers(d)]
        cancelled = any(d.get("severity", {}).get("effect") == "NO_SERVICE" for d in linked)
        cause = next((m for m in (_message(d) for d in linked) if m), None)
        trains.append(
            Train(
                number=number,
                mode=info.get("commercial_mode", ""),
                base_departure=_parse_dt(sec.get("base_departure_date_time", sec["departure_date_time"])),
                departure=_parse_dt(sec["departure_date_time"]),
                base_arrival=_parse_dt(sec.get("base_arrival_date_time", sec["arrival_date_time"])),
                arrival=_parse_dt(sec["arrival_date_time"]),
                cancelled=cancelled,
                cause=cause,
            )
        )
    return trains


def merge_trains(base: list[Train], realtime: list[Train]) -> list[Train]:
    """Merge theoretical and realtime trains, detecting silent cancellations."""
    rt_by_id = {t.id: t for t in realtime}
    latest_rt = max((t.base_departure for t in realtime), default=None)
    merged: list[Train] = []
    for train in base:
        rt = rt_by_id.pop(train.id, None)
        if rt is not None:
            merged.append(rt)
        elif train.cancelled or (latest_rt is not None and latest_rt > train.base_departure):
            merged.append(replace(train, cancelled=True))
        else:
            merged.append(train)
    merged.extend(rt_by_id.values())
    merged.sort(key=lambda t: t.base_departure)
    return merged


class SncfApiClient:
    """Thin async client for api.sncf.com."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._auth = aiohttp.BasicAuth(api_key, "")

    async def _get(self, path: str, params: list[tuple[str, str]]) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{API_BASE}/{path}",
                params=params,
                auth=self._auth,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status in (401, 403):
                    raise SncfAuthError(f"HTTP {resp.status}")
                if resp.status == 429:
                    raise SncfQuotaError("quota exceeded")
                if resp.status == 404:
                    # Navitia answers 404 "no_solution" when no journey exists
                    return await resp.json(content_type=None) or {}
                if resp.status >= 400:
                    raise SncfApiError(f"HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SncfApiError(str(err) or type(err).__name__) from err

    async def search_stations(self, query: str) -> list[Station]:
        """Search train stations by name."""
        data = await self._get("places", [("q", query), ("type[]", "stop_area"), ("count", "10")])
        return [
            Station(p["id"], p["name"])
            for p in data.get("places", [])
            if p.get("embedded_type") == "stop_area"
        ]

    async def get_trains(
        self, from_id: str, to_id: str, since: datetime, count: int
    ) -> list[Train]:
        """Return direct trains from `since`, with realtime status merged in."""
        common = [
            ("from", from_id),
            ("to", to_id),
            ("datetime", since.astimezone(NAVITIA_TZ).strftime("%Y%m%dT%H%M%S")),
            ("datetime_represents", "departure"),
            ("max_nb_transfers", "0"),
            ("direct_path", "none"),
            ("count", str(count)),
            ("min_nb_journeys", str(count)),
        ]
        base = await self._get("journeys", [*common, ("data_freshness", "base_schedule")])
        realtime = await self._get("journeys", [*common, ("data_freshness", "realtime")])
        return merge_trains(parse_journeys(base), parse_journeys(realtime))
```

- [ ] **Step 5: Run** — `.venv/bin/pytest tests/test_api.py -q` → all PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/sncf_trajets/models.py custom_components/sncf_trajets/api.py tests/navitia.py tests/test_api.py
git commit -m "feat: client API SNCF avec détection des suppressions"
```

---

### Task 3: Alert engine (pure logic)

**Files:**
- Create: `custom_components/sncf_trajets/alerts.py`
- Test: `tests/test_alerts.py`

**Interfaces:**
- Consumes: `Train` (Task 2), `DELAY_CHANGE_STEP` (Task 1)
- Produces: `Alert(type: str, train: Train)`; `compute_alerts(state: dict[str, dict], trains: list[Train], threshold: int) -> list[Alert]` (mutates `state`); `purge_state(state, now) -> bool` (True if something removed); `format_message(alert, trains) -> str`; `format_summary(trains: list[Train], threshold: int) -> str | None`; `train_label(train) -> str`.

- [ ] **Step 1: Failing tests** — `tests/test_alerts.py`

```python
"""Tests for the alert diff engine."""

from dataclasses import replace
from datetime import timedelta

from custom_components.sncf_trajets.alerts import (
    Alert,
    compute_alerts,
    format_message,
    format_summary,
    purge_state,
)
from custom_components.sncf_trajets.api import parse_journeys

from .navitia import at, journey, response


def train(delay=0, cancelled=False, number="17716", h=8, m=12, cause=None):
    (t,) = parse_journeys(response([journey(number, at(h, m), delay=delay)]))
    return replace(t, cancelled=cancelled, cause=cause)


def types(alerts):
    return [a.type for a in alerts]


def test_first_seen_on_time_no_alert():
    state = {}
    assert compute_alerts(state, [train()], 5) == []
    assert state["17716_20261005T0812"]["notified_delay"] == 0


def test_first_seen_already_delayed():
    assert types(compute_alerts({}, [train(delay=7)], 5)) == ["delay"]


def test_below_threshold_no_alert():
    assert compute_alerts({}, [train(delay=4)], 5) == []


def test_delay_then_changed_then_on_time():
    state = {}
    assert types(compute_alerts(state, [train(delay=7)], 5)) == ["delay"]
    assert compute_alerts(state, [train(delay=9)], 5) == []  # +2 < step
    assert types(compute_alerts(state, [train(delay=15)], 5)) == ["delay_changed"]
    assert types(compute_alerts(state, [train(delay=10)], 5)) == ["delay_changed"]
    assert types(compute_alerts(state, [train(delay=2)], 5)) == ["on_time"]
    assert compute_alerts(state, [train(delay=0)], 5) == []


def test_cancel_then_restore():
    state = {}
    assert types(compute_alerts(state, [train(cancelled=True)], 5)) == ["cancelled"]
    assert compute_alerts(state, [train(cancelled=True)], 5) == []
    assert types(compute_alerts(state, [train(delay=8)], 5)) == ["restored"]
    assert compute_alerts(state, [train(delay=8)], 5) == []
    assert types(compute_alerts(state, [train(delay=0)], 5)) == ["on_time"]


def test_purge_old_trains():
    state = {}
    compute_alerts(state, [train()], 5)
    assert purge_state(state, at(12, 0)) is False
    assert purge_state(state, at(21, 0)) is True
    assert state == {}


def test_messages():
    t_ok = train(number="17718", h=8, m=42)
    t = train(delay=7, cause="Panne de signalisation")
    assert format_message(Alert("delay", t), [t]) == "⚠️ TER 17716 08:12 → +7 min (départ 08:19). Panne de signalisation"
    assert format_message(Alert("delay_changed", t), [t]) == "⚠️ TER 17716 08:12 → maintenant +7 min (départ 08:19)"
    assert format_message(Alert("on_time", t), [t]) == "✅ TER 17716 08:12 de nouveau à l'heure"
    assert format_message(Alert("restored", t), [t]) == "✅ TER 17716 08:12 rétabli"
    c = train(cancelled=True, cause="Mouvement social")
    assert format_message(Alert("cancelled", c), [c, t_ok]) == "❌ TER 17716 08:12 SUPPRIMÉ. Mouvement social. Prochain train : 08:42"
    c2 = train(cancelled=True)
    assert format_message(Alert("cancelled", c2), [c2]) == "❌ TER 17716 08:12 SUPPRIMÉ"


def test_summary():
    assert format_summary([train(), train(delay=2, number="1")], 5) is None
    text = format_summary([train(delay=7), train(cancelled=True, number="17718", m=42)], 5)
    assert text == "⚠️ TER 17716 08:12 +7 min\n❌ TER 17718 08:42 supprimé"
```

- [ ] **Step 2: Run** — `.venv/bin/pytest tests/test_alerts.py -q` → FAIL

- [ ] **Step 3: Implement** — `custom_components/sncf_trajets/alerts.py` (pure part; Task 4 appends the notifier)

```python
"""Alert detection and dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .const import DELAY_CHANGE_STEP
from .models import Train

KEEP_STATE = timedelta(hours=12)


@dataclass
class Alert:
    """A state change worth notifying."""

    type: str  # delay, delay_changed, on_time, cancelled, restored
    train: Train


def compute_alerts(state: dict[str, dict], trains: list[Train], threshold: int) -> list[Alert]:
    """Diff trains against the notified state (mutated in place)."""
    alerts: list[Alert] = []
    for train in trains:
        prev = state.setdefault(train.id, {"notified_delay": 0, "cancelled": False})
        prev["base_departure"] = train.base_departure.isoformat()
        delay = train.delay_minutes
        if train.cancelled:
            if not prev["cancelled"]:
                alerts.append(Alert("cancelled", train))
                prev["cancelled"] = True
            continue
        if prev["cancelled"]:
            alerts.append(Alert("restored", train))
            prev["cancelled"] = False
            prev["notified_delay"] = delay if delay >= threshold else 0
            continue
        notified = prev["notified_delay"]
        if notified == 0 and delay >= threshold:
            alerts.append(Alert("delay", train))
            prev["notified_delay"] = delay
        elif notified > 0 and delay >= threshold and abs(delay - notified) >= DELAY_CHANGE_STEP:
            alerts.append(Alert("delay_changed", train))
            prev["notified_delay"] = delay
        elif notified > 0 and delay < threshold:
            alerts.append(Alert("on_time", train))
            prev["notified_delay"] = 0
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
```

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/test_alerts.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/sncf_trajets/alerts.py tests/test_alerts.py
git commit -m "feat: moteur de détection des alertes"
```

---

### Task 4: AlertNotifier (persistence, quiet hours, push, event)

**Files:**
- Modify: `custom_components/sncf_trajets/alerts.py` (append)
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: Task 3 functions, `in_quiet_hours`/`parse_time` (Task 1), `CONF_*` (Task 1)
- Produces: `AlertNotifier(hass, entry: ConfigEntry)` with `async async_load() -> None` and `async async_process(trains: list[Train], now: datetime) -> None`. Reads options via `{**entry.data, **entry.options}`; title = `entry.title`.

- [ ] **Step 1: Failing tests** — `tests/test_notifier.py`

```python
"""Tests for AlertNotifier."""

from dataclasses import replace

from homeassistant.core import ServiceCall
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_mock_service,
)

from custom_components.sncf_trajets.alerts import AlertNotifier
from custom_components.sncf_trajets.api import parse_journeys
from custom_components.sncf_trajets.const import DOMAIN, EVENT_ALERT

from .navitia import at, journey, response

DATA = {
    "api_key": "k",
    "from_id": "stop_area:A", "from_name": "La Verpillière",
    "to_id": "stop_area:B", "to_name": "Lyon Part-Dieu",
    "start": "07:30:00", "end": "09:30:00",
    "weekdays": ["mon", "tue", "wed", "thu", "fri"], "count": 3,
    "notify": ["mobile_app_pixel"], "threshold": 5,
    "quiet_start": "22:00:00", "quiet_end": "06:00:00",
}


def entry(hass, **overrides):
    e = MockConfigEntry(domain=DOMAIN, title="La Verpillière → Lyon Part-Dieu", data={**DATA, **overrides}, entry_id="e1")
    e.add_to_hass(hass)
    return e


def train(delay=0, cancelled=False, number="17716"):
    (t,) = parse_journeys(response([journey(number, at(8, 12), delay=delay)]))
    return replace(t, cancelled=cancelled)


async def test_push_and_event(hass):
    calls: list[ServiceCall] = async_mock_service(hass, "notify", "mobile_app_pixel")
    events = async_capture_events(hass, EVENT_ALERT)
    n = AlertNotifier(hass, entry(hass))
    await n.async_load()
    await n.async_process([train(cancelled=True)], at(7, 0))
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["title"] == "🚆 La Verpillière → Lyon Part-Dieu"
    assert calls[0].data["message"].startswith("❌ TER 17716 08:12 SUPPRIMÉ")
    assert calls[0].data["data"]["tag"] == "sncf_17716_20261005"
    assert calls[0].data["data"]["priority"] == "high"
    assert calls[0].data["data"]["push"] == {"interruption-level": "time-sensitive"}
    assert len(events) == 1
    assert events[0].data["type"] == "cancelled"
    assert events[0].data["train_number"] == "17716"
    assert events[0].data["entry_id"] == "e1"


async def test_no_duplicate_after_reload(hass):
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    e = entry(hass)
    n = AlertNotifier(hass, e)
    await n.async_load()
    await n.async_process([train(delay=7)], at(7, 0))
    n2 = AlertNotifier(hass, e)
    await n2.async_load()
    await n2.async_process([train(delay=7)], at(7, 2))
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_quiet_hours_then_summary(hass):
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    events = async_capture_events(hass, EVENT_ALERT)
    n = AlertNotifier(hass, entry(hass))
    await n.async_load()
    await n.async_process([train(delay=7)], at(23, 0, day=4))
    await hass.async_block_till_done()
    assert calls == []
    assert len(events) == 1  # event still fired
    await n.async_process([train(delay=12)], at(6, 2))
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["message"] == "⚠️ TER 17716 08:12 +12 min"


async def test_quiet_hours_back_to_normal_no_summary(hass):
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    n = AlertNotifier(hass, entry(hass))
    await n.async_load()
    await n.async_process([train(delay=7)], at(23, 0, day=4))
    await n.async_process([train(delay=0)], at(5, 0))
    await n.async_process([train(delay=0)], at(6, 2))
    await hass.async_block_till_done()
    assert calls == []


async def test_no_notify_targets(hass):
    events = async_capture_events(hass, EVENT_ALERT)
    n = AlertNotifier(hass, entry(hass, notify=[]))
    await n.async_load()
    await n.async_process([train(delay=7)], at(7, 0))
    await hass.async_block_till_done()
    assert len(events) == 1


async def test_missing_notify_service_does_not_crash(hass, caplog):
    events = async_capture_events(hass, EVENT_ALERT)
    n = AlertNotifier(hass, entry(hass, notify=["mobile_app_gone"]))
    await n.async_load()
    await n.async_process([train(delay=7)], at(7, 0))
    await hass.async_block_till_done()
    assert len(events) == 1
    assert "mobile_app_gone" in caplog.text
```

- [ ] **Step 2: Run** — `.venv/bin/pytest tests/test_notifier.py -q` → FAIL (`ImportError: AlertNotifier`)

- [ ] **Step 3: Implement** — append to `alerts.py` (and extend imports at top)

Add to imports:
```python
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .const import (
    CONF_NOTIFY,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_THRESHOLD,
    DEFAULT_QUIET_END,
    DEFAULT_QUIET_START,
    DEFAULT_THRESHOLD,
    DELAY_CHANGE_STEP,
    DOMAIN,
    EVENT_ALERT,
)
from .schedule import in_quiet_hours, parse_time

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
```

Append:
```python
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
        if data := await self._store.async_load():
            self._state = data

    async def async_process(self, trains: list[Train], now: datetime) -> None:
        """Diff, then notify or queue during quiet hours."""
        opts = self._opts
        threshold = opts.get(CONF_THRESHOLD, DEFAULT_THRESHOLD)
        known = set(self._state["trains"])
        changed = purge_state(self._state["trains"], now)
        alerts = compute_alerts(self._state["trains"], trains, threshold)
        changed = changed or bool(alerts) or set(self._state["trains"]) != known

        for alert in alerts:
            self._fire_event(alert)

        quiet = in_quiet_hours(
            now.time(),
            parse_time(opts.get(CONF_QUIET_START, DEFAULT_QUIET_START)),
            parse_time(opts.get(CONF_QUIET_END, DEFAULT_QUIET_END)),
        )
        pending: list[str] = self._state["pending"]
        if quiet:
            for alert in alerts:
                if alert.train.id not in pending:
                    pending.append(alert.train.id)
        else:
            if pending:
                concerned = [t for t in trains if t.id in pending]
                if summary := format_summary(concerned, threshold):
                    await self._push(summary, tag=f"sncf_summary_{now:%Y%m%d}")
                alerts = [a for a in alerts if a.train.id not in pending]
                pending.clear()
                changed = True
            for alert in alerts:
                await self._push_alert(alert, trains)

        if changed:
            await self._store.async_save(self._state)

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
            },
        )

    async def _push_alert(self, alert: Alert, trains: list[Train]) -> None:
        t = alert.train
        extra = {}
        if alert.type == "cancelled":
            extra = {"priority": "high", "ttl": 0, "push": {"interruption-level": "time-sensitive"}}
        await self._push(
            format_message(alert, trains),
            tag=f"sncf_{t.number}_{t.base_departure:%Y%m%d}",
            **extra,
        )

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
                    blocking=True,
                )
            except HomeAssistantError as err:
                _LOGGER.warning("Notification via notify.%s impossible : %s", service, err)
```

Note : `ServiceNotFound` hérite de `HomeAssistantError`.

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/test_notifier.py tests/test_alerts.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/sncf_trajets/alerts.py tests/test_notifier.py
git commit -m "feat: envoi des notifications push et événements"
```

---

### Task 5: Coordinator

**Files:**
- Create: `custom_components/sncf_trajets/coordinator.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `SncfApiClient.get_trains`, exceptions (Task 2), `AlertNotifier.async_process` (Task 4), `next_window`, `poll_interval`, `parse_time` (Task 1)
- Produces: `SncfTrajetCoordinator(hass, entry, client, notifier)` — `DataUpdateCoordinator[TrajetData]`; property `opts -> dict`.

- [ ] **Step 1: Failing tests** — `tests/test_coordinator.py`

```python
"""Tests for the coordinator."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sncf_trajets.api import SncfApiError, SncfAuthError, parse_journeys
from custom_components.sncf_trajets.const import DOMAIN
from custom_components.sncf_trajets.coordinator import SncfTrajetCoordinator

from .navitia import at, journey, response
from .test_notifier import DATA


def trains(*spec):
    return parse_journeys(response([journey(n, at(h, m), delay=d) for n, h, m, d in spec]))


@pytest.fixture
def setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, title="A → B", data=DATA, entry_id="e1")
    entry.add_to_hass(hass)
    client = MagicMock()
    client.get_trains = AsyncMock()
    notifier = MagicMock()
    notifier.async_process = AsyncMock()
    return SncfTrajetCoordinator(hass, entry, client, notifier), client, notifier


async def test_future_window_filtered_and_limited(setup, freezer):
    coord, client, notifier = setup
    freezer.move_to(at(14, 0, day=9))  # Friday afternoon -> Monday 12th
    client.get_trains.return_value = parse_journeys(response([
        journey(n, at(h, m, day=12))
        for n, h, m in [("A", 7, 0), ("B", 7, 42), ("C", 8, 12), ("D", 8, 42), ("E", 9, 12)]
    ]))
    data = await coord._async_update_data()
    args = client.get_trains.await_args.args
    assert args[0] == "stop_area:A" and args[1] == "stop_area:B"
    assert args[2] == at(7, 30, day=12)
    assert args[3] == 6
    assert [t.number for t in data.trains] == ["B", "C", "D"]
    assert data.is_future_window is True
    assert coord.update_interval == timedelta(minutes=15)
    notifier.async_process.assert_awaited_once()


async def test_inside_window_drops_departed_trains(setup, freezer):
    coord, client, _ = setup
    freezer.move_to(at(8, 0))
    client.get_trains.return_value = trains(("B", 7, 42, 0), ("Late", 7, 50, 15), ("C", 8, 12, 0))
    data = await coord._async_update_data()
    assert client.get_trains.await_args.args[2] == at(7, 30)
    assert [t.number for t in data.trains] == ["Late", "C"]
    assert data.is_future_window is False
    assert coord.update_interval == timedelta(minutes=2)


async def test_disruptions_deduplicated(setup, freezer):
    coord, client, _ = setup
    freezer.move_to(at(7, 0))
    ts = trains(("B", 7, 42, 0), ("C", 8, 12, 0))
    client.get_trains.return_value = [replace(t, cause="Grève") for t in ts]
    data = await coord._async_update_data()
    assert data.disruptions == ["Grève"]


async def test_auth_error(setup, freezer):
    coord, client, _ = setup
    freezer.move_to(at(7, 0))
    client.get_trains.side_effect = SncfAuthError("401")
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


async def test_api_error_first_refresh_raises(setup, freezer):
    coord, client, _ = setup
    freezer.move_to(at(7, 0))
    client.get_trains.side_effect = SncfApiError("boom")
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


async def test_api_error_keeps_stale_data_and_backs_off(setup, freezer):
    coord, client, notifier = setup
    freezer.move_to(at(7, 0))
    client.get_trains.return_value = trains(("B", 7, 42, 0))
    coord.data = await coord._async_update_data()
    client.get_trains.side_effect = SncfApiError("boom")
    data = await coord._async_update_data()
    assert data.stale is True
    assert [t.number for t in data.trains] == ["B"]
    assert coord.update_interval == timedelta(minutes=4)
    coord.data = data
    await coord._async_update_data()
    assert coord.update_interval == timedelta(minutes=8)
    assert notifier.async_process.await_count == 1  # no alerts on stale data
```

- [ ] **Step 2: Run** — `.venv/bin/pytest tests/test_coordinator.py -q` → FAIL

- [ ] **Step 3: Implement** — `custom_components/sncf_trajets/coordinator.py`

```python
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
            return replace(self.data, stale=True)
        self._failures = 0

        selected = [
            t
            for t in trains
            if window.start <= t.base_departure <= window.end
            and (t.departure >= now if not t.cancelled else t.base_departure >= now)
        ][:count]
        causes = list(dict.fromkeys(t.cause for t in selected if t.cause))

        await self.notifier.async_process(selected, now)
        self.update_interval = poll_interval(now, window)
        return TrajetData(
            window_start=window.start,
            window_end=window.end,
            trains=selected,
            disruptions=causes,
            last_update=now,
            is_future_window=now < window.start,
        )
```

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/test_coordinator.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/sncf_trajets/coordinator.py tests/test_coordinator.py
git commit -m "feat: coordinateur avec polling adaptatif"
```

---

### Task 6: Config flow, options flow, reauth + translations

**Files:**
- Create: `custom_components/sncf_trajets/config_flow.py`, `custom_components/sncf_trajets/strings.json`, `custom_components/sncf_trajets/translations/en.json`, `custom_components/sncf_trajets/translations/fr.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `SncfApiClient.search_stations`, `Station`, exceptions (Task 2), constants (Task 1)
- Produces: entry `data` with keys `api_key, from_id, from_name, to_id, to_name, start, end, weekdays, count, notify, threshold, quiet_start, quiet_end`; title `"<from_name> → <to_name>"`; unique_id `"<from_id>_<to_id>_<start>_<end>"`. Options flow writes the same schedule/notify keys into `entry.options`.

Flow steps: `user` (api_key) → `from_station` (query) → `from_pick` (station) → `to_station` → `to_pick` → `schedule` (start, end, weekdays, count) → `notify` (notify, threshold, quiet_start, quiet_end) → create entry. `user` is skipped if another entry already has a key (key reused). Reauth: `reauth` → `reauth_confirm` (api_key).

- [ ] **Step 1: Failing tests** — `tests/test_config_flow.py`

```python
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
    with patch(SEARCH, AsyncMock(side_effect=stations)), patch(SETUP, return_value=True):
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
    with patch(SETUP, return_value=True):
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
    with patch(SEARCH, AsyncMock(side_effect=stations)), patch(SETUP, return_value=True):
        r = await entry.start_reauth_flow(hass)
        assert r["step_id"] == "reauth_confirm"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"api_key": "new"})
    assert r["type"] is FlowResultType.ABORT
    assert r["reason"] == "reauth_successful"
    assert entry.data["api_key"] == "new"
```

- [ ] **Step 2: Run** — `.venv/bin/pytest tests/test_config_flow.py -q` → FAIL

- [ ] **Step 3: Implement** — `custom_components/sncf_trajets/config_flow.py`

```python
"""Config flow for SNCF Trajets."""

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
            await self._client(api_key).search_stations("paris")
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
```

`strings.json` and `translations/en.json` (identical content):

```json
{
  "config": {
    "step": {
      "user": {"title": "SNCF API key", "description": "Free key from https://numerique.sncf.com/startup/api/token-developpeur/", "data": {"api_key": "API key"}},
      "from_station": {"title": "Departure station", "data": {"query": "Search a station"}},
      "from_pick": {"title": "Departure station", "data": {"station": "Station"}},
      "to_station": {"title": "Arrival station", "data": {"query": "Search a station"}},
      "to_pick": {"title": "Arrival station", "data": {"station": "Station"}},
      "schedule": {"title": "Time window", "data": {"start": "From", "end": "To", "weekdays": "Days", "count": "Number of trains"}},
      "notify": {"title": "Notifications", "description": "Mobile devices to notify on delay or cancellation.", "data": {"notify": "Devices", "threshold": "Delay threshold", "quiet_start": "Quiet hours start", "quiet_end": "Quiet hours end"}},
      "reauth_confirm": {"title": "New API key", "data": {"api_key": "API key"}}
    },
    "error": {
      "invalid_auth": "Invalid API key",
      "cannot_connect": "Cannot reach the SNCF API",
      "no_station_found": "No station found",
      "same_station": "Arrival must differ from departure",
      "no_weekday": "Select at least one day"
    },
    "abort": {"already_configured": "This trajet already exists", "reauth_successful": "API key updated"}
  },
  "options": {
    "step": {
      "init": {"title": "Trajet settings", "data": {"start": "From", "end": "To", "weekdays": "Days", "count": "Number of trains", "notify": "Devices", "threshold": "Delay threshold", "quiet_start": "Quiet hours start", "quiet_end": "Quiet hours end"}}
    },
    "error": {"no_weekday": "Select at least one day"}
  },
  "selector": {
    "weekdays": {"options": {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}}
  },
  "entity": {
    "sensor": {
      "next_train": {"name": "Next train"},
      "train": {"name": "Train {index}", "state": {"on_time": "On time", "delayed": "Delayed", "cancelled": "Cancelled"}}
    },
    "binary_sensor": {"disruption": {"name": "Disruption"}}
  }
}
```

`translations/fr.json`:

```json
{
  "config": {
    "step": {
      "user": {"title": "Clé API SNCF", "description": "Clé gratuite sur https://numerique.sncf.com/startup/api/token-developpeur/", "data": {"api_key": "Clé API"}},
      "from_station": {"title": "Gare de départ", "data": {"query": "Rechercher une gare"}},
      "from_pick": {"title": "Gare de départ", "data": {"station": "Gare"}},
      "to_station": {"title": "Gare d'arrivée", "data": {"query": "Rechercher une gare"}},
      "to_pick": {"title": "Gare d'arrivée", "data": {"station": "Gare"}},
      "schedule": {"title": "Plage horaire", "data": {"start": "De", "end": "À", "weekdays": "Jours", "count": "Nombre de trains"}},
      "notify": {"title": "Notifications", "description": "Appareils mobiles à prévenir en cas de retard ou suppression.", "data": {"notify": "Appareils", "threshold": "Seuil de retard", "quiet_start": "Début du silence", "quiet_end": "Fin du silence"}},
      "reauth_confirm": {"title": "Nouvelle clé API", "data": {"api_key": "Clé API"}}
    },
    "error": {
      "invalid_auth": "Clé API invalide",
      "cannot_connect": "API SNCF injoignable",
      "no_station_found": "Aucune gare trouvée",
      "same_station": "La gare d'arrivée doit être différente du départ",
      "no_weekday": "Choisissez au moins un jour"
    },
    "abort": {"already_configured": "Ce trajet existe déjà", "reauth_successful": "Clé API mise à jour"}
  },
  "options": {
    "step": {
      "init": {"title": "Réglages du trajet", "data": {"start": "De", "end": "À", "weekdays": "Jours", "count": "Nombre de trains", "notify": "Appareils", "threshold": "Seuil de retard", "quiet_start": "Début du silence", "quiet_end": "Fin du silence"}}
    },
    "error": {"no_weekday": "Choisissez au moins un jour"}
  },
  "selector": {
    "weekdays": {"options": {"mon": "Lundi", "tue": "Mardi", "wed": "Mercredi", "thu": "Jeudi", "fri": "Vendredi", "sat": "Samedi", "sun": "Dimanche"}}
  },
  "entity": {
    "sensor": {
      "next_train": {"name": "Prochain train"},
      "train": {"name": "Train {index}", "state": {"on_time": "À l'heure", "delayed": "En retard", "cancelled": "Supprimé"}}
    },
    "binary_sensor": {"disruption": {"name": "Perturbation"}}
  }
}
```

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/test_config_flow.py -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/sncf_trajets/config_flow.py custom_components/sncf_trajets/strings.json custom_components/sncf_trajets/translations tests/test_config_flow.py
git commit -m "feat: assistant de configuration, options et réauthentification"
```

---

### Task 7: Entities + integration setup + card registration

**Files:**
- Modify: `custom_components/sncf_trajets/__init__.py`
- Create: `custom_components/sncf_trajets/sensor.py`, `custom_components/sncf_trajets/binary_sensor.py`, `custom_components/sncf_trajets/entity.py`, `custom_components/sncf_trajets/card.py` (`card.py` references `www/sncf-trajets-card.js`, created in Task 8; serving a missing file is harmless until then)
- Test: `tests/test_init.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `type SncfConfigEntry = ConfigEntry[SncfTrajetCoordinator]`; entities with unique ids `<entry_id>_next_train`, `<entry_id>_train_<i>`, `<entry_id>_disruption`; `async_register_card(hass) -> None`.

- [ ] **Step 1: Failing tests** — `tests/test_init.py`

```python
"""Integration-level tests."""

from dataclasses import replace
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sncf_trajets.api import parse_journeys
from custom_components.sncf_trajets.const import DOMAIN

from .navitia import at, journey, response
from .test_notifier import DATA

GET_TRAINS = "custom_components.sncf_trajets.api.SncfApiClient.get_trains"


def sample():
    ts = parse_journeys(response([journey("17714", at(7, 42)), journey("17716", at(8, 12), delay=7), journey("17718", at(8, 42))]))
    ts[2] = replace(ts[2], cancelled=True, cause="Mouvement social")
    return ts


async def setup_entry(hass, trains):
    entry = MockConfigEntry(domain=DOMAIN, title="La Verpillière → Lyon Part-Dieu", data={**DATA, "notify": []}, unique_id="u", entry_id="e1")
    entry.add_to_hass(hass)
    with patch(GET_TRAINS, AsyncMock(return_value=trains)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def eid(hass, unique_id, domain="sensor"):
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


async def test_entities(hass, freezer):
    freezer.move_to(at(7, 0))
    await setup_entry(hass, sample())

    nxt = hass.states.get(eid(hass, "e1_next_train"))
    assert nxt.attributes["from_name"] == "La Verpillière"
    assert nxt.attributes["to_name"] == "Lyon Part-Dieu"
    assert [t["number"] for t in nxt.attributes["trains"]] == ["17714", "17716", "17718"]
    assert nxt.attributes["disruptions"] == ["Mouvement social"]
    assert nxt.attributes["is_future_window"] is False
    assert nxt.attributes["stale"] is False

    assert hass.states.get(eid(hass, "e1_train_1")).state == "on_time"
    assert hass.states.get(eid(hass, "e1_train_2")).state == "delayed"
    assert hass.states.get(eid(hass, "e1_train_3")).state == "cancelled"
    assert hass.states.get(eid(hass, "e1_train_2")).attributes["delay_minutes"] == 7
    assert hass.states.get(eid(hass, "e1_disruption", "binary_sensor")).state == "on"


async def test_next_train_timestamp(hass, freezer):
    freezer.move_to(at(7, 0))
    await setup_entry(hass, sample())
    from homeassistant.util import dt as dt_util
    state = hass.states.get(eid(hass, "e1_next_train")).state
    assert dt_util.parse_datetime(state) == at(7, 42)


async def test_no_train(hass, freezer):
    freezer.move_to(at(7, 0))
    await setup_entry(hass, [])
    assert hass.states.get(eid(hass, "e1_next_train")).state == "unknown"
    assert hass.states.get(eid(hass, "e1_train_1")).state == "unknown"
    assert hass.states.get(eid(hass, "e1_disruption", "binary_sensor")).state == "off"


async def test_unload(hass, freezer):
    freezer.move_to(at(7, 0))
    entry = await setup_entry(hass, sample())
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
```

- [ ] **Step 2: Run** — `.venv/bin/pytest tests/test_init.py -q` → FAIL

- [ ] **Step 3: Implement**

`custom_components/sncf_trajets/__init__.py`:
```python
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
```

`custom_components/sncf_trajets/entity.py`:
```python
"""Base entity."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SncfTrajetCoordinator


class SncfEntity(CoordinatorEntity[SncfTrajetCoordinator]):
    """Entity attached to the trajet device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SncfTrajetCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="SNCF",
            entry_type=DeviceEntryType.SERVICE,
        )
```

`custom_components/sncf_trajets/sensor.py`:
```python
"""Sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
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
```

`custom_components/sncf_trajets/binary_sensor.py`:
```python
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
```

`custom_components/sncf_trajets/card.py`:
```python
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
    """Expose the card JS and add it to Lovelace resources (storage mode)."""
    if hass.data.get(_DONE) or hass.http is None:
        return
    hass.data[_DONE] = True
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD_FILE), True)])

    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA
    except ImportError:
        return
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
```

- [ ] **Step 4: Run** — `.venv/bin/pytest -q` → all PASS (whole suite)

- [ ] **Step 5: Commit**

```bash
git add custom_components tests/test_init.py
git commit -m "feat: capteurs, binary_sensor et enregistrement de la carte"
```

---

### Task 8: Lovelace card

**Files:**
- Create: `custom_components/sncf_trajets/www/sncf-trajets-card.js`
- Test: `tests/card/render.test.mjs` (Node built-in test runner, no dependency), run with `node --test tests/card/`

**Interfaces:**
- Consumes: attributes of `sensor.*_next_train` (Task 7): `trains[]` (number, mode, base_departure, departure, delay_minutes, cancelled), `disruptions[]`, `from_name`, `to_name`, `window_start`, `window_end`, `is_future_window`, `stale`, `last_update`, `threshold`.
- Produces: custom elements `sncf-trajets-card` and `sncf-trajets-card-editor`; exported pure function `renderContent(stateObj, config) -> string` (HTML) exposed as `globalThis.SncfTrajetsCard.renderContent` for tests.

- [ ] **Step 1: Failing test** — `tests/card/render.test.mjs`

```js
import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {} };
globalThis.window = globalThis;
await import("../../custom_components/sncf_trajets/www/sncf-trajets-card.js");
const { renderContent } = globalThis.SncfTrajetsCard;

const t = (number, dep, delay = 0, cancelled = false) => ({
  number, mode: "TER",
  base_departure: `2026-10-05T${dep}:00+02:00`,
  departure: new Date(Date.parse(`2026-10-05T${dep}:00+02:00`) + delay * 60000).toISOString(),
  delay_minutes: delay, cancelled,
});
const state = (attrs) => ({
  state: "2026-10-05T05:42:00+00:00",
  attributes: {
    from_name: "La Verpillière", to_name: "Lyon Part-Dieu", trains: [], disruptions: [],
    is_future_window: false, stale: false, threshold: 5,
    window_start: "2026-10-05T07:30:00+02:00", window_end: "2026-10-05T09:30:00+02:00",
    last_update: "2026-10-05T07:31:00+02:00", ...attrs,
  },
});

test("rows reflect status", () => {
  const html = renderContent(state({ trains: [t("17714", "07:42"), t("17716", "08:12", 7), t("17718", "08:42", 0, true)], disruptions: ["Mouvement social"] }), {});
  assert.match(html, /La Verpillière → Lyon Part-Dieu/);
  assert.match(html, /17714[\s\S]*À l'heure/);
  assert.match(html, /<s>08:12<\/s>[\s\S]*\+7/);
  assert.match(html, /class="row cancelled"[\s\S]*17718[\s\S]*Supprimé/);
  assert.match(html, /Mouvement social/);
});

test("empty and stale and future window", () => {
  const html = renderContent(state({ stale: true, is_future_window: true }), { title: "Boulot" });
  assert.match(html, /Boulot/);
  assert.match(html, /Aucun train direct dans la plage/);
  assert.match(html, /données non à jour/);
  assert.match(html, /07:30–09:30/);
});

test("escapes html", () => {
  const html = renderContent(state({ disruptions: ["<img src=x>"] }), {});
  assert.doesNotMatch(html, /<img/);
});
```

- [ ] **Step 2: Run** — `node --test tests/card/` → FAIL (file missing)

- [ ] **Step 3: Implement** — `custom_components/sncf_trajets/www/sncf-trajets-card.js`

```js
/* SNCF Trajets card — compact list */
const VERSION = "0.1.0";
const TZ = "Europe/Paris";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const hm = (iso) =>
  new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });

const day = (iso) =>
  new Date(iso).toLocaleDateString("fr-FR", { weekday: "long", timeZone: TZ });

function row(t, threshold) {
  const label = `${esc(t.mode)} ${esc(t.number)}`.trim();
  if (t.cancelled) {
    return `<div class="row cancelled"><span class="time">🚆 ${hm(t.base_departure)}</span><span class="num">${label}</span><span class="status bad">❌ Supprimé</span></div>`;
  }
  if (t.delay_minutes > 0) {
    const cls = t.delay_minutes >= threshold ? "warn" : "minor";
    return `<div class="row"><span class="time">🚆 <s>${hm(t.base_departure)}</s> ${hm(t.departure)}</span><span class="num">${label}</span><span class="status ${cls}">⚠️ +${t.delay_minutes}</span></div>`;
  }
  return `<div class="row"><span class="time">🚆 ${hm(t.base_departure)}</span><span class="num">${label}</span><span class="status ok">✅ À l'heure</span></div>`;
}

function renderContent(stateObj, config) {
  if (!stateObj) return `<div class="empty">Entité introuvable</div>`;
  const a = stateObj.attributes;
  const title = config.title || `${a.from_name} → ${a.to_name}`;
  const sub = a.is_future_window
    ? `<div class="sub">${esc(day(a.window_start))} ${hm(a.window_start)}–${hm(a.window_end)}</div>`
    : "";
  const trains = a.trains || [];
  const rows = trains.length
    ? trains.map((t) => row(t, a.threshold ?? 5)).join("")
    : `<div class="empty">Aucun train direct dans la plage</div>`;
  const disr = (a.disruptions || []).length
    ? `<div class="disruption">${a.disruptions.map((d) => `ℹ️ ${esc(d)}`).join("<br>")}</div>`
    : "";
  const upd = a.last_update ? `MAJ ${hm(a.last_update)}` : "";
  const stale = a.stale ? ` · <span class="bad">données non à jour</span>` : "";
  return `<div class="header">${esc(title)}</div>${sub}<div class="rows">${rows}</div>${disr}<div class="footer">${upd}${stale}</div>`;
}

const STYLE = `
  ha-card { padding: 12px 16px; }
  .header { font-size: 1.1em; font-weight: 500; }
  .sub { color: var(--secondary-text-color); font-size: .9em; text-transform: capitalize; }
  .rows { margin-top: 8px; }
  .row { display: grid; grid-template-columns: auto 1fr auto; gap: 8px; padding: 4px 0; align-items: center; }
  .row.cancelled { opacity: .55; }
  .num { color: var(--secondary-text-color); }
  .ok { color: var(--success-color, #2e7d32); }
  .warn { color: var(--warning-color, #ef6c00); font-weight: 600; }
  .minor { color: var(--secondary-text-color); }
  .bad { color: var(--error-color, #c62828); }
  .empty { color: var(--secondary-text-color); padding: 8px 0; }
  .disruption { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--divider-color); font-size: .9em; }
  .footer { margin-top: 8px; color: var(--secondary-text-color); font-size: .8em; }
`;

class SncfTrajetsCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error("entity requis");
    this._config = config;
  }

  set hass(hass) {
    this._hass = hass;
    const stateObj = hass.states[this._config.entity];
    if (stateObj === this._last) return;
    this._last = stateObj;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.innerHTML = `<style>${STYLE}</style><ha-card><div id="c"></div></ha-card>`;
    }
    this.shadowRoot.getElementById("c").innerHTML = renderContent(stateObj, this._config);
  }

  getCardSize() {
    return 2 + (this._last?.attributes?.trains?.length || 1);
  }

  static getConfigElement() {
    return document.createElement("sncf-trajets-card-editor");
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (e) => e.startsWith("sensor.") && hass.states[e].attributes.trains !== undefined && hass.states[e].attributes.from_name
    );
    return { entity: entity || "" };
  }
}

class SncfTrajetsCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.schema = [
        { name: "entity", required: true, selector: { entity: { integration: "sncf_trajets", domain: "sensor", device_class: "timestamp" } } },
        { name: "title", selector: { text: {} } },
      ];
      this._form.computeLabel = (s) => ({ entity: "Trajet (capteur Prochain train)", title: "Titre (optionnel)" })[s.name];
      this._form.addEventListener("value-changed", (ev) => {
        this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: ev.detail.value }, bubbles: true, composed: true }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = this._config;
  }
}

if (!customElements.get("sncf-trajets-card")) {
  customElements.define("sncf-trajets-card", SncfTrajetsCard);
  customElements.define("sncf-trajets-card-editor", SncfTrajetsCardEditor);
}
window.customCards = window.customCards || [];
window.customCards.push({
  type: "sncf-trajets-card",
  name: "SNCF Trajets",
  description: "Prochains trains et perturbations d'un trajet SNCF",
  preview: true,
});
globalThis.SncfTrajetsCard = { renderContent, VERSION };
```

- [ ] **Step 4: Run** — `node --test tests/card/` → PASS ; `.venv/bin/pytest -q` → PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/sncf_trajets/www tests/card
git commit -m "feat: carte Lovelace liste compacte"
```

---

### Task 9: HACS packaging, README, CI

**Files:**
- Create: `hacs.json`, `README.md`, `LICENSE` (MIT, `Copyright (c) 2026 glorp-fr`), `.github/workflows/tests.yml`, `.github/workflows/validate.yml`

- [ ] **Step 1: `hacs.json`**

```json
{
  "name": "SNCF Trajets",
  "render_readme": true,
  "homeassistant": "2025.1.0"
}
```

- [ ] **Step 2: `.github/workflows/tests.yml`**

```yaml
name: Tests
on: [push, pull_request]
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -r requirements_test.txt
      - run: pytest -q
  card:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - run: node --test tests/card/
```

- [ ] **Step 3: `.github/workflows/validate.yml`**

```yaml
name: Validate
on:
  push:
  pull_request:
  schedule:
    - cron: "0 3 * * 1"
jobs:
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master
  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: hacs/action@main
        with:
          category: integration
          ignore: brands
```

- [ ] **Step 4: `README.md`** (French) with sections: présentation (capture ASCII de la carte), installation HACS (dépôt personnalisé `https://github.com/glorp-fr/ha-sncf-trajets`, catégorie Intégration), clé API (à récupérer ici : https://numerique.sncf.com/startup/api/token-developpeur/), configuration (étapes de l'assistant), entités créées (tableau), carte (`type: custom:sncf-trajets-card` + `entity`, ressource auto ; en mode YAML ajouter `/sncf_trajets/sncf-trajets-card.js` type module), notifications (types et exemples de messages du §6.1 de la spec, heures de silence), automatisation exemple :

```yaml
automation:
  - alias: Lumière rouge si train supprimé
    trigger:
      - platform: event
        event_type: sncf_trajets_alert
        event_data:
          type: cancelled
    action:
      - service: light.turn_on
        target: {entity_id: light.entree}
        data: {color_name: red}
```

quota API (≈300 requêtes/jour/trajet), limites (trains directs uniquement).

- [ ] **Step 5: Verify** — `.venv/bin/pytest -q && node --test tests/card/` → PASS ; `python3 -c "import json,glob;[json.load(open(f)) for f in glob.glob('**/*.json',recursive=True) if '.venv' not in f]"` → no error

- [ ] **Step 6: Commit**

```bash
git add hacs.json README.md LICENSE .github
git commit -m "chore: packaging HACS, README et CI"
```

---

### Task 10: Real-world check, publication, notification

**Files:** none modified unless the real API reveals a parsing gap (then: add a fixture reproducing the gap to `tests/test_api.py`, fix `api.py`, commit).

- [ ] **Step 1: Live API smoke test** (clé fournie par l'utilisateur, jamais écrite dans le dépôt) — script in scratchpad:

```python
import asyncio, aiohttp, os, sys
from datetime import datetime, timedelta
sys.path.insert(0, "<repo root>")
from custom_components.sncf_trajets.api import SncfApiClient
from custom_components.sncf_trajets.const import NAVITIA_TZ

async def main():
    async with aiohttp.ClientSession() as s:
        c = SncfApiClient(s, os.environ["SNCF_KEY"])
        a = (await c.search_stations("La Verpillière"))[0]
        b = (await c.search_stations("Lyon Part-Dieu"))[0]
        print(a, b)
        for t in await c.get_trains(a.id, b.id, datetime.now(NAVITIA_TZ), 6):
            print(t.number, t.mode, t.base_departure, t.departure, t.delay_minutes, t.cancelled, t.cause)

asyncio.run(main())
```

Run: `SNCF_KEY=... .venv/bin/python <scratchpad>/smoke.py` — expected: two stations, a list of TER trains with sane times.

- [ ] **Step 2: Publish** — `gh repo create glorp-fr/ha-sncf-trajets --public --source . --push --description "Intégration Home Assistant (HACS) : horaires et perturbations SNCF par trajet, carte Lovelace et notifications mobiles"`; then `gh release create v0.1.0 --title "v0.1.0" --notes "Première version"`; check CI with `gh run list`.

- [ ] **Step 3: Notify interested people** (out of scope of the repo).

- [ ] **Step 4: Install on the user's HA** — HACS → dépôts personnalisés → URL du dépôt → installer → redémarrer → ajouter l'intégration → ajouter la carte. Done by the user (or guided).
