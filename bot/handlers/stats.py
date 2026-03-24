"""
/stats — daily nutrition progress.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import OnboardingState, User
from bot.services.meal_service import get_today_aggregate
from bot.services.stats_service import format_stats

router = Router(name="stats")


@router.message(Command("stats"))
async def cmd_stats(message: Message, user: User, db: AsyncSession) -> None:
    if user.onboarding_state != OnboardingState.completed:
        await message.answer("Сначала нужно заполнить профиль. Напиши /start.")
        return

    agg = await get_today_aggregate(user.id, db)
    await message.answer(format_stats(agg, user))
