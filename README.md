# Calories Bot

Telegram-бот нутрициолог на русском языке. Оценивает КБЖУ из фото, голосовых сообщений и текстовых описаний. Ведёт дневной баланс калорий и макросов.

## Стек

- Python 3.12
- aiogram 3 (Telegram bot framework)
- FastAPI + uvicorn (webhook / health endpoint)
- PostgreSQL 16
- SQLAlchemy 2 (async) + Alembic
- OpenAI API (GPT-4o-mini, GPT-4o vision, Whisper STT)
- Docker / Railway

---

## Локальный запуск (polling mode)

### 1. Клонировать репозиторий и создать `.env`

```bash
cp .env.example .env
# Отредактировать .env: вставить TELEGRAM_BOT_TOKEN, OPENAI_API_KEY и DATABASE_URL
```

### 2. Запустить PostgreSQL через Docker

```bash
docker compose up db -d
```

### 3. Установить зависимости

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Применить миграции

```bash
alembic upgrade head
```

### 5. Запустить бота

```bash
python main.py
```

Бот запустится в режиме polling. Логи — в stdout.

---

## Запуск через Docker Compose (всё вместе)

```bash
cp .env.example .env
# Заполнить .env

docker compose up --build
```

Сервис `bot` ждёт готовности `db` через healthcheck и запускает Alembic-миграции автоматически.

> Примечание: для автоматических миграций при старте контейнера добавить в `CMD`:
> `alembic upgrade head && python main.py`

---

## Деплой на Railway

### Требования
- Аккаунт Railway с подключённым GitHub
- Плагин PostgreSQL в проекте Railway

### Шаги

1. Создать новый проект на [railway.app](https://railway.app)
2. Добавить сервис из GitHub-репозитория
3. Добавить плагин **PostgreSQL** — Railway автоматически пробросит `DATABASE_URL`
4. В переменных окружения сервиса указать:

```
TELEGRAM_BOT_TOKEN=...
OPENAI_API_KEY=...
TELEGRAM_CHANNEL_ID=@healthy_normal
TELEGRAM_ADMIN_IDS=123456789
DATABASE_URL=<автоматически из Railway PostgreSQL>
DATABASE_URL_SYNC=postgresql+psycopg2://...  # заменить asyncpg на psycopg2 в строке
WEBHOOK_URL=https://<your-app>.railway.app
APP_ENV=production
LOG_LEVEL=INFO
```

5. Railway соберёт образ из `Dockerfile` и задеплоит
6. После деплоя: `alembic upgrade head` — выполнить один раз через Railway Shell или добавить в `Dockerfile CMD`

### Webhook vs Polling

| Режим | Когда | Как |
|---|---|---|
| Polling | `WEBHOOK_URL` не задан | `python main.py` |
| Webhook | `WEBHOOK_URL` задан | FastAPI слушает `POST /webhook` |

---

## Переменные окружения

Смотри [.env.example](.env.example) — все переменные с описанием.

---

## Структура проекта

```
calories-bot/
├── bot/
│   ├── ai/              # AI-провайдеры (text, vision, STT) + абстракции
│   ├── db/              # SQLAlchemy модели + сессия
│   ├── handlers/        # aiogram роутеры (по фичам)
│   ├── keyboards/       # Telegram кнопки
│   ├── middleware/      # DB session + user inject
│   ├── services/        # Бизнес-логика
│   └── utils/           # Логирование и утилиты
├── tests/               # Pytest тесты
├── docs/
│   ├── product_spec.md  # Полный спек MVP
│   ├── mvp_scope.md     # Что в MVP и что нет
│   └── future_improvements.md
├── alembic/             # Миграции БД
├── config.py            # Pydantic Settings
├── main.py              # Entrypoint (polling / webhook)
├── Dockerfile
└── docker-compose.yml
```

---

## Миграции

```bash
# Создать новую миграцию (после изменения моделей)
alembic revision --autogenerate -m "описание"

# Применить все миграции
alembic upgrade head

# Откатить последнюю
alembic downgrade -1
```

---

## Тесты

```bash
# Запустить тесты (нужен PostgreSQL с TEST_DATABASE_URL)
export TEST_DATABASE_URL=postgresql+asyncpg://calories:calories@localhost:5432/calories_bot_test
pytest tests/ -v
```

---

## Troubleshooting

### `asyncpg.exceptions.InvalidPasswordError` при запуске тестов
Проверь, что `TEST_DATABASE_URL` указывает на реальную PostgreSQL БД с правильными кредами.
По умолчанию: `postgresql+asyncpg://calories:calories@localhost:5432/calories_bot_test`.

### `alembic.util.exc.CommandError: Can't locate revision` после `git pull`
Кто-то добавил новую миграцию. Выполни `alembic upgrade head`.

### Бот не реагирует на сообщения (polling mode)
1. Убедись, что `TELEGRAM_BOT_TOKEN` корректный (`/getMe` в браузере через Bot API).
2. Проверь, что нет другого запущенного экземпляра бота с тем же токеном.
3. Посмотри логи: должна быть строка `bot_starting mode=polling`.

### Webhook не получает обновления (production)
1. Проверь, что `WEBHOOK_URL` доступен извне (Railway даёт публичный домен автоматически).
2. `GET /health` должен возвращать `{"status": "ok"}`.
3. Убедись, что `APP_ENV=production` и `WEBHOOK_URL` выставлены — иначе бот стартует в polling.

### `ValueError: Cannot calculate targets: profile is incomplete`
`apply_targets` вызван до завершения онбординга. Все поля профиля (sex, age, height, weight, activity_level, goal) должны быть заполнены.

### OpenAI API ошибки (429 / 503)
Провайдеры используют tenacity: 3 попытки с exponential backoff. Если все 3 падают — пользователь видит запрос на уточнение. Проверь лимиты OpenAI-аккаунта.

### Обратная связь не отправляется через 7 дней
Scheduler проверяет раз в 6 часов. Убедись, что `first_meal_at` записан (первый приём пищи сохранён) и `feedback_sent_at` равен `NULL`.

---

## Документация

- [docs/product_spec.md](docs/product_spec.md) — полный продуктовый спек, флоу, бизнес-правила
- [docs/mvp_scope.md](docs/mvp_scope.md) — что в MVP, ограничения, дисклеймеры
- [docs/future_improvements.md](docs/future_improvements.md) — идеи после MVP
- [docs/local_run.md](docs/local_run.md) — детальная инструкция по запуску локально
- [docs/env_checklist.md](docs/env_checklist.md) — все переменные окружения с описанием
- [docs/deploy_checklist.md](docs/deploy_checklist.md) — шаги деплоя на Railway
- [docs/smoke_test_checklist.md](docs/smoke_test_checklist.md) — ручной план тестирования перед запуском
- [docs/gap_report.md](docs/gap_report.md) — аудит готовности MVP
