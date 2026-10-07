"""Step 1 tests: intent gate, tiered context, analysis and context caching.

The rule that outranks every optimisation here: citations.verify() runs on every
model-generated answer. A fast reply carrying an invented IS clause is worse than a slow
correct one, so the no-context path is also the no-MODEL path -- it returns fixed text
rather than a cheap generated answer that could cite something.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import engine, engineering as englib, aptcontext as aptlib, aptspeed as S
from defaults import default_project


@pytest.fixture
def proj():
    p = default_project("Speed", "QA", "Hyderabad", "S-1", "owner")
    p["_id"] = "speedtest"
    p["updated_at"] = "2026-08-30T00:00:00Z"
    S.clear_cache()
    return p


# ---------------------------------------------------------------- intent gate
@pytest.mark.parametrize("msg", [
    "hi", "Hello!", "hey", "thanks", "thank you", "cheers", "ok", "got it",
    "ok thanks", "ok got it", "great", "good morning", "bye",
    "how are you", "how are you doing", "how r u", "how's it going", "what's up",
    "thank you so much", "see you", "awesome",
])
def test_chitchat_needs_no_context(msg):
    assert S.classify(msg) == S.NONE


def test_reply_for_returns_appropriate_text():
    assert S.reply_for("hi") == S.GREETING
    assert S.reply_for("how are you") == S.GREETING_HOW_ARE_YOU
    assert S.reply_for("thanks") == S.THANKS_REPLY
    assert S.reply_for("bye") == S.BYE_REPLY
    assert S.reply_for("ok") == S.ACK_REPLY
    assert "Explain a number" in S.reply_for("what can you do")



@pytest.mark.parametrize("msg", [
    "what can you do", "who are you", "how can you help", "help",
])
def test_capability_questions_need_no_context(msg):
    assert S.classify(msg) == S.NONE
    assert S.is_capability_question(msg)


@pytest.mark.parametrize("msg", [
    "why is the base shear 1240 kN", "show me the derivation",
    "which clause governs FAR", "what is on the critical path",
    "explain the embodied carbon", "how was the steel quantity derived",
    "what is the break-even sale rate",
])
def test_technical_questions_get_the_full_payload(msg):
    assert S.classify(msg) == S.FULL


@pytest.mark.parametrize("msg", [
    "how big is this project", "tell me about the towers", "is it compliant",
])
def test_general_questions_get_the_light_payload(msg):
    assert S.classify(msg) == S.LIGHT


def test_a_greeting_with_a_real_question_attached_is_not_chitchat():
    """"thanks" is chit-chat; "thanks, why is the base shear 1240 kN" is a question, and
    answering the second from the fixed path would be a wrong answer delivered fast."""
    assert S.classify("thanks, why is the base shear 1240 kN") == S.FULL
    assert S.classify("hi, what drives the cost per flat") == S.FULL


def test_classification_never_calls_a_model():
    """Routing with a model call would add a network round trip to save a local one."""
    import inspect
    src = inspect.getsource(S.classify) + inspect.getsource(S.is_capability_question)
    for banned in ("generate_markdown", "await", "async", "requests", "httpx"):
        assert banned not in src


# ---------------------------------------------------------------- tiers
def test_light_context_is_a_fraction_of_full(proj):
    an = engine.analyse(proj)
    eng = englib.analyse_engineering(proj, an)
    light = len(json.dumps(S.light_context(proj, an)))
    full = len(json.dumps(aptlib.build(proj, an, eng)))
    assert light < full / 10, f"light {light} vs full {full}"


def test_light_context_still_carries_what_a_general_question_needs(proj):
    an = engine.analyse(proj)
    c = S.light_context(proj, an)
    assert c["project"]["total_units"] > 0
    assert c["project"]["far"] is not None
    assert "failing_rules" in c["compliance"]
    assert c["cost_inr"]["total"] > 0


def test_light_context_says_it_is_a_summary(proj):
    """Otherwise the assistant reads an absent section as a value that does not exist."""
    an = engine.analyse(proj)
    assert "light summary" in S.light_context(proj, an)["note"]


# ---------------------------------------------------------------- caching
def test_light_tier_never_pays_for_the_engineering_modules(proj):
    calls = {"eng": 0}

    def counted_eng(p, a):
        calls["eng"] += 1
        return englib.analyse_engineering(p, a)

    S.cached_analysis(proj, engine.analyse, counted_eng, need_engineering=False)
    assert calls["eng"] == 0


def test_analysis_is_computed_once_per_project_version(proj):
    calls = {"an": 0, "eng": 0}

    def counted_an(p):
        calls["an"] += 1
        return engine.analyse(p)

    def counted_eng(p, a):
        calls["eng"] += 1
        return englib.analyse_engineering(p, a)

    for _ in range(3):
        S.cached_analysis(proj, counted_an, counted_eng, need_engineering=True)
    assert calls == {"an": 1, "eng": 1}


def test_editing_the_project_invalidates_the_cache(proj):
    calls = {"an": 0}

    def counted_an(p):
        calls["an"] += 1
        return engine.analyse(p)

    S.cached_analysis(proj, counted_an, englib.analyse_engineering, need_engineering=False)
    proj["updated_at"] = "2026-08-30T12:00:00Z"      # the user changed something
    S.cached_analysis(proj, counted_an, englib.analyse_engineering, need_engineering=False)
    assert calls["an"] == 2


def test_a_light_hit_is_upgraded_rather_than_recomputed(proj):
    """A light question then a full one must not analyse the project twice."""
    calls = {"an": 0}

    def counted_an(p):
        calls["an"] += 1
        return engine.analyse(p)

    S.cached_analysis(proj, counted_an, englib.analyse_engineering, need_engineering=False)
    S.cached_analysis(proj, counted_an, englib.analyse_engineering, need_engineering=True)
    assert calls["an"] == 1


def test_context_cache_is_what_actually_saves_the_time(proj):
    """engine.analyse is ~1 ms and analyse_engineering ~6 ms; aptcontext.build is ~380 ms
    because of the optimisers inside it. Caching only the first two saved nothing."""
    calls = {"build": 0}
    an = engine.analyse(proj)
    eng = englib.analyse_engineering(proj, an)

    def build():
        calls["build"] += 1
        return aptlib.build(proj, an, eng)

    for _ in range(3):
        S.cached_context(proj, S.FULL, build)
    assert calls["build"] == 1


def test_context_cache_separates_the_tiers(proj):
    an = engine.analyse(proj)
    light = S.cached_context(proj, S.LIGHT, lambda: S.light_context(proj, an))
    full = S.cached_context(proj, S.FULL, lambda: {"marker": "full"})
    assert light != full and full["marker"] == "full"


def test_caches_are_bounded(proj):
    for i in range(S._CACHE_MAX + 10):
        p = dict(proj, _id=f"p{i}", updated_at="x")
        S.cached_analysis(p, engine.analyse, englib.analyse_engineering,
                          need_engineering=False)
        S.cached_context(p, S.LIGHT, lambda: {"i": i})
    assert len(S._CACHE) <= S._CACHE_MAX
    assert len(S._CTX_CACHE) <= S._CACHE_MAX


# ---------------------------------------------------------------- the standing rule
def test_the_fixed_replies_carry_no_citations():
    """The no-context path skips the model, so nothing verifies its text. It must
    therefore contain nothing that looks like a clause reference."""
    import citations as CI
    for text in (S.GREETING, S.CAPABILITY, S.GREETING_HOW_ARE_YOU, S.THANKS_REPLY, S.BYE_REPLY, S.ACK_REPLY):
        assert CI.verify(text)["checked"] == 0


def test_chat_route_verifies_citations_on_every_generated_answer():
    """No tier may skip the guard. Pinned by reading the route, because the alternative
    is discovering it was skipped from a wrong clause number in production.

    Read line by line rather than by splitting the source on the substring "return": the
    earlier version did that, so rewording a nearby comment to drop the word "returned"
    moved the split point and quietly changed what was being checked. A guard test that can
    be switched off by editing prose is not a guard.
    """
    import inspect, server
    lines = inspect.getsource(server.ai_chat).splitlines()
    model = [i for i, ln in enumerate(lines) if "generate_markdown" in ln]
    # verify_with_extracts wraps verify(), so either spelling is the guard.
    guard = [i for i, ln in enumerate(lines) if "citelib.verify" in ln]

    assert len(model) == 1, f"expected one model call on this route, found {len(model)}"
    assert len(guard) == 1, f"expected one citation guard on this route, found {len(guard)}"
    assert guard[0] > model[0], "the guard must check the model's answer, not precede it"
    # Nothing may return between generating an answer and checking its citations. The one
    # early return that skips the guard is above the model call, on the path that never
    # asks a model at all.
    between = [ln.strip() for ln in lines[model[0]:guard[0]]]
    assert not any(ln == "return" or ln.startswith("return ") for ln in between), \
        "a return between the model call and the citation guard would ship an unchecked answer"
