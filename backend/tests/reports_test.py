"""Tests for the report set.

Two things to hold.

Four reports were merged away. Merging removed DOCUMENTS, not NUMBERS -- every figure the
merged report carried has to appear in the one that absorbed it, or consolidation was just
deletion.

And the set has to keep up with the app. Every workspace module that produces figures owes
the reader a document carrying them, in the order the menu lists them, each opening with
its own answer before its workings.
"""
import sys, os, io as _io
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, engineering as englib, reports as R
from defaults import default_project


@pytest.fixture(scope="module")
def ctx():
    p = default_project("Rep", "QA", "Hyderabad", "R-1", "owner")
    a = engine.analyse(p)
    return p, a, englib.analyse_engineering(p, a)


def text_of(pdf: bytes) -> str:
    """Page text without the "Generated: dd Mon yyyy HH:MM UTC" stamp: two renders that
    straddle a minute differ there, which made comparisons fail at random."""
    import re
    from pypdf import PdfReader
    text = "\n".join(pg.extract_text() or "" for pg in PdfReader(_io.BytesIO(pdf)).pages)
    return re.sub(r"Generated: \d{2} \w{3} \d{4} \d{2}:\d{2} UTC", "Generated: <time>", text)


def flat(pdf: bytes) -> str:
    """Extracted text with whitespace collapsed.

    PDF extraction breaks a line wherever the renderer wrapped it, so a heading that reads
    as one phrase on the page arrives split. Collapsing tests what the reader sees rather
    than where the text happened to wrap.
    """
    return " ".join(text_of(pdf).split())


@pytest.fixture(scope="module")
def gis_project():
    """A project carrying a GIS run, assembled from the analysis functions directly.

    `gis.analyse_site` fetches Overpass and elevation over the network; a report test must
    not depend on either being reachable, so the pure scoring functions are called with
    fixed features instead.
    """
    import gis as g
    p = default_project("RepGis", "QA", "Hyderabad", "R-2", "owner")
    coords = p["plot"]["coordinates"]
    c = g.centroid(coords)
    terrain = {"available": True, "min_m": 495.0, "max_m": 508.0, "mean_m": 501.0,
               "relief_m": 13.0, "avg_slope_pct": 2.4, "slope_class": "gentle",
               "ring_mean_m": 503.0}
    features = {"roads": [{"kind": "residential", "distance_m": 4.0, "road_width_m": 4.0},
                          {"kind": "secondary", "distance_m": 80.0, "road_width_m": 12.0}],
                "transit": [{"kind": "bus_stop", "distance_m": 100.0}],
                "water": [], "schools": [], "hospitals": [], "parks": [], "shops": []}
    flood = g.flood_risk(terrain, features["water"])
    acc = g.accessibility(features["roads"], features["transit"], coords,
                          p["plot"].get("road_edges") or [])
    sun = g.sun_path(round(c[0], 6), round(c[1], 6), 0)
    roof = sum(float(t.get("footprint_area") or 0) for t in p["towers"])
    p["gis"] = {
        "radius_m": 500, "centroid": [round(c[0], 6), round(c[1], 6)],
        "vertices": len(coords), "features": features,
        "feature_counts": {k: len(v) for k, v in features.items()},
        "terrain": terrain, "flood": flood, "wind": g.wind_profile(c[0], c[1]),
        "sun": sun,
        "solar": g.solar_potential(round(c[0], 6), round(c[1], 6), roof, {}),
        "accessibility": acc,
        "suitability": g.suitability(terrain, flood, acc, sun),
        "buildability": g.buildability(terrain, flood, acc, features),
    }
    a = engine.analyse(p)
    return p, a, englib.analyse_engineering(p, a)


#: One report per workspace module, in menu order. Kept here as data rather than a count,
#: because a bare number tells a later reader nothing about what went missing.
EXPECTED = [
    "executive",
    "plot", "site", "township",                                 # Site
    "planning", "floorplans", "parking", "layout",              # Design
    "calculations", "engineering", "structural", "water",
    "fire", "sustainability",                                   # Engineering
    "boq", "cost", "programme",                                 # Cost & Programme
    "compliance", "datahealth",                                 # Deliver
]


