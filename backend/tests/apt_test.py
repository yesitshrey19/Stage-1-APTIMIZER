"""Block 6 tests: APT's context budget and the citation guard.

The citation guard is the reason most of this file exists. A model states clause numbers
with total confidence and gets them wrong often enough that an unchecked citation is worse
than none -- a plausible "IS 456 Cl. 26.5.1.1" sends an engineer to a clause that does not
say what they were told. The guard has to catch the fakes AND leave the real ones alone,
because a flag that fires on correct citations gets ignored, and then so do the real ones.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, engineering as E, aptcontext as A, citations as CI, iscodes as C
from defaults import default_project


@pytest.fixture(scope="module")
def ctx():
    p = default_project("T", "C", "Hyderabad", "S", "o")
    an = engine.analyse(p)
    eng = E.analyse_engineering(p, an)
    return p, an, eng, A.build(p, an, eng)


# ---------------------------------------------------------------- context budget
def test_context_stays_under_the_token_budget(ctx):
    """Target is well under 10k tokens a message. This is the guard that keeps it there
    as modules are added."""
    _, _, _, c = ctx
    chars = len(json.dumps(c))
    assert chars // 4 < 10_000, f"{chars // 4} tokens"


def test_context_carries_no_raw_geometry(ctx):
    """Vertex arrays, per-floor room layouts, full activity lists and week-by-week cash
    flow are the biggest things in a project and the least useful in a conversation."""
    _, _, _, c = ctx
    blob = json.dumps(c)
    for banned in ("coordinates", "polygons", "polygons_local", "vertices",
                   "cash_flow", "activities", "rooms", "profile"):
        assert f'"{banned}"' not in blob, f"{banned} leaked into the context"


def test_numbers_are_rounded_on_the_way_in(ctx):
    """Long floats spend tokens on digits that are noise and invite the model to quote a
    precision the engine never claimed."""
    _, _, _, c = ctx

    def walk(v):
        if isinstance(v, float):
            assert round(v, 2) == v or round(v, 1) == v or round(v) == v, v
        elif isinstance(v, dict):
            [walk(x) for x in v.values()]
        elif isinstance(v, list):
            [walk(x) for x in v]
    walk(c)


def test_context_has_the_sections_a_question_needs(ctx):
    _, _, _, c = ctx
    for key in ("project", "towers", "compliance", "engineering", "cost",
                "parking", "utilities", "clause_registry"):
        assert key in c and c[key], key


def test_compliance_lists_every_rule_with_threshold_and_actual(ctx):
    """"Why does this pass" is as common a question as "why does this fail", and an
    absent rule reads as a rule that was not checked."""
    _, an, _, c = ctx
    assert len(c["compliance"]["rules"]) == len(an["compliance"]["results"])
    for r in c["compliance"]["rules"]:
        assert set(r) >= {"rule", "threshold", "actual", "status"}


def test_failing_rules_come_first(ctx):
    _, _, _, c = ctx
    statuses = [r["status"] for r in c["compliance"]["rules"]]
    assert statuses == sorted(statuses, key=lambda s: 0 if s == "fail" else 1)


def test_clause_registry_is_scoped_to_what_this_project_cites(ctx):
    _, _, _, c = ctx
    reg = c["clause_registry"]
    assert reg and len(reg) <= len(C.CLAUSES)
    for row in reg:
        assert set(row) == {"code", "clause", "topic"}   # no internal key


def test_engineering_outputs_carry_their_clause(ctx):
    _, _, _, c = ctx
    # A module with no outputs at all loses the key to _compact, which is intended.
    outs = [o for m in c["engineering"]["modules"].values() for o in m.get("outputs", [])]
    assert outs
    assert any(o.get("clause") for o in outs)


def test_absent_sections_are_omitted_not_sent_empty(ctx):
    """The prompt tells the assistant to say when data is missing. An empty section
    would read as data that exists and is zero."""
    p, an, eng, _ = ctx
    c = A.build(p, an, eng)
    assert "gis" not in c          # this project has no GIS run
    assert "revision_diff" not in c


# ---------------------------------------------------------------- citation guard
def test_a_real_citation_resolves():
    r = CI.verify("Base shear follows IS 1893 (Part 1):2016 Cl. 7.6.2.")
    assert r["resolved"] and not r["unverified"]


def test_a_real_citation_written_without_its_part_still_resolves():
    """Engineers write "IS 1893:2016" as often as "IS 1893 (Part 1):2016". Flagging the
    short form would fire the warning on correct citations, and a flag that cries wolf
    gets ignored when it matters."""
    r = CI.verify("Base shear follows IS 1893:2016 Cl. 7.6.2.")
    assert r["resolved"] and not r["unverified"]


def test_an_invented_standard_is_flagged():
    r = CI.verify("Per IS 9999:2099 Cl. 3.2.1 this is required.")
    assert r["unverified"] == ["IS 9999:2099 Cl. 3.2.1"]


def test_an_invented_nbc_part_is_flagged():
    """The NBC has 12 parts. A part number is which volume you are in, not an optional
    refinement, so it must not be collapsed away when matching."""
    bad = CI.verify("See NBC Part 47 Cl. 1.1.")
    assert bad["unverified"] == ["NBC Part 47 Cl. 1.1"]
    good = CI.verify("See NBC Part 4 Cl. 4.2.")
    assert not good["unverified"]


def test_a_real_code_with_an_unknown_clause_is_unconfirmed_not_condemned():
    """The registry is not the whole code, so an unrecognised clause on a real standard
    is unconfirmed rather than wrong. Reporting it as fake would be its own error."""
    r = CI.verify("IS 456 Cl. 99.99.99 applies.")
    assert r["code_only"] == ["IS 456 Cl. 99.99.99"]
    assert not r["unverified"]


def test_multi_reference_registry_entries_are_indexed_in_full():
    """Entries like "Table 3 / Annex E" carry two references. Indexing only the first
    made the second read as unverified."""
    entry = C.CLAUSES["seismic_zone"]
    assert "/" in entry["clause"]
    assert len(CI._all_clauses(entry["clause"])) == 2
    assert not CI.verify(f'Zone from {entry["code"]} Annex E.')["unverified"]


def test_text_with_no_citations_checks_nothing():
    r = CI.verify("The cost per flat is INR 22 lakh.")
    assert r["checked"] == 0 and not r["unverified"]


def test_citations_are_reported_not_stripped():
    """The guard returns lists alongside the text. It must never be the thing that edits
    the answer -- a clean reply with a hole in it is worse than a flagged one."""
    text = "Per IS 9999:2099 Cl. 1.1 you must do this."
    r = CI.verify(text)
    assert r["unverified"]
    assert "IS 9999:2099" in text        # the guard did not touch the source


def test_every_clause_in_the_registry_verifies_against_itself():
    """Whatever the app hands the model to cite from must come back resolvable, or the
    guard is flagging the app's own data."""
    for row in CI.registry_context():
        if not row["clause"]:
            continue
        probe = f'{row["code"]} {row["clause"].split("/")[0].strip()}'
        r = CI.verify(probe)
        assert not r["unverified"], f"registry entry failed its own check: {probe}"
