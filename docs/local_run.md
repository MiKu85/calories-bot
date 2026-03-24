# Local Run Guide

Complete instructions for running the bot locally from scratch (polling mode).

---

## Prerequisites

- Python 3.12
- Docker Desktop (for PostgreSQL)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- An OpenAI API key with access to `gpt-4o-mini`, `gpt-4o`, and `whisper-1`

---

## Step 1 — Clone and create `.env`

```bash
git clone <repo-url>
cd calories-bot
cp .env.example .env
```

Edit `.env` and fill in at minimum:

```
TELEGRAM_BOT_TOKEN=<your bot token>
OPENAI_API_KEY=<your openai key>
DATABASE_URL=postgresql+asyncpg://calories:calories@localhost:5432/calories_bot
DATABASE_URL_SYNC=postgresql+psycopg2://calories:calories@localhost:5432/calories_bot
TELEGRAM_CHANNEL_ID=@healthy_normal
```

Leave `WEBHOOK_URL` empty — this keeps the bot in polling mode.

---

## Step 2 — Start PostgreSQL

```bash
docker compose up db -d
```

Verify it's healthy:

```bash
docker compose ps
# db should show "healthy"
```

---

## Step 3 — Create Python virtual environment and install dependencies

```bash
python3.12 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Step 4 — Apply migrations

```bash
alembic upgrade head
```

Expected output ends with:
```
Running upgrade  -> a1b2c3d4e5f6, initial schema
```

If you see `No migrations to run` but the DB is empty, something is wrong — check that `DATABASE_URL_SYNC` is set correctly.

---

## Step 5 — Run the bot

```bash
python main.py
```

Expected log output (dev mode):
```
[info  ] bot_starting                    mode=polling
```

The bot is now running. Open Telegram and send `/start` to your bot.

---

## Step 6 — Run tests

Tests require a separate test database. Set `TEST_DATABASE_URL` before running:

```bash
# Create the test DB first (one time):
docker exec -it <db-container-name> psql -U calories -c "CREATE DATABASE calories_bot_test;"

# Then run:
export TEST_DATABASE_URL=postgresql+asyncpg://calories:calories@localhost:5432/calories_bot_test
pytest tests/ -v
```

The test suite uses `Base.metadata.create_all()` directly — you do **not** need to run `alembic upgrade head` on the test DB.

---

## All-in-one: Docker Compose (bot + db together)

```bash
cp .env.example .env
# Fill in TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, TELEGRAM_CHANNEL_ID in .env

docker compose up --build
```

The `bot` service automatically runs `alembic upgrade head` before starting (via the Dockerfile `CMD`).

Note: when using `docker compose up`, the `DATABASE_URL` and `DATABASE_URL_SYNC` are overridden by the values in `docker-compose.yml` to point to the `db` service.

---

## Stopping

```bash
# Stop the bot (Ctrl+C in terminal if running directly)
# Stop Docker services:
docker compose down

# To also delete the database volume:
docker compose down -v
```

---

## Common startup errors

### `asyncpg.exceptions.InvalidCatalogNameError: database "calories_bot" does not exist`
The database doesn't exist yet. Start PostgreSQL via `docker compose up db -d` — it creates the database automatically from `POSTGRES_DB=calories_bot`.

### `sqlalchemy.exc.ProgrammingError: table "users" does not exist`
Migrations haven't been applied. Run `alembic upgrade head`.

### `pydantic_settings.env_settings.EnvSettingsError: ... telegram_bot_token`
`TELEGRAM_BOT_TOKEN` is missing from `.env`. Check your `.env` file exists and is populated.

### `openai.AuthenticationError: Incorrect API key`
`OPENAI_API_KEY` is invalid or not set.

### Bot responds but subscription check fails for everyone
The bot is not an admin in the configured Telegram channel (`TELEGRAM_CHANNEL_ID`). Add the bot as an admin in the channel, or use a channel you control.
