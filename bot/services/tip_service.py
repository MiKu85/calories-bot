"""
Periodic tips service.

Rules:
  - Show a tip no more than once every 3 confirmed meals.
  - On the first meal (counter=0→1), show with 40% probability.
  - No tip repeats within 14 days for the same user.
  - Tips are loaded from data/tips.json at import time.

Usage:
    tip = await maybe_get_tip(user, db)   # None or a tip string
    # Call after incrementing the meal counter (i.e., after confirmation).
"""
from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import User

# ── Load tip pool ──────────────────────────────────────────────────────────────

_TIPS_PATH = Path(__file__).parent.parent.parent / "data" / "tips.json"

try:
    _TIPS: list[dict] = json.loads(_TIPS_PATH.read_text(encoding="utf-8"))
except Exception:
    _TIPS = []

_TIP_IDS: list[int] = [t["id"] for t in _TIPS]
_TIP_BY_ID: dict[int, str] = {t["id"]: t["text"] for t in _TIPS}

# ── Constants ──────────────────────────────────────────────────────────────────

_SHOW_EVERY_N_MEALS = 3        # show tip at most once per N meals
_FIRST_MEAL_PROBABILITY = 0.40 # chance of showing tip on the very first meal
_DEDUP_DAYS = 14               # don't repeat the same tip within this window


# ── Helpers ────────────────────────────────────────────────────────────────────

def _recent_tip_ids(user: User) -> set[int]:
    """Return IDs of tips shown within the last _DEDUP_DAYS."""
    history: list[dict] = user.tip_history or []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_DEDUP_DAYS)).date()
    recent: set[int] = set()
    for entry in history:
        try:
            shown_at = date.fromisoformat(entry["shown_at"])
            if shown_at >= cutoff:
                recent.add(int(entry["tip_id"]))
        except (KeyError, ValueError):
            pass
    return recent


def _pick_tip(user: User) -> str | None:
    """Pick a random tip not shown in the last _DEDUP_DAYS."""
    if not _TIP_IDS:
        return None
    recent = _recent_tip_ids(user)
    available = [tid for tid in _TIP_IDS if tid not in recent]
    if not available:
        # All tips shown recently — reset and pick any
        available = _TIP_IDS
    tip_id = random.choice(available)
    return _TIP_BY_ID.get(tip_id)


def _record_tip(user: User, text: str) -> None:
    """Record the shown tip in user.tip_history (in-place mutation, caller flushes)."""
    # Find tip_id by text
    tip_id: int | None = None
    for t in _TIPS:
        if t["text"] == text:
            tip_id = t["id"]
            break
    if tip_id is None:
        return

    history: list[dict] = list(user.tip_history or [])
    today_str = datetime.now(timezone.utc).date().isoformat()
    history.append({"tip_id": tip_id, "shown_at": today_str})

    # Keep only last 60 entries to avoid unbounded growth
    if len(history) > 60:
        history = history[-60:]

    user.tip_history = history
    user.tips_meal_counter = 0


# ── Public API ─────────────────────────────────────────────────────────────────

async def get_morning_tip(user: User, db: AsyncSession) -> str | None:
    """
    Get a tip for the morning summary (does NOT increment the meal counter).
    Uses the same 14-day dedup logic as maybe_get_tip.
    Records the tip in history so it won't repeat for 14 days.
    """
    tip = _pick_tip(user)
    if tip is None:
        return None
    _record_tip(user, tip)
    await db.flush()
    return tip


async def maybe_get_tip(user: User, db: AsyncSession) -> str | None:
    """
    Increment the meal counter and (maybe) return a tip string.

    Call this AFTER confirming a meal (not after saving — only confirmed meals count).
    Returns None if no tip should be shown this time.

    Side effects: mutates user.tips_meal_counter (and tip_history if tip shown).
    The caller is responsible for flushing/committing the session.
    """
    user.tips_meal_counter = (user.tips_meal_counter or 0) + 1
    counter = user.tips_meal_counter

    show: bool
    if counter == 1:
        # Very first confirmed meal — show with 40% probability
        show = random.random() < _FIRST_MEAL_PROBABILITY
    elif counter % _SHOW_EVERY_N_MEALS == 0:
        # Every 3rd confirmed meal — always show
        show = True
    else:
        show = False

    if not show:
        await db.flush()
        return None

    tip = _pick_tip(user)
    if tip is None:
        await db.flush()
        return None

    _record_tip(user, tip)
    await db.flush()
    return tip
