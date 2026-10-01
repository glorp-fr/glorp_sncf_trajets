"""Navitia (API SNCF) client."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import re
from typing import Any

import aiohttp

from .const import API_BASE, NAVITIA_TZ
from .models import Train

DEPARTURES_COUNT = 200
_TAG_RE = re.compile(r"<[^>]+>")
_DATE_RE = re.compile(r":(\d{4}-\d{2}-\d{2}):")


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


def _disruption_matches_number(disruption: dict, number: str, base_departure: datetime) -> bool:
    """Check if disruption targets the given train number.

    A disruption targets train number if, for any impacted object with
    embedded_type in ("trip", "vehicle_journey"):
    - pt.get("name") == number OR
    - f":{number}:" is contained in pt.get("id", "") or pt.get("name", "")

    If the pt_object id or name contains a service date in form :YYYY-MM-DD:,
    it must equal base_departure.date() (Paris local) for a match.
    If no date is present, match on number alone.

    Guard against empty number (never match on "").
    """
    if not number:
        return False

    for obj in disruption.get("impacted_objects", []):
        pt = obj.get("pt_object", {})
        if pt.get("embedded_type") in ("trip", "vehicle_journey"):
            pt_name = pt.get("name", "")
            pt_id = pt.get("id", "")

            # Check if name matches exactly or contains :number:
            if pt_name == number or f":{number}:" in pt_name or f":{number}:" in pt_id:
                # Check if there's a date component in the ID or name
                # Format: SNCF:2026-10-05:17716:1187:Train
                date_match = _DATE_RE.search(pt_id) or _DATE_RE.search(pt_name)
                if date_match:
                    # If date is present, it must match
                    disruption_date_str = date_match.group(1)
                    expected_date = base_departure.astimezone(NAVITIA_TZ).date().isoformat()
                    if disruption_date_str != expected_date:
                        continue

                return True

    return False


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
        base_departure = _parse_dt(sec.get("base_departure_date_time", sec["departure_date_time"]))
        linked = [
            by_id[link["id"]]
            for link in info.get("links", [])
            if link.get("type") == "disruption" and link.get("id") in by_id
        ]
        linked += [d for d in disruptions if d not in linked and _disruption_matches_number(d, number, base_departure)]
        cancelled = any(d.get("severity", {}).get("effect") == "NO_SERVICE" for d in linked)
        cause = next((m for m in (_message(d) for d in linked) if m), None)

        # Handle mode: convert REGION* to TER
        mode = info.get("commercial_mode") or ""
        if mode.upper().startswith("REGION"):
            mode = "TER"

        trains.append(
            Train(
                number=number,
                mode=mode,
                base_departure=base_departure,
                departure=_parse_dt(sec["departure_date_time"]),
                base_arrival=_parse_dt(sec.get("base_arrival_date_time", sec["arrival_date_time"])),
                arrival=_parse_dt(sec["arrival_date_time"]),
                cancelled=cancelled,
                cause=cause,
            )
        )
    return trains


def merge_trains(base: list[Train], realtime: list[Train]) -> list[Train]:
    """Merge theoretical and realtime trains.

    A base train missing from the realtime page while a later realtime train
    exists is only *suspected* cancelled (it may just be heavily delayed and
    pushed off the page); see confirm_suspected.
    """
    rt_by_id = {t.id: t for t in realtime}
    latest_rt = max((t.base_departure for t in realtime), default=None)
    merged: list[Train] = []
    for train in base:
        rt = rt_by_id.pop(train.id, None)
        if rt is not None:
            merged.append(rt)
        elif train.cancelled:
            merged.append(train)
        elif latest_rt is not None and latest_rt > train.base_departure:
            merged.append(replace(train, suspected_cancelled=True))
        else:
            merged.append(train)
    merged.extend(rt_by_id.values())
    merged.sort(key=lambda t: t.base_departure)
    return merged


def confirm_suspected(
    trains: list[Train], payload: dict[str, Any], limit: int = DEPARTURES_COUNT
) -> list[Train]:
    """Resolve suspected cancellations using a stop_area departures payload.

    Raises SncfApiError when the page cannot prove a train is absent.
    """
    if not any(t.suspected_cancelled for t in trains):
        return trains
    if "departures" not in payload:
        raise SncfApiError("cancellation unconfirmed")
    by_id = {d.get("id"): d for d in payload.get("disruptions", [])}
    departures = payload["departures"]
    last_dep: datetime | None = None
    if departures:
        try:
            last_dep = _parse_dt(departures[-1]["stop_date_time"]["departure_date_time"])
        except (KeyError, ValueError):
            last_dep = None
    result: list[Train] = []
    for train in trains:
        if not train.suspected_cancelled:
            result.append(train)
            continue
        found = None
        for dep in departures:
            info = dep.get("display_informations", {})
            sdt = dep.get("stop_date_time", {})
            if train.number not in (info.get("headsign"), info.get("trip_short_name")):
                continue
            try:
                base = _parse_dt(sdt["base_departure_date_time"])
            except (KeyError, ValueError):
                continue
            if base == train.base_departure:
                found = (info, sdt)
                break
        if found is None:
            conclusive = len(departures) < limit or (
                last_dep is not None and last_dep >= train.base_departure + timedelta(minutes=180)
            )
            if not conclusive:
                raise SncfApiError("cancellation unconfirmed")
            result.append(replace(train, cancelled=True, suspected_cancelled=False))
            continue
        info, sdt = found
        linked = [
            by_id[link["id"]]
            for link in info.get("links", [])
            if link.get("type") == "disruption" and link.get("id") in by_id
        ]
        cause = next((m for m in (_message(d) for d in linked) if m), None)
        if any(d.get("severity", {}).get("effect") == "NO_SERVICE" for d in linked):
            result.append(replace(train, cancelled=True, suspected_cancelled=False, cause=cause or train.cause))
            continue
        try:
            departure = _parse_dt(sdt["departure_date_time"])
        except (KeyError, ValueError):
            departure = train.base_departure
        result.append(
            replace(
                train,
                departure=departure,
                arrival=train.base_arrival + (departure - train.base_departure),
                cancelled=False,
                suspected_cancelled=False,
                cause=cause or train.cause,
            )
        )
    return result


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
                    try:
                        payload = await resp.json(content_type=None) or {}
                    except ValueError:
                        payload = {}
                    error_id = (payload.get("error") or {}).get("id") if isinstance(payload, dict) else None
                    if error_id == "no_solution":
                        return payload
                    raise SncfApiError(f"HTTP 404 {error_id or ''}".strip())
                if resp.status >= 400:
                    raise SncfApiError(f"HTTP {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
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
        merged = merge_trains(parse_journeys(base), parse_journeys(realtime))
        suspected = [t for t in merged if t.suspected_cancelled]
        if not suspected:
            return merged
        earliest = min(t.base_departure for t in suspected)
        try:
            payload = await self._get(
                f"stop_areas/{from_id}/departures",
                [
                    ("from_datetime", earliest.astimezone(NAVITIA_TZ).strftime("%Y%m%dT%H%M%S")),
                    ("data_freshness", "realtime"),
                    ("duration", "21600"),
                    ("count", str(DEPARTURES_COUNT)),
                ],
            )
        except SncfApiError:
            # Never alert on unconfirmed data: coordinator keeps previous data
            raise
        return confirm_suspected(merged, payload)
