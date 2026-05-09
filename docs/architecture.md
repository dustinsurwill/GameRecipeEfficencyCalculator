# Architecture Decisions

## 1. SQLModel over raw SQLAlchemy + Pydantic
One class = ORM model + Pydantic schema. Eliminates ~200 lines of duplication across 20 tables.
Tradeoff: SQLModel is in maintenance mode (late 2025). Migration to pure SQLAlchemy 2.0 later is mechanical.

## 2. fastapi-users was rejected
In maintenance mode and fights our identity model: assumes email is primary key, but we have nullable email (OAuth-only users). Auth built from primitives: pwdlib, PyJWT, authlib — ~400 lines total.

## 3. User identity separated from credentials
`User` = person (UUID PK, optional email). `OAuthAccount` = credential (many per user).
Account linking is **explicit only** — authenticated user POSTs to `/auth/oauth/{provider}/connect`. No silent email-match linking (security footgun → account hijacking).

## 4. JWT carries site-level roles only
JWT claims: sub, type, iat, exp, jti, curator (bool), superuser (bool).
Scoped roles (mod/modpack admin) checked against DB on every write. Permission changes take effect immediately — no stale JWT problem.

## 5. Refresh tokens persisted for revocation
`RefreshToken` table stores JTI per active session. Logout = delete row. Password change = delete ALL rows. Refresh = token rotation (delete old JTI, insert new).
Refresh token: HttpOnly cookie scoped to `/auth/refresh`. Access token: Bearer header (stateless).

## 6. Always use `.scalars().one_or_none()`
Critical SQLModel quirk: `session.exec(select(Model)...).one_or_none()` returns `(Model,)` tuple through TestClient's anyio bridge. Always call `.scalars().one_or_none()` to force scalar unwrapping.

## 7. Relationship `lazy="noload"` everywhere
All SQLModel Relationships use `sa_relationship_kwargs={"lazy": "noload"}`. Async SQLAlchemy cannot lazy-load — accessing a relationship outside async context raises `MissingGreenlet`. Use explicit `selectinload()` / `joinedload()` in query options when needed.

## 8. Draft/staging via status fields
`ModVersion.status` and `ModPack.status` default to `"draft"`. Only `"published"` visible in public API. Draft visible to: scope admins, site superusers.

## 9. Recipe status state machine
```
draft → pending_review  (contributor submits)
pending_review → approved  (admin/curator approves)
pending_review → rejected  (admin/curator rejects, reason required)
approved → draft  (admin retracts)
rejected → draft  (contributor revises)
```
`GET /recipes/{id}` returns 404 (not 403) for drafts the caller can't see — 403 leaks existence.

## 10. RecipeReview is append-only
Every approve/reject creates a new row. Current status = most recent review decision. Full history for community trust.

## 11. AdminGroup soft polymorphic FK
`scope_type` ("mod" | "modpack") + `scope_id` (UUID) instead of separate tables. No DB-level FK constraint — enforced at application layer. Unique: `(user_id, scope_type, scope_id)`.

## 12. tags_json as TEXT (temporary)
Tag memberships stored as JSON string for SQLite test compatibility. **Migrate to `TEXT[]` + GIN index in production** for `WHERE '{c:ores/gold}' = ANY(tags)` queries.

## 13. import_key as idempotency key
`Recipe.import_key` = UNIQUE file path from KubeJS export. Re-importing same export is safe (UNIQUE constraint). Import pipeline can UPSERT on import_key.

## 14. Alembic env.py imports app.models
`alembic/env.py` does `import app.models` → triggers `__init__.py` → all table models in FK order. When adding a new model file, add it to `app/models/__init__.py`, not `env.py`.

## 15. OAuth callback uses URL fragment
Redirect to `{frontend_url}/oauth/{provider}/callback#{access_token}`. Fragment never sent to server. Refresh token set as httpOnly cookie on the redirect. Frontend reads `window.location.hash.slice(1)` in onMount and clears with `history.replaceState`.

## 16. Frontend is CSR-only
`export const ssr = false` in `frontend/src/routes/+layout.ts`. Refresh token is httpOnly cookie scoped to API domain — SSR can't forward it. Auth bootstrapped client-side via `bootAuth()` (POST to `/auth/refresh` on every page load; 401 = logged out, expected and handled silently).

## 17. Svelte 5 runes throughout
Use `$state`, `$derived`, `$effect`, `$props` — not Svelte 4's `export let`, `$:`, `writable`. Exception: auth state (`accessToken`, `currentUser`) uses Svelte stores so they can be imported outside components.

---

## Known Issues and TODOs

### Medium priority
1. **test_pending_recipes scoping simplified** — any mod admin sees ALL pending recipes. Should join through `recipe_mod_version_links → mod_versions → mods`. See comment in `backend/app/api/v1/routes/recipes.py`.
2. **tags_json → Postgres ARRAY** — migrate `item_appearances.tags_json` to `TEXT[]` + GIN index.
3. **Email verification not wired** — fastapi-mail in pyproject.toml but no `/auth/verify` or password reset flow.
4. **OAuth state nonce needs server-side storage** — state param returned but not stored server-side for CSRF verification.

### Low priority
5. **pgvector for item identity matching** — embeddings of (item_name + tags) to suggest identity matches during import.
6. **Machine upgrade data entry UI** — DB schema ready, no import source. Data entered manually via admin UI (not yet built).
7. **Per-unit-time games (Factorio, Satisfactory)** — `Game.recipe_metric` distinguishes "discrete" / "throughput" but chain traverser doesn't account for `time_ticks` yet.