# Gap Report — MVP Acceptance Audit

Audit date: 2026-03-22
Auditor: code review (no live deployment tested)

Status legend:
- **DONE** — fully implemented, logic verified in code
- **PARTIAL** — implemented but has a known gap or risk
- **MISSING** — not implemented
- **RISKY** — implemented but has a real-world failure mode that must be addressed before launch

---

## 1. Subscription gate (`@healthy_normal`)

**Status: RISKY**

**What exists:**
- `bot/services/subscription.py`: `is_subscribed()` calls `bot.get_chat_member()`
- `/start` checks subscription before any other logic
- `subscription_kb()` provides channel link and re-check button
- `is_subscribed` flag on User model, kept in sync on `/start`

**What may break in real usage:**
- `getChatMember` requires the **bot to be an admin** in the channel. If the bot is not admin, the API returns `TelegramBadRequest` and the error handler returns `False` — meaning **all users will be blocked** with "Не вижу подписки" even if they are subscribed.
- This is not documented in README, deploy checklist, or `.env.example`.

**What blocks deployment:**
- Must add the bot as admin (or at minimum a member) in `@healthy_normal` before going live.

---

## 2. Onboarding

**Status: DONE**

**What exists:**
- 8 steps: name → sex → age → height → weight → activity → workouts → goal
- `OnboardingStates` FSM mirrors `OnboardingState` DB enum
- Resume after restart via `_DB_TO_FSM` map + `resume_onboarding()`
- Input validation: age 10–100, height 100–250 cm, weight 30–300 kg
- Workouts step is skippable

**What is missing:** Nothing for MVP scope.

**What may break:**
- If two rapid `/start` calls race (user double-taps), two User rows could be inserted. Mitigated by `UniqueConstraint` on `telegram_id` — the second insert would fail and trigger the error handler. Not a data corruption risk.

---

## 3. Target calculation (Mifflin-St Jeor)

**Status: DONE**

**What exists:**
- `bot/services/target_calculator.py`: pure functions `calculate_bmr()`, `calculate_targets()`
- Correct Mifflin-St Jeor formula for male/female
- Activity multipliers, goal multipliers, protein/fat constants
- `UserTargets` dataclass (frozen)
- 20 unit tests in `tests/test_target_calculator.py`

**What may break:** Nothing realistic.

---

## 4. Text meal logging

**Status: DONE**

**What exists:**
- `bot/handlers/meal.py`: `handle_text_meal()` with `OnboardingCompleted` filter
- `run_meal_pipeline()`: analyze → clarify or save+reply
- Inline keyboard: ✅ Верно / ✏️ Исправить / 📊 Статистика
- Soft-delete correction flow: `meal_fix:` callback deletes old meal + asks to re-describe
- `MealStates.awaiting_correction` FSM state so correction re-uses text handler

**What may break:**
- If a user sends text while in a non-meal FSM state (e.g., `ProfileEditStates.awaiting_weight`), `StateFilter` correctly blocks the meal handler. Tested in code logic.

---

## 5. Voice meal logging

**Status: DONE**

**What exists:**
- `bot/handlers/voice.py`: downloads Telegram OGG to in-memory `BytesIO`
- `OpenAISTTProvider.transcribe()` with 3-retry tenacity
- Transcription preview shown: `«Распознал: «текст»»`
- Empty/short transcription guard (`_MIN_TRANSCRIPTION_LENGTH = 3`)
- Delegates to `run_meal_pipeline()` (same as text)
- Audio bytes explicitly deleted after transcription

**What may break:**
- Very long voice messages (>25MB Telegram limit) won't be sent by Telegram client, not a bot-side issue.
- Whisper model accuracy for Russian food names is high but not perfect.

---

## 6. Photo meal analysis

**Status: DONE**

**What exists:**
- `bot/handlers/photo.py`: downloads highest-res photo to in-memory `BytesIO`
- `OpenAIVisionProvider.analyze_meal_photo()` with 3-retry tenacity
- Caption passed as `user_hint`
- Three-tier confidence disclaimer system
- `needs_clarification=True` → ask to describe by text/voice, do NOT save
- Photo bytes explicitly deleted after analysis
- Only caption (not photo bytes) stored as `raw_input`

**What may break:**
- GPT-4o vision may occasionally refuse to analyze food images. The provider handles parse errors with a `_fallback_clarification()` response, so the bot never crashes — but the user gets asked to describe by text, which is acceptable.

---

## 7. Daily stats (`/stats` + post-meal display)

**Status: DONE**

**What exists:**
- `bot/handlers/stats.py`: `/stats` command
- `bot/services/stats_service.py`: `format_stats()`, `format_meal_result()`
- Edge cases: no meals today, no targets set, over-target ("перебор" with positive number)
- `get_today_aggregate()` returns zeroed aggregate when no meals
- 20 unit tests in `tests/test_stats_service.py`

**What may break:**
- UTC date boundary: users in UTC+5 and later will see their "day" roll over at 8 PM local time. Documented as known limitation in `docs/mvp_scope.md`. Not a bug — accepted simplification.

---

## 8. `/profile` command

**Status: DONE**

