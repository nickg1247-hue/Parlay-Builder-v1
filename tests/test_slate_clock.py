from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.slate_clock import SLATE_TZ, slate_today


def test_slate_today_matches_eastern_calendar():
    assert slate_today() == datetime.now(SLATE_TZ).date()


def test_slate_timezone_is_new_york():
    assert SLATE_TZ == ZoneInfo("America/New_York")
