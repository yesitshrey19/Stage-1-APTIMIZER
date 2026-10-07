"""Every new payload must survive strict JSON, on healthy AND degenerate projects.

The failure this guards is specific and nasty: Python happily produces float('nan') and
float('inf') from a division, json.dumps writes them as bare NaN / Infinity, and that is
not valid JSON. The browser's JSON.parse throws, the module renders nothing, and the
error surfaces nowhere near the division that caused it.

The degenerate projects matter more than the healthy one. A project with no towers, no
units or no plot is what a real user has thirty seconds after clicking "new project", and
that is exactly when every denominator in the codebase is zero.
"""
import sys, os, copy, json, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, engineering as E, finance as F, optimise as O, planopt as P
import aptcontext as A, takeoff as T
from defaults import default_project


def strict(payload, label):
    """json.dumps rejecting NaN/Infinity -- exactly what a browser would choke on."""
    try:
        json.dumps(payload, allow_nan=False)
    except ValueError as exc:
        pytest.fail(f"{label}: not valid JSON -- {exc}")


def find_bad(v, path="$"):
    """Every non-finite number in a payload, with the path that reaches it."""
    bad = []
    if isinstance(v, float) and not math.isfinite(v):
        bad.append((path, v))
    elif isinstance(v, dict):
        for k, x in v.items():
            bad += find_bad(x, f"{path}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            bad += find_bad(x, f"{path}[{i}]")
    return bad


def variants():
    """A healthy project and the degenerate shapes a real one passes through."""
    base = default_project("T", "C", "Hyderabad", "S", "o")

    no_towers = copy.deepcopy(base)
    no_towers["towers"] = []

    no_units = copy.deepcopy(base)
    for t in no_units["towers"]:
        t["units"] = []

    one_floor = copy.deepcopy(base)
    for t in one_floor["towers"]:
        t["floors"] = 1

    no_plot = copy.deepcopy(base)
    no_plot["plot"] = {**(no_plot.get("plot") or {}), "coordinates": [],
                       "length": 0, "width": 0}

    single_type = copy.deepcopy(base)
    for t in single_type["towers"]:
        t["units"] = t["units"][:1]

    return [("healthy", base), ("no towers", no_towers), ("no units", no_units),
            ("one floor", one_floor), ("no plot", no_plot), ("single unit type", single_type)]


@pytest.mark.parametrize("label,proj", variants())
def test_every_payload_is_strict_json_and_finite(label, proj):
    an = engine.analyse(proj)
    eng = E.analyse_engineering(proj, an)

    payloads = {
        "engine.analyse": an,
        "analyse_engineering": eng,
        "finance": F.analyse(proj, an, {"sale_rate_per_sqft": 6500}),
        "optimise": O.analyse(proj, an, eng),
        "planopt": P.analyse(proj, an),
        "apt_context": A.build(proj, an, eng),
    }
    for name, payload in payloads.items():
        bad = find_bad(payload)
        assert not bad, f"{label} / {name}: non-finite at {bad[:5]}"
        strict(payload, f"{label} / {name}")


@pytest.mark.parametrize("label,proj", variants())
def test_optimisers_hold_their_contract_on_degenerate_projects(label, proj):
    """current/best/changes must survive a project with nothing in it -- that is when a
    divide-by-zero or an empty max() would otherwise take the whole module down."""
    an = engine.analyse(proj)
    eng = E.analyse_engineering(proj, an)
    for group in (O.analyse(proj, an, eng), P.analyse(proj, an)):
        for key, o in group.items():
            if not isinstance(o, dict) or "current" not in o:
                continue
            assert set(o["current"]) >= {"value", "unit", "label"}, f"{label}/{key}"
            assert set(o["best"]) >= {"value", "unit", "label"}, f"{label}/{key}"
            assert isinstance(o["changes"], list), f"{label}/{key}"
            assert isinstance(o["delta"]["pct"], (int, float)), f"{label}/{key}"


@pytest.mark.parametrize("label,proj", variants())
def test_apt_context_stays_in_budget_on_every_shape(label, proj):
    an = engine.analyse(proj)
    eng = E.analyse_engineering(proj, an)
    chars = len(json.dumps(A.build(proj, an, eng)))
    assert chars // 4 < 10_000, f"{label}: {chars // 4} tokens"


def test_beam_layout_is_strict_json_for_odd_grids():
    for bx, by, nx, ny in ((6, 4.5, 4, 3), (5, 5, 1, 1), (5, 5, 0, 0), (12.5, 3, 2, 9)):
        r = T.beam_layout(bx, by, nx, ny)
        assert not find_bad(r), (bx, by, nx, ny)
        strict(r, f"beam_layout {bx}x{by} {nx}x{ny}")


def test_a_zero_rate_project_does_not_divide_by_zero():
    """Every rate zeroed is what an imported project looks like before rates are set."""
    proj = default_project("T", "C", "Hyderabad", "S", "o")
    proj["rates"] = {k: 0 for k in (proj.get("rates") or {})}
    proj["labour_rates"] = {k: 0 for k in (proj.get("labour_rates") or {})}
    an = engine.analyse(proj)
    eng = E.analyse_engineering(proj, an)
    for name, payload in (("optimise", O.analyse(proj, an, eng)),
                          ("planopt", P.analyse(proj, an)),
                          ("finance", F.analyse(proj, an, {}))):
        assert not find_bad(payload), name
        strict(payload, name)
