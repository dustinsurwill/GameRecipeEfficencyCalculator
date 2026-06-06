# RecipeBook — Chain Query & Item Lookup Implementation Spec

## Context

This document specifies the chain traversal API, item lookup endpoints, and
frontend filter model. It is intended for Claude Code to implement against the
existing FastAPI + SQLModel codebase described in CLAUDE.md.

The existing CLI analyzer (atm10_ore_analyzer.py) has a working forward DFS
implementation that can be referenced for traversal logic, but the web version
diverges from it in several important ways documented below.

---

## 1. Item Lookup Endpoints (no traversal, implement first)

These are simple indexed queries. No graph traversal. No recursion.
Analogous to JEI's R key (producers) and U key (consumers).

### GET /api/v1/items/{item_id}/producers
What recipes produce this item?

`item_id` is a URL-encoded item ID string: `mekanism:dust_gold`, `minecraft:gold_ingot`, etc.
OR a tag string prefixed with `#`: `#c:ingots/gold`

**Query params:**
- `modpack_id` (uuid, optional) — filter to recipes active in this modpack
- `status` (default: `approved`) — recipe status filter; admins can pass `draft`
- `limit` (int, default 20, max 100)
- `offset` (int, default 0)

**Implementation:**
1. If item_id starts with `#`, resolve tag to set of item_appearance item_ids via tags_json
2. Query recipe_slots WHERE slot_type='output' AND (item_appearance_id IN resolved_set OR tag_string = tag)
3. Join to recipes, filter by status and modpack membership if modpack_id provided
4. Return full recipe with all slots loaded

**Response:**
```json
{
  "item_id": "mekanism:dust_gold",
  "resolved_from_tag": null,
  "total": 14,
  "recipes": [
    {
      "id": "uuid",
      "machine": {"name": "Enrichment Chamber", "recipe_type": "mekanism:enriching", "mod_namespace": "mekanism"},
      "inputs": [{"tag": "c:ores/gold", "item_id": null, "quantity": 1.0, "is_fluid": false}],
      "outputs": [{"tag": null, "item_id": "mekanism:dust_gold", "quantity": 2.0, "is_fluid": false}],
      "aux_inputs": [],
      "time_ticks": null,
      "energy_rf": null,
      "source_priority": 1
    }
  ]
}
```

### GET /api/v1/items/{item_id}/consumers
What recipes consume this item as an input?

Same params as producers. Same tag resolution logic.
Query recipe_slots WHERE slot_type='input' AND ...

**Response shape:** identical to producers but field name is `recipes` (consumer recipes).

### GET /api/v1/items/search
Search for items by name or ID fragment.

**Query params:**
- `q` (string, required, min 2 chars) — searches item_id and canonical_name
- `modpack_id` (uuid, optional) — restrict to items that appear in this modpack
- `limit` (int, default 20)

**Response:**
```json
{
  "results": [
    {
      "item_id": "mekanism:dust_gold",
      "canonical_name": "Gold Dust",
      "identity_id": "uuid",
      "mod_namespace": "mekanism",
      "tags": ["c:dusts/gold", "c:dusts"]
    }
  ]
}
```

---

## 2. Chain Query Endpoint

### GET /api/v1/chains

The primary chain analysis endpoint. Direction is determined by whether `start`
is provided.

**Query params:**

| Param | Type | Required | Default | Notes |
|---|---|---|---|---|
| `modpack_id` | uuid | yes | — | Determines which recipes are active |
| `target` | string | yes | — | Item ID or `#tag` |
| `start` | string | no | — | Item ID or `#tag`. If absent: backward traversal from target |
| `exclude` | string[] | no | [] | Repeatable. `"mekanism"` or `"mekanism:purifying"`. See §2.1 |
| `allow_cycles` | bool | no | false | If false, chains containing cycles are excluded but counted |
| `max_depth` | int | no | 10 | Max steps per chain. Hard cap at 15 |
| `limit` | int | no | 50 | Max chains in response. Hard cap at 200 |
| `quantity` | float | no | 1.0 | Scale all quantities by this. Applied post-traversal |

**Direction logic:**
- `start` provided → **forward DFS**: start item → follow recipes that consume it →
  find chains that produce target. Rank by `yield_per_start_item`.
