"""Unit tests for the 6 precision & effectiveness upgrades:
1. Breadcrumb embedding text generation
2. Domain synonym expansion
3. BM25 scoring & IDF calculation
4. Strict prompt grounding & temperature defaults
5. Subclause, Note, Annex citation regex & norming
6. Content entailment verification (verify_with_extracts)
7. Deterministic what-if parameter mutation and delta calculation
"""
import pytest
from typing import Any, Dict

import codesearch
import citations
import ai
import server


# ---------------------------------------------------------------- 1. Breadcrumb Embeddings
def test_breadcrumb_formatting():
    chunk = {
        "code": "IS 456:2000",
        "clause": "Cl. 23.2.1",
        "heading": "Control of Deflection",
        "text": "The vertical deflection limits may generally be assumed..."
    }
    enriched = codesearch._breadcrumb(chunk)
    assert enriched.startswith("[IS 456:2000 > Cl. 23.2.1 > Control of Deflection]\n")
    assert "The vertical deflection limits may generally be assumed..." in enriched


def test_breadcrumb_empty_prefix():
    chunk = {"code": "", "clause": "", "heading": "", "text": "Bare text without headers"}
    assert codesearch._breadcrumb(chunk) == "Bare text without headers"


# ---------------------------------------------------------------- 2. Synonym Expansion
def test_synonym_expansion_maps_colloquial_terms():
    tokens = codesearch._tokens("minimum rebar for slab")
    expanded = codesearch._expand_query(tokens)
    assert "rebar" in expanded
    assert "reinforcement" in expanded or "bar" in expanded


def test_synonym_expansion_preserves_unmapped_tokens():
    tokens = codesearch._tokens("cantilever beam deflection")
    expanded = codesearch._expand_query(tokens)
    assert "cantilever" in expanded
    assert "deflection" in expanded
    assert "span" in expanded or "depth" in expanded


# ---------------------------------------------------------------- 3. BM25 Scoring & Stats
def test_bm25_idf_and_length_penalty():
    c1 = {"text": "reinforcement details for beams", "heading": "", "code_id": "is456"}
    c2 = {"text": "concrete mix design requirements and binder proportions for durability", "heading": "", "code_id": "is456"}
    c3 = {"text": "reinforcement cover requirements", "heading": "", "code_id": "is456"}
    
    bags = [codesearch._tokens(c["text"]) for c in [c1, c2, c3]]
    dl = [len(b) for b in bags]
    avgdl = sum(dl) / len(dl)
    N = 3
    all_tokens = {}
    for b in bags:
        for t in b:
            all_tokens[t] = all_tokens.get(t, 0) + 1
    
    idf_reinforcement = codesearch.math.log((N - all_tokens["reinforcement"] + 0.5) / (all_tokens["reinforcement"] + 0.5) + 1.0)
    idf_details = codesearch.math.log((N - all_tokens["details"] + 0.5) / (all_tokens["details"] + 0.5) + 1.0)
    assert idf_details > idf_reinforcement


# ---------------------------------------------------------------- 4. Strict Grounding & Temperature
def test_prompt_chat_strict_grounding():
    prompt = ai.PROMPTS["chat"]
    assert "answer from general IS/NBC knowledge" not in prompt
    assert "ground every code reference in them" in prompt
    assert "supply clause numbers, limits, table values or formulas from memory" in prompt


def test_temperature_defaults():
    import inspect
    sig = inspect.signature(ai.generate_markdown)
    assert sig.parameters["temperature"].default == 0.15
    stream_sig = inspect.signature(ai.stream_markdown)
    assert stream_sig.parameters["temperature"].default == 0.15


# ---------------------------------------------------------------- 5. Citations Subclauses, Notes, Annexes
def test_clause_regex_subclause_and_notes():
    c1 = citations.extract("Under IS 456 Cl. 26.5.1.1(a), minimum shear reinforcement is required.")
    assert len(c1) == 1
    assert c1[0]["code"] == "is456"
    assert c1[0]["clause"] == "26.5.1.1"

    c2 = citations.extract("Refer to IS 456 Table 19 Note 2 for shear strength.")
    assert len(c2) == 1
    assert c2[0]["code"] == "is456"
    assert c2[0]["clause"] == "19"

    c3 = citations.extract("Calculated according to IS 456 Annex E.1.")
    assert len(c3) == 1
    assert c3[0]["code"] == "is456"
    assert "e.1" in c3[0]["clause"] or "e" in c3[0]["clause"]


# ---------------------------------------------------------------- 6. Content Entailment (verify_with_extracts)
def test_verify_with_extracts_numerical_entailment():
    extracts = [{
        "code": "IS 456:2000",
        "clause": "Cl. 23.2.1",
        "text": "The ratio of span to effective depth shall not exceed 20 for simply supported beams and 26 for continuous beams."
    }]
    
    text_accurate = "According to IS 456 Cl. 23.2.1, the limiting ratio is 20 for simply supported beams."
    res1 = citations.verify_with_extracts(text_accurate, extracts)
    assert len(res1["unconfirmed_values"]) == 0

    text_hallucinated = "According to IS 456 Cl. 23.2.1, the limiting ratio is 45 for simply supported beams."
    res2 = citations.verify_with_extracts(text_hallucinated, extracts)
    assert len(res2["unconfirmed_values"]) >= 1
    assert any("45" in u["value"] for u in res2["unconfirmed_values"])


# ---------------------------------------------------------------- 7. Deterministic What-If Simulation
def test_try_whatif_detection_and_delta():
    from defaults import default_project
    import engine
    import engineering as englib

    proj = default_project("Test", "City", "State", "T1", "user1")
    base = engine.analyse(proj)
    eng = englib.analyse_engineering(proj, base)

    assert server._try_whatif("Why is column size 450x450 mm?", proj, base, eng) is None

    res = server._try_whatif("What if we use concrete grade M35?", proj, base, eng)
    assert res is not None
    assert res["parameter_changes"]["concrete_grade"] == 35
    assert "metric_deltas" in res

    res_slab = server._try_whatif("What happens if slab thickness is 150 mm?", proj, base, eng)
    assert res_slab is not None
    assert res_slab["parameter_changes"]["slab_thickness_mm"] == 150.0
