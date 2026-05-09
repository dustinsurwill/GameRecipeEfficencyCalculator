# RecipeBook — Claude Code Handoff Document

## What We Are Building

A community-maintained web platform for looking up and comparing crafting recipes
across moddable games (Minecraft, Factorio, Satisfactory). The primary use case is
chain analysis: given any item, find every multi-step processing path to the target
item, ranked by yield per input.

The platform supports:
- Multiple games, each with multiple versions
- Mods and modpacks, each with their own versioned recipe sets
- Modpacks can override mod recipes (KubeJS, CraftTweaker, datapacks)
- Community recipe submission with a moderated approval workflow
- Draft/staging for new mod/modpack versions before they go public
- Chain traversal queries: start item/tag → target item/tag, generic across item types

The immediate real-world use case: ATM10 (All The Mods 10) modpack for Minecraft 1.21.1.
A working CLI analyzer (`backend/atm10_ore_analyzer.py`) parses KubeJS debug exports
and finds chains. The web platform ingests the same exports into a proper database.

---

## Project Structure

```
/
├── docker-compose.yml           # orchestrates db + api + frontend
├── CLAUDE.md
├── README.md
├── .gitignore
├── .dockerignore
│
├── backend/
│   ├── Dockerfile               # Python 3.13-slim, uv-based
│   ├── pyproject.toml           # uv/hatch build, all Python deps
│   ├── uv.lock
│   ├── alembic.ini
│   ├── .env.example
│   ├── atm10_ore_analyzer.py    # standalone CLI chain analyzer (used by importer)
│   ├── alembic/
│   │   ├── env.py               # async-compatible, imports app.models
│   │   └── versions/            # 9023b2d18d99_initial_schema.py (20 tables)
│   ├── app/
│   │   ├── config.py            # pydantic-settings, reads .env
│   │   ├── db.py                # async engine, get_session dep, TimestampMixin
│   │   ├── main.py              # FastAPI app, CORS, router mounts
│   │   ├── auth/
│   │   │   ├── password.py      # pwdlib Argon2 — verify_and_update()
│   │   │   ├── tokens.py        # JWT access+refresh, TokenData, dependencies
│   │   │   ├── refresh.py       # RefreshToken table, revocation helpers
│   │   │   └── oauth.py         # GitHub/Google/Authentik OAuth clients
│   │   ├── models/
│   │   │   ├── __init__.py      # CRITICAL: imports all table models in FK order
│   │   │   ├── user.py          # User, OAuthAccount + API schemas
│   │   │   ├── versioning.py    # Game, GameVersion, Mod, ModVersion, ModPack, ModPackModVersion
│   │   │   ├── identity.py      # ItemIdentity, ItemAppearance, MachineIdentity, upgrades
│   │   │   ├── recipe.py        # Recipe, RecipeSlot, RecipeModVersionLink, RecipeOverride
│   │   │   ├── status.py        # PublicationStatus, RecipeStatus enums + RecipeReview
│   │   │   └── admin.py         # AdminGroup, permission helpers, FastAPI deps
│   │   ├── importer/
│   │   │   └── pipeline.py      # KubeJS export ingestion CLI
│   │   └── api/v1/routes/
│   │       ├── auth.py          # register, login, refresh, logout, OAuth flows
│   │       ├── users.py         # /me profile, OAuth account unlinking
│   │       ├── admin.py         # member management, version publish/deprecate
│   │       ├── catalog.py       # Game/Mod/ModVersion/ModPack CRUD
│   │       ├── chains.py        # GET /chains — DFS traversal, yield ranking
│   │       └── recipes.py       # get, submit, review, retract, pending list
│   └── tests/
│       └── test_auth.py         # 6 passing tests (register, login, profile, unauthed)
│
└── frontend/
    ├── Dockerfile               # multi-stage: node build → nginx serve
    ├── nginx.conf               # SPA fallback + /api proxy to api:8000
    ├── .env.production          # VITE_API_BASE=/api/v1 (committed, no secrets)
    ├── package.json
    ├── vite.config.ts           # @tailwindcss/vite plugin
    ├── svelte.config.js         # adapter-static, fallback: 'index.html'
    └── src/
        ├── app.css              # Tailwind v4 + shadcn-svelte zinc theme variables
        ├── lib/
        │   ├── types.ts         # TypeScript interfaces for all API shapes
        │   ├── auth.ts          # accessToken/currentUser stores, bootAuth, logout
        │   ├── api.ts           # fetch wrapper: Bearer injection, 401→refresh→retry
        │   ├── utils.ts         # cn() helper, bits-ui type re-exports
        │   └── components/
        │       ├── NavBar.svelte
        │       └── ui/          # shadcn-svelte components (button, card, dialog, etc.)
        └── routes/
            ├── +layout.ts       # export const ssr = false
            ├── +layout.svelte   # bootAuth on mount, spinner until ready
            ├── +page.svelte     # redirect to /admin or /login
            ├── login/           # email+password form
            ├── register/        # display_name + email + password
            ├── profile/         # edit profile, unlink OAuth accounts
            ├── oauth/[provider]/callback/  # reads token from URL hash
            └── admin/
                ├── +layout.svelte    # auth guard
                ├── +page.svelte      # dashboard: modpacks + pending count
                ├── mods/[id]/        # versions table + members management
                ├── modpacks/[id]/    # publish/deprecate + members
                └── recipes/          # paginated queue with approve/reject dialog
```

