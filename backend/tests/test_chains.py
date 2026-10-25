"""Route search: exclusions, ranking and iterative deepening (pure graph, no database)."""
import uuid

from app.api.v1.routes.chains import (
    _backward_dfs, _deepen, _Excluder, _forward_dfs, _MachineInfo, _rank_backward, _rank_forward,
    _Recipe, _RecipeGraph, _Slot,
)


def rec(rtype, machine, ins, outs, key=None):
    r = _Recipe(id=uuid.uuid4(), recipe_type=rtype, machine=_MachineInfo(machine, machine),
                import_key=key or str(uuid.uuid4()), time_ticks=None, energy_rf=None)
    r.inputs = [_Slot(i, None, q, False) for i, q in ins]
    r.outputs = [_Slot(o, None, q, False) for o, q in outs]
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


def test_forward_ranking_prefers_fewer_steps():
    one = rec("t:one", "m1", [("s:a", 1)], [("t:z", 1)])
    s1 = rec("t:s1", "m2", [("s:a", 1)], [("s:b", 1)])
    s2 = rec("t:s2", "m3", [("s:b", 1)], [("t:z", 3)])
    g = graph(one, s1, s2)
    chains, _, _ = _deepen(lambda d, b: _forward_dfs(["s:a"], {"t:z"}, g, _Excluder(), d, False, b), 4)
    ranked = _rank_forward(chains, {"t:z"})
    assert [len(c[0]) for c in ranked] == [1, 2]
