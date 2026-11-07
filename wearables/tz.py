"""One reference zone for every calendar-day decision.

Apple prints every timestamp in the exporting phone's *current* zone, so the printed offset says nothing
about where a sample was taken. The UTC instant is right; days, weeks and nights are bucketed in New York.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
TZ_LABEL = "ET"

# A "night" runs 18:00 to 18:00 local time, so a post-midnight segment belongs to the night before.
SLEEP_NIGHT_SHIFT = timedelta(hours=18)


def local_day(dt: datetime) -> date:
    return dt.astimezone(TZ).date()


def local_night(dt: datetime) -> date:
    return (dt.astimezone(TZ) - SLEEP_NIGHT_SHIFT).date()
