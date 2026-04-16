"""
Meal service — save, soft-delete, patch, and daily aggregate management.

Daily aggregate is always recalculated from raw meal rows (no drift risk).
All aggregate operations use UTC date.

Soft-delete model
-----------------
Deleting a meal sets is_deleted=True + deleted_at=now().
Replacing a meal (patch / fix) soft-deletes the old record and inserts a new
one, linking them via replaced_by_meal_id.  This creates an immutable audit
trail: every "Исправить" operation adds a row instead of mutating one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from datetime import date as date_type

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.ai.schemas import MealAnalysisResult
from bot.db.models import ConfidenceLevel, DailyAggregate, Meal, MealInputType, User


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

    Sets first_meal_at and last_active_date on the user if needed.
    Recalculates the daily aggregate.
    Resets inactivity_reminder_count to 0.
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

    user.last_active_date = _today_utc()
    user.inactivity_reminder_count = 0

    await recalculate_daily_aggregate(user.id, _today_utc(), db)
    return meal


async def update_meal(
    meal: Meal,
    items: list[dict],
    totals: dict,
    db: AsyncSession,
) -> None:
    """
    Update an existing meal in-place with patched items and new totals.

    items: list of dicts with keys name, portion_description, calories,
           protein_g, fat_g, carbs_g (no ids — stripped before storing).
    totals: dict with keys calories, protein_g, fat_g, carbs_g.
    Recalculates the daily aggregate after update.
    """
    meal.meal_items = items
    meal.calories = totals["calories"]
    meal.protein_g = totals["protein_g"]
    meal.fat_g = totals["fat_g"]
    meal.carbs_g = totals["carbs_g"]
    await db.flush()
    await recalculate_daily_aggregate(meal.user_id, _today_utc(), db)


async def confirm_meal(meal: Meal, db: AsyncSession) -> None:
    meal.is_confirmed = True
    await db.flush()


async def delete_meal(meal: Meal, db: AsyncSession) -> None:
    meal.is_deleted = True
    meal.deleted_at = datetime.now(timezone.utc)
    await db.flush()
    await recalculate_daily_aggregate(meal.user_id, _today_utc(), db)


class MealSpec:
    """Describes one meal to create during a replace operation."""

    def __init__(
        self,
        items: list[dict],
        calories: float,
        protein_g: float,
        fat_g: float,
        carbs_g: float,
    ) -> None:
        self.items = items
        self.calories = calories
        self.protein_g = protein_g
        self.fat_g = fat_g
        self.carbs_g = carbs_g


async def replace_meal(
    old_meal: Meal,
    specs: list[MealSpec],
    db: AsyncSession,
) -> list[Meal]:
    """
    Replace old_meal with one or more new meals (atomic soft-delete + insert).

    Soft-deletes old_meal and inserts len(specs) new Meal rows.
    All new meals inherit old_meal.logged_at so they land on the same day.
    old_meal.replaced_by_meal_id is set to the first new meal's id.

    Returns the list of new Meal objects (ids are available after flush).
    The caller is responsible for committing the transaction.
    """
    now = datetime.now(timezone.utc)
    new_meals: list[Meal] = []

    for spec in specs:
        new_meal = Meal(
            user_id=old_meal.user_id,
            input_type=old_meal.input_type,
            raw_input=old_meal.raw_input,
            calories=spec.calories,
            protein_g=spec.protein_g,
            fat_g=spec.fat_g,
            carbs_g=spec.carbs_g,
            confidence=ConfidenceLevel.medium,
            meal_items=spec.items,
            is_confirmed=False,
            is_deleted=False,
            logged_at=old_meal.logged_at,
        )
        db.add(new_meal)
        new_meals.append(new_meal)

    await db.flush()  # assign ids to new meals

    # Soft-delete the old meal and record the replacement chain
    old_meal.is_deleted = True
    old_meal.deleted_at = now
    old_meal.replaced_by_meal_id = new_meals[0].id
    await db.flush()

    # Recalculate aggregate for the day of the original meal
    meal_date = old_meal.logged_at.date() if old_meal.logged_at.tzinfo else _today_utc()
    await recalculate_daily_aggregate(old_meal.user_id, meal_date, db)

    return new_meals


async def get_meal_by_id(meal_id: int, db: AsyncSession) -> Meal | None:
    result = await db.execute(select(Meal).where(Meal.id == meal_id))
    return result.scalar_one_or_none()


async def get_recent_meal(
    user_id: int,
    within_minutes: int,
    exclude_meal_id: int | None,
    db: AsyncSession,
) -> Meal | None:
    """
    Return the most recent non-deleted meal for this user within the given
    time window, optionally excluding a specific meal id.

    Used for duplicate detection: after saving a new meal, check whether
    a similar meal was logged recently.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
    stmt = (
        select(Meal)
        .where(
            Meal.user_id == user_id,
            Meal.is_deleted == False,  # noqa: E712
            Meal.logged_at >= cutoff,
        )
        .order_by(Meal.logged_at.desc())
    )
    result = await db.execute(stmt)
    meals = result.scalars().all()
    for meal in meals:
        if meal.id != exclude_meal_id:
            return meal
    return None


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
