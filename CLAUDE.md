# RecipeBook — Claude Code Handoff

Community recipe database for moddable games (Minecraft, Factorio, Satisfactory).
Core feature: chain analysis — find every multi-step processing path from item A to item B, ranked by yield per input.
Immediate use case: ATM10 (All The Mods 10) on MC 1.21.1.

Reference docs: [Database schema + tech stack + env vars](docs/schema.md) · [Architecture decisions + TODOs](docs/architecture.md)

---

## Project Structure

```
/
├── docker-compose.yml       # db + backend (one image serves the API and the website)
├── backend/
│   ├── app/
│   │   ├── config.py, db.py, memory.py   # settings · engine/session · malloc_trim + RSS helpers
│   │   ├── main.py          # app, security headers, error pages, router order (catch-alls LAST)
│   │   ├── auth/            # password.py, tokens.py, refresh.py, oauth.py
│   │   ├── models/          # __init__.py imports ALL models in FK order (required by Alembic)
│   │   ├── importer/pipeline.py   # KubeJS export ingestion CLI + shared `import_recipes()`
│   │   ├── importer/factorio.py + factorio_data.lua  # Factorio: Lua sandbox reads recipes/machines/resources from an install (needs `lua5.4`)
│   │   ├── factory.py       # factory calculator: whole-tree solve (recipe per item, machines, modules, power); pure
│   │   ├── contrib.py       # hand entry rules: create item / machine / recipe (API + /contribute share it)
│   │   ├── cli.py           # `python -m app.cli promote <email> --superuser|--curator`
│   │   ├── discovery/
│   │   │   ├── scope.py     # Scope = game + modpack + version -> set of mod_version_ids
│   │   │   ├── index.py     # in-memory DiscoveryIndex, one per scope (max 2 cached)
│   │   │   └── service.py   # queries shared by the JSON API and the HTML pages
│   │   ├── api/v1/routes/   # JSON API: auth, users, admin, catalog, chains, discover, recipes
│   │   └── web/             # server-rendered site (Jinja2 + HTMX)
│   │       ├── pages.py, account.py, admin.py, contribute.py   # routes (/contribute = hand-entry forms)
│   │       ├── deps.py      # scope/index deps, cookie session, CSRF (Origin) check
│   │       ├── templating.py, templates/        # Jinja2 env + helpers; templates/partials = HTMX fragments
│   │       └── static/      # css/app.css (hand-written), js/htmx.min.js (vendored, pinned), js/app.js
│   ├── alembic/             # env.py + versions/
│   ├── tests/               # conftest.py (SQLite fixtures), test_auth.py, test_web.py
│   └── atm10_ore_analyzer.py  # recipe parser used by the importer
└── docs/
```

There is **no Node/npm anywhere**: no build step, no bundler. Third-party browser code is vendored in
`web/static/js/` and pinned in `web/static/README.md`.

## URLs

`/{game}/{pack}/{version}/...` — `version` may be `latest`; pages link to the pinned version and carry
`<link rel=canonical>`. Everything under a scope (items, mods, machines, tags, routes) only sees that
version's mod versions. JSON equivalent: `/api/v1/discover/{game}/{pack}/{version}/...`.
Fixed routes (`/login`, `/admin`, `/static`, `/healthz`, `/api`) are registered before the catch-alls in
`main.py` — keep it that way when adding routes.

---

## Running Locally

```bash
docker compose up -d db
cd backend
cp .env.example .env        # set JWT_SECRET (or DEBUG=true for dev)
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload   # http://localhost:8000  (API docs at /docs when DEBUG=true)

uv run pytest tests/ -v     # SQLite in-memory, no Postgres needed

# Import recipe data (see README)
DEBUG=false uv run python -m app.importer --export-dir ../allthemons_export
# Factorio (Vanilla = base, Space Age = base + space-age + quality + elevated-rails); Steam path is the default
DEBUG=false uv run python -m app.importer.factorio [--factorio-dir ~/.local/share/Steam/steamapps/common/Factorio]
# Become able to edit the catalog / publish recipes (register on the site first)
uv run python -m app.cli promote you@example.com --superuser
```

> **uv note:** `alembic` and `uvicorn` are only on the uv venv PATH — always prefix with `uv run`.
> **Importer note:** set `DEBUG=false` or SQLAlchemy echoes every statement.
> **Process control:** don't `pkill -f`/`kill` by pattern from a shell tool; use `fuser -k 8000/tcp`.

---

## Memory (read before touching chains.py or the index)

Production target is < 8 GB total; the compose file caps the backend at 2 GB. A single process holds:
baseline ~100 MB, one scope's index ~90 MB, one scope's recipe graph ~230 MB; at most 2 of each are cached
(`MAX_INDEXES`, `_GRAPH_MAX`). Route searches are bounded three ways in `_Budget`: 3M node visits,
50k collected chains, 15 s wall clock — they run in threads that cannot be cancelled, so without these a
deep search kept allocating (~40 MB/s) long after the client disconnected (this is how a process reached 64 GB).
Concurrent searches are limited to 2 (`_search_slots`). `memory.trim()` returns freed heap to the OS after
builds/searches. Run ONE uvicorn worker: caches are per process.
`GET /healthz` reports RSS and cache counts.

---

## Chain Calculation

