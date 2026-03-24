"""
Meal service — save, soft-delete, and daily aggregate management.

Daily aggregate is always recalculated from raw meal rows (no drift risk).
All aggregate operations use UTC date.
"""
from __future__ import annotations

from datetime import datetime, timezone
from datetime import date as date_type

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.ai.schemas import MealAnalysisResult
from bot.db.models import DailyAggregate, Meal, MealInputType, User


def _today_utc() -> date_type:
    return datetime.now(timezone.utc).date()


async def save_meal(
    user: User,
    input_type: MealInputType,
    raw_input: str | None,
    result: MealAnalysisResult,
    db: AsyncSession,
) -> Meal:
    """
    Persist a meal from an AI analysis result.

    Sets first_meal_at on the user if this is their first meal.
    Recalculates the daily aggregate.
    """
    meal = Meal(
        user_id=user.id,
        input_type=input_type,
        raw_input=raw_input,
        calories=result.total_calories,
        protein_g=result.total_protein_g,
        fat_g=result.total_fat_g,
        carbs_g=result.total_carbs_g,
        confidence=result.confidence,
        confidence_notes=result.confidence_notes,
        meal_items=_items_to_json(result),
        is_confirmed=False,
        is_deleted=False,
    )
    db.add(meal)
    await db.flush()  # get meal.id

    if user.first_meal_at is None:
        user.first_meal_at = datetime.now(timezone.utc)

    await recalculate_daily_aggregate(user.id, _today_utc(), db)
    return meal


async def confirm_meal(meal: Meal, db: AsyncSession) -> None:
    meal.is_confirmed = True
    await db.flush()


async def delete_meal(meal: Meal, db: AsyncSession) -> None:
    meal.is_deleted = True
    await db.flush()
    await recalculate_daily_aggregate(meal.user_id, _today_utc(), db)


async def get_meal_by_id(meal_id: int, db: AsyncSession) -> Meal | None:
    result = await db.execute(select(Meal).where(Meal.id == meal_id))
    return result.scalar_one_or_none()


async def recalculate_daily_aggregate(
    user_id: int, target_date: date_type, db: AsyncSession
) -> DailyAggregate:
    """Recompute aggregate from all non-deleted meals for the given UTC date."""
    stmt = select(
        func.coalesce(func.sum(Meal.calories), 0.0).label("cal"),
        func.coalesce(func.sum(Meal.protein_g), 0.0).label("prot"),
        func.coalesce(func.sum(Meal.fat_g), 0.0).label("fat"),
        func.coalesce(func.sum(Meal.carbs_g), 0.0).label("carbs"),
        func.count(Meal.id).label("cnt"),
    ).where(
        Meal.user_id == user_id,
        Meal.is_deleted == False,  # noqa: E712
        cast(Meal.logged_at, Date) == target_date,
    )

    row = (await db.execute(stmt)).one()

    agg = await _get_or_create_aggregate(user_id, target_date, db)
    agg.total_calories = float(row.cal)
    agg.total_protein_g = float(row.prot)
    agg.total_fat_g = float(row.fat)
    agg.total_carbs_g = float(row.carbs)
    agg.meals_count = int(row.cnt)
    await db.flush()
    return agg


async def get_today_aggregate(user_id: int, db: AsyncSession) -> DailyAggregate:
    return await _get_or_create_aggregate(user_id, _today_utc(), db)


async def _get_or_create_aggregate(
    user_id: int, target_date: date_type, db: AsyncSession
) -> DailyAggregate:
    result = await db.execute(
        select(DailyAggregate).where(
            DailyAggregate.user_id == user_id,
            DailyAggregate.date == target_date,
        )
    )
    agg = result.scalar_one_or_none()
    if agg is None:
        agg = DailyAggregate(user_id=user_id, date=target_date)
        db.add(agg)
        await db.flush()
    return agg


def _items_to_json(result: MealAnalysisResult) -> list[dict]:
    return [
        {
            "name": item.name,
            "portion_description": item.portion_description,
            "calories": item.calories,
            "protein_g": item.protein_g,
            "fat_g": item.fat_g,
            "carbs_g": item.carbs_g,
        }
        for item in result.items
    ]