**What exists:**
- `build_profile_text()`: formats all profile fields + targets
- Edit flows: weight (text input), activity (inline kb), goal (inline kb), recalculate, reset (redirect)
- `ProfileEditStates.awaiting_weight` FSM
- Guard prevents profile activity/goal callbacks from intercepting onboarding callbacks (FSM state check)

**What may break:**
- Activity and goal edit callbacks use the same `activity:` and `goal:` prefixes as onboarding. The guard `if current == OnboardingStates.awaiting_activity: return` relies on FSM state being correctly set in onboarding. After restart mid-onboarding, FSM is restored by `resume_onboarding()` — so this should be safe.

---

## 9. `/reset` command

**Status: DONE**

**What exists:**
- Confirmation prompt with inline keyboard (Да/Отмена)
- `reset_onboarding()` clears all profile fields, sets `onboarding_state = new`
- Message correctly states "История приёмов пищи сохранится" — meal history is preserved
- `ResetStates.awaiting_confirm` FSM

**What may break:** Nothing realistic.

---

## 10. `/help` command

**Status: DONE**

**What exists:** Static text listing all commands and button descriptions.

---

## 11. Admin commands (`/admin`)

**Status: DONE**

**What exists:**
- `bot/handlers/admin.py`: silent ignore for non-admins
- `bot/services/admin_service.py`: 6 metrics (total users, new today, active today, meals today, onboarding completed, last 5 errors)
- Config: `TELEGRAM_ADMIN_IDS` parsed from comma-separated string

**What may break:**
- If `TELEGRAM_ADMIN_IDS` is not set (empty list), no one can use `/admin` — not a bug, just requires correct config.

---

## 12. Delayed feedback flow

**Status: DONE**

**What exists:**
- `bot/tasks/feedback_scheduler.py`: `feedback_scheduler_loop()` background asyncio task
- 30-second startup delay before first check
- Checks every 6 hours
- Eligibility: `first_meal_at` >= 7 days ago, `feedback_sent_at IS NULL`, `onboarding_state = completed`
- `feedback_sent_at` written to DB BEFORE Telegram send (crash-safe, no duplicates)
- Separate DB session per user to avoid long transactions
- `bot/handlers/feedback.py`: 4 options (like/inaccurate/inconvenient/comment) + optional voice
- `FeedbackRecord` stored with text + `has_voice_comment` flag

**What may break:**
- If Railway restarts the process (daily restarts are common), the scheduler restarts with a 30-second delay. The 6-hour check interval means a restart mid-cycle could delay feedback send by up to 6 hours. Acceptable.

---

## 13. Railway readiness / Docker

**Status: PARTIAL → Fixed**

**What existed (before this audit):**
- `Dockerfile`: `CMD ["python", "main.py"]` — **did not run migrations** before starting
- `docker-compose.yml`: no migration step
- No Alembic migration files in `alembic/versions/` — **`alembic upgrade head` was a no-op**

**What was fixed during this audit:**
- Created `alembic/versions/0001_initial_schema.py` with full initial schema
- Changed `Dockerfile CMD` to `alembic upgrade head && python main.py`

**Remaining risk:**
- The initial migration was written manually (not via `alembic --autogenerate`). It should be validated against a real PostgreSQL instance before production deploy: run `alembic upgrade head` on a clean DB and verify all tables exist.

---

## 14. `docs/mvp_scope.md`

**Status: DONE**

Content is accurate, complete, and up-to-date.

---

## 15. `docs/future_improvements.md`

**Status: DONE**

Content documents known post-MVP directions. Accurate.

---

## 16. Error handling

**Status: DONE**

**What exists:**
- `bot/handlers/errors.py`: catches all unhandled exceptions
- Logs with structlog, writes to `event_logs` table
- Sends generic Russian error message to user

**Gap (non-blocking):** EventLog writes for non-error events (meal_logged, onboarding_completed, etc.) are defined in the enum but never written. Admin stats only reads errors, so this has no functional impact.

---

## 17. Alembic migration infrastructure

**Status: PARTIAL → Fixed**

**What existed:** `alembic.ini` had a placeholder URL `driver://user:pass@localhost/dbname`. `alembic/env.py` correctly overrides this with `settings.database_url_sync`, so the placeholder is never actually used. However, it causes confusion during debugging.

**What was missing:** Initial migration file — now created.

---

## Summary Table

| Feature | Status | Blocks Deploy? |
|---|---|---|
| Subscription gate | RISKY | Yes — bot must be channel admin |
| Onboarding | DONE | No |
| Target calculation | DONE | No |
| Text meal logging | DONE | No |
| Voice meal logging | DONE | No |
| Photo meal analysis | DONE | No |
| Daily stats | DONE | No |
| /profile | DONE | No |
| /reset | DONE | No |
| /help | DONE | No |
| Admin commands | DONE | No |
| Delayed feedback flow | DONE | No |
| Alembic migrations | PARTIAL → Fixed | Was blocking |
| Docker / Railway | PARTIAL → Fixed | Was blocking |
| docs/mvp_scope.md | DONE | No |
| docs/future_improvements.md | DONE | No |
