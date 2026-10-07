"""Unit tests for area_derivation() and manual verification calculations.
Verifies:
1. Carpet area sum across units and floors.
2. Balcony area sum across units and floors.
3. Service core empirical formulas (corridor W*L, stairs W^2 * 2.6, lifts 4.5 m2).
4. Wall allowance calculation ((carpet + balcony) * wall_factor).
5. Built-up area per floor and per tower reconciliation.
6. Common area loading factor (+25%) on tower built-up.
7. Society amenities addition (clubhouse, pool, etc.).
8. Implied multiplier = total super built-up / total built-up.
"""
import pytest
import engine
import defaults


def test_area_derivation_structure():
    proj = defaults.default_project("Test", "Bengaluru", "Karnataka", "T1", "user1")
    an = engine.analyse(proj)
    assert "area_derivation" in an
    d = an["area_derivation"]
    assert "summary" in d
    assert "step_by_step_formulas" in d
    assert "towers" in d
    assert "society_amenities" in d
    assert len(d["step_by_step_formulas"]) == 8


def test_carpet_and_balcony_arithmetic():
    proj = defaults.default_project("Test", "Bengaluru", "Karnataka", "T1", "user1")
    an = engine.analyse(proj)
    d = an["area_derivation"]
    
    for t in d["towers"]:
        raw_t = next(rt for rt in proj["towers"] if rt["id"] == t["id"])
        expected_carpet_fl = sum(float(u.get("carpet_area", 0)) * int(u.get("count", 0)) for u in raw_t.get("units", []))
        expected_balcony_fl = sum(float(u.get("balcony_area", 0)) * int(u.get("count", 0)) for u in raw_t.get("units", []))
        assert t["carpet_per_floor_sqm"] == pytest.approx(expected_carpet_fl, 0.01)
        assert t["balcony_per_floor_sqm"] == pytest.approx(expected_balcony_fl, 0.01)


def test_service_core_empirical_formulas():
    proj = defaults.default_project("Test", "Bengaluru", "Karnataka", "T1", "user1")
    an = engine.analyse(proj)
    d = an["area_derivation"]

    for t in d["towers"]:
        raw_t = next(rt for rt in proj["towers"] if rt["id"] == t["id"])
        cw = float(raw_t.get("corridor_width", 0))
        cl = float(raw_t.get("corridor_length", 0))
        expected_corridor = cw * cl

        stairs = raw_t.get("staircases", [])
        expected_stairs = sum(float(s.get("width", 0)) ** 2 * 2.6 * int(s.get("count", 0)) for s in stairs)

        lifts = raw_t.get("lifts", [])
        expected_lifts = sum(int(l.get("count", 0)) * 4.5 for l in lifts)

        sc = t["service_core"]
        assert sc["corridor_sqm"] == pytest.approx(expected_corridor, 0.01)
        assert sc["stair_sqm"] == pytest.approx(expected_stairs, 0.01)
        assert sc["lift_sqm"] == pytest.approx(expected_lifts, 0.01)
        assert sc["total_floor_sqm"] == pytest.approx(expected_corridor + expected_stairs + expected_lifts, 0.01)


def test_wall_allowance_and_builtup_reconciliation():
    proj = defaults.default_project("Test", "Bengaluru", "Karnataka", "T1", "user1")
    an = engine.analyse(proj)
    d = an["area_derivation"]
    wall_factor = float(proj.get("config", {}).get("wall_thickness_factor", 0.10))

    for t in d["towers"]:
        carpet = t["carpet_per_floor_sqm"]
        balcony = t["balcony_per_floor_sqm"]
        expected_wall = (carpet + balcony) * wall_factor
        assert t["wall_allowance_floor_sqm"] == pytest.approx(expected_wall, 0.01)

        core = t["service_core"]["total_floor_sqm"]
        expected_bu_floor = carpet + balcony + expected_wall + core
        assert t["builtup_per_floor_sqm"] == pytest.approx(expected_bu_floor, 0.01)
        assert t["builtup_sqm"] == pytest.approx(expected_bu_floor * t["floors"], 0.01)


def test_super_builtup_and_implied_multiplier():
    proj = defaults.default_project("Test", "Bengaluru", "Karnataka", "T1", "user1")
    an = engine.analyse(proj)
    d = an["area_derivation"]
    s = d["summary"]
    loading = float(proj.get("config", {}).get("common_area_loading", 0.25))

    total_tower_bu = sum(t["builtup_sqm"] for t in d["towers"])
    total_tower_super = sum(t["super_builtup_sqm"] for t in d["towers"])
    amenities = s["society_amenities_sqm"]

    assert total_tower_bu == pytest.approx(s["builtup_area_sqm"], 0.05)
    # The loading applies to the homes' own built-up (carpet + balcony + walls), because
    # the loading IS each home's share of the core and the amenities.
    for t in d["towers"]:
        assert t["super_builtup_sqm"] == pytest.approx(t["apartment_builtup_sqm"] * (1 + loading), 0.05)

    # Amenities are recovered through the loading, not added a second time.
    assert s["super_builtup_area_sqm"] == pytest.approx(total_tower_super, 0.05)
    assert amenities > 0

    expected_implied_mult = round(s["super_builtup_area_sqm"] / s["builtup_area_sqm"], 4)
    assert s["implied_multiplier"] == pytest.approx(expected_implied_mult, 0.0001)
    # What is sold never exceeds what is built.
    assert s["super_builtup_area_sqm"] <= s["constructed_area_sqm"] + 0.01
    assert s["super_builtup_exceeds_constructed"] is False
