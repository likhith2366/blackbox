from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


def iso(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def iso_now() -> str:
    return iso(time.time())


def parse(s: str) -> float:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def shift(s: str, seconds: float) -> str:
    return iso(parse(s) + seconds)


def iso_days_before(s: str, days: float, hour: int | None = None, minute: int = 0) -> str:
    dt = datetime.fromtimestamp(parse(s), tz=timezone.utc) - timedelta(days=days)
    if hour is not None:
        dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return iso(dt.timestamp())
