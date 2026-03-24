# Smoke Test Checklist

Manual test plan for pre-launch verification. Run against a live bot connected to real services (Telegram, PostgreSQL, OpenAI).

Mark each step: ✅ Pass / ❌ Fail / ⚠️ Unexpected behavior

---

## Prerequisites

- Bot is running (polling or webhook)
- PostgreSQL has `alembic upgrade head` applied (tables exist)
- `OPENAI_API_KEY` is valid
- `TELEGRAM_CHANNEL_ID` is set to a channel where the bot is admin
- You have **two** Telegram accounts available: one subscribed to the channel, one not subscribed

---

## 1. `/start` — Basic entry

| # | Action | Expected |
|---|---|---|
| 1.1 | Send `/start` from unsubscribed account | Bot replies with "нужно подписаться" + subscription keyboard (2 buttons) |
| 1.2 | Press "Подписаться на канал" button | Opens channel URL in Telegram |
| 1.3 | Press "Я подписался — проверить" without subscribing | Bot replies "Не вижу подписки" + keyboard again |
| 1.4 | Subscribe to channel, then press "Я подписался — проверить" | Bot shows welcome message + "Начать" button |
| 1.5 | Send `/start` again from already-subscribed+completed account | Bot shows "Привет снова!" hint message |

---

## 2. Unsubscribed user flow

| # | Action | Expected |
|---|---|---|
| 2.1 | Send any text from unsubscribed account | No response (user middleware creates User but text handler requires completed onboarding; since onboarding state = new, catch-all fires: "Сначала нужно заполнить профиль. Напиши /start.") |
| 2.2 | Send `/stats` from unsubscribed account | "Сначала нужно заполнить профиль. Напиши /start." |

---

## 3. Onboarding completion

| # | Action | Expected |
|---|---|---|
| 3.1 | Press "Начать" after welcome | Bot asks for name |
| 3.2 | Send name text | Bot asks sex (inline keyboard) |
| 3.3 | Press male/female | Bot asks age |
| 3.4 | Send invalid age `abc` | Bot replies with validation error: "Введи возраст числом от 10 до 100" |
| 3.5 | Send age `9` (below minimum) | Same validation error |
| 3.6 | Send valid age e.g. `28` | Bot asks height |
| 3.7 | Send height `175` | Bot asks weight |
| 3.8 | Send weight `75` | Bot asks activity level (inline keyboard) |
| 3.9 | Press any activity level | Bot asks workouts per week (inline keyboard) |
| 3.10 | Press "Пропустить" | Bot asks goal |
| 3.11 | Press any goal | Bot shows calculated targets + completion message |
| 3.12 | Verify targets are non-zero | Calories > 0, protein/fat/carbs > 0 |

---

## 4. Target calculation verification

| # | Action | Expected |
|---|---|---|
| 4.1 | After onboarding, send `/profile` | Profile shows all entered fields + calculated targets |
| 4.2 | Change weight to `80` via /profile → Изменить вес | Targets recalculate and change |
| 4.3 | Change goal to "похудеть" | Calories decrease vs "поддерживать вес" |

---

## 5. Text meal logging

| # | Action | Expected |
|---|---|---|
| 5.1 | Send `Съел тарелку борща` | Bot analyzes and replies with meal KBJU + daily total + buttons |
| 5.2 | Verify reply has: calories, protein, fat, carbs | All four macros shown |
| 5.3 | Verify daily totals are non-zero | "Сегодня съедено: X ккал" |
| 5.4 | Press "✅ Верно" | Keyboard disappears (edit_reply_markup) |
| 5.5 | Send another meal | Daily totals increase |
| 5.6 | Send `Выпил кофе` | Bot analyzes; likely low calories near 0–5 ккал |
| 5.7 | Send ambiguous input `Что-то поел` | Bot asks for clarification (needs_clarification=True) |

---

## 6. Meal correction flow

| # | Action | Expected |
|---|---|---|
| 6.1 | Send a meal, press "✏️ Исправить" | Bot says "Опиши приём пищи заново" |
| 6.2 | Send corrected description | Bot re-analyzes and shows new result |
| 6.3 | Verify `/stats` shows only the corrected meal (original was soft-deleted) | Totals reflect only corrected meal |

---

## 7. Voice meal logging

| # | Action | Expected |
|---|---|---|
| 7.1 | Send a voice message saying `Съел гречку с курицей, примерно 300 грамм` | Bot shows transcription preview `«Распознал: «...»»` then meal result |
| 7.2 | Verify meal is saved (press ✅ Верно, check /stats) | Meal appears in daily total |
| 7.3 | Send a very short voice message (say "Ааа") | Bot says "Не расслышал — голосовое слишком короткое" |
| 7.4 | Send a voice message in noisy environment | Bot either transcribes or asks to retry/type |

---

## 8. Photo meal logging

