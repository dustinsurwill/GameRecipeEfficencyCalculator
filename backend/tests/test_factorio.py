"""Factorio recipe conversion (no database, no game install needed)."""
from app.importer.factorio import convert_recipes


def convert(*raw):
    return convert_recipes([{"type": "recipe", "__mod": "base", **r} for r in raw], lambda name: "base")


def sig(slots):
    return sorted((s.item, round(s.count, 4), s.fluid) for s in slots)


def test_plain_crafting_recipe():
    [r] = convert({"name": "iron-gear-wheel", "energy_required": 0.5,
                   "ingredients": [{"type": "item", "name": "iron-plate", "amount": 2}],
                   "results": [{"type": "item", "name": "iron-gear-wheel", "amount": 1}]})
    assert r.type == "factorio:crafting" and r.machine == "Assembling Machine"
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