- `start` absent → **backward BFS/DFS**: target item → find recipes that produce it →
  recursively find their inputs → leaf nodes (items with no producing recipe in
  this modpack) are raw materials. Rank by `raw_materials_per_target`.

### 2.1 Exclude Filter Parsing

```
exclude = "mekanism"           → match all recipe_types starting with "mekanism:"
exclude = "mekanism:purifying" → match only recipe_type == "mekanism:purifying"
```

Matching is against `MachineIdentity.recipe_types_json` (the stored list of
recipe type strings for that machine). A machine is excluded if ANY of its
recipe_types match an exclude rule.

Multiple `exclude` params are OR'd: exclude=mekanism&exclude=oritech:pulverizer
means "exclude all mekanism machines AND the oritech pulverizer specifically."

### 2.2 Cycle Detection

A cycle exists when an item appears in its own ancestry within the same chain —
i.e., the DFS would visit the same item node it already came from.

**What is NOT a cycle:**
- The same machine type appearing multiple times (e.g., Crusher at step 1 and
  step 4 with different inputs). This is valid and must not be flagged.
- The same recipe_type appearing at different depths.

**What IS a cycle:**
- Item X is an output of step N, and also an input to step N (or any ancestor
  step). The traversal would loop forever.

**Implementation:** maintain a `frozenset[str]` of item_ids seen in the current
path (not recipe_ids). If a candidate output item_id is already in this set,
the chain has a cycle at this point.

When `allow_cycles=false` (default): terminate the branch at the cycle point,
record it as a cycle, do not emit this chain. Count it in `cycles_excluded`.
When `allow_cycles=true`: emit the chain with `has_cycle=true` and
`cycle_at_step=N` indicating where the loop closes. Flag prominently in UI.

**Known cyclic patterns in modded Minecraft to expect:**
- Botania mana generation loops
- Some IC2/Mekanism electrolysis → recombination setups
- Void ore miner "infinite" ore generation feeding its own fuel

### 2.3 Forward DFS (start provided)

Port from atm10_ore_analyzer.py with these changes:

1. Use `RecipeGraph._by_input` (recipes_consuming) not `_by_output`
2. Cycle detection on item_ids not recipe_ids (see §2.2)
3. Tag resolution via ItemAppearance.tags_json at traversal time
4. Exclude filter applied at machine lookup time
5. Modpack filter: only follow recipes whose mod_version_ids are all present
   in the modpack's mod_version_links

Yield metric: `total output quantity of target item / input quantity of start item`
accounting for all intermediate step multiplications.

### 2.4 Backward Traversal (no start)

Algorithm: recursive descent from target.

```
function find_raw_materials(item_id, modpack, exclude, depth, path):
  if depth > max_depth: return DEPTH_EXCEEDED
  if item_id in path: record cycle, return CYCLE
  
  producing_recipes = recipes_producing(item_id, modpack, exclude)
  
  if producing_recipes is empty:
    return RAW_MATERIAL(item_id)  # leaf node — nothing produces this here
  
  for each recipe in producing_recipes:
    for each input_slot in recipe.inputs (non-fluid):
      resolve input_slot to concrete item_ids via tags
      for each resolved_item:
        recurse(resolved_item, depth+1, path + {item_id})
```

The result is a tree not a flat list. Each "chain" in the backward case is one
complete path from target back to raw materials.

Yield metric (backward): quantities are expressed as "X of raw_material needed
per 1 unit of target." Lower is better for expensive raw materials; higher
yield from raw → target is better.

**Important:** Raw material detection is modpack-scoped. An item is a raw
material if no approved recipe in this modpack produces it. `minecraft:gold_ore`
is raw; `mekanism:dust_gold` is not (it has producers in Mekanism).

### 2.5 Response Shape

