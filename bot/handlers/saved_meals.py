"""
«Мои блюда» — сохранённые шаблоны приёмов.

Поток:
  [💾 В мои блюда] под результатом приёма → спросить имя → сохранить шаблон.
  /meals → список блюд → тап → добавить готовые КБЖУ в дневник (без LLM).
  [⚙️ Управлять] → переименовать / удалить.

Ввод имени идёт через aiogram FSM. Хендлеры ввода намеренно пропускают команды
(«/cancel» и пр.) мимо — глобальный handle_cancel в meal-роутере очистит стейт.
"""
from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import MealInputType, OnboardingState, User
from bot.keyboards.saved import (
    saved_meal_actions_kb,
    saved_meals_list_kb,
    saved_meals_manage_kb,
)
from bot.services import saved_meal_service
from bot.services.meal_service import get_meal_by_id

logger = structlog.get_logger(__name__)
router = Router(name="saved_meals")

_NAME_MAX = 100
_EMPTY_HINT = (
    "У тебя пока нет сохранённых блюд.\n\n"
    "Занеси приём как обычно, при необходимости поправь цифры кнопкой «🔧 Исправить», "
    "затем нажми «💾 В мои блюда» — и в следующий раз просто выбери его в /meals."
)


class SavedMealStates(StatesGroup):
    waiting_name = State()
    waiting_rename = State()


def _valid_name(text: str | None) -> str | None:
    name = (text or "").strip()
    if not name or len(name) > _NAME_MAX:
        return None
    return name


# ── /meals: список ─────────────────────────────────────────────────────────────

@router.message(Command("meals"))
async def cmd_meals(message: Message, user: User, db: AsyncSession) -> None:
    if user.onboarding_state != OnboardingState.completed:
        return
    saved = await saved_meal_service.list_for_user(user.id, db)
    if not saved:
        await message.answer(_EMPTY_HINT)
        return
    await message.answer(
        "🍽 <b>Мои блюда</b>\nВыбери, что добавить в дневник:",
        reply_markup=saved_meals_list_kb(saved),
    )


# ── Сохранить приём как шаблон ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("meal_save_tpl:"))
async def cb_save_template(
    cb: CallbackQuery, user: User, db: AsyncSession, state: FSMContext
) -> None:
    meal_id = int(cb.data.split(":", 1)[1])
    meal = await get_meal_by_id(meal_id, db)
    if meal is None or meal.user_id != user.id:
        await cb.answer("Приём не найден", show_alert=True)
        return
    if await saved_meal_service.count_for_user(user.id, db) >= saved_meal_service.MAX_SAVED_PER_USER:
        await cb.answer(
            f"Достигнут предел в {saved_meal_service.MAX_SAVED_PER_USER} блюд. "
            "Удали лишнее в /meals → ⚙️ Управлять.",
            show_alert=True,
        )
        return
    await state.set_state(SavedMealStates.waiting_name)
    await state.update_data(source_meal_id=meal_id)
    await cb.message.answer(
        "Как назвать это блюдо? Пришли короткое название "
        "(например «Протеиновый коктейль»)."
    )
    await cb.answer()


@router.message(SavedMealStates.waiting_name, F.text & ~F.text.startswith("/"))
async def receive_name(
    message: Message, user: User, db: AsyncSession, state: FSMContext
) -> None:
    name = _valid_name(message.text)
    if name is None:
        await message.answer(f"Пришли название текстом, до {_NAME_MAX} символов.")
        return
    data = await state.get_data()
    meal = await get_meal_by_id(data.get("source_meal_id", 0), db)
    if meal is None or meal.user_id != user.id:
        await state.clear()
        await message.answer("Приём не найден — попробуй сохранить заново.")
        return
    if await saved_meal_service.name_exists(user.id, name, db):
        await message.answer("Блюдо с таким названием уже есть. Пришли другое имя.")
        return
    saved = await saved_meal_service.create_from_meal(user, meal, name, db)
    await state.clear()
    logger.bind(telegram_id=user.telegram_id).info("saved_meal_created", saved_id=saved.id)
    await message.answer(
        f"✅ Сохранил «{saved.name}» в твои блюда. Добавляй его через /meals."
    )


# ── Добавить блюдо из шаблона в дневник ────────────────────────────────────────

