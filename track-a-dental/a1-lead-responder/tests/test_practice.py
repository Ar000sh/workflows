from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

BERLIN = ZoneInfo("Europe/Berlin")


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=BERLIN)


def test_open_on_monday_morning(practice):
    assert practice.is_open(at(2026, 10, 5, 10))  # Monday


def test_closed_at_closing_time(practice):
    assert not practice.is_open(at(2026, 10, 5, 18))


def test_closed_on_friday_afternoon(practice):
    assert not practice.is_open(at(2026, 10, 9, 15))  # Friday, closes 14:00


def test_closed_on_saturday(practice):
    assert not practice.is_open(at(2026, 10, 10, 10))


def test_closed_on_easter_monday(practice):
    assert not practice.is_open(at(2026, 4, 6, 10))  # public holiday, a weekday


def test_converts_other_timezones(practice):
    # 07:30 UTC is 09:30 in Hannover (summer time)
    assert practice.is_open(datetime(2026, 10, 5, 7, 30, tzinfo=timezone.utc))


def test_naive_datetime_is_rejected(practice):
    with pytest.raises(ValueError):
        practice.is_open(datetime(2026, 10, 5, 10, 0))


def test_loads_contact_details(practice):
    assert practice.name == "Zahnarztpraxis BrightSmile"
    assert practice.phone == "0511 0000 100"
    assert practice.notdienst_phone == "0511 0000 199"
    assert practice.doctolib_url.startswith("https://www.doctolib.de/")
