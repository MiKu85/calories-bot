"""
Cleanup script: find and fix duplicate meals created by the patch fallback bug.

Bug: when a meal had no structured items (meal_items = NULL or []), the patch handler
fell back to run_meal_pipeline which created a NEW meal without soft-deleting the
original. This left two active records for the same eating event.

Detection heuristic:
  For each user, find pairs of meals (A, B) where:
    - Both are NOT deleted (is_deleted = FALSE)
    - B was created within WINDOW_MINUTES after A
    - A.meal_items IS NULL or '[]' (the "ghost" meal from the broken path)
  → Mark A as deleted (keep B, the newer re-analysis).

Usage:
  python scripts/cleanup_patch_duplicates.py          # dry run — just reports
  python scripts/cleanup_patch_duplicates.py --fix     # actually marks A as deleted
  python scripts/cleanup_patch_duplicates.py --days 7  # look back N days (default 30)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
WINDOW_MINUTES = 30   # max gap between A and B to be considered a pair
# ---------------------------------------------------------------------------


async def find_and_fix(db: AsyncSession, *, days: int, fix: bool) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Find all "ghost" meals: no items, not deleted, within the lookback window.
    ghost_stmt = text("""
        SELECT id, user_id, calories, logged_at, created_at
        FROM meals
        WHERE is_deleted = FALSE
          AND (meal_items IS NULL OR meal_items::text = '[]')
          AND created_at >= :cutoff
        ORDER BY user_id, created_at
    """)
    ghosts = (await db.execute(ghost_stmt, {"cutoff": cutoff})).fetchall()

    if not ghosts:
        print("No ghost meals found — nothing to do.")
        return

    print(f"Found {len(ghosts)} ghost meal(s) (no items, not deleted):")

    to_delete: list[int] = []

    for ghost in ghosts:
        # Check if there is a newer meal for the same user created within the window
        sibling_stmt = text("""
            SELECT id, calories, logged_at, created_at
            FROM meals
            WHERE user_id   = :uid
              AND is_deleted = FALSE
              AND id        != :gid
              AND created_at BETWEEN :created_at AND :created_at + INTERVAL ':window minutes'
            ORDER BY created_at ASC
            LIMIT 1
        """)
        # SQLAlchemy text() doesn't interpolate INTERVAL cleanly — use plain comparison
        window_end = ghost.created_at + timedelta(minutes=WINDOW_MINUTES)
        sibling_stmt2 = text("""
            SELECT id, calories, logged_at, created_at
            FROM meals
            WHERE user_id   = :uid
              AND is_deleted = FALSE
              AND id        != :gid
              AND created_at BETWEEN :ts_start AND :ts_end
            ORDER BY created_at ASC
            LIMIT 1
        """)
        sibling = (await db.execute(sibling_stmt2, {
            "uid": ghost.user_id,
            "gid": ghost.id,
            "ts_start": ghost.created_at,
            "ts_end": window_end,
        })).fetchone()

        if sibling:
            print(
                f"  Ghost meal id={ghost.id}  user={ghost.user_id}  "
                f"{ghost.calories:.0f} kcal  created={ghost.created_at.isoformat()}"
            )
            print(
                f"    → sibling id={sibling.id}  "
                f"{sibling.calories:.0f} kcal  created={sibling.created_at.isoformat()}"
            )
            to_delete.append(ghost.id)
        else:
            print(
                f"  Ghost meal id={ghost.id}  user={ghost.user_id}  "
                f"{ghost.calories:.0f} kcal  — no sibling found, skip"
            )

    if not to_delete:
        print("\nNo duplicates to fix.")
        return

    print(f"\n{len(to_delete)} ghost meal(s) have a sibling and should be marked deleted:")
    print("  ids:", to_delete)

    if not fix:
        print("\nDRY RUN — pass --fix to apply.")
        return

    mark_stmt = text("""
        UPDATE meals
        SET is_deleted = TRUE
        WHERE id = ANY(:ids)
    """)
    await db.execute(mark_stmt, {"ids": to_delete})
    await db.commit()
    print(f"\nMarked {len(to_delete)} meal(s) as deleted. Done.")


async def main(days: int, fix: bool) -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # Try to load from .env file
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not db_url:
        print("ERROR: DATABASE_URL not set. Export it or create a .env file.")
        sys.exit(1)

    # Ensure async driver
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        await find_and_fix(db, days=days, fix=fix)

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix duplicate meals from patch fallback bug")
    parser.add_argument("--fix", action="store_true", help="Apply fixes (default: dry run)")
    parser.add_argument("--days", type=int, default=30, help="Look-back window in days (default 30)")
    args = parser.parse_args()

    asyncio.run(main(days=args.days, fix=args.fix))
