"""Server-rendered site: scoping across games/versions, cookie auth, CSRF, search caps."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.v1.routes.chains import _Budget
from app.models.identity import ItemAppearance, ItemIdentity, MachineIdentity
from app.models.recipe import Recipe, RecipeModVersionLink, RecipeSlot
from app.models.status import PublicationStatus, RecipeStatus
from app.models.versioning import (
    Game, GameVersion, Mod, ModPackIdentity, ModPackModVersion, ModPackVersion, ModVersion,
)
from tests.conftest import factory

HTML = {"Accept": "text/html"}


@pytest.fixture
async def seeded():
    """
    minecraft/atm10: 1.0 -> {mekanism}, 2.0 -> {mekanism, newmod}   (2.0 is newer = latest)
    factorio/space: 1.0 -> {base}
    One recipe in mekanism: Gold Dust -> Gold Ingot.
    """
    async with factory() as s:
        mc, fa = Game(name="Minecraft", slug="minecraft"), Game(name="Factorio", slug="factorio")
        s.add_all([mc, fa])
        await s.flush()
        mcv, fav = (GameVersion(game_id=mc.id, version="1.21.1"), GameVersion(game_id=fa.id, version="2.0"))
        s.add_all([mcv, fav])
        await s.flush()

        def mod(game, gv, name, ns):
            m = Mod(game_id=game.id, name=name, slug=ns, namespace=ns)
            return m, lambda: ModVersion(mod_id=m.id, game_version_id=gv.id, version="1", status=PublicationStatus.PUBLISHED)

        mek, mek_v = mod(mc, mcv, "Mekanism", "mekanism")
        new, new_v = mod(mc, mcv, "New Mod", "newmod")
        base, base_v = mod(fa, fav, "Base", "base")
        s.add_all([mek, new, base])
        await s.flush()
        mv_mek, mv_new, mv_base = mek_v(), new_v(), base_v()
        s.add_all([mv_mek, mv_new, mv_base])
        await s.flush()

        now = datetime(2026, 1, 1)
        atm = ModPackIdentity(game_id=mc.id, name="All The Mods 10", slug="atm10")
        sp = ModPackIdentity(game_id=fa.id, name="Space Age", slug="space")
        s.add_all([atm, sp])
        await s.flush()
        v1 = ModPackVersion(identity_id=atm.id, game_version_id=mcv.id, version="1.0", status="published", created_at=now)
        v2 = ModPackVersion(identity_id=atm.id, game_version_id=mcv.id, version="2.0", status="published", created_at=now + timedelta(days=30))
        fv = ModPackVersion(identity_id=sp.id, game_version_id=fav.id, version="1.0", status="published", created_at=now)
        s.add_all([v1, v2, fv])
        await s.flush()
        s.add_all([
            ModPackModVersion(modpack_id=v1.id, mod_version_id=mv_mek.id),
            ModPackModVersion(modpack_id=v2.id, mod_version_id=mv_mek.id),
            ModPackModVersion(modpack_id=v2.id, mod_version_id=mv_new.id),
            ModPackModVersion(modpack_id=fv.id, mod_version_id=mv_base.id),
        ])

        def item(game, mv, item_id, name):
            ident = ItemIdentity(canonical_name=name, game_id=game.id)
            return ident, lambda: ItemAppearance(identity_id=ident.id, mod_version_id=mv.id, item_id=item_id,
                                                 namespace=item_id.split(":")[0], tags_json="[]")

        rows = [item(mc, mv_mek, "mekanism:dust_gold", "Gold Dust"), item(mc, mv_mek, "mekanism:ingot_gold", "Gold Ingot"),
                item(mc, mv_new, "newmod:widget", "Widget"), item(fa, mv_base, "base:iron-plate", "Iron Plate")]
        s.add_all([r[0] for r in rows])
        await s.flush()
        apps = [r[1]() for r in rows]
        s.add_all(apps)
        machine = MachineIdentity(game_id=mc.id, name="Enrichment Chamber", recipe_types_json='["mekanism:enriching"]')
        s.add(machine)
        await s.flush()
        rec = Recipe(machine_identity_id=machine.id, recipe_type="mekanism:enriching", import_key="r1", status=RecipeStatus.APPROVED)
        s.add(rec)
        await s.flush()
        s.add_all([
            RecipeSlot(recipe_id=rec.id, slot_type="input", item_appearance_id=apps[0].id),
            RecipeSlot(recipe_id=rec.id, slot_type="output", item_appearance_id=apps[1].id),
            RecipeModVersionLink(recipe_id=rec.id, mod_version_id=mv_mek.id),
        ])
        # a recipe whose type prefix is not a mod id (as in Factorio's `crafting`) still counts for its mod
        press = MachineIdentity(game_id=mc.id, name="Press", recipe_types_json='["tool:press"]')
        s.add(press)
        await s.flush()
        rec2 = Recipe(machine_identity_id=press.id, recipe_type="tool:press", import_key="r2", status=RecipeStatus.APPROVED)
        s.add(rec2)
        await s.flush()
        s.add_all([
            RecipeSlot(recipe_id=rec2.id, slot_type="input", item_appearance_id=apps[0].id),
            RecipeSlot(recipe_id=rec2.id, slot_type="output", item_appearance_id=apps[2].id),
            RecipeModVersionLink(recipe_id=rec2.id, mod_version_id=mv_new.id),
        ])
        await s.commit()


# ── Scoping ───────────────────────────────────────────────────────────────────

async def test_items_are_scoped_to_pack_version(client: TestClient, seeded):
    v1 = client.get("/minecraft/atm10/1.0/items", headers=HTML).text
    v2 = client.get("/minecraft/atm10/2.0/items", headers=HTML).text
    assert "Gold Dust" in v1 and "Widget" not in v1
    assert "Gold Dust" in v2 and "Widget" in v2


async def test_games_do_not_leak_into_each_other(client: TestClient, seeded):
    factorio = client.get("/factorio/space/1.0/items", headers=HTML).text
    assert "Iron Plate" in factorio and "Gold Dust" not in factorio
    assert "Iron Plate" not in client.get("/minecraft/atm10/latest/items", headers=HTML).text


async def test_latest_resolves_to_newest_version(client: TestClient, seeded):
    r = client.get("/minecraft/atm10/latest", headers=HTML)
    assert r.status_code == 200 and "2.0" in r.text
    assert '<link rel="canonical" href="/minecraft/atm10/2.0">' in r.text


async def test_item_outside_scope_is_404(client: TestClient, seeded):
    assert client.get("/minecraft/atm10/2.0/items/newmod:widget", headers=HTML).status_code == 200
    assert client.get("/minecraft/atm10/1.0/items/newmod:widget", headers=HTML).status_code == 404


async def test_unknown_scope_pages_are_404_html_not_json(client: TestClient, seeded):
    for url in ("/nope", "/minecraft/nope", "/minecraft/atm10/9.9", "/nope/atm10/1.0/items"):
        r = client.get(url, headers=HTML)
        assert r.status_code == 404, url
        assert "text/html" in r.headers["content-type"], url


async def test_item_page_shows_recipe_and_machine(client: TestClient, seeded):
    page = client.get("/minecraft/atm10/1.0/items/mekanism:ingot_gold", headers=HTML).text
    assert "Enrichment Chamber" in page and "Gold Dust" in page


async def test_recipe_counts_are_per_scope(client: TestClient, seeded):
    ov = client.get("/api/v1/discover/minecraft/atm10/1.0/overview").json()
    assert ov["counts"]["recipes"] == 1 and ov["counts"]["items"] == 2
    ov2 = client.get("/api/v1/discover/minecraft/atm10/2.0/overview").json()
    assert ov2["counts"]["items"] == 3
    assert client.get("/api/v1/discover/factorio/space/1.0/overview").json()["counts"]["recipes"] == 0


async def test_search_fragment(client: TestClient, seeded):
    html = client.get("/minecraft/atm10/1.0/_search", params={"q": "gold"}, headers=HTML).text
    assert "Gold Dust" in html and "Gold Ingot" in html and "Widget" not in html


async def test_html_is_escaped(client: TestClient, seeded):
    r = client.get("/minecraft/atm10/1.0/items", params={"q": "<script>alert(1)</script>"}, headers=HTML)
    assert "<script>alert(1)</script>" not in r.text


async def test_security_headers(client: TestClient, seeded):
    h = client.get("/", headers=HTML).headers
    assert "script-src 'self'" in h["content-security-policy"] and h["x-frame-options"] == "DENY"


# ── Route finder + search caps ────────────────────────────────────────────────

async def test_route_finder_renders_a_route(client: TestClient, seeded):
    r = client.get("/minecraft/atm10/1.0/routes",
                   params={"ran": 1, "target": "mekanism:ingot_gold", "start": "mekanism:dust_gold"}, headers=HTML)
    assert r.status_code == 200 and "Route 1" in r.text and "Enrichment Chamber" in r.text


async def test_route_finder_requires_a_target(client: TestClient, seeded):
    r = client.get("/minecraft/atm10/1.0/routes", params={"ran": 1}, headers=HTML)
    assert "Pick the item you want" in r.text


def test_budget_caps_visits_chains_and_time():
    b = _Budget(limit=3)
    assert [b.spend() for _ in range(5)] == [True, True, True, False, False] and b.hit

    b = _Budget(max_chains=2)
    assert b.add_chain() and not b.add_chain() and b.hit and not b.spend()

    b = _Budget(limit=10_000_000, seconds=-1)   # deadline already passed
    spent = sum(b.spend() for _ in range(5000))
    assert b.hit and spent < 5000                # stops within ~1024 visits of the deadline


# ── Accounts ──────────────────────────────────────────────────────────────────

REG = {"display_name": "Web Tester", "email": "web@example.com", "password": "correct-horse-1"}


def test_register_login_logout_cycle(client: TestClient):
    r = client.post("/register", data=REG, follow_redirects=False)
    assert r.status_code == 303 and "rb_session" in r.cookies
    assert "HttpOnly" in r.headers["set-cookie"]
    assert client.get("/profile", headers=HTML).status_code == 200

    old = client.cookies.get("rb_session")
    assert client.post("/logout", follow_redirects=False).status_code == 303
    client.cookies.clear()
    assert client.get("/profile", follow_redirects=False).status_code == 303

    client.cookies.set("rb_session", old)            # a revoked session cookie must not work
    assert client.get("/profile", follow_redirects=False).status_code == 303
    client.cookies.clear()

    ok = client.post("/login", data={"email": REG["email"], "password": REG["password"], "next": "/profile"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/profile"


def test_login_failures_do_not_reveal_whether_email_exists(client: TestClient):
    client.post("/register", data=REG)
    client.cookies.clear()
    wrong = client.post("/login", data={"email": REG["email"], "password": "nope-nope-nope"})
    unknown = client.post("/login", data={"email": "nobody@example.com", "password": "nope-nope-nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert "don’t match" in wrong.text and "don’t match" in unknown.text


def test_login_next_cannot_redirect_off_site(client: TestClient):
    client.post("/register", data=REG)
    client.cookies.clear()
    for bad in ("//evil.example", "https://evil.example", "/\\evil.example"):
        r = client.post("/login", data={"email": REG["email"], "password": REG["password"], "next": bad}, follow_redirects=False)
        assert r.headers["location"] == "/", bad
        client.cookies.clear()


def test_cross_site_post_is_blocked(client: TestClient):
    client.post("/register", data=REG)
    r = client.post("/profile", data={"display_name": "Pwned"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.post("/profile", data={"display_name": "Renamed User"}, follow_redirects=False).status_code == 303


def test_profile_rejects_script_avatar_urls(client: TestClient):
    client.post("/register", data=REG)
    r = client.post("/profile", data={"display_name": "Web Tester", "avatar_url": "javascript:alert(1)"})
    assert r.status_code == 422


def test_register_validation_and_duplicates(client: TestClient):
    assert client.post("/register", data={**REG, "password": "short"}).status_code == 422
    assert client.post("/register", data=REG).status_code == 200      # redirected to home
    client.cookies.clear()
    assert client.post("/register", data=REG).status_code == 409


def test_admin_requires_login_and_review_queue_requires_scope(client: TestClient):
    assert client.get("/admin", follow_redirects=False).headers["location"].startswith("/login?next=")
    client.post("/register", data=REG)
    assert client.get("/admin", headers=HTML).status_code == 200
    assert client.get("/admin/recipes", headers=HTML).status_code == 403


def test_json_api_refresh_token_roundtrip(client: TestClient):
    """Regression: refresh rows used aware datetimes (Postgres rejected them) and returned Rows."""
    client.post("/api/v1/auth/register", json={"display_name": "Json User", "email": "j@example.com", "password": "password123"})
    assert client.post("/api/v1/auth/login", json={"email": "j@example.com", "password": "password123"}).status_code == 200


async def test_mod_recipe_counts_use_the_owning_mod_not_the_type_prefix(client: TestClient, seeded):
    mods = {m["namespace"]: m for m in client.get("/api/v1/discover/minecraft/atm10/2.0/mods").json()["items"]}
    assert mods["mekanism"]["recipe_count"] == 1
    assert mods["newmod"]["recipe_count"] == 1          # its recipe's type is `tool:press`
    assert "Press" in client.get("/minecraft/atm10/2.0/machines", headers=HTML).text


# ── Factory page ──────────────────────────────────────────────────────────────

@pytest.fixture
async def factory_world(seeded):
    """The seeded Factorio gets a timed smelting recipe with machine tiers: iron-plate from iron-ore."""
    import json
    from sqlalchemy import select
    async with factory() as s:
        fa = (await s.execute(select(Game).where(Game.slug == "factorio"))).scalars().one()
        fa.recipe_metric = "throughput"
        mv = (await s.execute(select(ModVersion).join(Mod).where(Mod.game_id == fa.id))).scalars().one()
        plate = (await s.execute(select(ItemAppearance).where(ItemAppearance.item_id == "base:iron-plate"))).scalars().one()
        ident = ItemIdentity(canonical_name="Iron ore", game_id=fa.id)
        s.add(ident)
        await s.flush()
        ore = ItemAppearance(identity_id=ident.id, mod_version_id=mv.id, item_id="base:iron-ore", namespace="base", tags_json="[]")
        furnace = MachineIdentity(game_id=fa.id, name="Furnace", is_time_based=True, recipe_types_json='["base:smelting"]',
                                  speed_tiers_json=json.dumps([{"name": "Stone furnace", "speed": 1, "categories": ["smelting"], "power_kw": 90, "fuel": True, "module_slots": 0, "allowed_effects": []},
                                                               {"name": "Electric furnace", "speed": 2, "categories": ["smelting"], "power_kw": 180, "module_slots": 2, "allowed_effects": ["speed"]}]))
        s.add_all([ore, furnace])
        await s.flush()
        rec = Recipe(machine_identity_id=furnace.id, recipe_type="base:smelting", import_key="f1", time_ticks=192, status=RecipeStatus.APPROVED)
        s.add(rec)
        await s.flush()
        s.add_all([RecipeSlot(recipe_id=rec.id, slot_type="input", item_appearance_id=ore.id, quantity=1),
                   RecipeSlot(recipe_id=rec.id, slot_type="output", item_appearance_id=plate.id, quantity=1),
                   RecipeModVersionLink(recipe_id=rec.id, mod_version_id=mv.id)])
        await s.commit()


async def test_factory_page_sizes_the_tree_and_accepts_decimal_rates(client: TestClient, factory_world):
    page = client.get("/factorio/space/1.0/factory", params={"target": "base:iron-plate", "rate": "37.5", "per": "min"}, headers=HTML)
    assert page.status_code == 200
    # 37.5/min = 0.625/s, 3.2 s each at speed 2 -> exactly 1 electric furnace; 37.5/min of ore needed
    assert "Electric furnace" in page.text and "37.5" in page.text and "Iron ore" in page.text
    assert "180 kW" in page.text
    tiny = client.get("/factorio/space/1.0/factory", params={"target": "base:iron-plate", "rate": "0.005", "per": "s"}, headers=HTML)
    assert tiny.status_code == 200 and "0.005" in tiny.text
    pick = client.get("/factorio/space/1.0/factory", params={"target": "base:iron-plate", "rate": "60", "t": "Furnace|Stone furnace"}, headers=HTML).text
    assert "Stone furnace" in pick and "fuel" in pick


async def test_factory_page_is_only_for_time_based_games(client: TestClient, seeded):
    assert client.get("/minecraft/atm10/1.0/factory", headers=HTML).status_code == 404
    assert "Factory" not in client.get("/minecraft/atm10/1.0/items", headers=HTML).text.split("</nav>")[0]


async def test_route_page_hides_minecraft_crafting_option_elsewhere(client: TestClient, factory_world):
    fact = client.get("/factorio/space/1.0/routes", headers=HTML).text
    assert "Ignore crafting-table" not in fact and "Mystical Agriculture" not in fact
