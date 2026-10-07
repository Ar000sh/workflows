"""Structured practice facts that code needs (not just the AI): hours, holidays, phone numbers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays
import yaml

from lead_responder.config import DATA_DIR

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


@dataclass(frozen=True)
class Practice:
    name: str
    address: str
    phone: str
    email: str
    notdienst_phone: str
    doctolib_url: str
    timezone: str
    holiday_subdivision: str
    opening_hours: dict[int, tuple[time, time]]  # weekday (0 = Monday) -> (opens, closes)

    def is_open(self, now: datetime) -> bool:
        if now.tzinfo is None:
            # Guessing a timezone could send the wrong emergency number.
            raise ValueError("now must be timezone-aware")
        local = now.astimezone(ZoneInfo(self.timezone))
        if local.date() in holidays.Germany(subdiv=self.holiday_subdivision, years=local.year):
            return False
        hours = self.opening_hours.get(local.weekday())
        if hours is None:
            return False
        opens, closes = hours
        return opens <= local.time() < closes


def load_practice(path: Path = DATA_DIR / "practice.yaml") -> Practice:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    opening_hours = {
        WEEKDAYS.index(day): (time.fromisoformat(opens), time.fromisoformat(closes))
        for day, (opens, closes) in raw["opening_hours"].items()
    }
    return Practice(
        name=raw["name"],
        address=raw["address"],
        phone=raw["phone"],
        email=raw["email"],
        notdienst_phone=raw["notdienst_phone"],
        doctolib_url=raw["doctolib_url"],
        timezone=raw["timezone"],
        holiday_subdivision=raw["holiday_subdivision"],
        opening_hours=opening_hours,
    )