---

## Database Schema (20 tables)

### Auth layer
- **users** — the person. email nullable (OAuth-only users may have none).
  Fields: id (UUID PK), display_name, email, email_verified, hashed_password
  (nullable), is_active, is_superuser, is_curator, bio, avatar_url.

- **oauth_accounts** — one row per OAuth credential per user. Multiple rows
  can point to the same user_id. provider_user_id is the immutable ID from
  the provider (not email — emails change).
  Fields: id, user_id (FK), provider, provider_user_id, provider_email,
  access_token, refresh_token, token_expires_at, last_used_at.

- **refresh_tokens** — persisted JTI claims for refresh token revocation.
  Fields: jti (PK, string), user_id (FK), expires_at, user_agent, ip_address.

### Versioning layer
- **games** — Minecraft, Factorio, Satisfactory.
  recipe_metric: "discrete" (no time cost) or "throughput" (per-second rates).

- **game_versions** — MC 1.20.1, MC 1.21.1, etc.

- **mods** — Mekanism, Create, Immersive Engineering. Tied to a game.
  namespace field = the item ID prefix ("mekanism", "create", etc).

- **mod_versions** — Mekanism 10.7.18.84 for MC 1.21.1.
  status: draft | published | deprecated | archived (default: draft).

- **modpacks** — ATM10, SkyFactory 5. Tied to a game_version.
  status: draft | published | deprecated | archived (default: draft).

- **modpack_mod_versions** — junction: which mod versions are in a modpack.
  A recipe is "active" in a modpack if ALL its required mod_versions are
  members of that modpack.

### Identity layer
- **item_identities** — "Gold Dust" as a concept, independent of which mod
  adds it. Multiple item IDs across mods resolve to one identity.

- **item_appearances** — "mekanism:dust_gold in mekanism@10.7.18" — one row
  per (item_id, mod_version) pair. tags_json stores tag memberships.

- **machine_identities** — "Enrichment Chamber" as a concept. recipe_types_json
  lists all recipe type strings that map to this machine.

- **machine_upgrade_items** — grinding balls, speed upgrades, etc. Linked to
  a machine. max_slots = how many can be inserted simultaneously.

- **machine_upgrade_modifiers** — one modifier per upgrade per stat type.
  modifier_type: YIELD_MAIN | YIELD_BONUS | SPEED | ENERGY_USE.

### Recipe layer
- **recipes** — one recipe per import_key (file path from export, unique).
  source_priority: 0=game, 1=mod, 2=modpack, 3=community.
  status: draft | pending_review | approved | rejected (default: draft).
  modpack_id nullable (null = from a mod directly).
  contributor_id nullable (set for community submissions).

- **recipe_slots** — inputs, outputs, aux_inputs (chemicals/fluids).
  Either tag_string or item_appearance_id must be set. quantity is already
  probability-weighted (chance outputs are pre-multiplied at import).
  slot_type: "input" | "output" | "aux_input".

- **recipe_mod_version_links** — which mod versions a recipe requires.
  Composite PK (recipe_id, mod_version_id).