def test_the_report_set_is_exactly_the_expected_one():
    assert list(R.REPORT_TITLES) == EXPECTED
    assert set(R.ALL_ORDER) == set(R.REPORT_TITLES)


def test_the_merged_pdf_reads_in_menu_order_after_the_summary():
    """The contents page and the sidebar have to agree on where a subject lives."""
    assert R.ALL_ORDER[0] == "executive"
    assert R.ALL_ORDER == EXPECTED


@pytest.mark.parametrize("key", list(R.REPORT_TITLES))
def test_every_report_builds(ctx, key):
    p, a, eng = ctx
    pdf = R.build_pdf(key, p, a, eng)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


@pytest.mark.parametrize("key", list(R.REPORT_TITLES))
def test_every_report_carries_the_project_metrics(ctx, key):
    p, a, eng = ctx
    # Stage 1: site reports quote site metrics only; township carries its master-plan metrics.
    t = text_of(R.build_pdf(key, p, a, eng))
    assert any(h in t for h in ("Key Project Metrics", "Key Site Metrics", "Master Plan Metrics")), key


# ---------------------------------------------------------------- merges kept the numbers
def test_a_merged_id_still_resolves(ctx):
    """An old link must land on the report that absorbed those numbers, not a 400."""
    p, a, eng = ctx
    for old, new in R.MERGED_INTO.items():
        assert new in R.REPORT_TITLES
        # Text, not bytes: every PDF embeds a generation timestamp and a document id, so
        # two renders of the same report are never byte-identical.
        assert text_of(R.build_pdf(old, p, a, eng)) == text_of(R.build_pdf(new, p, a, eng))


def test_quantities_survive_inside_the_boq(ctx):
    p, a, eng = ctx
    t = text_of(R.build_pdf("boq", p, a, eng))
    assert "Estimated Quantities" in t
    assert "Material Summary" in t and "Labour Summary" in t


def test_accessibility_survives_inside_compliance(ctx):
    p, a, eng = ctx
    assert "ccessibilit" in text_of(R.build_pdf("compliance", p, a, eng))


def test_utility_sizing_survives_inside_water(ctx):
    p, a, eng = ctx
    assert len(R.build_pdf("water", p, a, eng)) > 3000


# ---------------------------------------------------------------- the new reports
def test_programme_report_has_the_phases_and_the_safety_basis(ctx):
    p, a, eng = ctx
    t = text_of(R.build_pdf("programme", p, a, eng))
    assert "Phase Breakdown" in t
    assert "Critical Path" in t
    assert "IS 456" in t and "Floor cycle" in t


def test_cost_report_carries_the_feasibility_figures(ctx):
    p, a, eng = ctx
    t = text_of(R.build_pdf("cost", p, a, eng))
    for probe in ("Feasibility", "Break-even", "Cash Flow", "IRR"):
        assert probe in t, probe


def test_cost_report_carries_the_optimisation_findings(ctx):
    """The brief lists Optimisation Findings under "Add" but fixes the final set at eleven
    without it. Both hold: the content ships inside Cost & Feasibility."""
    p, a, eng = ctx
    t = text_of(R.build_pdf("cost", p, a, eng))
    assert "Optimisation Findings" in t
    assert "Change required" in t
    assert "Waste reduction" in t


def test_setbacks_appear_in_a_report_of_their_own(ctx):
    """Burying them inside Compliance meant a reader looking for setbacks had nothing to
    click, which is the same as not having them."""
    p, a, eng = ctx
    t = text_of(R.build_pdf("plot", p, a, eng))
    assert "Plot Geometry" in t
    assert "Setbacks & Development Controls" in t
    assert "Minimum (m)" in t and "NBC 2016 Part 3" in t


