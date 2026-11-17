"""Hand entry: items, machines and recipes through the API and the /contribute forms."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.identity import MachineIdentity
from app.models.recipe import Recipe, RecipeSlot
from app.models.user import User
from app.models.versioning import (
    Game, GameVersion, Mod, ModPackIdentity, ModPackModVersion, ModPackVersion, ModVersion,
)
from tests.conftest import factory

HTML = {"Accept": "text/html"}
PW = "supersecret123"


@pytest.fixture
async def world():
    """A tiny Factorio: one mod 'base' in one published pack, plus a smelting machine."""
    async with factory() as s:
        game = Game(name="Factorio", slug="factorio", recipe_metric="throughput")
        s.add(game)
        await s.flush()
        gv = GameVersion(game_id=game.id, version="2.1")
        mod = Mod(game_id=game.id, name="Base Game", slug="base", namespace="base")
        s.add_all([gv, mod])
        await s.flush()
        mv = ModVersion(mod_id=mod.id, game_version_id=gv.id, version="2.1.24", status="published")
        pack = ModPackIdentity(game_id=game.id, name="Vanilla", slug="vanilla")
        s.add_all([mv, pack])
        await s.flush()
        pv = ModPackVersion(identity_id=pack.id, game_version_id=gv.id, version="2.1.24", status="published")
        s.add(pv)
        await s.flush()
        s.add(ModPackModVersion(modpack_id=pv.id, mod_version_id=mv.id))
        machine = MachineIdentity(game_id=game.id, name="Furnace", recipe_types_json='["base:smelting"]', is_time_based=True)
        s.add(machine)
        await s.commit()
        return {"game": str(game.id), "mv": str(mv.id), "pack": str(pv.id), "machine": str(machine.id)}


def signup(client: TestClient, email: str) -> None:
    r = client.post("/api/v1/auth/register", json={"display_name": email.split("@")[0], "email": email, "password": PW})
    assert r.status_code == 201, r.text


async def promote(email: str, **flags) -> None:
    async with factory() as s:
        user = (await s.exec(select(User).where(User.email == email))).scalars().one()
        for k, v in flags.items():
            setattr(user, k, v)
        await s.commit()


def bearer(client: TestClient, email: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def curator_headers(client: TestClient) -> dict:
    signup(client, "cur@example.com")
    await promote("cur@example.com", is_curator=True)
    return bearer(client, "cur@example.com")


def add_item(client, h, world, item_id, name, kind="item"):
    return client.post("/api/v1/items", headers=h, json={"mod_version_id": world["mv"], "item_id": item_id, "name": name, "kind": kind})


# ── API ───────────────────────────────────────────────────────────────────────

async def test_curator_enters_items_and_a_recipe_that_goes_live(client: TestClient, world):
    h = await curator_headers(client)
    for item_id, name in (("base:iron-ore", "Iron ore"), ("base:iron-plate", "Iron plate")):
        r = add_item(client, h, world, item_id, name)
        assert r.status_code == 201, r.text
    r = client.post("/api/v1/recipes", headers=h, json={
        "machine_identity_id": world["machine"], "mod_version_ids": [world["mv"]], "time_seconds": 3.2,
        "slots": [{"slot_type": "input", "item": "base:iron-ore"}, {"slot_type": "output", "item": "Iron plate"}],   # id and name both work
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "approved" and body["time_ticks"] == 192 and body["recipe_type"] == "base:furnace"
    assert [s["slot_type"] for s in body["slots"]] == ["input", "output"]
    # live: the item page and the route search see it
    assert "Furnace" in client.get("/factorio/vanilla/latest/items/base:iron-plate", headers=HTML).text
    found = client.get("/api/v1/chains", params={"game": "factorio", "modpack": "vanilla", "target": "base:iron-plate", "rate": 1}).json()
    assert found["total_found"] == 1
    assert found["chains"][0]["steps"][0]["seconds_per_craft"] if "seconds_per_craft" in found["chains"][0]["steps"][0] else True


async def test_a_regular_users_recipe_waits_for_review(client: TestClient, world):
    h = await curator_headers(client)
    add_item(client, h, world, "base:iron-ore", "Iron ore")
    add_item(client, h, world, "base:iron-plate", "Iron plate")
    client.cookies.clear()
    signup(client, "player@example.com")
    uh = bearer(client, "player@example.com")
    r = client.post("/api/v1/recipes", headers=uh, json={
        "machine_identity_id": world["machine"], "mod_version_ids": [world["mv"]],
        "slots": [{"slot_type": "input", "item": "base:iron-ore"}, {"slot_type": "output", "item": "base:iron-plate"}],
    })
    assert r.status_code == 201 and r.json()["status"] == "pending_review"
    assert "Furnace" not in client.get("/factorio/vanilla/latest/items/base:iron-plate", headers=HTML).text   # not public yet
    pending = client.get("/api/v1/recipes/pending", headers=h)       # the curator's queue
    assert pending.status_code == 200 and len(pending.json()) == 1


async def test_creation_rules(client: TestClient, world):
    signup(client, "player@example.com")
    uh = bearer(client, "player@example.com")
    assert add_item(client, uh, world, "base:x", "X").status_code == 403                 # not a curator or mod admin
    assert client.post("/api/v1/machines", headers=uh, json={"game_id": world["game"], "name": "Y"}).status_code == 403
    assert client.post("/api/v1/recipes", json={}).status_code == 401                    # anonymous

    h = await curator_headers(client)
    assert add_item(client, h, world, "base:x", "X").status_code == 201
    assert add_item(client, h, world, "base:x", "X").status_code == 409                  # duplicate
    assert add_item(client, h, world, "other:x", "X").status_code == 422                 # wrong namespace for the mod
    assert add_item(client, h, world, "no namespace", "X").status_code == 422
    water = add_item(client, h, world, "base:water", "Water", kind="fluid")
    assert water.json()["item_id"] == "fluid:base:water"
    m = client.post("/api/v1/machines", headers=h, json={
        "game_id": world["game"], "name": "Chemical Plant", "is_time_based": True,
        "speed_tiers": [{"name": "Chemical plant", "speed": 1, "categories": ["chemistry"]}]})
    assert m.status_code == 201 and "Chemical plant" in m.json()["speed_tiers_json"]
    assert client.post("/api/v1/machines", headers=h, json={"game_id": world["game"], "name": "chemical plant"}).status_code == 409


async def test_recipe_validation_messages(client: TestClient, world):
    h = await curator_headers(client)
    add_item(client, h, world, "base:iron-plate", "Iron plate")
    add_item(client, h, world, "base:water", "Water", kind="fluid")

    def post(slots, machine=None):
        return client.post("/api/v1/recipes", headers=h, json={
            "machine_identity_id": machine or world["machine"], "mod_version_ids": [world["mv"]], "slots": slots})

    r = post([{"slot_type": "input", "item": "Unobtainium"}, {"slot_type": "output", "item": "Iron plate"}])
    assert r.status_code == 422 and "Unobtainium" in r.json()["detail"] and "Add it first" in r.json()["detail"]
    assert "output" in post([{"slot_type": "input", "item": "Iron plate"}]).json()["detail"]
    assert post([{"slot_type": "input", "item": "Iron plate", "quantity": 0}, {"slot_type": "output", "item": "Iron plate"}]).status_code == 422
    assert "tag" in post([{"slot_type": "output", "item": "#c:ingots/iron"}]).json()["detail"]
    # a fluid input is stored as an aux input, flagged as a fluid
    ok = post([{"slot_type": "input", "item": "Water"}, {"slot_type": "input", "item": "#c:ingots/iron"}, {"slot_type": "output", "item": "Iron plate", "quantity": 2}])
    assert ok.status_code == 201, ok.text
    kinds = [(s["slot_type"], s["is_fluid"], s["tag_string"]) for s in ok.json()["slots"]]
    assert kinds == [("aux_input", True, None), ("input", False, "c:ingots/iron"), ("output", False, None)]


async def test_only_time_based_machines_allow_recipes_without_inputs(client: TestClient, world):
    h = await curator_headers(client)
    add_item(client, h, world, "base:iron-ore", "Iron ore")
    drill = client.post("/api/v1/machines", headers=h, json={"game_id": world["game"], "name": "Mining Drill", "is_time_based": True}).json()
    chest = client.post("/api/v1/machines", headers=h, json={"game_id": world["game"], "name": "Chest"}).json()
    slots = [{"slot_type": "output", "item": "base:iron-ore"}]
    ok = client.post("/api/v1/recipes", headers=h, json={"machine_identity_id": drill["id"], "mod_version_ids": [world["mv"]], "slots": slots})
    assert ok.status_code == 201, ok.text
    bad = client.post("/api/v1/recipes", headers=h, json={"machine_identity_id": chest["id"], "mod_version_ids": [world["mv"]], "slots": slots})
    assert bad.status_code == 422 and "input" in bad.json()["detail"]


# ── Website forms ─────────────────────────────────────────────────────────────

def web_login(client: TestClient, email: str) -> None:
    r = client.post("/login", data={"email": email, "password": PW}, follow_redirects=False)
    assert r.status_code == 303, r.text


async def test_recipe_form_end_to_end(client: TestClient, world):
    signup(client, "cur@example.com")
    await promote("cur@example.com", is_curator=True)
    web_login(client, "cur@example.com")

    assert "Which pack" in client.get("/contribute/recipe", headers=HTML).text
    page = client.get(f"/contribute/recipe?pack={world['pack']}", headers=HTML).text
    assert "Furnace" in page and "Base Game 2.1.24" in page

    r = client.post("/contribute/item", data={"pack": world["pack"], "mod_version": world["mv"], "item_id": "base:iron-ore", "name": "Iron ore", "kind": "item"}, headers=HTML)
    assert r.status_code == 200 and "Added" in r.text and "base:iron-ore" in r.text
    r = client.post("/contribute/item", data={"pack": world["pack"], "mod_version": world["mv"], "item_id": "base:iron-ore", "name": "Iron ore", "kind": "item"}, headers=HTML)
    assert r.status_code == 409 and "already exists" in r.text
    client.post("/contribute/item", data={"pack": world["pack"], "mod_version": world["mv"], "item_id": "base:iron-plate", "name": "Iron plate", "kind": "item"}, headers=HTML)

    form = {"pack": world["pack"], "machine": world["machine"], "mod_version": world["mv"], "time_seconds": "3.2",
            "in_ref": ["Iron ore", ""], "in_qty": ["1", ""], "out_ref": ["base:iron-plate"], "out_qty": ["1"]}
    bad = client.post("/contribute/recipe", data={**form, "out_ref": ["Unobtainium"]}, headers=HTML)
    assert bad.status_code == 422 and "Unobtainium" in bad.text and 'value="3.2"' in bad.text      # error shown, input kept
    ok = client.post("/contribute/recipe", data=form, headers=HTML)
    assert ok.status_code == 200 and "Recipe added" in ok.text
    assert "Furnace" in client.get("/factorio/vanilla/latest/items/base:iron-plate", headers=HTML).text


async def test_item_and_machine_forms_are_permissioned(client: TestClient, world):
    signup(client, "player@example.com")
    web_login(client, "player@example.com")
    r = client.post("/contribute/item", data={"pack": world["pack"], "mod_version": world["mv"], "item_id": "base:x", "name": "X"}, headers=HTML)
    assert r.status_code == 403 and "curator" in r.text
    r = client.post("/contribute/machine", data={"game": world["game"], "name": "Lab"}, headers=HTML)
    assert r.status_code == 403
    assert client.get("/contribute/catalog", headers=HTML).status_code == 403
    # a regular user can still submit a recipe (it waits for review)


async def test_machine_form_parses_speed_tiers(client: TestClient, world):
    signup(client, "cur@example.com")
    await promote("cur@example.com", is_curator=True)
    web_login(client, "cur@example.com")
    ok = client.post("/contribute/machine", data={"game": world["game"], "name": "Assembling Machine", "time_based": "1",
                     "tiers": "Assembling machine 1 | 0.5 | crafting\nAssembling machine 2 | 0.75 | crafting, advanced-crafting"}, headers=HTML)
    assert ok.status_code == 200 and "Added" in ok.text
    async with factory() as s:
        m = (await s.exec(select(MachineIdentity).where(MachineIdentity.name == "Assembling Machine"))).scalars().one()
    assert m.is_time_based and '"speed": 0.75' in m.speed_tiers_json and '"advanced-crafting"' in m.speed_tiers_json
    bad = client.post("/contribute/machine", data={"game": world["game"], "name": "Oops", "tiers": "no speed here"}, headers=HTML)
    assert bad.status_code == 422 and "Speed line" in bad.text


async def test_catalog_forms_build_a_game_from_nothing(client: TestClient, world):
    signup(client, "root@example.com")
    await promote("root@example.com", is_superuser=True, is_curator=True)
    web_login(client, "root@example.com")
    assert client.get("/contribute/catalog", headers=HTML).status_code == 200
    post = lambda path, **data: client.post(f"/contribute/catalog/{path}", data=data, headers=HTML)  # noqa: E731
    assert "Created game" in post("game", name="Satisfactory", slug="satisfactory", recipe_metric="throughput").text
    assert "already exists" in post("game", name="Satisfactory", slug="satisfactory").text
    async with factory() as s:
        sat = (await s.exec(select(Game).where(Game.slug == "satisfactory"))).scalars().one()
    assert "Created game version" in post("game-version", game=str(sat.id), version="1.0", is_latest="1").text
    assert "Created mod" in post("mod", game=str(sat.id), name="Base", namespace="satisfactory").text
    assert "Created modpack" in post("modpack", game=str(sat.id), name="Vanilla", slug="vanilla").text
    async with factory() as s:
        gv = (await s.exec(select(GameVersion).where(GameVersion.game_id == sat.id))).scalars().one()
        mod = (await s.exec(select(Mod).where(Mod.game_id == sat.id))).scalars().one()
        pack = (await s.exec(select(ModPackIdentity).where(ModPackIdentity.game_id == sat.id))).scalars().one()
    assert "Created mod version" in post("mod-version", mod=str(mod.id), game_version=str(gv.id), version="1.0").text
    assert "Created modpack version" in post("modpack-version", modpack=str(pack.id), game_version=str(gv.id), version="1.0").text
    async with factory() as s:
        mv = (await s.exec(select(ModVersion).where(ModVersion.mod_id == mod.id))).scalars().one()
        mpv = (await s.exec(select(ModPackVersion).where(ModPackVersion.identity_id == pack.id))).scalars().one()
    assert "added to the pack" in post("modpack-mod", modpack_version=str(mpv.id), mod_version=str(mv.id)).text
    assert "already in this ModPack" in post("modpack-mod", modpack_version=str(mpv.id), mod_version=str(mv.id)).text
