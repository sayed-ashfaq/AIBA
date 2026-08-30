# Running AIBA locally

Backend (FastAPI + the deep-agents orchestrator) + frontend (Vite/React) + your local Postgres.

## Prerequisites

- Local Postgres running, with `dvdrental` and/or `Adventureworks` restored (see `postgres_commands.md` if you need to reload one).
- `backend/.env` filled in — needs at minimum `GROQ_API_KEY`, `METADATA_DATABASE_URL`, `CREDENTIALS_ENCRYPTION_KEY`, `SESSION_SECRET_KEY`. The last one only signs the Google-OAuth-state cookie, but the app won't boot without *some* value in it even if you never touch Google login.

## 1. One-time: create and migrate the metadata database

This is AIBA's own store (users, chats, saved connections) — separate from any business database you point the agent at.

```bash
psql -h localhost -p 5432 -U postgres -c "CREATE DATABASE aiba_metadata;"
cd backend
uv run alembic upgrade head
```

Only needed once per machine. If you ever add a new migration, re-run `alembic upgrade head`.

## 2. Start the backend

```bash
cd backend
uv run python main.py
```

Runs on `http://localhost:8010` with auto-reload — code edits restart it for you, no need to kill/relaunch manually.

## 3. Start the frontend

```bash
cd frontend
npm install   # first time only or if not installed
npm run dev
```

Runs on `http://localhost:5173` and already points at `localhost:8010` by default (see `src/api/client.js` — override with `VITE_API_URL` if you ever run the backend elsewhere).

## 4. First-time account + connection

Open `http://localhost:5173`:

1. Sign up (email/password — Google login needs OAuth credentials in `.env`, not set up here).
2. Add a connection (the connection bar) — either paste a full URL or fill in host/port/user/password/dbname. For the local sample databases:
   - `postgresql://postgres:12345@localhost:5432/dvdrental`
   - `postgresql://postgres:12345@localhost:5432/Adventureworks`
3. Activate it, then ask a question in the chat.



### Skipping the UI (curl)

Useful for quick checks without touching the frontend:

```bash
# sign up + save the session cookie
curl -c cookies.txt -X POST http://localhost:8010/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.dev","password":"somepassword123"}'

# save + activate a connection
curl -b cookies.txt -X POST http://localhost:8010/connections \
  -H "Content-Type: application/json" \
  -d '{"name":"dvdrental","db_type":"postgres","url":"postgresql://postgres:12345@localhost:5432/dvdrental"}'

# ask something
curl -b cookies.txt -X POST http://localhost:8010/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"How many films do we have?"}'
```

## Troubleshooting

- **`ValidationError: session_secret_key Field required`** — `.env` is missing `SESSION_SECRET_KEY`. Any random string works locally: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
- **App can't reach `aiba_metadata`** — you skipped step 1, or Postgres isn't running.
- **`/chat` says no active connection** — nothing's activated yet for the signed-in user; add/activate one first (step 4).
- **Backend won't pick up a code change** — only happens if it was started without reload (e.g. `uvicorn main:app` directly instead of `python main.py`). Restart it.