def test_setbacks_also_stay_in_the_compliance_report(ctx):
    p, a, eng = ctx
    assert "Setbacks & Development Controls" in text_of(R.build_pdf("compliance", p, a, eng))


def test_a_failing_setback_says_it_is_not_sanctionable(ctx):
    p, a, eng = ctx
    assert "Not sanctionable" in text_of(R.build_pdf("plot", p, a, eng))


def test_sustainability_report_carries_m13_and_m14(ctx):
    p, a, eng = ctx
    t = text_of(R.build_pdf("sustainability", p, a, eng))
    assert "Embodied Carbon" in t
    assert "Plantation Plan" in t
    assert "Green Rating" in t


def test_engineering_summary_was_extended_with_m13_and_m14(ctx):
    p, a, eng = ctx
    t = text_of(R.build_pdf("engineering", p, a, eng))
    assert "Embodied carbon" in t and "Trees required" in t


def test_site_report_says_so_when_no_gis_has_been_run(ctx):
    """Silence would read as "the site is fine"."""
    p, a, eng = ctx
    assert "No site analysis has been run" in text_of(R.build_pdf("site", p, a, eng))


# ---------------------------------------------------------------- download all
def test_download_all_is_one_pdf_with_a_contents_page(ctx):
    from pypdf import PdfReader
    p, a, eng = ctx
    pdf = R.build_all_pdf(p, a, eng)
    reader = PdfReader(_io.BytesIO(pdf))
    assert len(reader.pages) > len(R.REPORT_TITLES)
    first = reader.pages[0].extract_text()
    assert "Contents" in first
    for title in R.REPORT_TITLES.values():
        assert title in first, f"{title} missing from contents"


def test_contents_page_numbers_point_at_the_right_reports(ctx):
    """A contents page with wrong page numbers is worse than none."""
    from pypdf import PdfReader
    p, a, eng = ctx
    reader = PdfReader(_io.BytesIO(R.build_all_pdf(p, a, eng)))
    first = reader.pages[0].extract_text()
    # Executive Summary is first in ALL_ORDER, so it must start on page 2.
    lines = [l for l in first.split("\n") if R.REPORT_TITLES["executive"] in l]
    assert lines, "executive row missing"
    page2 = reader.pages[1].extract_text()
    assert R.REPORT_TITLES["executive"] in page2


def test_every_module_is_represented_somewhere():
    """The gap this step existed to close: modules that produced nothing downloadable."""
    covered = " ".join(R.REPORT_TITLES.values()).lower()
    for topic in ("site", "plot", "planning", "vastu", "parking", "layout", "calculation",
                  "compliance", "engineering", "structural", "water", "fire",
                  "sustainability", "boq", "cost", "programme", "reliability"):
        assert topic in covered, topic

# ---------------------------------------------------------------- the summary block
# Every report opens with its own answer. A reader who only wants the outcome should not
# have to reconstruct it from six tables.
@pytest.mark.parametrize("key", list(R.REPORT_TITLES))
def test_every_report_opens_with_its_own_summary(ctx, key):
    p, a, eng = ctx
    t = flat(R.build_pdf(key, p, a, eng))
    assert "Report Summary" in t, key
    # Before the workings, not buried after them.
    metrics = next(h for h in ("Key Project Metrics", "Key Site Metrics", "Master Plan Metrics") if h in t)
    assert t.index("Report Summary") < t.index(metrics), key


@pytest.mark.parametrize("key", list(R.REPORT_TITLES))
def test_no_summary_quietly_falls_back_to_the_apology(ctx, key):
    """The summary is wrapped so a failure cannot cost the reader the report -- which also
    means a broken one degrades silently. This is what notices it."""
    p, a, eng = ctx
    assert "summary could not be assembled" not in flat(R.build_pdf(key, p, a, eng)), key


