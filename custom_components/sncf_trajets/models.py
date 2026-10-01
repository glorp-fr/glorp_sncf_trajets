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
    suspected_cancelled: bool = False

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