Forward searches (a start item is given) prune with `_steps_to` (fewest steps from each item to the target, so branches that cannot
reach it in the remaining depth are dropped) and collect each machine sequence a few times only (`_SeqCounter`).
Search is iterative deepening (depth 1, 2, 3 …, stops once a complete pass has ~400 routes) so short routes are
never starved by a deep one. Routes are ranked by `_route_cost` (steps, then distinct machines, then log ratio
floored at 0.25) and de-duplicated by machine sequence + start item. `_Excluder` handles skips: recipe types,
machine ids, items (any slot), mods (recipe types + any item); target/start are exempt. The /routes page has an
"Advanced options" panel for these; `DEFAULT_SKIP_RECIPE_TYPES` in `web/pages.py` (Mystical Agriculture
reprocessor) applies until the form is submitted.

The DFS in `chains.py` is CPU-bound and runs in `asyncio.to_thread()` behind a 2-slot semaphore, over a
graph cached per scope (`_get_graph`). Responses include `"truncated": true` when a cap was hit.
Call `invalidate_graph_cache()` / `discovery.index.invalidate()` after an import (both also expire on a TTL).

---

## What to Work On Next

1. **Parser coverage** — 43.4k of 46.3k recipe files parse (`atm10_ore_analyzer.py`, structure-driven: any
   pattern+key is a grid, `_INPUT_KEYS`/`_AUX_KEYS`/`_OUTPUT_KEYS` vocabularies, `basePredicate`/`result`
   wrappers, chances, fluids as aux inputs; output tags are resolved on import via `TagIndex.canonical_item`).
   The ~2.9k left are mostly not item flow (BotanyPots soils/block crops, enchantments, upgrades, spawners).
   Fluids and chemicals are stored as item appearances with ids `fluid:ns:name` / `chemical:ns:name` (still/flowing
   variants merged, fluid tags from `tags/minecraft/fluid`) and ARE route-graph nodes (`_Recipe.gin`); water is free,
   non-consumed casts are not followed, chemicals don't count as side inputs. The importer skips recipes whose
   `import_key` already exists, so after parser changes `TRUNCATE recipe_slots, recipe_mod_version_links, recipes CASCADE` and re-import.
   Route quality: items whose only producers are excluded (crafting) count as "raw" (e.g. armor -> ingot recycling).
2. **Rate limiting** — slowapi on `GET /chains` and the `/routes` page, keyed by client IP.
3. **Hand entry** — done: `POST /api/v1/{recipes,items,machines}` + `/contribute` forms (recipe: any user, curators publish at
   once, others go to the review queue; item: curator/mod admin; machine: curator; catalog: superuser). Missing: editing or
   deleting what was entered, tag creation, a typeahead for item fields (they take ids or exact names), bulk entry.
4. **Email wiring** — fastapi-mail for verification and password reset.
5. **tags_json → Postgres ARRAY** — migration + GIN index. Tag member lists only include items seen in recipes.
6. **Admin scope ids** — mod/modpack admin memberships use the *identity* id; `Recipe.modpack_id` and review
   checks still compare against version ids. Unify.
7. **Factorio** — imported from the game's Lua by `importer/factorio.py` (re-running replaces everything keyed `factorio/`).
   Two packs: Vanilla (base) and Space Age (+ space-age/quality/elevated-rails; Space Age's `base-data-updates.lua` is
   applied, so base is a separate mod version "x (Space Age)" there). Recipes net out catalysts, chances are expected values,
   a recipe craftable in several machines is stored once per machine (circuits: assembling machine + electromagnetic plant).
   Mining drills, pumpjacks, offshore pumps are zero-input recipes; the backward search treats them as source steps.
   Machine data lives in `MachineIdentity.speed_tiers_json` (speed, power_kw, fuel, module_slots, allowed_effects); the
   game's modules are `MachineUpgradeItem/Modifier` rows (SPEED / YIELD_BONUS / ENERGY_USE, additive fractions);
   `Recipe.allow_productivity` says where productivity modules may go.
   Route search: for throughput games the forward search enters a recipe through ANY item input and routes are
   de-duplicated per recipe (not per machine), so lava -> automation science pack finds the iron-gear branches too.
   `/{game}/{pack}/{version}/factory` (throughput games only) solves the whole tree: cheapest recipe per item (recipes from
   a non-primary mod carry a big penalty, so base wins where it can; productivity counts, so electromagnetic plants win over assemblers for circuits), swap any item's recipe via "other ways", pick a machine
   tier per type, fill all slots with a module. Beacons (n per machine x module, `profile` diminishing returns, power from a machines-per-beacon figure) and
   belt counts (`Game.constants_json`: belts + beacon, filtered to the pack's mods) are on the factory page; leftover fluids are used up (`factory._pick_converters`: cracking recipes fed by a leftover fluid run just often enough to eat it; the solver repeats its demand pass to a fixed point so byproducts are credited); every item page of a throughput game has
   "Machine ratios" (`factory.ratios`: what one machine makes/eats and how many default-recipe feeder machines per input, e.g. 2 : 1). Rates may be decimals. Not covered: pollution, beacon-power sharing beyond a flat machines-per-beacon figure, fuel for
   burner machines (reported as kW), mining productivity, quality, boilers/spoilage, recycling recipes, fluid temperatures,
   planet `surface_conditions`, resource yield %. Minecraft's upgrade items (speed/energy modules of mods) still have no
   import source, so its pages show no power or module effects.