| # | Action | Expected |
|---|---|---|
| 8.1 | Send a clear photo of food (e.g., apple on table) | Bot analyzes and replies with meal result + disclaimer |
| 8.2 | Verify disclaimer is shown | "Оценка по фото — приблизительная." |
| 8.3 | Send photo with caption `маленький кусок пиццы примерно 100 граммов` | Caption is used as hint; result mentions pizza |
| 8.4 | Press ✅ Верно | Keyboard disappears |
| 8.5 | Send a photo of a non-food object (e.g., a book) | Bot asks for clarification: "На фото не видно еды" |

---

## 9. Low-confidence photo fallback

| # | Action | Expected |
|---|---|---|
| 9.1 | Send a blurry or very dark photo of food | Bot either returns medium/low confidence result with disclaimer, or asks for clarification |
| 9.2 | If needs_clarification=True: | Bot asks to describe by text/voice; meal is NOT saved |
| 9.3 | If low confidence but clarification not required: | Reply includes "Уверенность низкая" note + standard disclaimer |

---

## 10. `/stats` command

| # | Action | Expected |
|---|---|---|
| 10.1 | Send `/stats` with no meals today | "Сегодня приёмов пищи пока нет." + goal on day |
| 10.2 | Log a meal, then send `/stats` | Shows all 4 macros with consumed/target/remaining |
| 10.3 | Send `/stats` when over calorie target | Shows "перебор X ккал" (positive number, not negative) |
| 10.4 | Press "📊 Статистика за сегодня" inline button after a meal | Same stats shown inline without /stats command |

---

## 11. `/profile` command

| # | Action | Expected |
|---|---|---|
| 11.1 | Send `/profile` | Profile text + 5-button keyboard |
| 11.2 | Press "Изменить вес" | Bot asks for new weight |
| 11.3 | Send invalid weight `abc` | "Введи вес числом от 30 до 300" |
| 11.4 | Send valid weight | Targets recalculated, profile shown |
| 11.5 | Press "Изменить активность" | Activity keyboard shown |
| 11.6 | Press any activity | Targets recalculated, profile shown |
| 11.7 | Press "Изменить цель" | Goal keyboard shown |
| 11.8 | Press "Пересчитать цели" | Targets recalculated, same profile shown |
| 11.9 | Press "Сбросить профиль" | Redirects to /reset flow (confirmation prompt) |

---

## 12. `/reset` command

| # | Action | Expected |
|---|---|---|
| 12.1 | Send `/reset` | Confirmation prompt with "Да, сбросить" / "Отмена" |
| 12.2 | Press "Отмена" | "Сброс отменён." Keyboard disappears. |
| 12.3 | Send `/reset` again, press "Да, сбросить" | "Профиль сброшен. Напиши /start чтобы начать заново." |
| 12.4 | Send `/stats` | "Сначала нужно заполнить профиль." |
| 12.5 | Send `/start` | Welcome message + "Начать" button (onboarding restart) |
| 12.6 | Complete onboarding again | New targets calculated |
| 12.7 | Verify old meal history is preserved | `/stats` may still show today's meals if any were logged before reset |

---

## 13. Admin-only commands

| # | Action | Expected |
|---|---|---|
| 13.1 | Send `/admin` from a non-admin account | No response (silent ignore) |
| 13.2 | Send `/admin` from admin account (telegram_id in TELEGRAM_ADMIN_IDS) | Admin stats message with 6 metrics |
| 13.3 | Verify stats show correct counts | Total users ≥ 1, onboarding completed ≥ 1 |
| 13.4 | Verify "Ошибок нет." if no errors have occurred | Correct |

---

## 14. Feedback flow trigger conditions

| # | Condition | Expected |
|---|---|---|
| 14.1 | User `first_meal_at` is less than 7 days ago | Feedback NOT sent |
| 14.2 | User `first_meal_at` is >= 7 days ago, `feedback_sent_at IS NULL`, `onboarding_state = completed` | Feedback message sent on next scheduler cycle (up to 6 hours) |
| 14.3 | Feedback received: press "Всё нравится" | FeedbackRecord created with text "Всё нравится". Bot asks for voice comment. |
| 14.4 | Press "Пропустить" voice step | "Спасибо за обратную связь!" Keyboard disappears. |
| 14.5 | Press "Написать комментарий" | Bot asks for text comment |
| 14.6 | Send comment text | Bot asks for voice |
| 14.7 | Send voice in voice step | FeedbackRecord.has_voice_comment = True. "Спасибо!" |
| 14.8 | Re-trigger feedback message manually (set feedback_sent_at = NULL in DB) | New feedback message sent on next cycle |

---

## 15. Resume after restart

| # | Action | Expected |
|---|---|---|
| 15.1 | Start onboarding, complete steps up to "age" | User is in `awaiting_age` state in DB |
| 15.2 | Restart the bot process | |
| 15.3 | Send `/start` | Bot resumes from "Сколько тебе лет?" (age step) |

---

## Pass criteria

All items in sections 1–13 must pass for launch readiness. Section 14 can be verified by directly checking the DB (`feedback_sent_at` field) rather than waiting 7 days.