```json
{
  "target": "c:ingots/gold",
  "start": "c:ores/gold",
  "direction": "forward",
  "modpack_id": "uuid",
  "quantity": 1.0,
  "total_found": 242,
  "returned": 50,
  "cycles_found": 3,
  "cycles_excluded": 3,
  "excluded_applied": ["mekanism", "oritech:pulverizer"],
  "chains": [
    {
      "rank": 1,
      "yield_per_start_item": 16.0,
      "raw_materials_per_target": null,
      "has_cycle": false,
      "cycle_at_step": null,
      "raw_inputs": [
        {
          "tag": "c:ores/gold",
          "item_id": "minecraft:gold_ore",
          "quantity": 1.0,
          "is_fluid": false
        }
      ],
      "final_outputs": [
        {
          "item_id": "minecraft:gold_ingot",
          "canonical_name": "Gold Ingot",
          "quantity": 16.0
        }
      ],
      "steps": [
        {
          "step": 1,
          "machine": {
            "id": "uuid",
            "name": "Oritech Pulverizer",
            "recipe_type": "oritech:pulverizer",
            "mod_namespace": "oritech"
          },
          "recipe_id": "uuid",
          "inputs": [
            {"tag": "c:ores/gold", "item_id": null, "quantity": 1.0, "is_fluid": false}
          ],
          "outputs": [
            {"tag": null, "item_id": "alltheores:gold_clump", "canonical_name": "Gold Clump", "quantity": 2.0, "is_fluid": false},
            {"tag": null, "item_id": "minecraft:experience_nugget", "canonical_name": "Experience Nugget", "quantity": 1.5, "is_fluid": false}
          ],
          "aux_inputs": [
            {"item_id": "mekanism:hydrogen_chloride", "canonical_name": "Hydrogen Chloride", "quantity": 1.0, "is_fluid": true}
          ],
          "time_ticks": null,
          "energy_rf": null
        }
      ],
      "machines_required": [
        {
          "id": "uuid",
          "name": "Oritech Pulverizer",
          "recipe_type": "oritech:pulverizer",
          "mod_namespace": "oritech"
        },
        {
          "id": "uuid",
          "name": "Chemical Injection Chamber",
          "recipe_type": "mekanism:injecting",
          "mod_namespace": "mekanism"
        }
      ],
      "chemicals_required": ["mekanism:hydrogen_chloride", "mekanism:oxygen"]
    }
  ]
}
```

### 2.6 Performance

The chain traversal is CPU-bound. Wrap in `asyncio.to_thread()` as already done.

Cache key: `chains:{modpack_id}:{target}:{start}:{sorted(exclude)}:{allow_cycles}:{max_depth}`
TTL: 5 minutes. Invalidate when any recipe in the modpack changes status to/from approved.

For the backward traversal without start, the result set can be very large
(hundreds of paths to raw materials for complex items). Apply `limit` before
returning but always return `total_found` so the client knows what was cut.

---

## 3. Frontend Filter Model

### 3.1 localStorage

```typescript
interface ModpackChainFilter {
  excluded: string[];    // mix of "mekanism" and "mekanism:purifying"
  allowCycles: boolean;  // default false
  maxDepth: number;      // default 10
}

// Storage key: `recipebook:filter:${modpackId}`
// One filter state per modpack so ATM10 and Vault Hunters don't bleed
```

### 3.2 URL serialization

Filters are serialized into URL search params so chains links are shareable:

```
/chains?modpack=atm10&target=%23c%3Aingots%2Fgold&start=%23c%3Aores%2Fgold
       &exclude=mekanism&exclude=oritech%3Apulverizer
```

URL params override localStorage on page load. localStorage is updated when
the user changes filters. This means:
- Opening a shared link: sees the sharer's filter
- Changing a filter: updates localStorage for their next visit

### 3.3 Filter UI structure

The filter panel is populated from the `machines_required` across all chains
in the current response. Structure:

```
[ ] Mekanism                          ← mod-level toggle (indeterminate if partial)
    [x] Enrichment Chamber  (mekanism:enriching)
    [x] Purification Chamber (mekanism:purifying)
    [ ] Chemical Injection Chamber (mekanism:injecting)   ← unchecked = excluded
[ ] Oritech
    [x] Pulverizer (oritech:pulverizer)
    [x] Centrifuge (oritech:centrifuge)
```

Unchecking a machine adds its `recipe_type` to `excluded[]`.
Unchecking a mod adds its `mod_namespace` to `excluded[]` and removes any
individual recipe_type entries for that mod (the mod-level rule supersedes them).
Checking a mod back removes the mod-namespace entry from `excluded[]`.

