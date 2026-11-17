"""Factorio recipe conversion (no database, no game install needed)."""
from app.importer.factorio import convert_recipes, mining_recipes, speed_tiers


def convert(*raw):
    return convert_recipes([{"type": "recipe", "__mod": "base", **r} for r in raw], lambda name: "base")


def sig(slots):
    return sorted((s.item, round(s.count, 4), s.fluid) for s in slots)


def test_plain_crafting_recipe():
    [r] = convert({"name": "iron-gear-wheel", "energy_required": 0.5,
                   "ingredients": [{"type": "item", "name": "iron-plate", "amount": 2}],
                   "results": [{"type": "item", "name": "iron-gear-wheel", "amount": 1}]})
    assert r.type == "base:crafting" and r.machine == "Assembling Machine"
    assert r.time_ticks == 30
    assert sig(r.inputs) == [("base:iron-plate", 2, False)]
    assert sig(r.outputs) == [("base:iron-gear-wheel", 1, False)]


def test_fluids_are_aux_inputs_and_fluid_outputs():
    [r] = convert({"name": "advanced-oil-processing", "categories": ["oil-processing"], "energy_required": 5,
                   "ingredients": [{"type": "fluid", "name": "water", "amount": 50},
                                   {"type": "fluid", "name": "crude-oil", "amount": 100}],
                   "results": [{"type": "fluid", "name": "heavy-oil", "amount": 25}]})
    assert r.machine == "Oil Refinery"
    assert r.inputs == []
    assert sig(r.aux_inputs) == [("base:crude-oil", 100, True), ("base:water", 50, True)]
    assert sig(r.outputs) == [("base:heavy-oil", 25, True)]


def test_catalyst_is_cancelled_kovarex():
    [r] = convert({"name": "kovarex", "categories": ["centrifuging"],
                   "ingredients": [{"type": "item", "name": "uranium-235", "amount": 40, "ignored_by_stats": 40},
                                   {"type": "item", "name": "uranium-238", "amount": 5, "ignored_by_stats": 2}],
                   "results": [{"type": "item", "name": "uranium-235", "amount": 41, "ignored_by_stats": 40},
                               {"type": "item", "name": "uranium-238", "amount": 2, "ignored_by_stats": 2}]})
    assert sig(r.inputs) == [("base:uranium-238", 3, False)]
    assert sig(r.outputs) == [("base:uranium-235", 1, False)]


def test_chances_and_ranges_are_expected_values():
    [r] = convert({"name": "x", "ingredients": [{"type": "item", "name": "in", "amount": 1}],
                   "results": [{"type": "item", "name": "a", "amount": 2, "independent_probability": 0.25},
                               {"type": "item", "name": "b", "amount": 1, "shared_probability": {"min": 0.4, "max": 0.6}},
                               {"type": "item", "name": "c", "amount_min": 1, "amount_max": 3}]})
    assert sig(r.outputs) == [("base:a", 0.5, False), ("base:b", 0.2, False), ("base:c", 2, False)]


def test_chance_catalyst_is_not_cancelled():
    [r] = convert({"name": "reprocess", "categories": ["crushing"],
                   "ingredients": [{"type": "item", "name": "chunk", "amount": 1, "ignored_by_stats": 1}],
                   "results": [{"type": "item", "name": "chunk", "amount": 1, "ignored_by_stats": 1,
                                "shared_probability": {"min": 0, "max": 0.4}}]})
    assert sig(r.inputs) == [("base:chunk", 1, False)]
    assert sig(r.outputs) == [("base:chunk", 0.4, False)]


def test_skips_parameter_hidden_and_empty_recipes():
    assert convert(
        {"name": "parameter-0", "parameter": True},
        {"name": "loader", "hidden": True, "ingredients": [{"type": "item", "name": "a", "amount": 1}],
         "results": [{"type": "item", "name": "b", "amount": 1}]},
        {"name": "egg", "results": [{"type": "item", "name": "biter-egg", "amount": 1}]},
    ) == []


