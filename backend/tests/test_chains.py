"""Route search: exclusions, ranking and iterative deepening (pure graph, no database)."""
import uuid

from app.api.v1.routes.chains import (
    _backward_dfs, _Budget, _deepen, _Excluder, _forward_dfs, _link_inputs, _MachineInfo, _rank_backward,
    _rank_forward, _Recipe, _RecipeGraph, _Slot,
)


def rec(rtype, machine, ins, outs, key=None, aux=()):
    """ids starting with 'fluid:' are fluid slots; `aux` entries are (id, qty) non-fluid catalysts."""
    r = _Recipe(id=uuid.uuid4(), recipe_type=rtype, machine=_MachineInfo(machine, machine),
                import_key=key or str(uuid.uuid4()), time_ticks=None, energy_rf=None)
    r.inputs = [_Slot(i, None, q, i.startswith("fluid:")) for i, q in ins]
    r.outputs = [_Slot(o, None, q, o.startswith("fluid:")) for o, q in outs]
    r.aux_inputs = [_Slot(i, None, q, False) for i, q in aux]
    _link_inputs(r)
    return r


def graph(*recipes):
    return _RecipeGraph(list(recipes), {})


def back(g, target, excl, depth=6):
    chains, _, _ = _deepen(lambda d, b: _backward_dfs([target], g, excl, d, False, b), depth)
    return _rank_backward(chains)


def test_exclusions_by_type_machine_item_and_mod():
    r = rec("mysticalagriculture:reprocessor", "m1", [("mysticalagriculture:seed", 2)], [("minecraft:iron_ingot", 1)])
    assert not _Excluder().blocks(r)
    assert _Excluder(types=["mysticalagriculture"]).blocks(r)
    assert _Excluder(types=["mysticalagriculture:reprocessor"]).blocks(r)
    assert not _Excluder(types=["mysticalagriculture:other"]).blocks(r)
    assert _Excluder(machines=["m1"]).blocks(r)
    assert _Excluder(items=["mysticalagriculture:seed"]).blocks(r)       # any slot, here an input
    assert _Excluder(items=["minecraft:iron_ingot"]).blocks(r)           # ... or an output
    assert _Excluder(mods=["mysticalagriculture"]).blocks(r)
    assert not _Excluder(mods=["mekanism"]).blocks(r)
    # start/target items are exempt, otherwise skipping a mod would make its own items unreachable
    assert not _Excluder(items=["mysticalagriculture:seed"], keep={"mysticalagriculture:seed"}).blocks(r)


def test_excluded_machine_drops_the_route():
    reproc = rec("ma:reprocessor", "reproc", [("ma:seed", 1)], [("ma:essence", 1)])
    craft = rec("minecraft:crafting_shapeless", "craft", [("ma:essence", 1)], [("minecraft:diamond", 1)])
    drill = rec("oritech:drill", "drill", [("oritech:node", 1)], [("minecraft:diamond", 1)])
    g = graph(reproc, craft, drill)
    assert len(back(g, "minecraft:diamond", _Excluder())) == 2
    only = back(g, "minecraft:diamond", _Excluder(machines=["reproc"]))
    # the craft route now bottoms out at 'ma:essence' as if it were raw; the drill route is still there
    assert {c[3] for c in only} == {"ma:essence", "oritech:node"}
    only = back(g, "minecraft:diamond", _Excluder(mods=["ma"]))
    assert [c[3] for c in only] == ["oritech:node"]


def test_fewer_steps_beat_a_tiny_fraction_and_trivial_routes_are_dropped():
    # long route with a huge multiplier (0.01 raw per item) vs a plain 1-step route
    a = rec("t:a", "ma", [("raw:x", 1)], [("mid:1", 100)])
    b = rec("t:b", "mb", [("mid:1", 1)], [("mid:2", 1)])
    c = rec("t:c", "mc", [("mid:2", 1)], [("goal:g", 1)])
    d = rec("t:d", "md", [("raw:y", 2)], [("goal:g", 1)])
    ranked = back(graph(a, b, c, d), "goal:g", _Excluder())
    assert [c[3] for c in ranked] == ["raw:y", "raw:x"]
    assert all(chain for chain, *_ in ranked)
    assert back(graph(d), "raw:y", _Excluder()) == []        # nothing makes it: no zero-step 'route'


