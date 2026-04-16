"""
Data migration: find and soft-delete duplicate meals created by the old "Fix" bug.

Old behavior: pressing "Исправить" created a new INSERT without soft-deleting the
previous record.  This left chains of 2-5 records per editing session, all active,
all counting toward the daily total.

Algorithm:
  For each (user, calendar-day) group of non-deleted meals (ordered by created_at):
    1. Build chains using time-proximity only: consecutive records ≤ WINDOW_MINUTES apart
       belong to the same chain.
    2. Find the "final generation" inside each chain: all records created within
       SPLIT_SECONDS of the last record.  This preserves intentional splits
       (two meals created a few seconds apart by the split flow).
    3. Skip the chain if the final generation contains a ghost record (0 kcal + no items).
    4. Soft-delete everything except the final generation.

Only records created BEFORE the deployment cutoff are considered (new code uses
replace_meal which soft-deletes old records automatically).

Usage:
  python scripts/fix_patch_chains.py           # dry run
  python scripts/fix_patch_chains.py --fix      # apply
  python scripts/fix_patch_chains.py --days 60  # look back N days (default 30)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

WINDOW_MINUTES = 5    # max gap between records in the same edit chain
SPLIT_SECONDS = 120   # records this close to the chain tail are treated as a split pair
# Deployment time of the fix (UTC): records created after this use the new replace_meal flow
DEPLOY_CUTOFF = datetime(2026, 4, 16, 10, 39, 0, tzinfo=timezone.utc)


def _is_ghost(meal_row) -> bool:
    """True if the record has 0 calories and no items (ghost from fallback bug)."""
    if meal_row.calories and meal_row.calories > 0:
        return False
    items = meal_row.meal_items
    if not items:
        return True
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            return True
    return len(items) == 0


def _build_chains(meals: list) -> list[list]:
    """Group time-sorted meals into chains using time-proximity only."""
    if not meals:
        return []
    chains: list[list] = [[meals[0]]]
    for meal in meals[1:]:
        prev = chains[-1][-1]
        gap_min = (meal.created_at - prev.created_at).total_seconds() / 60
        if gap_min <= WINDOW_MINUTES:
            chains[-1].append(meal)
        else:
            chains.append([meal])
    return chains


def _final_generation(chain: list) -> list:
    """
    Return the subset of records at the tail of the chain that are
    within SPLIT_SECONDS of the last record.
    These are likely intentional splits and should ALL be kept.
    """
    if not chain:
        return []
    last_ts = chain[-1].created_at
    gen = []
    for meal in reversed(chain):
        if (last_ts - meal.created_at).total_seconds() <= SPLIT_SECONDS:
            gen.append(meal)
        else:
            break
    return list(reversed(gen))  # restore chronological order


async def find_and_fix(db: AsyncSession, *, days: int, fix: bool) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    stmt = text("""
        SELECT id, user_id, calories, meal_items, logged_at, created_at
        FROM meals
        WHERE is_deleted = FALSE
          AND created_at >= :cutoff
          AND created_at < :deploy_cutoff
        ORDER BY user_id, logged_at::date, created_at
    """)
    rows = (await db.execute(stmt, {
        "cutoff": cutoff,
        "deploy_cutoff": DEPLOY_CUTOFF,
    })).fetchall()

    if not rows:
        print("No active pre-deployment meals found in the lookback window.")
        return

    # Group by (user_id, calendar day based on logged_at)
    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        day = row.logged_at.date() if row.logged_at else row.created_at.date()
        groups[(row.user_id, day)].append(row)

    to_delete: list[int] = []
    skipped_chains: list[str] = []
    report_lines: list[str] = []

    for (user_id, day), meals in sorted(groups.items()):
        chains = _build_chains(meals)
        for chain in chains:
            if len(chain) < 2:
                continue  # single record — nothing to fix

            final_gen = _final_generation(chain)
            keepers = final_gen
            victims = [m for m in chain if m not in final_gen]

            # Skip if any keeper is a ghost record
            ghost_keepers = [m for m in keepers if _is_ghost(m)]
            if ghost_keepers:
                skipped_chains.append(
                    f"  SKIP User {user_id}, {day}: chain ids={[m.id for m in chain]} — "
                    f"final generation contains ghost record(s) "
                    f"(ids {[m.id for m in ghost_keepers]}, 0 kcal / no items). "
                    f"Manual review needed."
                )
                continue

            victim_ids = [m.id for m in victims]
            to_delete.extend(victim_ids)

            before_sum = sum(m.calories for m in chain)
            after_sum = sum(m.calories for m in keepers)
            report_lines.append(
                f"  User {user_id}, {day}: chain of {len(chain)} "
                f"(ids: {[m.id for m in chain]}) — "
                f"keep {[m.id for m in keepers]} ({after_sum:.0f} kcal), "
                f"delete {len(victims)}, "
                f"counter: {before_sum:.0f} → {after_sum:.0f} kcal"
            )

    if report_lines:
        print(f"Chains to fix ({len(report_lines)}):\n")
        for line in report_lines:
            print(line)

    if skipped_chains:
        print(f"\nSkipped chains — need manual review ({len(skipped_chains)}):\n")
        for line in skipped_chains:
            print(line)

    if not to_delete:
        print("\nNo meals to soft-delete." if not skipped_chains else "")
        return

    print(f"\nTotal meals to soft-delete: {len(to_delete)}")
    print(f"IDs: {to_delete}")

    if not fix:
        print("\nDRY RUN — pass --fix to apply.")
        return

    now = datetime.now(timezone.utc)
    mark_stmt = text("""
        UPDATE meals
        SET is_deleted = TRUE,
            deleted_at = :now
        WHERE id = ANY(:ids)
    """)
    await db.execute(mark_stmt, {"ids": to_delete, "now": now})

    # Recalculate daily_aggregates for affected (user_id, date) pairs
    affected_pairs = {
        (row.user_id, (row.logged_at or row.created_at).date())
        for row in rows
        if row.id in set(to_delete)
    }
    for uid, day in affected_pairs:
        recalc_stmt = text("""
            INSERT INTO daily_aggregates (user_id, date, total_calories, total_protein_g,
                                          total_fat_g, total_carbs_g, meals_count, updated_at)
            SELECT :uid, :day,
                   COALESCE(SUM(calories), 0),
                   COALESCE(SUM(protein_g), 0),
                   COALESCE(SUM(fat_g), 0),
                   COALESCE(SUM(carbs_g), 0),
                   COUNT(*),
                   NOW()
            FROM meals
            WHERE user_id = :uid
              AND is_deleted = FALSE
              AND logged_at::date = :day
            ON CONFLICT (user_id, date) DO UPDATE
              SET total_calories  = EXCLUDED.total_calories,
                  total_protein_g = EXCLUDED.total_protein_g,
                  total_fat_g     = EXCLUDED.total_fat_g,
                  total_carbs_g   = EXCLUDED.total_carbs_g,
                  meals_count     = EXCLUDED.meals_count,
                  updated_at      = EXCLUDED.updated_at
        """)
        await db.execute(recalc_stmt, {"uid": uid, "day": str(day)})

    await db.commit()
    print(f"\nMarked {len(to_delete)} meal(s) as deleted. Daily aggregates recalculated. Done.")


async def main(days: int, fix: bool) -> None:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    if not db_url:
        print("ERROR: DATABASE_URL not set.")
        sys.exit(1)

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
    parser = argparse.ArgumentParser(description="Fix edit-chain duplicates from old patch bug")
    parser.add_argument("--fix", action="store_true", help="Apply fixes (default: dry run)")
    parser.add_argument("--days", type=int, default=30, help="Look-back window in days (default 30)")
    args = parser.parse_args()
    asyncio.run(main(days=args.days, fix=args.fix))
