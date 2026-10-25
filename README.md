# RecipeBook

A community-maintained web platform for looking up and comparing crafting recipes
across moddable games (Minecraft, Factorio, Satisfactory). Enter any item as a
starting point, pick a target, and it finds every multi-step processing chain ranked
by yield per input — accounting for modpack recipe overrides and machine upgrades.

Built for ATM10 (All The Mods 10), generalizes to any game with a KubeJS recipe export.

---

## Quick Start (Docker)

```bash
git clone <repo-url>
cd GameRecipeEfficencyCalculator

# Configure backend secrets
cp backend/.env.example backend/.env
# Edit backend/.env — set JWT_SECRET at minimum:
#   JWT_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")

export JWT_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")
docker compose up --build
```

- Website + API: http://localhost:8080 (API docs at `/docs` only when `DEBUG=true`)
- Database: localhost:5432 (user/pass: `recipebook`)

The container runs `alembic upgrade head` on startup, as a non-root user, capped at 2 GB.
Then import data (below) and open `/minecraft/atm10/latest`.

---

## Local Development

**Backend** (Python 3.13+, [uv](https://docs.astral.sh/uv/) recommended):

```bash
cd backend
cp .env.example .env      # fill in DATABASE_URL, JWT_SECRET; set DEBUG=true
uv sync
alembic upgrade head
uv run uvicorn app.main:app --reload
# → http://localhost:8000   (API docs: /docs when DEBUG=true)
```

The website is server-rendered (Jinja2 + HTMX) by the same process — there is no frontend build
and no Node. Edit templates in `backend/app/web/templates/` and refresh.

**Tests** (no Postgres needed — uses in-memory SQLite):

```bash
cd backend
uv run pytest tests/ -v
```

---

## Importing Recipe Data

The import pipeline reads a KubeJS debug export folder and populates the database:

```bash
cd backend
DEBUG=false uv run python -m app.importer \
  --export-dir ../allthemons_export \
  --game minecraft \
  --game-version 1.21.1 \
  --modpack atm10 \
  --modpack-version 0.18.0
```

The import is idempotent — running it again on the same export is safe.

---

## Project Structure

```
/
├── docker-compose.yml
├── backend/
│   ├── app/
│   │   ├── api/v1/routes/   # JSON API: auth, users, admin, catalog, chains, discover, recipes
│   │   ├── web/             # server-rendered site: routes, Jinja2 templates, static css/js
│   │   ├── discovery/       # scope (game+pack+version), in-memory index, shared queries
│   │   ├── auth/            # JWT, Argon2 passwords, OAuth (GitHub/Google/Authentik)
│   │   ├── models/          # SQLModel ORM + Pydantic schemas
│   │   └── importer/        # KubeJS export ingestion
│   ├── alembic/             # database migrations
│   └── tests/
└── docs/
```

---

## Tech Stack

| | |
|---|---|
| Framework | FastAPI + SQLModel (SQLAlchemy 2 + Pydantic 2) |
| Website | Jinja2 templates + HTMX 2 (vendored), hand-written CSS — no Node, no build step |
| Database | PostgreSQL 18 via asyncpg; SQLite for tests |
| Auth | Cookie session (DB-backed, revocable) for the site; Bearer JWT for the JSON API; Argon2; OAuth via authlib |
| Migrations | Alembic (async) |

---

## Environment Variables

Copy `backend/.env.example` to `backend/.env`. Required variables:

| Variable | Description |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host/db` |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://user:pass@host/db` (Alembic only) |
| `JWT_SECRET` | Random hex string (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `DEBUG` | `true` in development (enables /docs, allows HTTP cookies and the placeholder secret). The app refuses to start with `DEBUG=false` and a default `JWT_SECRET`. |

OAuth providers (all optional — leave blank to disable):

| Variable | |
|---|---|
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub OAuth app |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth app |
| `AUTHENTIK_CLIENT_ID` / `AUTHENTIK_CLIENT_SECRET` / `AUTHENTIK_BASE_URL` | Authentik |

OAuth callback URL to register: `https://<your-domain>/api/v1/auth/oauth/<provider>/callback`. Sign-in starts at `/auth/oauth/<provider>`; a `state` nonce cookie is verified on return.

---

## Getting Admin Access (Local Dev)

After registering an account, promote it to superuser in the database:

```bash
# With Docker (Postgres running via docker compose up):
docker exec -it recipebook-db-1 psql -U recipebook -d recipebook \
  -c "UPDATE users SET is_superuser = true WHERE email = 'you@example.com';"

# Without Docker (uv + DATABASE_URL from backend/.env):
cd backend
uv run python - <<'EOF'
import asyncio
from sqlmodel import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.config import settings
from app.models.user import User

async def main():
    engine = create_async_engine(settings.database_url)
    async with AsyncSession(engine) as s:
        result = await s.exec(select(User).where(User.email == "you@example.com"))
        u = result.one()
        u.is_superuser = True
        s.add(u)
        await s.commit()

asyncio.run(main())
EOF
```

Reload — the site reads roles from the database on every request, so no re-login is needed. The Admin page lists the modpacks and mods you manage (everything, for superusers).

---

## Contributing

Submit recipes through the web UI — they go into a pending review queue where
curators can approve or reject with a reason. Approved recipes become visible
in chain queries immediately.

For code contributions, open a pull request. The backend test suite (`uv run pytest`)
must stay green. For new API endpoints, follow the patterns in
`backend/app/api/v1/routes/`.
