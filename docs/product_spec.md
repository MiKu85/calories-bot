# MVP Product Spec — Telegram Nutrition Bot

> Reference document for implementation. Generated after initial scaffold.
> Update as decisions are made.

---

## Product Overview

Telegram-бот нутрициолог на русском языке. Пользователь описывает еду — фото, голосом или текстом — и получает оценку КБЖУ (калории, белки, жиры, углеводы). Бот ведёт дневной баланс и показывает остаток после каждого приёма пищи.

**Главная ценность:** не нужно вручную искать калорийность — просто описал или сфотографировал.

---

## User Roles

| Роль | Описание |
|---|---|
| User | Подписан на @healthy_normal, прошёл онбординг, логирует еду |
| Admin | Telegram-пользователь из `TELEGRAM_ADMIN_IDS`, видит /admin-статистику |

---

## User Flows

### Flow 1: Первый запуск

```
/start
  → проверка подписки на @healthy_normal
    → не подписан: кнопка [Подписаться] + [Проверить снова]
    → подписан: приветствие + дисклеймер + кнопка [Начать]
      → онбординг (Flow 2)
```

### Flow 2: Онбординг (один вопрос за раз)

```
Имя → Пол → Возраст → Рост → Вес → Уровень активности → Кол-во тренировок → Цель
  → расчёт КБЖУ-целей
  → сообщение с результатами + кнопка [Поехали!]
```

Правила:
- Каждый вопрос — отдельное сообщение
- Неважные поля (кол-во тренировок) можно пропустить кнопкой [Пропустить]
- Критичные поля (пол, возраст, рост, вес) — обязательны, пропуск недоступен
- При некорректном вводе — повторный вопрос с подсказкой

### Flow 3: Логирование еды — текст / голос

```
Пользователь пишет текст или отправляет голосовое
  → (голос) транскрипция Whisper → текст
  → анализ текста через TextProvider
    → confidence HIGH/MEDIUM:
        показать результат + инлайн-кнопки [✅ Верно] [✏️ Исправить] [📊 Статистика за сегодня]
        → [✅ Верно]: сохранить приём, показать дневной баланс
        → [✏️ Исправить]: попросить описать заново
    → confidence LOW / needs_clarification:
        показать уточняющий вопрос, не сохранять
```

### Flow 4: Логирование еды — фото

```
Пользователь отправляет фото
  → анализ VisionProvider
    → confidence HIGH/MEDIUM: тот же флоу что и текст
    → confidence LOW / needs_clarification:
        явно сообщить что не уверен
        попросить уточнить текстом или голосом
        (не сохранять и не показывать неточные цифры)
```

### Flow 5: После подтверждения приёма

```
Сохранить Meal + обновить DailyAggregate
Показать:
  - КБЖУ этого приёма
  - Съедено сегодня
  - Осталось сегодня
  - Короткая мотивирующая фраза
Инлайн: [✅ Верно] [✏️ Исправить] [📊 Статистика за сегодня]
```

### Flow 6: /stats

```
→ показать:
   Калории: X / Y (осталось Z)
   Белки: ...
   Жиры: ...
   Углеводы: ...
   Приёмов сегодня: N
   Статус-фраза: "идёт по плану" / "чуть выше плана" / "есть ещё запас"
```

### Flow 7: /profile

```
→ показать профиль + цели
→ кнопки: [Изменить вес] [Изменить активность] [Изменить цель]
          [Пересчитать цели] [Сбросить профиль]
```

### Flow 8: /reset

```
→ что сбросить?
  [Сбросить онбординг] [Сбросить день] [Полный сброс]
  → подтверждение → выполнить
```

### Flow 9: Фидбэк (разовый, через 7 дней после первой еды)

```
Триггер: now() - first_meal_at >= 7 дней, feedback_sent_at IS NULL
→ задать вопрос о боте
→ любой ответ → попросить оставить голосовой комментарий
→ записать FeedbackRecord, проставить feedback_sent_at
```

### Flow 10: Admin-команды

```
/admin → доступно только telegram_id из TELEGRAM_ADMIN_IDS
→ всего пользователей
→ активных сегодня
→ новых сегодня
→ приёмов пищи сегодня
→ завершили онбординг
→ последние ошибки
```

---

## Business Rules