- **recipe_overrides** — explicit record of modpack recipe replacements.
  base_recipe_id → override_recipe_id, with modpack_id and notes.

- **recipe_reviews** — append-only audit log of approve/reject decisions.
  Fields: recipe_id, reviewer_id, decision, reason, previous_status.

### Admin layer
- **admin_groups** — scoped permissions. scope_type + scope_id is a soft
  polymorphic FK (scope_type="mod" → scope_id is a mods.id, etc).
  role: "admin" | "maintainer".
  Unique constraint on (user_id, scope_type, scope_id).

---

## Architecture Decisions and Reasoning

### 1. SQLModel over raw SQLAlchemy + Pydantic
One class definition gives you the ORM model AND the Pydantic validation
schema. For a schema with 20 tables and 4 API schema variants per table
(Read, Create, Update, internal), this eliminates ~200 lines of duplication.
The tradeoff: SQLModel is in maintenance mode (as of late 2025). The
successor is unannounced. Decision: use it anyway — it works, the API is
stable, and migration to pure SQLAlchemy 2.0 later is mechanical.

### 2. fastapi-users was rejected
Also in maintenance mode, AND fights the identity model we need:
- fastapi-users assumes email is the primary identity key
- We have nullable email (OAuth-only users may have none)
- We explicitly do NOT want email-match auto-linking between OAuth accounts
Auth is built from primitives: pwdlib (Argon2), PyJWT, authlib (OAuth client).
~400 lines total — less than fastapi-users adds for things we don't need.

### 3. User identity is separated from credentials
User = the person (UUID PK, display_name, optional email).
OAuthAccount = one credential linked to a person (many per user).
This handles "I have 6 Google accounts" correctly — each is a separate
OAuthAccount row pointing to the same User row.
Account linking ONLY happens explicitly: authenticated user POSTs to
/auth/oauth/{provider}/connect. No silent email-match linking.
Rationale: email-match linking is a security footgun (account hijacking).

### 4. JWT carries site-level roles only, NOT scoped admin roles
JWT claims: sub (user UUID), type, iat, exp, jti, curator (bool), superuser (bool).
Scoped roles (mod_admin, modpack_admin) are checked against the DB on every
write operation. This means permission changes take effect immediately —
no "I removed someone as admin but their JWT hasn't expired yet" problem.
The DB lookup is one indexed query; only happens on writes, not reads.

### 5. Refresh tokens are persisted for revocation
RefreshToken table stores JTI (JWT ID) per active session.
On logout: delete the row. On password change: delete ALL rows for user.
On refresh: token rotation — delete old JTI, insert new one, issue new pair.
Refresh token sent as HttpOnly cookie to /auth/refresh path only.
Access token sent as Bearer header (stateless, no DB lookup to verify).

### 6. .scalars().one_or_none() not .one_or_none()
Critical SQLModel quirk: session.exec(select(Model)...).one_or_none() returns
a Row tuple (Model,) instead of a Model instance when running through
TestClient's anyio bridge. Always use .scalars().one_or_none() to force
scalar unwrapping. This affects every query that returns a single ORM object.

### 7. Relationship lazy="noload" everywhere
All SQLModel Relationships use sa_relationship_kwargs={"lazy": "noload"}.
Async SQLAlchemy cannot lazy-load relationships — accessing them outside
an async context raises MissingGreenlet. Instead, use explicit
selectinload() or joinedload() in the query options when you need related
objects. This is verbose but prevents subtle async bugs.

### 8. Draft/staging with status fields
ModVersion.status and ModPack.status default to "draft".
Only "published" versions are visible in public API responses.
Draft versions are visible to: scope admins (mod/modpack), site superusers.
Rationale: mod maintainers need to import and test a new version before
making it public. Modpack curators need to stage a new pack version.

### 9. Recipe status state machine
draft → pending_review (contributor submits)
pending_review → approved (admin/curator approves)
pending_review → rejected (admin/curator rejects, reason required)
approved → draft (admin retracts for correction)
rejected → draft (contributor revises and can resubmit)
GET /recipes/{id} returns 404 (not 403) for drafts the caller can't see.
Rationale: 403 leaks that a draft exists; 404 does not.

