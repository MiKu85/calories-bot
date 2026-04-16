"""
Morning summary scheduler — background asyncio task.

Checks every CHECK_INTERVAL_SECONDS.
For each onboarded user whose local time is 08:xx and who hasn't received
today's summary yet, builds and sends the morning summary message.

Active users (logged meals yesterday): get the full morning summary.
Inactive users (no meals yesterday): get a re-engagement reminder on a
limited schedule — 2 consecutive mornings, then after a week, then after
a month. After that, silence.

Railway-compatible: runs in the same process as the bot (no Redis, no Celery).
Restart-safe: morning_sent_date is persisted BEFORE the Telegram call so
the user won't receive a duplicate on restart.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import structlog
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# aliased to avoid collision with the local `select` calls that reference the original
sa_select = select

from bot.db.models import DailyAggregate, OnboardingState, User
from bot.services.morning_summary import DayData, UserTargets, build_morning_summary
from bot.services.tip_service import get_morning_tip
from bot.services.weekly_stats import WeeklyData, build_weekly_message, compute_week_summary

logger = structlog.get_logger(__name__)

CHECK_INTERVAL_SECONDS = 5 * 60  # check every 5 minutes
_STARTUP_DELAY_SECONDS = 60      # let the bot fully initialise before first check
_SEND_HOUR = 7                   # local hour to send (07:xx)
_SEND_MINUTE = 30                # local minute within _SEND_HOUR (07:30)

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

# Re-engagement reminder texts.
# Sent only when the user hasn't logged anything for N+ days.
_REMINDER_1 = (
    "Вчера приёмы пищи не записаны — ничего страшного.\n\n"
    "Напиши или сфотографируй, что ешь сегодня, и продолжим."
)
_REMINDER_2 = (
    "Привет! Второй день без записей — бывает.\n\n"
    "Если хочешь вернуться к отслеживанию, просто напиши первый приём, "
    "и я посчитаю всё как обычно."
)
_REMINDER_WEEK = (
    "Привет! Ты не заходил(а) уже неделю.\n\n"
    "Если захочешь вернуться — напиши что-нибудь из еды, "
    "и продолжим как будто и не было перерыва."
)
_REMINDER_MONTH = (
    "Прошёл месяц с последнего использования.\n\n"
    "Если решишь вернуться — я здесь, профиль сохранён. "
    "Просто напиши что ел(а)."
)

# (min_days_inactive, required_count, text)
# Send a reminder when days_since_last_active >= min_days AND count == required_count.
# days_since_last_active = (local_today - last_active_date).days
#   1 → active yesterday (normal summary)
#   2 → missed yesterday (first inactive morning)
#   3 → missed two days in a row
#   8+ → a week gone
#   31+ → a month gone
_INACTIVITY_SCHEDULE: list[tuple[int, int, str]] = [
    (2,  0, _REMINDER_1),
    (3,  1, _REMINDER_2),
    (8,  2, _REMINDER_WEEK),
    (31, 3, _REMINDER_MONTH),
]


def _tz_offset(tz: str) -> int:
    return _TZ_OFFSETS.get(tz, _TZ_OFFSETS[_DEFAULT_TZ])


def _local_hour(tz: str) -> int:
    return (datetime.now(timezone.utc).hour + _tz_offset(tz)) % 24


def _local_minute() -> int:
    return datetime.now(timezone.utc).minute


def _local_date(tz: str) -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=_tz_offset(tz))).date()


# ── Summary builders ──────────────────────────────────────────────────────────

async def _build_daily_summary(
    user: User,
    agg_yest: DailyAggregate,
    yesterday_utc: date,
    day_before_utc: date,
    session_factory: async_sessionmaker[AsyncSession],
) -> str | None:
    """Build the normal (non-Monday) morning summary."""
    async with session_factory() as db:
        fresh = await db.get(User, user.id)
        if fresh is None:
            return None

        agg_before = (await db.execute(
            sa_select(DailyAggregate).where(
                DailyAggregate.user_id == fresh.id,
                DailyAggregate.date == day_before_utc,
            )
        )).scalar_one_or_none()

        yesterday_data = DayData(
            calories=agg_yest.total_calories,
            protein_g=agg_yest.total_protein_g,
            fat_g=agg_yest.total_fat_g,
            carbs_g=agg_yest.total_carbs_g,
            meals_count=agg_yest.meals_count,
        )
        day_before_data: DayData | None = DayData(
            calories=agg_before.total_calories,
            protein_g=agg_before.total_protein_g,
            fat_g=agg_before.total_fat_g,
            carbs_g=agg_before.total_carbs_g,
            meals_count=agg_before.meals_count,
        ) if agg_before else None

        targets = UserTargets(
            calories=fresh.daily_calories_target or 0.0,
            protein_g=fresh.daily_protein_g_target or 0.0,
            fat_g=fresh.daily_fat_g_target or 0.0,
            carbs_g=fresh.daily_carbs_g_target or 0.0,
        )

        morning_tip = await get_morning_tip(fresh, db)

        text = build_morning_summary(
            name=fresh.preferred_name or "",
            yesterday=yesterday_data,
            targets=targets,
            day_before=day_before_data,
            tip=morning_tip,
        )

        if fresh.inactivity_reminder_count != 0:
            fresh.inactivity_reminder_count = 0
        await db.commit()

    return text


async def _build_weekly_summary(
    user: User,
    local_today: date,
    session_factory: async_sessionmaker[AsyncSession],
) -> str | None:
    """Build the Monday morning weekly summary (covers Mon–Sun of the preceding week)."""
    from datetime import timedelta as _td
    # local_today is Monday — preceding week is Mon(local_today - 7) to Sun(local_today - 1)
    week_end = local_today - _td(days=1)    # last Sunday
    week_start = local_today - _td(days=7)  # preceding Monday

    async with session_factory() as db:
        fresh = await db.get(User, user.id)
        if fresh is None:
            return None

        # Fetch daily aggregates for the 7-day window
        week_days = [week_start + _td(days=i) for i in range(7)]
        agg_rows = (await db.execute(
            sa_select(DailyAggregate).where(
                DailyAggregate.user_id == fresh.id,
                DailyAggregate.date >= week_start,
                DailyAggregate.date <= week_end,
            )
        )).scalars().all()

        agg_by_date = {a.date: a for a in agg_rows}
        week_data = [
            WeeklyData(
                day=d,
                calories=agg_by_date[d].total_calories if d in agg_by_date else 0,
                protein_g=agg_by_date[d].total_protein_g if d in agg_by_date else 0,
                fat_g=agg_by_date[d].total_fat_g if d in agg_by_date else 0,
                carbs_g=agg_by_date[d].total_carbs_g if d in agg_by_date else 0,
                meals_count=agg_by_date[d].meals_count if d in agg_by_date else 0,
            )
            for d in week_days
        ]

        target_cal = fresh.daily_calories_target or 0.0
        target_prot = fresh.daily_protein_g_target or 0.0
        target_fat = fresh.daily_fat_g_target or 0.0
        target_carbs = fresh.daily_carbs_g_target or 0.0

        summary = compute_week_summary(week_data, target_cal, target_prot, target_fat, target_carbs)
        morning_tip = await get_morning_tip(fresh, db)

        if fresh.inactivity_reminder_count != 0:
            fresh.inactivity_reminder_count = 0
        await db.commit()

    text = build_weekly_message(
        name=fresh.preferred_name or "",
        summary=summary,
        target_calories=target_cal,
        target_protein_g=target_prot,
        target_fat_g=target_fat,
        target_carbs_g=target_carbs,
        tip=morning_tip,
    )
    return text


# ── Public entry point ─────────────────────────────────────────────────────────

async def morning_scheduler_loop(
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
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
    now_utc = datetime.now(timezone.utc)
    yesterday_utc = (now_utc - timedelta(days=1)).date()
    day_before_utc = (now_utc - timedelta(days=2)).date()
    sent_count = 0

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

        local_h = _local_hour(tz)
        local_m = _local_minute()
        # Send at 07:30 local (accept 07:30–07:34, since check interval is 5 min)
        if local_h != _SEND_HOUR or local_m < _SEND_MINUTE or local_m >= _SEND_MINUTE + 5:
            continue

        local_today = _local_date(tz)

        if user.morning_sent_date == local_today:
            continue

        async with session_factory() as db:
            fresh = await db.get(User, user.id)
            if fresh is None or fresh.morning_sent_date == local_today:
                continue

            agg_yest = (await db.execute(
                select(DailyAggregate).where(
                    DailyAggregate.user_id == fresh.id,
                    DailyAggregate.date == yesterday_utc,
                )
            )).scalar_one_or_none()

            was_active_yesterday = agg_yest is not None and agg_yest.meals_count > 0

            # Mark as sent BEFORE Telegram call (restart-safe dedup)
            fresh.morning_sent_date = local_today
            await db.commit()

        text: str | None
        is_monday = local_today.weekday() == 0  # 0 = Monday

        if was_active_yesterday:
            if is_monday:
                # ── Monday: weekly summary replaces daily summary ───────────
                text = await _build_weekly_summary(
                    user=user,
                    local_today=local_today,
                    session_factory=session_factory,
                )
            else:
                # ── Normal morning summary ──────────────────────────────────
                text = await _build_daily_summary(
                    user=user,
                    agg_yest=agg_yest,
                    yesterday_utc=yesterday_utc,
                    day_before_utc=day_before_utc,
                    session_factory=session_factory,
                )

        else:
            # ── Inactivity reminder schedule ────────────────────────────────
            # Determine how long the user has been inactive.
            # last_active_date is None for users who haven't logged yet — skip them.
            if user.last_active_date is None:
                continue

            days_inactive = (local_today - user.last_active_date).days
            count = user.inactivity_reminder_count

            text = None
            new_count = count
            for min_days, required_count, reminder_text in _INACTIVITY_SCHEDULE:
                if days_inactive >= min_days and count == required_count:
                    text = reminder_text
                    new_count = count + 1
                    break

            if text is None:
                # Either too early or all reminders already sent — stay silent
                continue

            # Persist new count BEFORE send
            async with session_factory() as db:
                fresh = await db.get(User, user.id)
                if fresh is None:
                    continue
                fresh.inactivity_reminder_count = new_count
                await db.commit()

        if text is None:
            continue

        try:
            await bot.send_message(chat_id=user.telegram_id, text=text)
            sent_count += 1
            logger.info(
                "morning_summary_sent",
                telegram_id=user.telegram_id,
                active=was_active_yesterday,
            )
        except Exception as exc:
            logger.warning(
                "morning_summary_send_failed",
                telegram_id=user.telegram_id,
                error=str(exc),
            )

        await asyncio.sleep(0.05)

    return sent_count