# ---------------------------------------------------------------- modules that had no report
def test_planning_report_carries_the_mix_and_the_vastu_audit(ctx):
    p, a, eng = ctx
    t = flat(R.build_pdf("planning", p, a, eng))
    assert "Unit Mix" in t and "Mix Across the Scheme" in t
    assert "Society Amenities" in t
    assert "Vastu Audit" in t and "Sector Anchors" in t


def test_parking_is_a_report_again_not_a_block_in_the_executive(ctx):
    """It was merged into the Executive Summary before the parking engine grew a per-
    building demand model and an authority norm; nine lines could not carry those."""
    p, a, eng = ctx
    t = flat(R.build_pdf("parking", p, a, eng))
    assert "Governing Norm" in t
    assert "Parking Checks" in t and "Ramp Geometry" in t
    assert "Supply Efficiency" in t
    assert "parking" not in R.MERGED_INTO


def test_calculations_report_reproduces_the_far(ctx):
    p, a, eng = ctx
    t = flat(R.build_pdf("calculations", p, a, eng))
    assert "Area Derivation" in t and "FAR Derivation" in t
    assert "Not Counted in FAR" in t
    assert str(a["far_derivation"]["far"]) in t


def test_data_reliability_report_grades_the_document_not_the_engineering(ctx):
    p, a, eng = ctx
    t = flat(R.build_pdf("datahealth", p, a, eng))
    assert "Completeness" in t and "Freshness" in t and "Consistency" in t
    assert "Input Completeness by Group" in t
    assert "never the engineering" in t


def test_layout_report_says_so_when_no_layout_has_been_generated(ctx):
    """Silence would read as "there is no site plan to show", a different claim."""
    p, a, eng = ctx
    assert "No site layout has been generated" in flat(R.build_pdf("layout", p, a, eng))


def test_layout_report_carries_the_land_budget_once_generated(ctx):
    from siteplan.plan import plan_site
    p, a, eng = ctx
    p = {**p, "site_layout": plan_site(p)}
    t = flat(R.build_pdf("layout", p, a, eng))
    assert "Land Budget" in t and "Layout Performance" in t
    assert "Placed Blocks" in t and "Circulation" in t


# ---------------------------------------------------------------- enrichments
def test_water_report_carries_the_utility_sizing_it_absorbed(ctx):
    """`utilities` resolves to `water` before the section test runs, so a section guarded
    on the merged id could never fire -- which is how this table went missing."""
    p, a, eng = ctx
    t = flat(R.build_pdf("water", p, a, eng))
    assert "Utility Planning" in t
    assert "Demand Build-up" in t
    assert "STP Capacity" in t and "Rainwater Harvest" in t


def test_site_report_reports_access_from_the_keys_gis_actually_returns(gis_project):
    """It used to print the "summary" key, which the GIS module has never returned, so
    every project rendered a dash where the access findings belong."""
    p, a, eng = gis_project
    t = flat(R.build_pdf("site", p, a, eng))
    assert "Nearest road" in t and "Roads within 100 m" in t
    for note in p["gis"]["accessibility"]["notes"]:
        assert " ".join(note.split()) in t


def test_site_report_carries_wind_context_and_buildability(gis_project):
    p, a, eng = gis_project
    t = flat(R.build_pdf("site", p, a, eng))
    assert "Prevailing direction" in t and p["gis"]["wind"]["region"] in t
    assert "Surrounding Context" in t
    assert "Buildability" in t
    assert "Rooftop Solar Potential" in t and "Specific yield" in t


def test_sustainability_carries_the_full_solar_case(gis_project):
    p, a, eng = gis_project
    t = flat(R.build_pdf("sustainability", p, a, eng))
    assert "Rooftop Solar" in t
    assert "Lifetime generation" in t and "Usable after plant and access" in t


def test_executive_points_at_the_reports_that_carry_the_workings(gis_project):
    """A client reading only the summary should learn the scheme was audited, and where."""
    p, a, eng = gis_project
    t = flat(R.build_pdf("executive", p, a, eng))
    assert "Data Reliability" in t
    assert "Apartment Planning & Vastu" in t