@router.callback_query(F.data.startswith("saved_add:"))
async def cb_saved_add(cb: CallbackQuery, user: User, db: AsyncSession) -> None:
    saved = await saved_meal_service.get(int(cb.data.split(":", 1)[1]), db)
    if saved is None or saved.user_id != user.id:
        await cb.answer("Блюдо не найдено", show_alert=True)
        return
    # Импорт здесь — save_and_reply_meal в meal-хендлере, избегаем циклического импорта.
    from bot.handlers.meal import save_and_reply_meal

    result = saved_meal_service.to_analysis_result(saved)
    await save_and_reply_meal(
        cb.message, result, MealInputType.saved, saved.name, user, db
    )
    await cb.answer(f"Добавил «{saved.name}»")


# ── Управление: список → блюдо → переименовать / удалить ───────────────────────

@router.callback_query(F.data == "saved_list")
async def cb_list(cb: CallbackQuery, user: User, db: AsyncSession) -> None:
    saved = await saved_meal_service.list_for_user(user.id, db)
    if not saved:
        await cb.message.edit_text(_EMPTY_HINT)
        await cb.answer()
        return
    await cb.message.edit_text(
        "🍽 <b>Мои блюда</b>\nВыбери, что добавить в дневник:",
        reply_markup=saved_meals_list_kb(saved),
    )
    await cb.answer()


@router.callback_query(F.data == "saved_manage")
async def cb_manage(cb: CallbackQuery, user: User, db: AsyncSession) -> None:
    saved = await saved_meal_service.list_for_user(user.id, db)
    if not saved:
        await cb.message.edit_text(_EMPTY_HINT)
        await cb.answer()
        return
    await cb.message.edit_text(
        "⚙️ <b>Управление блюдами</b>\nВыбери блюдо:",
        reply_markup=saved_meals_manage_kb(saved),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("saved_edit:"))
async def cb_edit(cb: CallbackQuery, user: User, db: AsyncSession) -> None:
    saved = await saved_meal_service.get(int(cb.data.split(":", 1)[1]), db)
    if saved is None or saved.user_id != user.id:
        await cb.answer("Блюдо не найдено", show_alert=True)
        return
    await cb.message.edit_text(
        f"«{saved.name}»\n{int(saved.calories)} ккал · "
        f"Б {saved.protein_g:.0f} · Ж {saved.fat_g:.0f} · У {saved.carbs_g:.0f}",
        reply_markup=saved_meal_actions_kb(saved.id),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("saved_delete:"))
async def cb_delete(cb: CallbackQuery, user: User, db: AsyncSession) -> None:
    saved = await saved_meal_service.get(int(cb.data.split(":", 1)[1]), db)
    if saved is None or saved.user_id != user.id:
        await cb.answer("Блюдо не найдено", show_alert=True)
        return
    name = saved.name
    await saved_meal_service.delete(saved, db)
    await cb.message.edit_text(f"🗑 Удалил «{name}».")
    await cb.answer()


@router.callback_query(F.data.startswith("saved_rename:"))
async def cb_rename(
    cb: CallbackQuery, user: User, db: AsyncSession, state: FSMContext
) -> None:
    saved = await saved_meal_service.get(int(cb.data.split(":", 1)[1]), db)
    if saved is None or saved.user_id != user.id:
        await cb.answer("Блюдо не найдено", show_alert=True)
        return
    await state.set_state(SavedMealStates.waiting_rename)
    await state.update_data(saved_id=saved.id)
    await cb.message.answer(f"Пришли новое название для «{saved.name}»:")
    await cb.answer()


@router.message(SavedMealStates.waiting_rename, F.text & ~F.text.startswith("/"))
async def receive_rename(
    message: Message, user: User, db: AsyncSession, state: FSMContext
) -> None:
    name = _valid_name(message.text)
    if name is None:
        await message.answer(f"Пришли название текстом, до {_NAME_MAX} символов.")
        return
    data = await state.get_data()
    saved = await saved_meal_service.get(data.get("saved_id", 0), db)
    if saved is None or saved.user_id != user.id:
        await state.clear()
        await message.answer("Блюдо не найдено.")
        return
    if await saved_meal_service.name_exists(user.id, name, db, exclude_id=saved.id):
        await message.answer("Блюдо с таким названием уже есть. Пришли другое имя.")
        return
    await saved_meal_service.rename(saved, name, db)
    await state.clear()
    await message.answer(f"✅ Переименовал в «{name}».")
