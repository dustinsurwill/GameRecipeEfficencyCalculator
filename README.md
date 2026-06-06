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
#   DEBUG=true

docker compose up
```

- Frontend: http://localhost:8080
- API docs: http://localhost:8081/docs (only in DEBUG mode)
- Database: localhost:5432 (user/pass: `recipebook`)

The API container runs `alembic upgrade head` automatically on startup.

---

## Local Development

**Backend** (Python 3.13+, [uv](https://docs.astral.sh/uv/) recommended):

```bash
cd backend
cp .env.example .env      # fill in DATABASE_URL, JWT_SECRET; set DEBUG=true
uv sync
alembic upgrade head
uv run fastapi dev app/main.py
# → http://localhost:8000/docs
```

**Frontend** (Node 20+):

```bash
cd frontend
npm install
# frontend/.env already points at localhost:8000 for dev
npm run dev
# → http://localhost:5173
```

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
python -m app.importer \
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
│   │   ├── api/v1/routes/   # auth, users, admin, catalog, chains, recipes
│   │   ├── auth/            # JWT, Argon2 passwords, OAuth (GitHub/Google/Authentik)
│   │   ├── models/          # SQLModel ORM + Pydantic schemas (20 tables)
│   │   └── importer/        # KubeJS export ingestion
│   ├── alembic/             # database migrations
│   └── tests/
└── frontend/
    └── src/
        ├── lib/             # api.ts, auth.ts, types.ts, UI components
        └── routes/          # login, register, profile, admin/*, oauth callback
```

---

## Tech Stack

**Backend**
| | |
|---|---|
| Framework | FastAPI + SQLModel (SQLAlchemy 2 + Pydantic 2) |
| Database | PostgreSQL 18 via asyncpg; SQLite for tests |
| Auth | PyJWT (HS256), pwdlib Argon2, authlib OAuth |
| Migrations | Alembic (async) |

**Frontend**
| | |
|---|---|
| Framework | SvelteKit 2 (Svelte 5, CSR-only) |
| Styling | Tailwind CSS v4 + shadcn-svelte (bits-ui) |
| Language | TypeScript 6 |
| Served by | nginx (SPA fallback + /api proxy) |

---

## Environment Variables

Copy `backend/.env.example` to `backend/.env`. Required variables:

| Variable | Description |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host/db` |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://user:pass@host/db` (Alembic only) |
| `JWT_SECRET` | Random hex string (`python -c "import secrets; print(secrets.token_hex(32))"`) |
| `DEBUG` | Set `true` in development (enables /docs, HTTP cookies) |

OAuth providers (all optional — leave blank to disable):

| Variable | |
|---|---|
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | GitHub OAuth app |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth app |
| `AUTHENTIK_CLIENT_ID` / `AUTHENTIK_CLIENT_SECRET` / `AUTHENTIK_BASE_URL` | Authentik |

OAuth callback URL to register: `http://<your-domain>/api/v1/auth/oauth/<provider>/callback`

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

Log out and log back in — the refreshed JWT will include `superuser: true` and the Admin link will appear in the navbar.

---

## Contributing

Submit recipes through the web UI — they go into a pending review queue where
curators can approve or reject with a reason. Approved recipes become visible
in chain queries immediately.

For code contributions, open a pull request. The backend test suite (`uv run pytest`)
must stay green. For new API endpoints, follow the patterns in
`backend/app/api/v1/routes/`.
