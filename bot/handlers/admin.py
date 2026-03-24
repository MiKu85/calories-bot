"""
Admin-only commands.

Access control: handler checks telegram_id against TELEGRAM_ADMIN_IDS from config.
Non-admins get no response — silent ignore to avoid information leakage.
"""
from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User
from bot.services.admin_service import format_admin_stats, get_admin_stats
from config import settings

logger = structlog.get_logger(__name__)
router = Router(name="admin")


def _is_admin(user: User) -> bool:
    return user.telegram_id in settings.telegram_admin_ids


@router.message(Command("admin"))
async def cmd_admin(message: Message, user: User, db: AsyncSession) -> None:
    if not _is_admin(user):
        logger.warning("admin_access_denied", telegram_id=user.telegram_id)
        return  # silent — don't reveal that this command exists

    stats = await get_admin_stats(db)
    await message.answer(format_admin_stats(stats))
