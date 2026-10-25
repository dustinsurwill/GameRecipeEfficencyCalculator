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

## 15. Server-rendered site (Jinja2 + HTMX), no Node
The UI moved from a SvelteKit SPA to templates served by FastAPI. Reasons: npm supply-chain exposure (build and dev machines), item/recipe pages are public content that should be real, indexable, shareable URLs, and the index already lives in the API process so a page is a dict lookup plus a template. HTMX is a single vendored file; CSS is hand-written. `hx-boost` on `<body>` makes navigation AJAX with real URLs; filters use `hx-select` against the same page URL (no separate fragment endpoints); only typeahead, "show more" and the route-finder picker have `_fragment` routes. Every page works without JS except live typeahead.

## 16. Sessions: cookie for the site, Bearer for the JSON API
The site stores the same DB-backed refresh JWT in an `HttpOnly; SameSite=Lax; Path=/` cookie (`rb_session`) and loads the user from the DB on each request (roles are never stale; logout revokes server-side). State-changing requests also require `Origin`/`Referer` to match `Host` (`same_origin`). Admin pages call the JSON API's handler functions directly with an identity built from the session, so permission checks live in one place. OAuth: `/auth/oauth/{provider}` sets a `state` cookie, the provider callback (`/api/v1/auth/oauth/{provider}/callback`) verifies it, then starts a session and redirects home.

## 17. Everything is scoped: game → modpack → version
`discovery.scope.Scope` resolves `(game_slug, pack_slug, version|latest)` to the set of mod_version_ids pinned by that modpack version. Index, recipe queries and chain graph are all filtered by it and cached per scope (max 2 each). Items, machines, tags and recipes from other games/versions never appear. Unpublished packs still resolve (so a fresh import is browsable) but published versions win for `latest`.

## 18. Memory is bounded by construction
See "Memory" in CLAUDE.md. Incident: a process reached 64 GB after a few deep route lookups — the DFS ran in an uncancellable thread with no cap on visited nodes or collected chains, kept running after the client disconnected, and re-loaded the whole graph per request. Fixes: shared per-scope graph cache, 3-way search budget (visits / chains / seconds), 2 concurrent searches, max depth 12, `malloc_trim` after builds, 2 GB container limit. Regression tests cover the budget.

---

## Known Issues and TODOs

### Medium priority
1. **test_pending_recipes scoping simplified** — any mod admin sees ALL pending recipes. Should join through `recipe_mod_version_links → mod_versions → mods`. See comment in `backend/app/api/v1/routes/recipes.py`.
2. **tags_json → Postgres ARRAY** — migrate `item_appearances.tags_json` to `TEXT[]` + GIN index.
3. **Email verification not wired** — fastapi-mail in pyproject.toml but no `/auth/verify` or password reset flow.
4. **Route-finder ranking** — see CLAUDE.md "What to Work On Next".

### Low priority
5. **pgvector for item identity matching** — embeddings of (item_name + tags) to suggest identity matches during import.
6. **Machine upgrade data entry UI** — DB schema ready, no import source. Data entered manually via admin UI (not yet built).
7. **Per-unit-time games (Factorio, Satisfactory)** — `Game.recipe_metric` distinguishes "discrete" / "throughput" but chain traverser doesn't account for `time_ticks` yet.