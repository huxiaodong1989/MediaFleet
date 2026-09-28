import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_FIXED_TIMEZONES = {
    "asia/shanghai": timezone(timedelta(hours=8)),
    "prc": timezone(timedelta(hours=8)),
    "cst-8": timezone(timedelta(hours=8)),
    "utc+8": timezone(timedelta(hours=8)),
    "utc+08:00": timezone(timedelta(hours=8)),
}


def app_now() -> datetime:
    """Return the app's business-local naive datetime for DB DateTime columns."""
    timezone_name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai"
    try:
        return datetime.now(ZoneInfo(timezone_name)).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        fixed_timezone = _FIXED_TIMEZONES.get(timezone_name.strip().lower())
        if fixed_timezone:
            return datetime.now(fixed_timezone).replace(tzinfo=None)
        return datetime.now()
