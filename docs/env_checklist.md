# Environment Variables Checklist

All variables are read via `pydantic-settings` from `.env` file or system environment.
Source of truth: `config.py`.

---

## Required — local and Railway both

| Variable | Example | What breaks if missing |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `7123456789:AAF...` | Bot fails to start: `ValidationError telegram_bot_token` |
| `DATABASE_URL` | `postgresql+asyncpg://calories:pass@localhost:5432/calories_bot` | `create_async_engine` fails at import time; app crashes before any handler runs |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://calories:pass@localhost:5432/calories_bot` | `alembic upgrade head` fails; migrations cannot be applied |

---

## Required — must be set correctly for real usage

| Variable | Example | Notes |
|---|---|---|
| `TELEGRAM_CHANNEL_ID` | `@healthy_normal` | If wrong/missing, all users get "Не вижу подписки". Default is `@healthy_normal`. |
| `OPENAI_API_KEY` | `sk-proj-...` | Required for all AI features (text, vision, STT). All meal logging fails if missing or invalid. |
| `TELEGRAM_ADMIN_IDS` | `123456789,987654321` | Comma-separated. If empty, `/admin` is silently unavailable for everyone. Not required for user-facing features. |

---

## Required for Railway / production

| Variable | Example | Notes |
|---|---|---|
| `WEBHOOK_URL` | `https://calories-bot.railway.app` | If set, bot runs in webhook mode (FastAPI). If empty, runs in polling mode. Must be publicly reachable HTTPS URL. |
| `APP_ENV` | `production` | If set to `production`: JSON logging enabled, SQLAlchemy echo disabled. Default is `development`. |

---

## Optional — have safe defaults

| Variable | Default | Notes |
|---|---|---|
| `TEXT_MODEL_PROVIDER` | `openai` | Currently only `openai` is supported |
| `TEXT_MODEL_NAME` | `gpt-4o-mini` | Any OpenAI chat model that supports structured JSON output |
| `VISION_MODEL_PROVIDER` | `openai` | Currently only `openai` is supported |
| `VISION_MODEL_NAME` | `gpt-4o` | Must support vision input |
| `STT_PROVIDER` | `openai` | Currently only `openai` is supported |
| `STT_MODEL_NAME` | `whisper-1` | OpenAI STT model |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `WEBHOOK_PATH` | `/webhook` | Path where Telegram sends updates |
| `APP_HOST` | `0.0.0.0` | Bind address for uvicorn |
| `APP_PORT` | `8000` | Port for uvicorn (Railway injects `PORT` — align if needed) |

---

## Local run vs Railway deploy

| Variable | Local run | Railway |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Required | Required (set in Railway Variables) |
| `DATABASE_URL` | Set in `.env` to local PG | Auto-injected by Railway PostgreSQL plugin |
| `DATABASE_URL_SYNC` | Set in `.env` | Must be set manually in Railway Variables (Railway only provides `DATABASE_URL`) |
| `OPENAI_API_KEY` | Set in `.env` | Required in Railway Variables |
| `TELEGRAM_CHANNEL_ID` | Set in `.env` | Required in Railway Variables |
| `TELEGRAM_ADMIN_IDS` | Optional | Optional, set in Railway Variables |
| `WEBHOOK_URL` | Leave empty (polling) | Set to your Railway public URL |
| `APP_ENV` | `development` (default) | Set to `production` |

---

## Important: `DATABASE_URL_SYNC` on Railway

Railway's PostgreSQL plugin provides `DATABASE_URL` in the format:
```
postgresql://user:pass@host:port/dbname
```

You need to create `DATABASE_URL_SYNC` manually by:
1. Copying `DATABASE_URL` value
2. Replacing the scheme: `postgresql://` → `postgresql+psycopg2://`

Example:
```
DATABASE_URL=postgresql://calories:secret@db.railway.internal:5432/railway
DATABASE_URL_SYNC=postgresql+psycopg2://calories:secret@db.railway.internal:5432/railway
```

`DATABASE_URL` also needs the `asyncpg` driver for the bot:
```
DATABASE_URL=postgresql+asyncpg://calories:secret@db.railway.internal:5432/railway
```

Railway's variable may not include the driver prefix — add `+asyncpg` to the scheme.

---

## `.env.example` status

The `.env.example` file is **complete** — it documents all variables with sample values and comments. Use it as the template for both local and production configuration.

---

## Channel admin requirement

`TELEGRAM_CHANNEL_ID` alone is not enough. The bot account must be added as an **admin** (or at minimum a member) in the configured channel. Otherwise `getChatMember` returns a Telegram error and all users are blocked.

Steps:
1. Open the channel in Telegram
2. Go to Channel Settings → Administrators
3. Add your bot as an administrator (read access is sufficient)
