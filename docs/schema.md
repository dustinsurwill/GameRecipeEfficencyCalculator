# Database Schema (20 tables)

## Auth layer

- **users** — the person. email nullable (OAuth-only users may have none).
  Fields: id (UUID PK), display_name, email, email_verified, hashed_password (nullable), is_active, is_superuser, is_curator, bio, avatar_url.

- **oauth_accounts** — one row per OAuth credential per user. provider_user_id is the immutable ID from the provider (not email — emails change).
  Fields: id, user_id (FK), provider, provider_user_id, provider_email, access_token, refresh_token, token_expires_at, last_used_at.

- **refresh_tokens** — persisted JTI claims for refresh token revocation.
  Fields: jti (PK, string), user_id (FK), expires_at, user_agent, ip_address.

## Versioning layer

- **games** — Minecraft, Factorio, Satisfactory. recipe_metric: "discrete" or "throughput".
- **game_versions** — MC 1.20.1, MC 1.21.1, etc.
- **mods** — Mekanism, Create, IE. Tied to a game. namespace = item ID prefix ("mekanism", etc).
- **mod_versions** — Mekanism 10.7.18.84 for MC 1.21.1. status: draft | published | deprecated | archived.
- **modpacks** — ATM10, SkyFactory 5. Tied to a game_version. status: draft | published | deprecated | archived.
- **modpack_mod_versions** — junction: which mod versions are in a modpack. A recipe is "active" in a modpack if ALL its required mod_versions are members.

## Identity layer

- **item_identities** — "Gold Dust" as a concept, independent of which mod adds it.
- **item_appearances** — "mekanism:dust_gold in mekanism@10.7.18" — one row per (item_id, mod_version). tags_json stores tag memberships (TEXT; migrate to TEXT[] + GIN index for production).
- **machine_identities** — "Enrichment Chamber" as a concept. recipe_types_json lists recipe type strings.
- **machine_upgrade_items** — grinding balls, speed upgrades. max_slots = how many can be inserted.
- **machine_upgrade_modifiers** — one modifier per upgrade per stat. modifier_type: YIELD_MAIN | YIELD_BONUS | SPEED | ENERGY_USE.

## Recipe layer

- **recipes** — one row per import_key (file path, UNIQUE — idempotency key). source_priority: 0=game, 1=mod, 2=modpack, 3=community. status: draft | pending_review | approved | rejected. modpack_id nullable.
- **recipe_slots** — inputs/outputs/aux_inputs. Either tag_string or item_appearance_id set. quantity is probability-weighted. slot_type: "input" | "output" | "aux_input".
- **recipe_mod_version_links** — which mod versions a recipe requires. Composite PK (recipe_id, mod_version_id).
- **recipe_overrides** — base_recipe_id → override_recipe_id with modpack_id and notes.
- **recipe_reviews** — append-only audit log. Fields: recipe_id, reviewer_id, decision, reason, previous_status.

## Admin layer

- **admin_groups** — scoped permissions. scope_type ("mod" | "modpack") + scope_id (soft polymorphic FK). role: "admin" | "maintainer". Unique: (user_id, scope_type, scope_id).

---

## Tech Stack

### Backend
| Concern | Library | Version | Notes |
|---|---|---|---|
| Web framework | FastAPI | ≥0.115 | with standard extras |
| ORM | SQLModel | ≥0.0.21 | SQLAlchemy 2.0 + Pydantic 2 |
| Async DB driver | asyncpg | ≥0.29 | PostgreSQL production |
| Sync DB driver | psycopg2-binary | ≥2.9 | Alembic migrations only |
| Migrations | Alembic | ≥1.13 | async env.py configured |
| Password hashing | pwdlib[argon2] | ≥0.2 | use verify_and_update(), not verify() |
| JWT | PyJWT | ≥2.9 | HS256, not python-jose |
| OAuth client | authlib | ≥1.3 | AsyncOAuth2Client |
| HTTP client | httpx | ≥0.27 | OAuth token exchange |
| Config | pydantic-settings | ≥2.3 | reads .env file |
| Email | fastapi-mail | ≥1.4 | not yet wired up |
| Test runner | pytest + pytest-asyncio | — | asyncio_mode=auto |
| Test DB | aiosqlite | — | in-memory SQLite for tests |
| Linter | ruff | ≥0.6 | |

### Frontend
| Concern | Library | Version | Notes |
|---|---|---|---|
| Framework | SvelteKit | 2.x | adapter-static, CSR-only |
| Language | TypeScript | 6.x | strict mode |
| Styling | Tailwind CSS | 4.x | CSS-first, `@tailwindcss/vite` plugin |
| UI components | bits-ui + shadcn-svelte | 2.x | Tailwind v4 CSS variables |
| Icons | @lucide/svelte | 1.x | |
| Build tool | Vite | 7.x | NOTE: pinned to 7.x — Vite 8 breaks @tailwindcss/vite in prod builds via rolldown |
| Web server | nginx | alpine | SPA fallback + /api proxy |

### Infrastructure
- PostgreSQL 18 (docker: `postgres:18-alpine`)
- Docker Compose: db + backend + frontend services
- nginx: per-request DNS via `resolver 127.0.0.11` + `set $backend` variable (required for Docker service discovery)
- uv: Python package manager — always prefix commands with `uv run` (e.g. `uv run alembic`, `uv run uvicorn`)

---

## Environment Variables

### Backend (`backend/.env.example`)

Required:
- `DATABASE_URL` — `postgresql+asyncpg://...`
- `DATABASE_URL_SYNC` — `postgresql+psycopg2://...` (Alembic only)
- `JWT_SECRET` — `python -c "import secrets; print(secrets.token_hex(32))"`

Optional (features disabled if blank):
- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
- `AUTHENTIK_CLIENT_ID` / `AUTHENTIK_CLIENT_SECRET` / `AUTHENTIK_BASE_URL`
- `MAIL_*` — email verification, password reset
- `FRONTEND_URL` — default `http://localhost:5173` (dev) / `http://localhost` (prod)
- `DEBUG` — `true` enables /docs and HTTP cookies

### Frontend
- `VITE_API_BASE=http://localhost:8000/api/v1` in `frontend/.env` for local dev
- Production value `/api/v1` committed in `frontend/.env.production`