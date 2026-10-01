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
        "sections": [
            {"type": "crow_fly"},
            section,
            {"type": "crow_fly"},
        ],
    }


def disruption(id_, effect, text, number=None):
    impacted = []
    if number:
        impacted = [{"pt_object": {"embedded_type": "trip", "id": f"SNCF:2026-10-05:{number}:1187:Train", "name": f"SNCF:2026-10-05:{number}:1187:Train"}}]
    return {
        "id": id_,
        "severity": {"effect": effect},
        "messages": [{"text": f"<p>{text}</p>"}],
        "impacted_objects": impacted,
    }


def response(journeys=(), disruptions=()):
    return {"journeys": list(journeys), "disruptions": list(disruptions)}


def departure(number, base_dep, delay=0, links=()):
    dep = base_dep + timedelta(minutes=delay)
    return {
        "display_informations": {
            "headsign": number,
            "trip_short_name": number,
            "links": [{"type": "disruption", "id": i} for i in links],
        },
        "stop_date_time": {
            "base_departure_date_time": nav(base_dep),
            "departure_date_time": nav(dep),
            "data_freshness": "realtime" if delay else "base_schedule",
        },
    }


def departures_response(departures=(), disruptions=()):
    return {"departures": list(departures), "disruptions": list(disruptions)}
