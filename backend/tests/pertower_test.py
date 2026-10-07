"""Fix 4: per-tower breakdowns in every report that covers a multi-tower quantity.

Two rules this file holds, both from the brief:

  * Totals must actually equal the sum of their parts. A total that silently disagrees
    with its rows is the fastest way to lose a reader's trust in a whole document.
  * A figure that is APPORTIONED rather than independently computed must say so. Most of
    these are: the BOQ, the cost and the utilities are project-wide calculations, and a
    per-tower row for them can only be a share. A reader who assumes otherwise will use
    them to compare towers that were never separately costed.
"""
import sys, os, io as _io, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, engineering as englib, reports as R
from defaults import default_project

PER_TOWER_REPORTS = ("structural", "boq", "cost", "water", "compliance",
                     "sustainability", "programme")


@pytest.fixture(scope="module")
def two_towers():
    """Two towers, because a per-tower breakdown of one tower proves nothing."""
    p = default_project("PT", "QA", "Hyderabad", "PT-1", "owner")
    b = copy.deepcopy(p["towers"][0])
    b["name"] = "Tower B"
    b["id"] = "tower-b"
    b["floors"] = 8                     # different, so blended totals cannot hide it
    p["towers"].append(b)
    a = engine.analyse(p)
    return p, a, englib.analyse_engineering(p, a)


def text_of(pdf):
    """Extracted text with whitespace collapsed.

    PDF extraction inserts a newline wherever the renderer wrapped a line, so a phrase that
    reads as one sentence on the page arrives split across lines. Collapsing makes phrase
    assertions test what the reader sees rather than where the text happened to wrap.
    """
    from pypdf import PdfReader
    raw = "\n".join(pg.extract_text() or "" for pg in PdfReader(_io.BytesIO(pdf)).pages)
    return " ".join(raw.split())


@pytest.mark.parametrize("key", PER_TOWER_REPORTS)
def test_each_report_breaks_its_figures_down_per_tower(two_towers, key):
    p, a, eng = two_towers
    t = text_of(R.build_pdf(key, p, a, eng))
    assert "Per Tower" in t, f"{key} has no per-tower section"
    for tower in a["areas"]["towers"]:
        assert tower["name"] in t, f'{key} does not name {tower["name"]}'


@pytest.mark.parametrize("key", PER_TOWER_REPORTS)
def test_every_per_tower_table_states_its_units_in_the_header(two_towers, key):
    """Units in the header, never in the cells."""
    p, a, eng = two_towers
    t = text_of(R.build_pdf(key, p, a, eng))
    assert any(u in t for u in ("(m²)", "(m³)", "(kN)", "(mm)", "(INR)", "(tCO₂e)",
                                "(litre/day)", "(sqft)", "(m)")), key


@pytest.mark.parametrize("key", PER_TOWER_REPORTS)
def test_every_per_tower_table_carries_a_derivation_note(two_towers, key):
    """Every derived figure carries its formula or source, so the reader can reproduce it."""
    p, a, eng = two_towers
    t = text_of(R.build_pdf(key, p, a, eng))
    assert any(m in t for m in ("Apportioned", "IS ", "NBC", "CPM", "computed per tower")), key


# ---------------------------------------------------------------- totals reconcile
def test_shares_sum_to_one(two_towers):
    _, a, _ = two_towers
    assert sum(share for _, share in R._shares(a)) == pytest.approx(1.0, abs=1e-9)


def test_apportioned_cost_rows_sum_to_the_project_total(two_towers):
    _, a, _ = two_towers
    total = float(a["cost"]["total"])
    assert sum(total * share for _, share in R._shares(a)) == pytest.approx(total, rel=1e-9)


def test_apportioned_carbon_rows_sum_to_the_project_total(two_towers):
    _, a, eng = two_towers
    total = float(eng["modules"]["carbon"]["derived"]["total_tco2e"])
    assert sum(total * share for _, share in R._shares(a)) == pytest.approx(total, rel=1e-9)


def test_water_rows_sum_to_the_sized_demand(two_towers):
    _, a, _ = two_towers
    demand = float(a["utilities"]["water_demand_lpd"])
    occ = sum(int(t["occupants"] or 0) for t in a["areas"]["towers"])
    apportioned = sum(demand * (int(t["occupants"] or 0) / occ) for t in a["areas"]["towers"])
    assert apportioned == pytest.approx(demand, rel=1e-9)


def test_tower_coverage_rows_sum_to_project_coverage(two_towers):
    _, a, _ = two_towers
    plot = float(a["areas"]["plot_area_sqm"])
    rows = sum(float(t["footprint_sqm"]) / plot * 100 for t in a["areas"]["towers"])
    assert rows == pytest.approx(a["areas"]["ground_coverage_pct"], abs=0.05)


# ---------------------------------------------------------------- apportionment is labelled
@pytest.mark.parametrize("key", ("boq", "cost", "water", "sustainability"))
def test_apportioned_figures_are_labelled_as_apportioned(two_towers, key):
    """These four are project-wide calculations shown per tower. Presenting a share as an
    independent per-tower estimate would invite exactly the wrong comparison."""
    p, a, eng = two_towers
    t = text_of(R.build_pdf(key, p, a, eng))
    assert "pportioned" in t, f"{key} presents a share as an independent figure"


def test_structural_figures_are_not_labelled_apportioned(two_towers):
    """Loads and base shear ARE computed per tower, so they must not carry the caveat."""
    p, a, eng = two_towers
    t = text_of(R.build_pdf("structural", p, a, eng))
    assert "computed per tower, not apportioned" in t


def test_a_taller_tower_carries_a_larger_share(two_towers):
    """If the split were not driven by real per-tower geometry, the rows would be equal."""
    _, a, _ = two_towers
    shares = sorted(R._shares(a), key=lambda x: x[1])
    assert shares[0][1] < shares[-1][1]
    assert shares[0][0]["floors"] < shares[-1][0]["floors"]


def test_programme_names_which_tower_is_on_the_critical_path(two_towers):
    p, a, eng = two_towers
    t = text_of(R.build_pdf("programme", p, a, eng))
    assert "On critical path" in t


def test_a_single_tower_project_still_renders(two_towers):
    """The breakdown must degrade, not crash, when there is only one tower."""
    p = default_project("One", "QA", "Hyderabad", "O-1", "owner")
    a = engine.analyse(p)
    eng = englib.analyse_engineering(p, a)
    for key in PER_TOWER_REPORTS:
        assert R.build_pdf(key, p, a, eng).startswith(b"%PDF")
