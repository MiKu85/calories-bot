"""
/reset — сброс онбординга.

Спрашивает подтверждение перед сбросом.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.services.meal_service import delete_meal, get_today_meals
from bot.services.user_service import reset_onboarding

router = Router(name="reset")


class ResetStates(StatesGroup):
    awaiting_confirm = State()


def _confirm_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="Да, сбросить", callback_data="reset:confirm")
    builder.button(text="Отмена", callback_data="reset:cancel")
    builder.adjust(2)
    return builder.as_markup()


@router.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext) -> None:
    await state.set_state(ResetStates.awaiting_confirm)
    await message.answer(
        "Сбросить профиль и пройти настройку заново?\n\n"
        "<i>Все данные профиля и цели будут удалены. История приёмов пищи сохранится.</i>",
        reply_markup=_confirm_kb(),
    )


@router.callback_query(ResetStates.awaiting_confirm, F.data == "reset:confirm")
async def confirm_reset(
    callback: CallbackQuery, state: FSMContext, user: User, db: AsyncSession
) -> None:
    await callback.answer()
    await reset_onboarding(user, db)
    await state.clear()

    # Meals are kept by design, but the day's total carries over into the new
    # profile and reads as "bot keeps adding to old data" — offer to clear it.
    meals = await get_today_meals(user.id, db)
    if meals:
        total = round(sum(m.calories for m in meals))
        builder = InlineKeyboardBuilder()
        builder.button(text="🗑 Очистить сегодня", callback_data="reset:clear_day")
        builder.button(text="Оставить", callback_data="reset:keep_day")
        builder.adjust(2)
        await callback.message.answer(
            "Профиль сброшен. Напиши /start, чтобы начать заново.\n\n"
            f"<i>В дневнике за сегодня осталось {len(meals)} приёма(ов) "
            f"на {total} ккал — они продолжат учитываться в дневном итоге.</i>",
            reply_markup=builder.as_markup(),
        )
    else:
        await callback.message.answer(
            "Профиль сброшен. Напиши /start, чтобы начать заново."
        )


@router.callback_query(F.data == "reset:clear_day")
async def clear_day(callback: CallbackQuery, user: User, db: AsyncSession) -> None:
    await callback.answer("Дневник за сегодня очищен.")
    for meal in await get_today_meals(user.id, db):
        await delete_meal(meal, db)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Дневник за сегодня очищен — счёт с нуля.")


@router.callback_query(F.data == "reset:keep_day")
async def keep_day(callback: CallbackQuery) -> None:
    await callback.answer("Ок, приёмы остаются.")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(ResetStates.awaiting_confirm, F.data == "reset:cancel")
async def cancel_reset(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.answer("Сброс отменён.")