Filtering is applied client-side against the already-fetched chains — no
re-fetch on filter change. Chains where ANY step's machine recipe_type matches
an exclude rule are hidden. Show a count: "47 of 242 chains shown (195 hidden
by filter)."

### 3.4 Cycle toggle

A separate toggle in the filter panel: "Show infinite cycles" (default off).
When toggled on, the frontend adds `allow_cycles=true` to the API request
(this DOES trigger a re-fetch since cycles are excluded server-side).
Cyclic chains are rendered with a warning banner: "⚠ This chain contains a
loop at step N — verify it is intentional before building."

---

## 4. Throughput Mode (Factorio / Satisfactory)

**Not needed for the immediate ATM10 use case but must not be designed out.**

When `Game.recipe_metric == "throughput"`:

- Yield metric changes from `quantity per input` to `quantity per second per input`
- Each step's contribution is divided by `recipe.time_ticks / 20` (ticks → seconds)
- The ranking still works the same way but the numbers represent rates
- `final_outputs` in the response includes `quantity_per_second` alongside `quantity`
- A chain where step 1 takes 4 ticks producing 4x and step 2 takes 40 ticks
  producing 40x have the same throughput yield — they should rank equally

The `direction` query param behaves identically. The only difference is the
yield calculation and the response units.

Gate this behind `Game.recipe_metric == "throughput"` check in the chain
service — discrete games should never see per-second math.

---

## 5. Implementation Order

1. `GET /api/v1/items/{item_id}/producers` and `/consumers` — no traversal,
   straightforward DB queries, immediately useful for the item detail page
2. `GET /api/v1/items/search` — needed for the chain query UI's item picker
3. Chain endpoint with forward DFS only (`start` required initially)
4. Cycle detection added to forward DFS
5. Backward traversal (`start` optional)
6. Response caching
7. `quantity` scaling

---

## 6. Tag Resolution at Query Time

The `tags_json` column on `item_appearances` is a JSON string of tag arrays.
Tag resolution is needed in two places:

**At import time** (already partially done): populate tags_json when ingesting
a KubeJS export. The tag files in `export/tags/minecraft/item/` provide the
full membership.

**At query time** (needed for chain traversal): given a slot with `tag_string`
= `"c:ores/gold"`, find all `item_appearances.item_id` values where
`tags_json` contains `"c:ores/gold"`.

In SQLite (dev/test): `WHERE json_each.value = ? FROM item_appearances,
json_each(item_appearances.tags_json)`

In PostgreSQL (production): `WHERE ? = ANY(tags)` with a GIN index.
The Alembic migration to convert tags_json TEXT → TEXT[] should be written
as part of this work. The migration:
```sql
ALTER TABLE item_appearances ADD COLUMN tags TEXT[];
UPDATE item_appearances SET tags = ARRAY(SELECT json_array_elements_text(tags_json::json));
CREATE INDEX ix_item_appearances_tags ON item_appearances USING GIN(tags);
-- Keep tags_json for SQLite compatibility in tests
```

In the Python query layer, use a repository pattern that abstracts this:
```python
async def items_for_tag(session, tag_string, modpack_id=None) -> list[str]:
    # Returns list of item_ids (strings) matching this tag
    # Uses GIN query on postgres, json_each on sqlite
    # Detects dialect from session.bind
```

---

## 7. What NOT to Build (scope boundaries)

- **No DFS on the producers/consumers endpoints** — they are single-hop only.
  If someone wants the full tree, they use the chains endpoint.
- **No server-side machine availability state** — the "which machines do I have"
  filter lives entirely in the browser (localStorage + URL params). There is no
  `user_machine_filters` table. See CLAUDE.md architecture decisions.
- **No real-time inventory sync** — we are not EMI. We don't connect to a
  running game instance.
- **No Litematic parsing** — out of scope for this sprint. The endpoint design
  should not be blocked waiting for it.
- **No throughput implementation yet** — just ensure the forward/backward DFS
  is not designed in a way that makes adding it later require a rewrite.
  The `Game.recipe_metric` flag is the branch point.
