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
    await callback.message.answer(
        "Профиль сброшен. Напиши /start, чтобы начать заново."
    )


@router.callback_query(ResetStates.awaiting_confirm, F.data == "reset:cancel")
async def cancel_reset(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await callback.message.answer("Сброс отменён.")
