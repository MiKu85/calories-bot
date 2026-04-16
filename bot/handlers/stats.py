"""
/stats — daily nutrition progress.
/export — weekly .txt export.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import DailyAggregate, Meal, OnboardingState, User
from bot.services.meal_service import get_today_aggregate
from bot.services.stats_service import format_stats
from bot.services.weekly_stats import (
    WeeklyData,
    build_txt_export,
    build_weekly_message,
    compute_week_summary,
)

router = Router(name="stats")


def _export_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Выгрузить за неделю", callback_data="export_week")
    builder.adjust(1)
    return builder.as_markup()


@router.message(Command("stats"))
async def cmd_stats(message: Message, user: User, db: AsyncSession) -> None:
    if user.onboarding_state != OnboardingState.completed:
        await message.answer("Сначала нужно заполнить профиль. Напиши /start.")
        return

    agg = await get_today_aggregate(user.id, db)
    await message.answer(format_stats(agg, user), reply_markup=_export_kb())


@router.message(Command("export"))
async def cmd_export(message: Message, user: User, db: AsyncSession) -> None:
    if user.onboarding_state != OnboardingState.completed:
        await message.answer("Сначала нужно заполнить профиль.")
        return
    await _send_export(message, user, db)


@router.callback_query(F.data == "export_week")
async def export_week_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    await callback.answer()
    await _send_export(callback.message, user, db)


async def _send_export(
    message: Message,
    user: User,
    db: AsyncSession,
) -> None:
    """Generate and send the weekly .txt export."""
    now_utc = datetime.now(timezone.utc)
    # Export last 7 complete days (yesterday back 6 more days)
    week_end = (now_utc - timedelta(days=1)).date()
    week_start = week_end - timedelta(days=6)

    week_days = [week_start + timedelta(days=i) for i in range(7)]

    # Fetch daily aggregates
    agg_rows = (await db.execute(
        select(DailyAggregate).where(
            DailyAggregate.user_id == user.id,
            DailyAggregate.date >= week_start,
            DailyAggregate.date <= week_end,
        )
    )).scalars().all()

    agg_by_date = {a.date: a for a in agg_rows}
    week_data = [
        WeeklyData(
            day=d,
            calories=agg_by_date[d].total_calories if d in agg_by_date else 0,
            protein_g=agg_by_date[d].total_protein_g if d in agg_by_date else 0,
            fat_g=agg_by_date[d].total_fat_g if d in agg_by_date else 0,
            carbs_g=agg_by_date[d].total_carbs_g if d in agg_by_date else 0,
            meals_count=agg_by_date[d].meals_count if d in agg_by_date else 0,
        )
        for d in week_days
    ]

    # Fetch individual meals for detail
    meal_rows = (await db.execute(
        select(Meal).where(
            Meal.user_id == user.id,
            Meal.is_deleted == False,  # noqa: E712
            Meal.logged_at >= datetime.combine(week_start, datetime.min.time()).replace(tzinfo=timezone.utc),
            Meal.logged_at <= datetime.combine(week_end + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc),
        ).order_by(Meal.logged_at)
    )).scalars().all()

    # Group meals by date
    week_meals: dict[date, list[dict]] = {}
    for m in meal_rows:
        d = m.logged_at.date() if m.logged_at else now_utc.date()
        if d not in week_meals:
            week_meals[d] = []
        week_meals[d].append({
            "logged_at": m.logged_at,
            "calories": m.calories,
            "protein_g": m.protein_g,
            "fat_g": m.fat_g,
            "carbs_g": m.carbs_g,
            "meal_items": m.meal_items,
        })

    txt = build_txt_export(
        name=user.preferred_name or "",
        week_meals=week_meals,
        week_data=week_data,
        start_date=week_start,
        end_date=week_end,
        target_calories=user.daily_calories_target or 0.0,
        target_protein_g=user.daily_protein_g_target or 0.0,
        target_fat_g=user.daily_fat_g_target or 0.0,
        target_carbs_g=user.daily_carbs_g_target or 0.0,
    )

    filename = f"diary_{week_start}_{week_end}.txt"
    doc = BufferedInputFile(txt.encode("utf-8"), filename=filename)
    await message.answer_document(
        doc,
        caption=f"📥 Дневник питания за {week_start.day}–{week_end.day} "
                f"{_month_name(week_end.month)}",
    )


def _month_name(month: int) -> str:
    names = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля",
        5: "мая", 6: "июня", 7: "июля", 8: "августа",
        9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
    }
    return names.get(month, "")
