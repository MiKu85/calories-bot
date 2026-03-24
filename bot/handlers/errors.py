"""
Global error handler for the aiogram dispatcher.

Catches all unhandled exceptions, logs them, writes an EventLog entry,
and sends a user-friendly Russian message.
"""
from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import ErrorEvent
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import EventLog, EventType

logger = structlog.get_logger(__name__)
router = Router(name="errors")

_USER_MESSAGE = (
    "Что-то пошло не так. Попробуй ещё раз или напиши /help."
)


@router.errors(ExceptionTypeFilter(Exception))
async def handle_any_exception(event: ErrorEvent, db: AsyncSession | None = None) -> None:
    exc = event.exception
    update = event.update

    telegram_id: int | None = None
    if update.message:
        telegram_id = update.message.from_user.id if update.message.from_user else None
    elif update.callback_query:
        telegram_id = update.callback_query.from_user.id

    logger.error(
        "unhandled_exception",
        exc_type=type(exc).__name__,
        exc=str(exc),
        telegram_id=telegram_id,
        update_id=update.update_id,
    )

    # Persist to event_log if we have a db session
    if db is not None:
        try:
            # Resolve internal user_id if possible — skip if not found
            from sqlalchemy import select
            from bot.db.models import User

            user_id: int | None = None
            if telegram_id:
                result = await db.execute(select(User.id).where(User.telegram_id == telegram_id))
                user_id = result.scalar_one_or_none()

            db.add(EventLog(
                user_id=user_id,
                event_type=EventType.error,
                payload={
                    "exc_type": type(exc).__name__,
                    "exc_message": str(exc),
                    "update_id": update.update_id,
                },
            ))
            await db.commit()
        except Exception as log_exc:
            logger.error("error_log_failed", exc=str(log_exc))

    # Reply to user
    try:
        if update.message:
            await update.message.answer(_USER_MESSAGE)
        elif update.callback_query:
            await update.callback_query.message.answer(_USER_MESSAGE)
            await update.callback_query.answer()
    except Exception:
        pass  # Don't let reply failure mask the original error
