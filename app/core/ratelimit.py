"""Per-platform daily caps and polite pacing, so Sumit's LinkedIn account never gets restricted."""

import asyncio
import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.config import get_settings
from app.core.db import ToolCall, kv_get, kv_set, session


class RateLimited(RuntimeError):
    pass


def _local_midnight_utc() -> datetime:
    tz = ZoneInfo(get_settings().timezone)
    midnight = datetime.combine(datetime.now(tz).date(), time.min, tzinfo=tz)
    return midnight.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def used_today(key: str) -> int:
    with session() as s:
        return s.scalar(
            select(func.count()).select_from(ToolCall).where(ToolCall.key == key, ToolCall.at >= _local_midnight_utc())
        ) or 0


def check(key: str, daily_cap: int | None) -> None:
    if is_paused(key.split(".")[0]):
        raise RateLimited(f"{key.split('.')[0]} is paused for today")
    if daily_cap is not None and used_today(key) >= daily_cap:
        raise RateLimited(f"{key}: daily cap {daily_cap} reached")


def record(key: str, agent: str, ok: bool = True) -> None:
    with session() as s:
        s.add(ToolCall(key=key, agent=agent, ok=int(ok)))


def pause_for_today(platform: str) -> None:
    tz = ZoneInfo(get_settings().timezone)
    tomorrow = datetime.combine(datetime.now(tz).date() + timedelta(days=1), time.min, tzinfo=tz)
    kv_set(f"pause:{platform}", tomorrow.isoformat())


def resume(platform: str) -> None:
    kv_set(f"pause:{platform}", "")


def is_paused(platform: str) -> bool:
    until = kv_get(f"pause:{platform}")
    return bool(until) and datetime.now(ZoneInfo(get_settings().timezone)) < datetime.fromisoformat(until)


async def polite_sleep(min_s: float, max_s: float) -> None:
    await asyncio.sleep(random.uniform(min_s, max_s))
