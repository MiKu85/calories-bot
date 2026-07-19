"""
Сервис «Мои блюда» — сохранённые шаблоны приёмов пищи.

Заводятся из уже посчитанного (и при необходимости исправленного) приёма и
добавляются в дневник выбором из списка, минуя LLM. Это убирает трение для
повторяющейся еды и фиксирует стабильные КБЖУ — см. [[project_migration_railway]].
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.ai.schemas import ConfidenceLevel, MealAnalysisResult, MealItem
from bot.db.models import Meal, SavedMeal, User

# Предел на пользователя — чтобы список в /meals оставался обозримым списком кнопок.
MAX_SAVED_PER_USER = 50


async def list_for_user(user_id: int, db: AsyncSession) -> list[SavedMeal]:
    res = await db.execute(
        select(SavedMeal).where(SavedMeal.user_id == user_id).order_by(SavedMeal.name)
    )
    return list(res.scalars().all())


async def count_for_user(user_id: int, db: AsyncSession) -> int:
    res = await db.execute(
        select(func.count()).select_from(SavedMeal).where(SavedMeal.user_id == user_id)
    )
    return int(res.scalar_one())


async def get(saved_id: int, db: AsyncSession) -> SavedMeal | None:
    return await db.get(SavedMeal, saved_id)


async def name_exists(
    user_id: int, name: str, db: AsyncSession, *, exclude_id: int | None = None
) -> bool:
    """Проверка занятости имени без учёта регистра (UniqueConstraint регистрозависим)."""
    stmt = select(SavedMeal.id).where(
        SavedMeal.user_id == user_id,
        func.lower(SavedMeal.name) == name.strip().lower(),
    )
    if exclude_id is not None:
        stmt = stmt.where(SavedMeal.id != exclude_id)
    res = await db.execute(stmt)
    return res.first() is not None


async def create_from_meal(
    user: User, meal: Meal, name: str, db: AsyncSession
) -> SavedMeal:
    """Сохранить приём как шаблон, копируя итоговые КБЖУ и состав."""
    saved = SavedMeal(
        user_id=user.id,
        name=name.strip(),
        calories=meal.calories,
        protein_g=meal.protein_g,
        fat_g=meal.fat_g,
        carbs_g=meal.carbs_g,
        meal_items=meal.meal_items,
    )
    db.add(saved)
    await db.flush()
    return saved


async def rename(saved: SavedMeal, name: str, db: AsyncSession) -> None:
    saved.name = name.strip()
    await db.flush()


async def delete(saved: SavedMeal, db: AsyncSession) -> None:
    await db.delete(saved)
    await db.flush()


def to_analysis_result(saved: SavedMeal) -> MealAnalysisResult:
    """
    Собрать MealAnalysisResult из шаблона, чтобы переиспользовать штатный путь
    сохранения приёма (save_meal / save_and_reply_meal). confidence=high и
    needs_clarification=False — данные заведомо точные, повторный разбор не нужен.
    """
    items = [MealItem(**it) for it in (saved.meal_items or [])]
    return MealAnalysisResult(
        items=items,
        total_calories=saved.calories,
        total_protein_g=saved.protein_g,
        total_fat_g=saved.fat_g,
        total_carbs_g=saved.carbs_g,
        confidence=ConfidenceLevel.high,
        needs_clarification=False,
    )
