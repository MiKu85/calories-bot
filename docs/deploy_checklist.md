# Deployment Checklist — Railway

---

## Required services on Railway

| Service | How to add |
|---|---|
| Bot (Python app) | Add service from GitHub repository |
| PostgreSQL | Add PostgreSQL plugin — Railway injects `DATABASE_URL` automatically |

No Redis, no Celery, no separate workers. The feedback scheduler runs as an asyncio background task in the same process.

---

## Required environment variables (set in Railway → Variables)

| Variable | Value | Notes |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `7123456789:AAF...` | From @BotFather |
| `OPENAI_API_KEY` | `sk-proj-...` | OpenAI platform key |
| `DATABASE_URL` | `postgresql+asyncpg://...` | Railway injects `DATABASE_URL` without driver prefix — **prepend `+asyncpg`** |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://...` | Copy `DATABASE_URL`, replace scheme with `postgresql+psycopg2://` |
| `TELEGRAM_CHANNEL_ID` | `@healthy_normal` | Channel users must subscribe to |
| `TELEGRAM_ADMIN_IDS` | `123456789` | Comma-separated. Must match your personal Telegram ID |
| `WEBHOOK_URL` | `https://<app>.railway.app` | Railway generates this URL — copy from Railway → Settings → Domains |
| `APP_ENV` | `production` | Enables JSON logs + disables SQLAlchemy echo |
| `LOG_LEVEL` | `INFO` | |

### Note on DATABASE_URL

Railway provides:
```
DATABASE_URL=postgresql://user:pass@host:port/dbname
```

You need **two** variables set manually:
```
DATABASE_URL=postgresql+asyncpg://user:pass@host:port/dbname
DATABASE_URL_SYNC=postgresql+psycopg2://user:pass@host:port/dbname
```

---

## First-time deploy — step by step

### Step 1: Prepare the repository
- All tests pass locally: `pytest tests/ -v`
- Alembic migration exists: `alembic/versions/0001_initial_schema.py` (already committed)
- `.env.example` is up to date

### Step 2: Create Railway project
1. Go to [railway.app](https://railway.app) → New Project
2. Add service from GitHub repository
3. Add PostgreSQL plugin

### Step 3: Configure environment variables
Set all variables from the table above in Railway → Variables tab.

**Critical:** `DATABASE_URL` must have `+asyncpg` driver prefix. `DATABASE_URL_SYNC` must have `+psycopg2`.

### Step 4: Set the bot webhook URL
- Railway generates a public domain automatically (e.g., `https://calories-bot-production.railway.app`)
- Set `WEBHOOK_URL` to this URL (without trailing slash)

### Step 5: Add the bot to the channel
- Open your Telegram channel (`TELEGRAM_CHANNEL_ID`)
- Go to Channel Settings → Administrators
- Add the bot as administrator

**This is required.** Without channel admin, all users will be blocked by the subscription check.

### Step 6: Deploy
Push code to GitHub or trigger a Railway deploy. Railway builds from `Dockerfile`.

The `Dockerfile CMD` runs:
```sh
alembic upgrade head && python main.py
```

This applies migrations on every startup (idempotent — skips already-applied ones) then starts the bot.

### Step 7: Verify

After deploy succeeds:

1. Check health endpoint:
   ```
   GET https://<app>.railway.app/health
   → {"status": "ok"}
   ```

2. Check Railway logs for:
   ```
   webhook_set url=https://<app>.railway.app/webhook
   ```

3. Send `/start` to the bot in Telegram — subscription gate should appear.

4. Subscribe to the channel and complete onboarding — target calculation should succeed.

5. Send a meal by text, voice, and photo.

6. Send `/admin` from admin account — stats should appear.

---

## Subsequent deploys

1. Push code to GitHub — Railway auto-deploys
2. Migrations run automatically on startup (`alembic upgrade head` in CMD)
3. No manual steps needed unless new env vars were added

---

## Rollback

1. In Railway: Deployments tab → select previous successful deployment → Redeploy
2. If a new Alembic migration was applied in the rolled-back release:
   ```bash
   # Via Railway Shell:
   alembic downgrade -1
   ```

---

## Common failure points

| Symptom | Cause | Fix |
|---|---|---|
| All users blocked by subscription check | Bot is not admin in channel | Add bot as admin to `TELEGRAM_CHANNEL_ID` |
| `table "users" does not exist` in logs | `DATABASE_URL_SYNC` not set, so migrations failed silently | Set correct `DATABASE_URL_SYNC` and redeploy |
| `AuthenticationError` from OpenAI | `OPENAI_API_KEY` missing or invalid | Set correct key in Railway Variables |
| `webhook_set` not in logs | `WEBHOOK_URL` is empty or set to wrong value | Check `WEBHOOK_URL` — must be public HTTPS URL of Railway service |
| `pydantic_settings validation error` | Required env var is missing | Check Railway Variables against env_checklist.md |
| Bot starts but crashes on first message | Missing `DATABASE_URL` driver prefix | Ensure URL starts with `postgresql+asyncpg://` |
| `/admin` returns nothing | Telegram ID not in `TELEGRAM_ADMIN_IDS` | Add your ID (comma-separated), redeploy |

---

## Architecture notes (for operations)

- **Webhook mode** is used in production (`WEBHOOK_URL` set). FastAPI listens on `POST /webhook`. No webhook management needed — bot sets it on startup.
- **Polling mode** is used locally when `WEBHOOK_URL` is empty. Do not run polling and webhook simultaneously with the same token.
- **Feedback scheduler** runs as `asyncio.create_task` in the same process. No external scheduler needed. Runs every 6 hours starting 30 seconds after startup.
- **DB sessions**: each handler gets its own session via `DbSessionMiddleware`. Auto-committed on success, rolled back on exception.
- **AI retries**: text/vision/STT providers retry 3 times with exponential backoff (2–10s). On permanent failure, user is asked to retry or describe by text.
- **Photo/voice bytes**: never stored to disk or object storage. Processed in-memory and released immediately after analysis.