def test_same_machines_and_start_collapse_to_one_route():
    r1 = rec("minecraft:crafting_shaped", "craft", [("raw:x", 4)], [("goal:g", 1)])
    r2 = rec("minecraft:crafting_shaped", "craft", [("raw:x", 1)], [("goal:g", 1)])
    ranked = back(graph(r1, r2), "goal:g", _Excluder())
    assert len(ranked) == 1 and ranked[0][4] == 1            # the cheaper layout wins


def test_deepening_finds_short_routes_when_deep_search_would_blow_the_budget():
    # a wide, deep fan-out hanging off 'goal:g' plus one direct recipe
    recs = [rec("t:direct", "d", [("raw:x", 1)], [("goal:g", 1)])]
    prev = "goal:g"
    for i in range(8):
        for j in range(6):
            recs.append(rec(f"t:n{i}{j}", f"m{i}{j}", [(f"n{i + 1}:{j}", 1)], [(prev, 1)]))
        prev = "n%d:0" % (i + 1)
    ranked = back(graph(*recs), "goal:g", _Excluder(), depth=8)
    assert ranked and len(ranked[0][0]) == 1


def test_forward_ranking_modes():
    one = rec("t:one", "m1", [("s:a", 1)], [("t:z", 1)])
    s1 = rec("t:s1", "m2", [("s:a", 1)], [("s:b", 1)])
    s2 = rec("t:s2", "m3", [("s:b", 1)], [("t:z", 3)])
    g = graph(one, s1, s2)
    chains, _, _ = _deepen(lambda d, b: _forward_dfs(["s:a"], {"t:z"}, g, _Excluder(), d, False, b), 4)
    assert [len(c[0]) for c in _rank_forward(chains, {"t:z"}, by_yield=False)] == [1, 2]
    # ore processing: a longer route with a better yield comes first when ranking by yield
    assert [len(c[0]) for c in _rank_forward(chains, {"t:z"}, by_yield=True)] == [2, 1]


def test_recipe_using_the_item_in_two_slots_is_charged_for_both():
    mix = rec("t:mix", "mix", [("s:gem", 1), ("s:gem", 1)], [("t:z", 3)])
    chains, _, _ = _deepen(lambda d, b: _forward_dfs(["s:gem"], {"t:z"}, graph(mix), _Excluder(), d, False, b), 2)
    (chain, *_), = _rank_forward(chains, {"t:z"})
    assert chain[0][1] == 0.5                      # 1 gem = half a run (not a full run)
    assert chain[0][0].outputs[0].quantity * chain[0][1] == 1.5


def test_routes_needing_other_items_rank_below_self_contained_ones_in_yield_mode():
    pure = rec("t:p", "mp", [("s:a", 1)], [("t:z", 2)])
    side = rec("t:s", "ms", [("s:a", 1), ("other:x", 1)], [("t:z", 5)])
    g = graph(pure, side)
    chains, _, _ = _deepen(lambda d, b: _forward_dfs(["s:a"], {"t:z"}, g, _Excluder(), d, False, b), 2)
    ranked = _rank_forward(chains, {"t:z"}, by_yield=True)
    assert [c[0][0][0].machine.id for c in ranked] == ["mp", "ms"]


def test_a_recipe_with_nothing_to_follow_makes_its_output_a_starting_point():
    spawn = rec("t:spawn", "spawner", [], [("goal:g", 1)])
    use = rec("t:use", "m", [("goal:g", 1)], [("final:f", 1)])
    ranked = back(graph(spawn, use), "final:f", _Excluder())
    assert [(len(c[0]), c[3]) for c in ranked] == [(1, "goal:g")]


