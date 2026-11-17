"""Factory calculator: recipe choice, machine counts, modules, loops and byproducts (pure, no database)."""
from app.factory import Module, Settings, solve
from tests.test_chains import graph, rec

NAMES = {"b:ore": "Ore", "b:plate": "Plate", "b:gear": "Gear", "b:slag": "Slag"}


def tiers(*specs):
    return [{"name": n, "speed": sp, "categories": [], "power_kw": kw, "module_slots": slots,
             "allowed_effects": ["speed", "productivity", "consumption"], "fuel": False} for n, sp, kw, slots in specs]


def timed(r, seconds, machine_tiers=None, prod=False, mod="b"):
    r.time_ticks = round(seconds * 60)
    r.machine.tiers = machine_tiers or []
    r.allow_productivity = prod
    r.mod = mod
    return r


def line():
    mine = timed(rec("b:mining", "drill", [], [("b:ore", 1)]), 1, tiers(("Drill", 0.5, 90, 0)))
    smelt = timed(rec("b:smelting", "furnace", [("b:ore", 1)], [("b:plate", 1)]), 3.2,
                  tiers(("Stone furnace", 1, 90, 0), ("Electric furnace", 2, 180, 2)))
    gear = timed(rec("b:crafting", "asm", [("b:plate", 2)], [("b:gear", 1)]), 0.5, tiers(("Assembler", 0.75, 75, 2)), prod=True)
    return mine, smelt, gear


def by_item(result):
    """rows are one per recipe; index them by every item they make"""
    return {m["item_id"]: r for r in result["rows"] for m in r["makes"]}


def test_whole_tree_is_sized_from_the_target_down():
    res = solve(graph(*line()), NAMES, "b:gear", 1.0)           # 1 gear / s
    rows = by_item(res)
    assert [r["item_id"] for r in res["rows"]] == ["b:gear", "b:plate", "b:ore"]
    assert rows["b:gear"]["machines_exact"] == 0.5 / 0.75 and rows["b:gear"]["machines"] == 1
    assert rows["b:plate"]["rate"] == 2 and rows["b:ore"]["rate"] == 2
    # fastest tier by default: 2 plates/s * 3.2 s / speed 2 = 3.2 furnaces -> 4
    assert (rows["b:plate"]["tier"], rows["b:plate"]["machines"]) == ("Electric furnace", 4)
    assert rows["b:plate"]["power_kw"] == 4 * 180
    assert rows["b:ore"]["machines"] == 4 and res["machines"] == 1 + 4 + 4        # 2 ore/s at 0.5 each
    assert res["power_kw"] == 75 + 720 + 360
    assert [(x["name"], x["rate"]) for x in res["resources"]] == [("Ore", 2)]     # the mined ore is the raw resource


def test_tier_preference_and_fractional_rates():
    res = solve(graph(*line()), NAMES, "b:gear", 0.25, Settings(tier_pref={"furnace": "Stone furnace"}))
    plate = by_item(res)["b:plate"]
    assert plate["tier"] == "Stone furnace" and abs(plate["machines_exact"] - 0.5 * 3.2) < 1e-9 and plate["machines"] == 2


def test_default_recipe_is_the_cheapest_and_overrides_win():
    mine, smelt, gear = line()
    dear = timed(rec("sa:casting", "foundry", [("b:ore", 5)], [("b:plate", 1)]), 1, tiers(("Foundry", 4, 100, 0)), mod="sa")
    g = graph(mine, smelt, gear, dear)
    assert by_item(solve(g, NAMES, "b:plate", 1))["b:plate"]["machine"] == "furnace"
    res = solve(g, NAMES, "b:plate", 1, Settings(overrides={"b:plate": dear.id}))
    assert by_item(res)["b:plate"]["machine"] == "foundry" and by_item(res)["b:ore"]["rate"] == 5
    alts = by_item(solve(g, NAMES, "b:plate", 1))["b:plate"]["makes"][0]["alternatives"]
    assert [a["machine"] for a in alts] == ["foundry"]


def test_foreign_mod_recipes_are_only_a_last_resort():
    mine, smelt, gear = line()
    cheap_but_foreign = timed(rec("sa:magic", "magic", [("b:ore", 1)], [("b:plate", 100)]), 1, tiers(("Magic", 1, 1, 0)), mod="sa")
    assert by_item(solve(graph(mine, smelt, gear, cheap_but_foreign), NAMES, "b:plate", 1))["b:plate"]["machine"] == "furnace"
    only = solve(graph(mine, cheap_but_foreign), NAMES, "b:plate", 1)       # nothing else makes it: it is used
    assert by_item(only)["b:plate"]["machine"] == "magic"