### 10. RecipeReview is append-only
Every approve/reject decision creates a new RecipeReview row.
The recipe's current status is the most recent review's decision.
This gives full review history for free — important for community trust
(contributors can see exactly who rejected their recipe and why).

### 11. AdminGroup uses soft polymorphic FK
scope_type ("mod" | "modpack") + scope_id (UUID) rather than separate
ModAdminGroup and ModPackAdminGroup tables. The permission logic is
identical for both and this keeps helpers DRY. Trade-off: no DB-level
FK constraint on scope_id. Enforced at application layer.
Unique constraint: (user_id, scope_type, scope_id).

### 12. item_appearances.tags_json as TEXT
Tag memberships stored as JSON string in TEXT column for SQLite compatibility
during development and testing. In Postgres production, this should be
migrated to a proper ARRAY column or a separate item_appearance_tags junction
table for indexed set-intersection queries. The import pipeline handles this
at write time (not query time).

### 13. import_key is the idempotency key for recipes
Recipe.import_key is UNIQUE and set to the file path from the KubeJS export
(e.g. "recipes/mekanism/enriching/gold_dust_from_ore.json").
Re-importing the same export is safe — the UNIQUE constraint prevents
duplicate rows. The import pipeline can UPSERT on import_key.

### 14. Alembic env.py imports app.models not individual files
alembic/env.py does `import app.models` which triggers __init__.py which
imports all table models in FK-dependency order. When adding a new model
file, add it to app/models/__init__.py — don't add it to env.py directly.

### 15. OAuth callback uses URL fragment for access token
The OAuth callback redirects to `{frontend_url}/oauth/{provider}/callback#{access_token}`.
The fragment is never sent to any server — only the browser sees it. The
refresh token is set as an httpOnly cookie on the redirect response.
The frontend reads `window.location.hash.slice(1)` in onMount and clears it
from the URL with `history.replaceState`.

### 16. Frontend is CSR-only
`export const ssr = false` in `frontend/src/routes/+layout.ts`. The refresh
token is in an httpOnly cookie scoped to the API domain; SSR cannot forward
it. All auth state is bootstrapped client-side via `bootAuth()` which POSTs
to /auth/refresh on every page load.

### 17. SvelteKit frontend uses Svelte 5 runes
The frontend uses Svelte 5 (`$state`, `$derived`, `$effect`, `$props`) not
Svelte 4 (`export let`, reactive `$:`, `writable`). Auth state (accessToken,
currentUser) uses Svelte stores (not runes) so they can be imported and used
outside components.

---

## Known Issues and TODOs

### Medium priority
1. **test_pending_recipes scoping is simplified.** If a user has any mod admin
   role, they see ALL pending recipes. Full implementation should join through
   recipe_mod_version_links → mod_versions → mods to scope precisely.
   See comment in `backend/app/api/v1/routes/recipes.py`.

2. **tags_json should become Postgres ARRAY in production.** Migrate
   item_appearances.tags_json from TEXT to TEXT[] (Postgres array) and add a
   GIN index for fast tag set-intersection queries.
   The chain traverser query pattern: WHERE '{c:ores/gold}' = ANY(tags).

3. **Email verification not implemented.** fastapi-mail is in pyproject.toml
   but not wired up. No flow for /auth/verify or password reset yet.

4. **OAuth state parameter needs server-side storage.** The state nonce is
   returned from GET /auth/oauth/{provider} but not stored server-side for
   CSRF verification. A short-lived DB or Redis entry is needed before OAuth
   goes to production.

### Low priority / future
5. **pgvector for item identity matching.** When importing a new modpack
   version, some items will be new. pgvector embeddings of (item_name + tags)
   could suggest likely identity matches for curator review.

6. **Machine upgrade modifiers need data entry UI.** The DB schema is ready
   but there's no import source — upgrade data must be entered manually.
   The data_source field tracks provenance: "item_tooltip" | "wiki" | "community".

7. **Per-unit-time games (Factorio, Satisfactory).** Game.recipe_metric
   distinguishes "discrete" from "throughput" but the chain traverser and
   yield calculations don't yet account for time_ticks. For throughput games,
   yield should be items/second, and chains compared on throughput not count.

---

## Tech Stack

