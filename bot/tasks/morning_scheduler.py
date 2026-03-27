"""
Morning summary scheduler — background asyncio task.

Checks every CHECK_INTERVAL_SECONDS.
For each onboarded user whose local time is 08:xx and who hasn't received
today's summary yet, builds and sends the morning summary message.

Railway-compatible: runs in the same process as the bot (no Redis, no Celery).
Restart-safe: morning_sent_date is persisted BEFORE the Telegram call so
the user won't receive a duplicate on restart.

Timezone support: maps IANA timezone names to fixed UTC offsets.
DST is not handled — most users are in Russian timezones which don't observe DST.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import structlog
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.db.models import DailyAggregate, OnboardingState, User
from bot.services.morning_summary import DayData, UserTargets, build_morning_summary

logger = structlog.get_logger(__name__)

CHECK_INTERVAL_SECONDS = 5 * 60  # check every 5 minutes
_STARTUP_DELAY_SECONDS = 60      # let the bot fully initialise before first check
_SEND_HOUR = 8                   # local hour to send (08:xx)

# Fixed UTC offsets for common IANA timezone names.
# Keys must match values stored in users.timezone column.
_TZ_OFFSETS: dict[str, int] = {
    "Europe/Kaliningrad": 2,
    "Europe/Moscow": 3,
    "Europe/Samara": 4,
    "Asia/Yekaterinburg": 5,
    "Asia/Omsk": 6,
    "Asia/Krasnoyarsk": 7,
    "Asia/Irkutsk": 8,
    "Asia/Yakutsk": 9,
    "Asia/Vladivostok": 10,
    "Asia/Magadan": 11,
    "Asia/Kamchatka": 12,
    "Europe/Kiev": 3,
    "Europe/London": 0,
    "Europe/Berlin": 1,
    "Europe/Paris": 1,
    "Asia/Dubai": 4,
    "Asia/Almaty": 5,
    "Asia/Tashkent": 5,
    "Asia/Bishkek": 6,
    "UTC": 0,
}
_DEFAULT_TZ = "Europe/Moscow"


def _tz_offset(tz: str) -> int:
    return _TZ_OFFSETS.get(tz, _TZ_OFFSETS[_DEFAULT_TZ])


def _local_hour(tz: str) -> int:
    return (datetime.now(timezone.utc).hour + _tz_offset(tz)) % 24


def _local_date(tz: str) -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=_tz_offset(tz))).date()


# ── Public entry point ─────────────────────────────────────────────────────────

async def morning_scheduler_loop(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Entry point — call once at startup via asyncio.create_task().

    Waits _STARTUP_DELAY_SECONDS before the first check so the bot has time to
    fully initialise (especially in webhook mode).
    """
    await asyncio.sleep(_STARTUP_DELAY_SECONDS)
    logger.info("morning_scheduler_started", interval_seconds=CHECK_INTERVAL_SECONDS)

    while True:
        try:
            sent = await _send_pending_summaries(bot, session_factory)
            if sent:
                logger.info("morning_scheduler_cycle_done", sent=sent)
        except Exception as exc:
            logger.error("morning_scheduler_error", error=str(exc))

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


# ── Internal: find and send ────────────────────────────────────────────────────

async def _send_pending_summaries(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """
    Find all eligible users for this cycle and send morning summaries.
    Returns the number of messages sent.
    """
    now_utc = datetime.now(timezone.utc)
    yesterday_utc = (now_utc - timedelta(days=1)).date()
    day_before_utc = (now_utc - timedelta(days=2)).date()
    sent_count = 0

    # Load candidates (all onboarded users with targets set)
    async with session_factory() as db:
        result = await db.execute(
            select(User).where(
                User.onboarding_state == OnboardingState.completed,
                User.daily_calories_target.isnot(None),
            )
        )
        users = result.scalars().all()

    for user in users:
        tz = user.timezone or _DEFAULT_TZ

        # Only act during the send hour in the user's local time
        if _local_hour(tz) != _SEND_HOUR:
            continue

        local_today = _local_date(tz)

        # Skip if already sent today
        if user.morning_sent_date == local_today:
            continue

        # Open a per-user session to fetch data and mark as sent
        async with session_factory() as db:
            fresh = await db.get(User, user.id)
            if fresh is None or fresh.morning_sent_date == local_today:
                continue  # concurrent cycle already handled this user

            # Fetch yesterday's and day-before's aggregates
            agg_yest = (await db.execute(
                select(DailyAggregate).where(
                    DailyAggregate.user_id == fresh.id,
                    DailyAggregate.date == yesterday_utc,
                )
            )).scalar_one_or_none()

            agg_before = (await db.execute(
                select(DailyAggregate).where(
                    DailyAggregate.user_id == fresh.id,
                    DailyAggregate.date == day_before_utc,
                )
            )).scalar_one_or_none()

            yesterday_data = DayData(
                calories=agg_yest.total_calories if agg_yest else 0.0,
                protein_g=agg_yest.total_protein_g if agg_yest else 0.0,
                fat_g=agg_yest.total_fat_g if agg_yest else 0.0,
                carbs_g=agg_yest.total_carbs_g if agg_yest else 0.0,
                meals_count=agg_yest.meals_count if agg_yest else 0,
            )
            day_before_data: DayData | None = DayData(
                calories=agg_before.total_calories if agg_before else 0.0,
                protein_g=agg_before.total_protein_g if agg_before else 0.0,
                fat_g=agg_before.total_fat_g if agg_before else 0.0,
                carbs_g=agg_before.total_carbs_g if agg_before else 0.0,
                meals_count=agg_before.meals_count if agg_before else 0,
            ) if agg_before else None

            targets = UserTargets(
                calories=fresh.daily_calories_target or 0.0,
                protein_g=fresh.daily_protein_g_target or 0.0,
                fat_g=fresh.daily_fat_g_target or 0.0,
                carbs_g=fresh.daily_carbs_g_target or 0.0,
            )

            text = build_morning_summary(
                name=fresh.preferred_name or "",
                yesterday=yesterday_data,
                targets=targets,
                day_before=day_before_data,
            )

            # Persist sent date BEFORE Telegram call (restart-safe dedup)
            fresh.morning_sent_date = local_today
            await db.commit()

        # Send outside the DB session to avoid holding the connection
        try:
            await bot.send_message(chat_id=user.telegram_id, text=text)
            sent_count += 1
            logger.info("morning_summary_sent", telegram_id=user.telegram_id)
        except Exception as exc:
            logger.warning(
                "morning_summary_send_failed",
                telegram_id=user.telegram_id,
                error=str(exc),
            )

        await asyncio.sleep(0.05)  # stay within Telegram rate limits

    return sent_count