### Подписка
- Без подписки на @healthy_normal — никакого доступа
- Проверка через Telegram Bot API `getChatMember`
- Повторная проверка — только по нажатию кнопки [Проверить снова]
- Статус подписки не кэшируется надолго (проверять при /start и при кнопке)

### Онбординг
- Нельзя логировать еду без завершённого онбординга
- Если критичные поля (пол/возраст/рост/вес) не заполнены — цели не рассчитываются
- При изменении веса/активности/цели — цели пересчитываются автоматически

### Расчёт целей (Mifflin-St Jeor)
```
Мужчины: BMR = 10×вес + 6.25×рост - 5×возраст + 5
Женщины: BMR = 10×вес + 6.25×рост - 5×возраст - 161

Multipliers активности:
  sedentary:   ×1.2
  light:       ×1.375
  moderate:    ×1.55
  active:      ×1.725
  very_active: ×1.9

Коррекция цели:
  lose:     TDEE × 0.85  (дефицит 15%)
  maintain: TDEE × 1.0
  gain:     TDEE × 1.10  (профицит 10%)

Макросы (от веса):
  Белки:  lose 1.8 г/кг, maintain 1.6 г/кг, gain 2.0 г/кг
  Жиры:   0.8 г/кг (все цели)
  Углеводы: (calories - protein_cal - fat_cal) / 4
```

### Приёмы пищи
- Голос → транскрипция → тот же пайплайн, что и текст
- Фото с низкой уверенностью → уточнить, не сохранять
- Напитки — только если явно упомянуты (текст/голос) или чётко видны (фото)
- Фото не хранятся после анализа
- Редактирование приёма = описать заново (не редактор ингредиентов)

### Дневной агрегат
- UTC-дата (timezone в future improvements)
- UPSERT при каждом подтверждённом приёме пищи
- Удалённые приёмы (`is_deleted=True`) исключаются из агрегата

---

## Edge Cases и Failure Handling

| Ситуация | Поведение |
|---|---|
| Пользователь не подписан | Показать кнопки, заблокировать доступ |
| Telegram API недоступен при проверке подписки | Сообщить об ошибке, попросить попробовать позже |
| AI-провайдер вернул ошибку (3 retry) | Сообщить пользователю, логировать в EventLog |
| AI вернул невалидный JSON | Fallback: needs_clarification=True, попросить описать заново |
| Фото без еды | VisionProvider возвращает needs_clarification + объяснение |
| Голосовое слишком короткое / тишина | STT возвращает пустой текст → попросить повторить |
| Профиль неполный, пользователь хочет логировать | Напомнить завершить профиль |
| Пользователь пишет вне ожидаемого флоу | Подсказка с доступными командами |
| Повторный /start у существующего пользователя | Не сбрасывать профиль, показать статус |
| Admin-команда от не-admin | Тихо игнорировать или ответить "нет доступа" |
| Дублированное нажатие инлайн-кнопки | Идемпотентно — повторное подтверждение не создаёт второй приём |

---

## Technical Architecture

```
┌─────────────┐     ┌──────────────────────────────────────┐
│   Telegram  │────▶│              aiogram 3               │
└─────────────┘     │  handlers/ (routers per flow)        │
                    │  keyboards/ (inline + reply)          │
                    │  middlewares/ (auth, db session)      │
                    └──────────────┬───────────────────────┘
                                   │
                    ┌──────────────▼───────────────────────┐
                    │           services/                   │
                    │  user_service, meal_service,          │
                    │  stats_service, target_calculator     │
                    └──────────────┬───────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                     │
  ┌───────────▼──────┐  ┌──────────▼──────┐  ┌─────────▼────────┐
  │   bot/ai/        │  │   bot/db/        │  │   config.py      │
  │ TextProvider     │  │ SQLAlchemy async │  │ Pydantic Settings│
  │ VisionProvider   │  │ Alembic          │  └──────────────────┘
  │ STTProvider      │  │ PostgreSQL       │
  └──────────────────┘  └─────────────────┘

Deployment:
  Railway (production) — webhook mode, FastAPI + uvicorn
  Local (dev) — polling mode
```

### Режим работы
- **Dev:** `polling` — просто `python main.py`
- **Prod:** `webhook` — FastAPI `/webhook`, Railway переменная `WEBHOOK_URL`
- Health check: `GET /health`

---

## Data Model