### Backend
| Concern | Library | Version | Notes |
|---|---|---|---|
| Web framework | FastAPI | ≥0.115 | with standard extras |
| ORM | SQLModel | ≥0.0.21 | built on SQLAlchemy 2.0 + Pydantic 2 |
| Async DB driver | asyncpg | ≥0.29 | PostgreSQL production |
| Sync DB driver | psycopg2-binary | ≥2.9 | Alembic migrations only |
| Async support | greenlet | ≥3.0 | SQLAlchemy async requirement |
| Migrations | Alembic | ≥1.13 | async env.py configured |
| Password hashing | pwdlib[argon2] | ≥0.2 | API: verify_and_update() not verify() |
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
| Build tool | Vite | 8.x | |
| Web server | nginx | alpine | SPA fallback + /api proxy |

---

## Environment Variables

### Backend (see `backend/.env.example`)

Required:
- `DATABASE_URL` — `postgresql+asyncpg://...`
- `DATABASE_URL_SYNC` — `postgresql+psycopg2://...` (Alembic only)
- `JWT_SECRET` — random hex string: `python -c "import secrets; print(secrets.token_hex(32))"`

Optional (features disabled if blank):
- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET`
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
- `AUTHENTIK_CLIENT_ID` / `AUTHENTIK_CLIENT_SECRET` / `AUTHENTIK_BASE_URL`
- `MAIL_*` (email verification, password reset)
- `FRONTEND_URL` — default `http://localhost:5173` (dev) / `http://localhost` (prod)
- `DEBUG` — set `true` to enable /docs and HTTP cookies (default false)

### Frontend
- `VITE_API_BASE` — set in `frontend/.env` for local dev:
  `VITE_API_BASE=http://localhost:8000/api/v1`
- Production value (`/api/v1`) is committed in `frontend/.env.production`

---

## Running Locally

```bash
# Backend
cd backend
cp .env.example .env     # fill in DATABASE_URL, JWT_SECRET, set DEBUG=true
uv sync
alembic upgrade head
uv run fastapi dev app/main.py   # http://localhost:8000/docs

# Tests (SQLite in-memory, no Postgres needed)
uv run pytest tests/ -v

# Generate a migration after model changes
alembic revision --autogenerate -m "describe the change"

# Import a KubeJS export (after running the server once to apply migrations)
python -m app.importer \
  --export-dir ../allthemons_export \
  --game minecraft --game-version 1.21.1 \
  --modpack atm10 --modpack-version 0.18.0

# Frontend (separate terminal)
cd frontend
cp .env.production .env   # edit to point at localhost:8000 if not already
npm install
npm run dev               # http://localhost:5173

# Full stack via Docker
docker compose up         # frontend at :80, API at :8000, DB at :5432
```

---

## What Claude Code Should Work On Next

Suggested order based on dependencies:

1. **Chain explorer UI** — `frontend/src/routes/chains/+page.svelte`:
   modpack selector dropdown → start item/tag input → target item/tag input
   → ranked results table with step-by-step machine sequence and yield per input.
   Consumes `GET /api/v1/chains` (already implemented in `backend/app/api/v1/routes/chains.py`).

2. **Public catalog pages** — `frontend/src/routes/mods/`, `/modpacks/`, `/games/`:
   listing published content, linking to chain explorer with pre-filled modpack.

3. **Recipe submission UI** — authenticated users submit new recipes via
   `POST /api/v1/recipes` (already implemented in `backend/app/api/v1/routes/recipes.py`).

4. **Email wiring** — fastapi-mail integration for account verification (`/auth/verify`)
   and password reset. The mail config vars are already in `.env.example`.

5. **OAuth state nonce** — short-lived DB or Redis entry for CSRF protection
   during the OAuth flow. See Architecture Decision 15 and TODO item 4 above.

6. **tags_json → Postgres ARRAY** — write an Alembic migration to alter
   `item_appearances.tags_json` from TEXT to TEXT[] and add a GIN index.
   Update the chain traverser query in `chains.py` to use `= ANY(tags)`.

7. **Machine upgrade data entry** — admin UI for entering grinding ball /
   speed upgrade modifier stats into `machine_upgrade_items` and
   `machine_upgrade_modifiers` tables.
