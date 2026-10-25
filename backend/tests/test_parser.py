"""Recipe parser: structure-driven handling of mod recipe types (no database)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atm10_ore_analyzer import TagIndex, parse_recipe  # noqa: E402


def parse(tmp_path, data: dict, mod="somemod"):
    root = tmp_path.resolve()
    f = root / "recipes" / mod / "r.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data))
    return parse_recipe(f, root)


def sig(slots):
    return sorted((s.tag or s.item or s.chemical, round(s.count, 4), s.fluid) for s in slots)


def test_any_pattern_and_key_recipe_is_a_grid(tmp_path):
    r = parse(tmp_path, {"type": "cucumber:shaped_no_mirror", "pattern": [" E ", "   ", " EE"],
                         "key": {"E": {"item": "ma:dye_essence"}}, "result": {"id": "dyenamics:wine_dye", "count": 6}})
    assert sig(r.inputs) == [("ma:dye_essence", 3, False)]
    assert sig(r.outputs) == [("dyenamics:wine_dye", 6, False)]


def test_tag_only_output_is_kept(tmp_path):
    r = parse(tmp_path, {"type": "cucumber:shaped_tag", "pattern": ["EE"], "key": {"E": {"item": "ma:e"}},
                         "result": {"tag": "c:ingots/electrum", "count": 4}})
    assert sig(r.outputs) == [("c:ingots/electrum", 4, False)]


def test_base_predicate_and_nested_result_with_probability(tmp_path):
    r = parse(tmp_path, {"type": "immersiveengineering:arc_furnace", "input": {"item": "a:gem"},
                         "additives": [{"item": "a:gem"}], "results": [{"basePredicate": {"item": "m:ingot"}, "count": 4}]})
    assert sig(r.outputs) == [("m:ingot", 4, False)]
    r = parse(tmp_path, {"type": "railcraft:crusher", "ingredient": {"tag": "c:ores/iron"}, "outputs": [
        {"count": 2, "probability": 1.0, "result": {"tag": "c:dusts/iron"}},
        {"probability": 0.1, "result": {"tag": "c:dusts/nickel"}}]})
    assert sig(r.outputs) == [("c:dusts/iron", 2, False), ("c:dusts/nickel", 0.1, False)]


def test_secondary_output_is_scaled_by_its_chance(tmp_path):
    r = parse(tmp_path, {"type": "mekanism:sawing", "input": {"tag": "minecraft:planks"},
                         "main_output": {"id": "minecraft:stick", "count": 6}, "secondary_chance": 0.25,
                         "secondary_output": {"id": "mekanism:sawdust", "count": 1}})
    assert sig(r.outputs) == [("mekanism:sawdust", 0.25, False), ("minecraft:stick", 6, False)]


def test_casting_fluid_in_item_out_and_cast_is_a_catalyst(tmp_path):
    r = parse(tmp_path, {"type": "productivemetalworks:item_casting", "cast": {"item": "pm:gem_cast"},
                         "consume_cast": False, "fluid": {"amount": 100, "fluid": "oritech:molten_fluxite"},
                         "result": {"count": 1, "id": "oritech:fluxite"}})
    assert r.inputs == []                                  # nothing the route graph would follow
    assert sig(r.aux_inputs) == [("oritech:molten_fluxite", 100, True), ("pm:gem_cast", 1, False)]
    assert sig(r.outputs) == [("oritech:fluxite", 1, False)]
    r = parse(tmp_path, {"type": "x:casting", "cast": {"item": "pm:cast"}, "consume_cast": True,
                         "fluid": {"amount": 1, "fluid": "a:f"}, "result": {"id": "a:i"}})
    assert sig(r.inputs) == [("pm:cast", 1, False)]


def test_fluid_key_is_the_product_when_nothing_else_is(tmp_path):
    r = parse(tmp_path, {"type": "immersiveengineering:fermenter", "input": {"item": "minecraft:glow_berries"},
                         "fluid": {"amount": 100, "id": "ie:ethanol"}})
    assert sig(r.inputs) == [("minecraft:glow_berries", 1, False)]
    assert sig(r.outputs) == [("ie:ethanol", 100, True)]


def test_named_slot_dicts_and_empty_fluid_placeholders(tmp_path):
    r = parse(tmp_path, {"type": "ae2:inscriber", "ingredients": {"top": {"item": "ae2:press"}, "middle": {"item": "minecraft:gold_ingot"}},
                         "result": {"id": "ae2:printed", "count": 1}})
    assert sig(r.inputs) == [("ae2:press", 1, False), ("minecraft:gold_ingot", 1, False)]
    r = parse(tmp_path, {"type": "oritech:deep_drill", "ingredients": [{"item": "o:node"}], "fluidInput": {"fluid": "minecraft:empty", "amount": 0},
                         "results": [{"id": "minecraft:diamond", "count": 1}]})
    assert r.aux_inputs == []


def test_dynamic_ingredients_are_skipped_but_mystical_essence_is_named(tmp_path):
    r = parse(tmp_path, {"type": "mysticalagriculture:infusion", "ingredients": [
        {"tag": "c:dusts/x"}, {"type": "mysticalagriculture:crop_component", "component": "essence", "crop": "mysticalagriculture:x"},
        {"type": "mod:something_dynamic", "foo": "bar"}], "result": {"id": "mysticalagriculture:x_seeds"}})
    assert sig(r.inputs) == [("c:dusts/x", 1, False), ("mysticalagriculture:x_essence", 1, False)]


def test_non_item_recipes_are_not_parsed(tmp_path):
    assert parse(tmp_path, {"type": "botanypots:block_derived_soil", "block": "minecraft:dirt", "growth_modifier": 0.1}) is None
    assert parse(tmp_path, {"type": "ars_nouveau:enchantment", "enchantment": "x:y", "level": 1, "pedestalItems": []}) is None


def test_botanypots_crop_expands_item_drops_only(tmp_path):
    r = parse(tmp_path, {"type": "botanypots:crop", "input": {"item": "a:seed"}, "soil": {"tag": "botanypots:soil/dirt"}, "grow_time": 1200,
                         "drops": [{"type": "botanypots:loot_table", "table_id": "x"},
                                   {"type": "botanypots:items", "items": [{"result": {"id": "a:fruit", "count": 2}, "chance": 0.5}]}]})
    assert sig(r.outputs) == [("a:fruit", 1, False)]


def test_canonical_item_for_a_tag_prefers_known_namespaces(tmp_path):
    d = tmp_path / "item" / "c" / "dusts"
    d.mkdir(parents=True)
    (d / "iron.json").write_text(json.dumps(["zzmod:iron_dust", "alltheores:iron_dust", "aamod:iron_dust"]))
    ix = TagIndex(tmp_path)
    assert ix.canonical_item("c:dusts/iron") == "alltheores:iron_dust"
    assert ix.canonical_item("c:nope") is None