def test_recipe_craftable_in_two_machines_is_stored_once_per_machine():
    # Space Age adds the electromagnetics category to circuits: assembling machine OR electromagnetic plant
    rs = convert({"name": "electronic-circuit", "categories": ["crafting", "electromagnetics"],
                  "ingredients": [{"type": "item", "name": "iron-plate", "amount": 1}],
                  "results": [{"type": "item", "name": "electronic-circuit", "amount": 1}]})
    assert [(r.machine, r.type, r.id.rsplit("/", 1)[-1]) for r in rs] == [
        ("Assembling Machine", "base:crafting", "electronic-circuit"),
        ("Electromagnetic Plant", "base:electromagnetics", "electronic-circuit@electromagnetics"),
    ]


def test_several_categories_of_one_machine_collapse_and_hand_crafting_is_ignored():
    rs = convert({"name": "x", "categories": ["crafting", "advanced-crafting", "hand-crafting"],
                  "ingredients": [{"type": "item", "name": "a", "amount": 1}],
                  "results": [{"type": "item", "name": "b", "amount": 1}]})
    assert [r.machine for r in rs] == ["Assembling Machine"]


def test_mining_pumping_and_fluid_requirements():
    owner = lambda name: "base"  # noqa: E731
    resources = [
        {"name": "iron-ore", "__mod": "base", "minable": {"result": "iron-ore", "mining_time": 1}},
        {"name": "uranium-ore", "__mod": "base", "minable": {"result": "uranium-ore", "mining_time": 2,
                                                              "required_fluid": "sulfuric-acid", "fluid_amount": 10}},
        {"name": "crude-oil", "__mod": "base", "category": "basic-fluid",
         "minable": {"mining_time": 1, "results": [{"type": "fluid", "name": "crude-oil", "amount": 10}]}},
        {"name": "decoration", "__mod": "base", "minable": {}},
    ]
    tiles = [{"name": "water", "fluid": "water"}, {"name": "deepwater", "fluid": "water"},
             {"name": "lava", "fluid": "lava"}, {"name": "grass"}]
    rs = {r.id.rsplit("/", 1)[-1]: r for r in mining_recipes(resources, tiles, "base", owner)}
    assert sorted(rs) == ["crude-oil", "iron-ore", "lava", "uranium-ore", "water"]   # one pump recipe per fluid
    assert (rs["iron-ore"].machine, rs["iron-ore"].time_ticks, rs["iron-ore"].inputs) == ("Mining Drill", 60, [])
    assert sig(rs["uranium-ore"].aux_inputs) == [("base:sulfuric-acid", 10, True)]
    assert rs["uranium-ore"].time_ticks == 120
    assert (rs["crude-oil"].machine, sig(rs["crude-oil"].outputs)) == ("Pumpjack", [("base:crude-oil", 10, True)])
    assert (rs["water"].machine, rs["water"].type) == ("Offshore Pump", "base:offshore-pumping")


def test_speed_tiers_follow_categories_and_sort_slowest_first():
    data = {
        "assembling-machine": [
            {"name": "assembling-machine-3", "__mod": "base", "crafting_speed": 1.25, "crafting_categories": ["crafting"]},
            {"name": "assembling-machine-1", "__mod": "base", "crafting_speed": 0.5, "crafting_categories": ["crafting"]},
            {"name": "electromagnetic-plant", "__mod": "space-age", "crafting_speed": 2, "crafting_categories": ["electromagnetics"]},
        ],
        "mining-drill": [{"name": "pumpjack", "__mod": "base", "mining_speed": 1, "resource_categories": ["basic-fluid"]}],
    }
    tiers = speed_tiers(data, {"assembling-machine-1": "Assembling machine 1"})
    assert [(t["name"], t["speed"]) for t in tiers["Assembling Machine"]] == [("Assembling machine 1", 0.5), ("Assembling machine 3", 1.25)]
    assert tiers["Electromagnetic Plant"][0]["mod"] == "space-age"
    assert tiers["Pumpjack"][0]["categories"] == ["mining-basic-fluid"]
