"""
Admin service — statistics queries for bot admin commands.

All queries return simple data structures; formatting is done in the handler.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import EventLog, EventType, Meal, OnboardingState, User


@dataclass
class AdminStats:
    total_users: int
    new_today: int
    active_today: int
    meals_today: int
    onboarding_completed: int
    latest_errors: list[dict]  # [{created_at, message}]


async def get_admin_stats(db: AsyncSession) -> AdminStats:
    today = datetime.now(timezone.utc).date()

    total_users = await db.scalar(select(func.count(User.id))) or 0

    new_today = await db.scalar(
        select(func.count(User.id)).where(cast(User.created_at, Date) == today)
    ) or 0

    active_today = await db.scalar(
        select(func.count(func.distinct(Meal.user_id))).where(
            cast(Meal.logged_at, Date) == today,
            Meal.is_deleted == False,  # noqa: E712
        )
    ) or 0

    meals_today = await db.scalar(
        select(func.count(Meal.id)).where(
            cast(Meal.logged_at, Date) == today,
            Meal.is_deleted == False,  # noqa: E712
        )
    ) or 0

    onboarding_completed = await db.scalar(
        select(func.count(User.id)).where(
            User.onboarding_state == OnboardingState.completed
        )
    ) or 0

    errors_result = await db.execute(
        select(EventLog)
        .where(EventLog.event_type == EventType.error)
        .order_by(EventLog.created_at.desc())
        .limit(5)
    )
    errors = errors_result.scalars().all()
    latest_errors = [
        {
            "created_at": e.created_at.strftime("%d.%m %H:%M") if e.created_at else "?",
            "message": (e.payload or {}).get("exc_message", "—")[:80],
        }
        for e in errors
    ]

    return AdminStats(
        total_users=total_users,
        new_today=new_today,
        active_today=active_today,
        meals_today=meals_today,
        onboarding_completed=onboarding_completed,
        latest_errors=latest_errors,
    )


def format_admin_stats(stats: AdminStats) -> str:
    lines = ["<b>Статистика бота</b>", ""]
    lines.append(f"Пользователей всего: {stats.total_users}")
    lines.append(f"Новых сегодня: {stats.new_today}")
    lines.append(f"Активных сегодня: {stats.active_today}")
    lines.append(f"Приёмов пищи сегодня: {stats.meals_today}")
    lines.append(f"Завершили онбординг: {stats.onboarding_completed}")

    lines.append("")
    if stats.latest_errors:
        lines.append("<b>Последние ошибки:</b>")
        for e in stats.latest_errors:
            lines.append(f"· {e['created_at']} — {e['message']}")
    else:
        lines.append("Ошибок нет.")

    return "\n".join(lines)
