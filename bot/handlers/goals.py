"""
/goals — просмотр и пересчёт целей по КБЖУ.
"""
from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import OnboardingState, User
from bot.services.user_service import apply_targets

logger = structlog.get_logger(__name__)
router = Router(name="goals")


@router.message(Command("goals"))
async def cmd_goals(message: Message, user: User, db: AsyncSession) -> None:
    if user.onboarding_state != OnboardingState.completed:
        await message.answer("Сначала нужно заполнить профиль. Напиши /start.")
        return

    if not user.targets_set:
        await message.answer(
            "Цели ещё не рассчитаны.\n\n"
            "Открой /profile и заполни данные — я всё посчитаю автоматически."
        )
        return

    text = (
        "<b>Ваши цели на день</b>\n\n"
        f"Калории: <b>{int(user.daily_calories_target)}</b> ккал\n"
        f"Белки: <b>{int(user.daily_protein_g_target)}</b> г\n"
        f"Жиры: <b>{int(user.daily_fat_g_target)}</b> г\n"
        f"Углеводы: <b>{int(user.daily_carbs_g_target)}</b> г\n\n"
        "Чтобы изменить — открой /profile и обнови вес, активность или цель.\n"
        "Я пересчитаю нормы автоматически."
    )
    await message.answer(text)
