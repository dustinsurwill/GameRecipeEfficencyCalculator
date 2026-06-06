# RecipeBook — Claude Code Handoff

Community recipe database for moddable games (Minecraft, Factorio, Satisfactory).
Core feature: chain analysis — find every multi-step processing path from item A to item B, ranked by yield per input.
Immediate use case: ATM10 (All The Mods 10) on MC 1.21.1.

Reference docs: [Database schema + tech stack + env vars](docs/schema.md) · [Architecture decisions + TODOs](docs/architecture.md)

---

## Project Structure

```
/
├── docker-compose.yml       # db + backend + frontend
├── backend/
│   ├── app/
│   │   ├── config.py        # pydantic-settings, reads .env
│   │   ├── db.py            # async engine, get_session, TimestampMixin
│   │   ├── main.py          # FastAPI app, CORS, router mounts
│   │   ├── auth/            # password.py, tokens.py, refresh.py, oauth.py
│   │   ├── models/          # __init__.py imports ALL models in FK order (required by Alembic)
│   │   │   └── user, versioning, identity, recipe, status, admin
│   │   ├── importer/
│   │   │   └── pipeline.py  # KubeJS export ingestion CLI
│   │   └── api/v1/routes/
│   │       ├── auth.py      # register, login, refresh, logout, OAuth
│   │       ├── users.py     # /me, OAuth unlink
│   │       ├── admin.py     # member mgmt, version publish/deprecate
│   │       ├── catalog.py   # Game/Mod/ModVersion/ModPack CRUD
│   │       ├── chains.py    # GET /chains — DFS traversal, yield ranking
│   │       └── recipes.py   # get, submit, review, retract, pending list
│   ├── alembic/             # env.py + versions/
│   ├── tests/test_auth.py   # 6 tests (register, login, profile, unauthed)
│   └── atm10_ore_analyzer.py  # standalone CLI analyzer
└── frontend/
    ├── nginx.conf           # SPA fallback + /api proxy (lazy DNS via resolver 127.0.0.11)
    ├── src/
    │   ├── lib/             # types.ts, auth.ts (stores), api.ts (fetch wrapper), utils.ts
    │   └── routes/
    │       ├── +layout.ts   # ssr = false
    │       ├── +layout.svelte  # bootAuth on mount (401 on /auth/refresh = logged out, expected)
    │       ├── +page.svelte    # PUBLIC chain explorer (no login required)
    │       ├── login/, register/, profile/, oauth/[provider]/callback/
    │       └── admin/       # auth guard in admin/+layout.svelte
    └── vite.config.ts       # MUST have tailwindcss() plugin first, then sveltekit()
```

---

## Running Locally

```bash
# Backend
cd backend
cp .env.example .env        # set DATABASE_URL, JWT_SECRET, DEBUG=true
uv sync
uv run alembic upgrade head
uv run fastapi dev app/main.py   # http://localhost:8000/docs

uv run pytest tests/ -v     # SQLite in-memory, no Postgres needed
uv run alembic revision --autogenerate -m "describe change"

# Frontend
cd frontend
cp .env.production .env     # edit VITE_API_BASE to http://localhost:8000/api/v1
npm install
npm run dev                 # http://localhost:5173

# Full stack
docker compose up           # :8080 frontend, :8081 backend, :5432 db
```

> **uv note:** `alembic` and `uvicorn` are only on the uv venv PATH — always prefix with `uv run`.

---

## Chain Calculation: Worker Processes

The DFS in `chains.py` is CPU-bound. It is already wrapped in `asyncio.to_thread()` so it does not block the event loop during traversal. For true CPU parallelism across simultaneous requests, run multiple uvicorn workers:

```bash
# Production (Docker / gunicorn)
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker

# Or directly
uv run uvicorn app.main:app --workers 4
```

Rule of thumb: `workers = 2 × CPU_cores + 1`. For development a single worker is fine. Note that `fastapi dev` always runs 1 worker with hot-reload; use the gunicorn command for load testing.

---

## What to Work On Next

1. **Backend rate limiting** — `backend/app/api/v1/routes/chains.py`: add slowapi rate limit on `GET /chains` keyed by client IP (anonymous: ~20 req/min). Frontend already handles 429.

2. **Recipe submission UI** — authenticated users submit via `POST /api/v1/recipes` (already implemented).

3. **Email wiring** — fastapi-mail for `/auth/verify` and password reset. Mail config already in `.env.example`.

4. **OAuth state nonce** — server-side storage (DB or Redis) for CSRF protection. See [architecture.md](docs/architecture.md#known-issues-and-todos).

5. **tags_json → Postgres ARRAY** — Alembic migration + GIN index on `item_appearances`. See [architecture.md](docs/architecture.md#12-tags_json-as-text-temporary).