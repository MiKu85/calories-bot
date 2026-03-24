"""
Feedback scheduler — background asyncio task.

Runs every CHECK_INTERVAL_HOURS hours.
Finds users whose first_meal_at was >= TRIGGER_DAYS_AFTER days ago and who
have not yet received a feedback request, then sends them the feedback message.

Railway-compatible: runs in the same process as the bot (no Redis, no Celery).
Restart-safe: feedback_sent_at is persisted in DB before sending, so
if the process crashes after DB write but before Telegram delivery,
the user won't get a duplicate request on next restart.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.models import OnboardingState, User
from bot.keyboards.feedback import feedback_options_kb

logger = structlog.get_logger(__name__)

TRIGGER_DAYS_AFTER = 7
CHECK_INTERVAL_HOURS = 6
_STARTUP_DELAY_SECONDS = 30  # don't run immediately on cold start

_FEEDBACK_TEXT = (
    "Привет! Ты пользуешься ботом уже неделю — как впечатления?\n\n"
    "Выбери один вариант или напиши комментарий:"
)


async def feedback_scheduler_loop(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Entry point — call once at startup via asyncio.create_task().

    Waits STARTUP_DELAY before the first check so the bot has time to
    fully initialise (especially in webhook mode).
    """
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)
    logger.info("feedback_scheduler_started", interval_hours=CHECK_INTERVAL_HOURS)

    while True:
        try:
            sent = await _send_pending_feedback(bot, session_factory)
            if sent:
                logger.info("feedback_scheduler_cycle_done", sent=sent)
        except Exception as exc:
            logger.error("feedback_scheduler_error", error=str(exc))

        await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)


async def _send_pending_feedback(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """
    Find eligible users and send feedback request.
    Returns number of messages sent.
    """
    threshold = datetime.now(timezone.utc) - timedelta(days=TRIGGER_DAYS_AFTER)
    sent_count = 0

    async with session_factory() as db:
        result = await db.execute(
            select(User).where(
                User.first_meal_at.isnot(None),
                User.first_meal_at <= threshold,
                User.feedback_sent_at.is_(None),
                User.onboarding_state == OnboardingState.completed,
            )
        )
        users = result.scalars().all()

    for user in users:
        # Open a fresh session per user to avoid long-running transactions
        async with session_factory() as db:
            # Re-fetch inside this session to attach the object
            fresh = await db.get(User, user.id)
            if fresh is None or fresh.feedback_sent_at is not None:
                continue  # already handled or deleted

            # Mark as sent BEFORE Telegram call so we don't retry on restart
            fresh.feedback_sent_at = datetime.now(timezone.utc)
            await db.commit()

            # Send Telegram message
            try:
                await bot.send_message(
                    chat_id=fresh.telegram_id,
                    text=_FEEDBACK_TEXT,
                    reply_markup=feedback_options_kb(),
                )
                sent_count += 1
                logger.info("feedback_sent", telegram_id=fresh.telegram_id)
            except Exception as exc:
                logger.warning(
                    "feedback_send_failed",
                    telegram_id=fresh.telegram_id,
                    error=str(exc),
                )
            # Small delay to avoid hitting Telegram rate limits
            await asyncio.sleep(0.1)

    return sent_count
