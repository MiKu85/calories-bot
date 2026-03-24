"""
Telegram channel subscription checker.

Uses Bot.get_chat_member to verify the user has joined the required channel.
Does not cache results — always calls the API (lightweight, fast).
"""
from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

logger = structlog.get_logger(__name__)

_INACTIVE_STATUSES = {"left", "kicked"}


async def is_subscribed(bot: Bot, user_id: int, channel_id: str) -> bool:
    """
    Returns True if the user is an active member of the channel.
    Returns False if not a member or on any API error (fail-safe).
    """
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status not in _INACTIVE_STATUSES
    except TelegramBadRequest as exc:
        # e.g. bot is not admin in channel, or channel not found
        logger.warning("subscription_check_bad_request", error=str(exc), channel=channel_id)
        return False
    except Exception as exc:
        logger.error("subscription_check_error", error=str(exc), channel=channel_id)
        return False
