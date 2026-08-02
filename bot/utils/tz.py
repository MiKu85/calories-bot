"""
Локальное время пользователя.

Приёмы хранятся в UTC, а пользователь сверяет их с часами в телефоне: приём,
записанный в 09:58 по Москве, в списке выглядел как 06:58 и не сопоставлялся
с сообщением в чате. Всё, что показывает время человеку, идёт через local_time.
"""
from __future__ import annotations

import zoneinfo
from datetime import datetime, timezone

_DEFAULT_TZ = "Europe/Moscow"


def user_tz(tz_name: str | None) -> zoneinfo.ZoneInfo:
    try:
        return zoneinfo.ZoneInfo(tz_name or _DEFAULT_TZ)
    except Exception:
        return zoneinfo.ZoneInfo(_DEFAULT_TZ)


def to_local(dt: datetime | None, tz_name: str | None) -> datetime | None:
    """Перевести UTC-время в часовой пояс пользователя."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(user_tz(tz_name))


def local_time(dt: datetime | None, tz_name: str | None, fmt: str = "%H:%M") -> str:
    """Отформатировать время приёма так, как его видит пользователь."""
    local = to_local(dt, tz_name)
    return local.strftime(fmt) if local else ""
