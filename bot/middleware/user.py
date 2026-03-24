"""
User middleware.

Resolves (or creates) the User record for every incoming update and injects
it into handler data under the key "user". Requires DbSessionMiddleware to
run first (i.e., registered after it in aiogram's middleware chain).
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import OnboardingState, User

logger = structlog.get_logger(__name__)


class UserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        telegram_user = data.get("event_from_user")
        if telegram_user is None or telegram_user.is_bot:
            return await handler(event, data)

        db: AsyncSession = data["db"]
        user = await _get_or_create_user(db, telegram_user)
        data["user"] = user
        return await handler(event, data)


async def _get_or_create_user(db: AsyncSession, tg_user) -> User:
    result = await db.execute(select(User).where(User.telegram_id == tg_user.id))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            telegram_id=tg_user.id,
            telegram_username=tg_user.username,
            onboarding_state=OnboardingState.new,
        )
        db.add(user)
        await db.flush()
        logger.info("user_created", telegram_id=tg_user.id)
    else:
        # Keep username in sync silently
        if user.telegram_username != tg_user.username:
            user.telegram_username = tg_user.username

    return user
