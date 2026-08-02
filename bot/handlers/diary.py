"""
Дневник по дням — просмотр, правка и удаление любого приёма, не только последнего.

Кнопки «🔧 Исправить» / «🗑 Удалить» живут под сообщением с результатом приёма и
теряются, как только чат прокрутился дальше. Этот экран даёт постоянную точку
входа: список приёмов за выбранный день с навигацией по датам, где каждый приём
можно поправить или убрать задним числом.

Команда /day, кнопка «📈 Мой день» под приёмом.
"""
from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, timedelta, timezone

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.handlers.meal import MealStates, OnboardingCompleted
from bot.keyboards.meal import day_meals_kb
from bot.services.meal_service import (
    delete_meal,
    get_meal_by_id,
    get_meals_for_date,
)
from bot.utils.tz import local_time

logger = structlog.get_logger(__name__)
router = Router(name="diary")

# Как далеко назад пускаем навигацию — дальше дневник теряет смысл для правок
_MAX_DAYS_BACK = 30

_MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _today_utc() -> date_type:
    return datetime.now(timezone.utc).date()


def _day_label(d: date_type) -> str:
    today = _today_utc()
    if d == today:
        return "сегодня"
    if d == today - timedelta(days=1):
        return "вчера"
    return f"{d.day} {_MONTHS_GEN[d.month - 1]}"


def _short_items(meal_items: list | None, limit: int = 3) -> str:
    """Названия позиций через запятую, с «и ещё N» для длинных приёмов."""
    if not meal_items:
        return "без описания"
    names = [str(i.get("name", "")).strip() for i in meal_items if i.get("name")]
    if not names:
        return "без описания"
    head = ", ".join(names[:limit])
    rest = len(names) - limit
    return f"{head} и ещё {rest}" if rest > 0 else head


async def render_day(
    user: User, db: AsyncSession, target_date: date_type
) -> tuple[str, InlineKeyboardMarkup]:
    """Собрать текст и клавиатуру экрана дневника за указанную дату."""
    meals = await get_meals_for_date(user.id, target_date, db)

    lines = [f"<b>Дневник за {_day_label(target_date)}</b>", ""]

    if not meals:
        lines.append("В этот день приёмов пищи нет.")
    else:
        for idx, meal in enumerate(meals, start=1):
            time_label = local_time(meal.logged_at, user.timezone)
            lines.append(
                f"{idx}. <b>{time_label}</b> · {round(meal.calories)} ккал · "
                f"Б {round(meal.protein_g)} · Ж {round(meal.fat_g)} · У {round(meal.carbs_g)}"
            )
            lines.append(f"   <i>{_short_items(meal.meal_items)}</i>")

        total_cal = sum(m.calories for m in meals)
        total_p = sum(m.protein_g for m in meals)
        total_f = sum(m.fat_g for m in meals)
        total_c = sum(m.carbs_g for m in meals)
        total_fib = sum(m.fiber_g or 0 for m in meals)

        lines.append("")
        lines.append(
            f"<b>Итого:</b> {round(total_cal)} ккал · Б {round(total_p)} · "
            f"Ж {round(total_f)} · У {round(total_c)} · Клетчатка {round(total_fib)}"
        )
        if user.targets_set:
            diff = user.daily_calories_target - total_cal
            if diff >= 0:
                lines.append(
                    f"Цель {int(user.daily_calories_target)} ккал — осталось {round(diff)}"
                )
            else:
                lines.append(
                    f"Цель {int(user.daily_calories_target)} ккал — перебор {round(-diff)}"
                )
        lines.append("")
        lines.append("<i>Нажми на приём, чтобы исправить, или 🗑 — чтобы удалить.</i>")

    has_next = target_date < _today_utc()
    has_prev = target_date > _today_utc() - timedelta(days=_MAX_DAYS_BACK)
    kb = day_meals_kb(
        meals,
        target_date,
        tz_name=user.timezone,
        has_prev=has_prev,
        has_next=has_next,
    )
    return "\n".join(lines), kb


async def _show_day(message: Message, user: User, db: AsyncSession, d: date_type) -> None:
    text, kb = await render_day(user, db, d)
    await message.answer(text, reply_markup=kb)


# ── /day ──────────────────────────────────────────────────────────────────────

@router.message(Command("day"), OnboardingCompleted())
async def cmd_day(message: Message, user: User, db: AsyncSession) -> None:
    await _show_day(message, user, db, _today_utc())


# ── «📈 Мой день» под приёмом ─────────────────────────────────────────────────

@router.callback_query(F.data == "meal_stats")
async def meal_stats_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    await callback.answer()
    await _show_day(callback.message, user, db, _today_utc())


# ── Навигация по дням ─────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("day_nav:"))
async def day_nav_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    await callback.answer()
    target_date = date_type.fromisoformat(callback.data.split(":", 1)[1])
    text, kb = await render_day(user, db, target_date)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        # Сообщение не изменилось или слишком старое для правки
        await callback.message.answer(text, reply_markup=kb)


# ── Удаление приёма из списка ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("meal_del_day:"))
async def meal_delete_from_day_callback(
    callback: CallbackQuery, user: User, db: AsyncSession
) -> None:
    await callback.answer("Удалено 🗑")
    _, meal_id_raw, date_raw = callback.data.split(":", 2)
    meal = await get_meal_by_id(int(meal_id_raw), db)
    if meal and meal.user_id == user.id and not meal.is_deleted:
        await delete_meal(meal, db)
        logger.bind(telegram_id=user.telegram_id).info(
            "meal_deleted_from_diary", meal_id=meal.id
        )

    target_date = date_type.fromisoformat(date_raw)
    text, kb = await render_day(user, db, target_date)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)


# ── Правка приёма из списка ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("meal_fix_day:"))
async def meal_fix_from_day_callback(
    callback: CallbackQuery, user: User, state: FSMContext, db: AsyncSession
) -> None:
    """
    Правка приёма за любой день. Список кнопок намеренно НЕ убираем: после
    исправления пользователь часто правит и соседний приём.
    """
    await callback.answer()
    _, meal_id_raw, _date_raw = callback.data.split(":", 2)
    meal_id = int(meal_id_raw)

    meal = await get_meal_by_id(meal_id, db)
    if meal is None or meal.user_id != user.id or meal.is_deleted:
        await callback.message.answer("Этот приём уже удалён.")
        return

    await state.set_state(MealStates.awaiting_patch)
    await state.update_data(patch_meal_id=meal_id)

    time_label = local_time(meal.logged_at, user.timezone)
    await callback.message.answer(
        f"Исправляю приём <b>{time_label}</b> · {round(meal.calories)} ккал\n"
        f"<i>{_short_items(meal.meal_items, limit=5)}</i>\n\n"
        "Что именно исправить? Напиши свободным текстом — например:\n"
        "· «сырники не 220, а 150г»\n"
        "· «убери кофе»\n"
        "· «добавь ложку сметаны»\n"
        "· «исправь весь приём» + новый состав\n\n"
        "/cancel — отменить."
    )