def test_modules_change_machines_power_and_inputs_where_allowed():
    mine, smelt, gear = line()
    speed = Module("Speed 3", {"speed": 0.5, "consumption": 0.7})
    prod = Module("Prod 3", {"productivity": 0.1, "speed": -0.15, "consumption": 0.8})
    modules = {gear.machine.id: {"Speed 3": speed, "Prod 3": prod}, smelt.machine.id: {"Prod 3": prod}}
    g = graph(mine, smelt, gear)
    base = by_item(solve(g, NAMES, "b:gear", 1))
    fast = by_item(solve(g, NAMES, "b:gear", 1, Settings(module="Speed 3", modules=modules)))["b:gear"]
    assert fast["modules"] == 2 and abs(fast["machines_exact"] - base["b:gear"]["machines_exact"] / 2) < 1e-9   # +100% speed
    assert abs(fast["power_kw"] - 75 * 2.4) < 1e-9
    rich = by_item(solve(g, NAMES, "b:gear", 1, Settings(module="Prod 3", modules=modules)))
    assert abs(rich["b:gear"]["productivity"] - 0.2) < 1e-9                       # 2 slots x 10%
    assert abs(rich["b:plate"]["rate"] - 2 / 1.2) < 1e-9                          # fewer plates for the same gears
    assert rich["b:plate"]["modules"] == 0                                        # smelting does not allow productivity here


def test_a_loop_is_broken_and_reported():
    a = timed(rec("t:a", "ma", [("b:plate", 1)], [("b:gear", 1)]), 1, tiers(("A", 1, 10, 0)))
    b = timed(rec("t:b", "mb", [("b:gear", 1)], [("b:plate", 1)]), 1, tiers(("B", 1, 10, 0)))
    ore = timed(rec("t:c", "mc", [("b:ore", 1)], [("b:plate", 1)]), 1, tiers(("C", 1, 10, 0)))
    g = graph(a, b, ore)
    assert not solve(g, NAMES, "b:gear", 1)["warnings"]                          # the default takes the ore, no loop
    res = solve(g, NAMES, "b:gear", 1, Settings(overrides={"b:plate": b.id}))     # forcing plate <- gear makes one
    assert res["found"] and "other way round" in res["warnings"][0]


def test_byproducts_offset_demand_and_the_rest_is_surplus():
    split = timed(rec("t:split", "sp", [("b:ore", 1)], [("b:plate", 1), ("b:slag", 2)]), 1, tiers(("S", 1, 1, 0)))
    use = timed(rec("t:use", "us", [("b:plate", 1), ("b:slag", 1)], [("b:gear", 1)]), 1, tiers(("U", 1, 1, 0)))
    res = solve(graph(split, use), NAMES, "b:gear", 1)
    # slag is made as a byproduct of plate: 1 gear needs 1 plate (-> 2 slag made) and 1 slag, 1 slag left over
    assert [(s["item_id"], round(s["rate"], 6)) for s in res["surplus"]] == [("b:slag", 1.0)]
    split_row = by_item(res)["b:plate"]
    assert split_row["crafts_per_s"] == 1 and {m["item_id"] for m in split_row["makes"]} == {"b:plate", "b:slag"}


def test_one_recipe_chosen_for_several_items_is_one_row():
    split = timed(rec("t:split", "sp", [("b:ore", 1)], [("b:plate", 1), ("b:slag", 1)]), 1, tiers(("S", 1, 5, 0)))
    use = timed(rec("t:use", "us", [("b:plate", 1), ("b:slag", 3)], [("b:gear", 1)]), 1, tiers(("U", 1, 1, 0)))
    res = solve(graph(split, use), NAMES, "b:gear", 1)
    assert [r["machine"] for r in res["rows"]] == ["us", "sp"]                 # one row for the splitter, not two
    row = by_item(res)["b:slag"]
    assert row["crafts_per_s"] == 3                                             # slag is the limit
    assert [(s["item_id"], round(s["rate"], 6)) for s in res["surplus"]] == [("b:plate", 2.0)]   # plate is the leftover


def test_unknown_target_is_not_found():
    assert not solve(graph(*line()), NAMES, "b:nothing", 1)["found"]


def test_built_in_productivity_counts_only_where_the_recipe_allows_it_and_stacks_with_modules():
    cast = timed(rec("t:cast", "foundry", [("b:ore", 1)], [("b:plate", 1)]), 1,
                 [{**tiers(("Foundry", 4, 100, 4))[0], "base_productivity": 0.5}], prod=True)
    belt = timed(rec("t:belt", "foundry", [("b:ore", 1)], [("b:gear", 1)]), 1,
                 [{**tiers(("Foundry", 4, 100, 4))[0], "base_productivity": 0.5}], prod=False)
    ore = timed(rec("t:mine", "drill", [], [("b:ore", 1)]), 1, tiers(("Drill", 1, 1, 0)))
    g = graph(cast, belt, ore)
    plate = by_item(solve(g, NAMES, "b:plate", 3))
    assert abs(plate["b:plate"]["productivity"] - 0.5) < 1e-9 and plate["b:plate"]["base_productivity"] == 0.5
    assert abs(plate["b:ore"]["rate"] - 2) < 1e-9                         # 3 plates from 2 ore at +50%
    assert by_item(solve(g, NAMES, "b:gear", 3))["b:ore"]["rate"] == 3   # recipe does not allow productivity
    prod = Module("Prod", {"productivity": 0.1})
    both = by_item(solve(g, NAMES, "b:plate", 3, Settings(module="Prod", modules={cast.machine.id: {"Prod": prod}})))
    assert abs(both["b:plate"]["productivity"] - 0.9) < 1e-9 and abs(both["b:ore"]["rate"] - 3 / 1.9) < 1e-9