def melt_cast():
    melt = rec("t:melt", "melter", [("raw:ore", 1)], [("fluid:m:molten", 90)])
    cast = rec("t:cast", "caster", [("fluid:m:molten", 90)], [("goal:ingot", 1)], aux=[("m:ingot_cast", 1)])
    return melt, cast


def test_routes_follow_fluids_forward_and_backward():
    g = graph(*melt_cast())
    ranked = back(g, "goal:ingot", _Excluder())
    assert [(r.machine.id for r, _ in c[0]) and [r.machine.id for r, _ in c[0]] for c in ranked] == [["melter", "caster"]]
    assert ranked[0][3] == "raw:ore" and ranked[0][4] == 1                  # 1 ore -> 90 mB -> 1 ingot
    chains, _, _ = _deepen(lambda d, b: _forward_dfs(["raw:ore"], {"goal:ingot"}, g, _Excluder(), d, False, b), 4)
    (chain, *_), = _rank_forward(chains, {"goal:ingot"})
    assert [r.machine.id for r, _ in chain] == ["melter", "caster"]


def test_catalysts_and_water_are_not_followed():
    melt, cast = melt_cast()
    assert [g.item_id for g in cast.gin] == ["fluid:m:molten"]               # the non-consumed cast is not an input
    wash = rec("t:wash", "washer", [("raw:ore", 1), ("fluid:minecraft:water", 1000)], [("goal:clean", 1)])
    assert [g.item_id for g in wash.gin] == ["raw:ore"]
    ranked = back(graph(wash), "goal:clean", _Excluder())
    assert [c[3] for c in ranked] == ["raw:ore"]                             # not 'water'


def test_excluding_a_mod_also_blocks_its_fluids():
    melt, cast = melt_cast()
    assert _Excluder(mods=["m"]).blocks(melt) and _Excluder(mods=["m"]).blocks(cast)
    assert _Excluder(items=["fluid:m:molten"]).blocks(cast)


def test_fluid_side_inputs_flag_a_route_but_chemicals_do_not():
    from app.api.v1.routes.chains import _has_side_inputs
    cast = rec("t:c", "caster", [("raw:redstone", 1), ("fluid:m:molten", 960)], [("goal:g", 1)])
    assert _has_side_inputs([(cast, 1.0)])
    wash = rec("t:w", "washer", [("raw:ore", 1), ("chemical:mekanism:hcl", 1)], [("goal:g", 1)])
    assert not _has_side_inputs([(wash, 1.0)])
    melt, cast_only = melt_cast()
    assert not _has_side_inputs([(melt, 1.0), (cast_only, 1.0)])


def test_forward_search_prunes_branches_that_cannot_reach_the_target():
    from app.api.v1.routes.chains import _steps_to
    # a 6-step chain to the target, plus a wide dead-end tree hanging off the start item
    chain = [rec(f"t:{i}", f"m{i}", [(f"c:{i}", 1)], [(f"c:{i + 1}", 1)]) for i in range(6)]
    junk = [rec("t:j", f"j{i}", [("c:0", 1)], [(f"junk:{i}", 1)]) for i in range(30)]
    junk += [rec("t:jj", f"jj{i}", [(f"junk:{i % 30}", 1)], [(f"junk2:{i}", 1)]) for i in range(300)]
    g = graph(*chain, *junk)
    steps = _steps_to({"c:6"}, g, _Excluder())
    assert steps["c:0"] == 6 and "junk:1" not in steps
    visits = []
    for pruned in (None, steps):
        b = _Budget()
        found, _ = _forward_dfs(["c:0"], {"c:6"}, g, _Excluder(), 6, False, b, pruned)
        assert len(found) == 1
        visits.append(b.left)
    assert visits[1] > visits[0]      # fewer nodes spent when pruned