### users
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| telegram_id | bigint unique | |
| telegram_username | varchar | |
| preferred_name | varchar | |
| sex | enum | male/female |
| age | int | |
| height_cm | float | |
| weight_kg | float | |
| activity_level | enum | sedentary/light/moderate/active/very_active |
| workouts_per_week | int | nullable |
| goal | enum | lose/maintain/gain |
| daily_calories_target | float | null пока профиль неполный |
| daily_protein_g_target | float | |
| daily_fat_g_target | float | |
| daily_carbs_g_target | float | |
| onboarding_state | enum | new → ... → completed |
| is_subscribed | bool | |
| onboarding_completed_at | timestamptz | |
| first_meal_at | timestamptz | триггер для фидбэка |
| feedback_sent_at | timestamptz | |
| created_at / updated_at | timestamptz | |

### meals
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| user_id | FK users | |
| input_type | enum | text/voice/photo |
| raw_input | text | текст или транскрипция |
| calories / protein_g / fat_g / carbs_g | float | |
| confidence | enum | high/medium/low |
| confidence_notes | text | |
| meal_items | JSON | [{name, portion, calories, ...}] |
| is_confirmed | bool | подтверждён пользователем |
| is_deleted | bool | soft delete |
| logged_at / created_at | timestamptz | |

### daily_aggregates
| Поле | Тип | Описание |
|---|---|---|
| id | PK | |
| user_id | FK | |
| date | date | UTC |
| total_calories / protein / fat / carbs | float | |
| meals_count | int | |
| updated_at | timestamptz | |

UNIQUE (user_id, date)

### feedback_records
- id, user_id (unique FK), feedback_text, has_voice_comment, created_at

### event_logs
- id, user_id (nullable), event_type, payload (JSON), created_at

---

## Modules / Services

| Модуль | Ответственность |
|---|---|
| `handlers/start.py` | /start, проверка подписки |
| `handlers/onboarding.py` | FSM онбординга, вопрос за вопросом |
| `handlers/meal.py` | текст / голос / фото → анализ → подтверждение |
| `handlers/profile.py` | /profile, редактирование полей |
| `handlers/stats.py` | /stats |
| `handlers/reset.py` | /reset |
| `handlers/admin.py` | /admin, проверка admin_ids |
| `handlers/help.py` | /help |
| `services/user_service.py` | get_or_create_user, update_profile |
| `services/meal_service.py` | save_meal, delete_meal, get_today_meals |
| `services/stats_service.py` | get_daily_aggregate, upsert_aggregate |
| `services/target_calculator.py` | Mifflin-St Jeor, macro calculation |
| `services/subscription.py` | check_channel_subscription via Bot API |
| `services/feedback_service.py` | check_feedback_trigger, save_feedback |
| `bot/ai/` | TextProvider, VisionProvider, STTProvider + factory |
| `bot/db/` | models, session, engine |
| `bot/keyboards/` | inline и reply клавиатуры |
| `config.py` | Pydantic Settings из .env |
| `main.py` | polling / webhook entrypoint |

---

## Telegram States (FSM)

```
OnboardingState:
  new → awaiting_name → awaiting_sex → awaiting_age
      → awaiting_height → awaiting_weight → awaiting_activity
      → awaiting_workouts → awaiting_goal → completed

MealState (временный, per-message):
  idle → analyzing → awaiting_confirmation
       → (confirmed → saved) | (correction → idle)

ResetState:
  idle → awaiting_reset_choice → awaiting_confirm → done
```

Хранение FSM-состояния: aiogram `MemoryStorage` (dev) / можно заменить на Redis (prod).
Онбординг-стейт дублируется в БД (поле `onboarding_state`) для восстановления после рестарта.

---

## Known Limitations

- Оценки КБЖУ приблизительны, особенно для фото и сложных блюд
- Дата агрегата — UTC (timezone пользователя не учитывается)
- Нет валидации физиологических граней (например, вес 5 кг)
- Whisper может ошибаться на специфичных названиях блюд
- Проверка подписки не кэшируется — при высоком трафике возможны задержки
- FSM в MemoryStorage сбрасывается при перезапуске бота

---

## Explicit Out-of-Scope (MVP)

- Оплата и подписки
- Еженедельная / ежемесячная аналитика
- Экспорт (Google Sheets, CSV)
- Редактор ингредиентов
- Аллергии и предпочтения
- Интеграция с базами данных продуктов
- Таймзоны
- Напоминания
- Реферальная механика
- Веб-панель администратора
- Расширенная аналитика удержания
- A/B тесты онбординга
