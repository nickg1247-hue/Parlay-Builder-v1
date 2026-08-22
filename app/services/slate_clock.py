"""Calendar day used by slates, boards, and scores.

Sports days roll at midnight America/New_York, not the server's local/UTC date.
A VPS in UTC would otherwise show tomorrow's games after 8pm ET.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

SLATE_TZ = ZoneInfo("America/New_York")


def slate_now() -> datetime:
    return datetime.now(SLATE_TZ)


def slate_today() -> date:
    return slate_now().date()
