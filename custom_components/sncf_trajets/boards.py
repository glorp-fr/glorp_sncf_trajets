"""Gares & Connexions departure boards (platform / voie)."""

from __future__ import annotations

import asyncio
from datetime import datetime
import re
from typing import Any

import aiohttp

BOARD_URL = "https://www.garesetconnexions.sncf/schedule-table/Departures/00{uic}"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "application/json",
}
_UIC_RE = re.compile(r":(\d{8})$")


class BoardError(Exception):
    """Departure board unavailable (unofficial API, never fatal)."""


def uic_from_stop_area(stop_area_id: str | None) -> str | None:
    """'stop_area:SNCF:87723395' -> '87723395'."""
    match = _UIC_RE.search(stop_area_id or "")
    return match.group(1) if match else None


def parse_board(payload: Any) -> dict[tuple[str, datetime], str]:
    """Map (train number, scheduled departure) to the announced track."""
    if not isinstance(payload, list):
        raise BoardError("unexpected payload")
    result: dict[tuple[str, datetime], str] = {}
    for item in payload:
        try:
            number = str(item["trainNumber"])
            scheduled = datetime.fromisoformat(item["scheduledTime"])
            track = str((item.get("platform") or {}).get("track") or "").strip()
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if track and scheduled.tzinfo is not None:
            result[(number, scheduled)] = track
    return result


class BoardClient:
    """Thin async client for the station departure board."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def get_platforms(self, uic: str) -> dict[tuple[str, datetime], str]:
        """Return the tracks currently announced at this station."""
        try:
            async with self._session.get(
                BOARD_URL.format(uic=uic),
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    raise BoardError(f"HTTP {resp.status}")
                payload = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise BoardError(str(err) or type(err).__name__) from err
        return parse_board(payload)
