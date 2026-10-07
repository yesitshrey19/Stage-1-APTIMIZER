"""What APT does BEFORE it calls the model.

Every message used to run `engine.analyse()`, `analyse_engineering()` over fourteen
modules, `plan_schedule()` and eleven optimisers, then send a maximum-size prompt --
including for "hi". The model was never the bottleneck; the work in front of it was.

Three things fix that, in order of how much they save:

  1. An INTENT GATE. "hi" needs no project data at all, so it never builds any.
  2. TIERED CONTEXT. Most questions need identity and headline numbers, not fourteen
     modules and every optimiser. Only a question that names a module, a number or a
     clause pays for the full payload.
  3. A CACHE on the analysis, keyed on the project's `updated_at`. Two questions in a row
     about an unchanged project do the engineering work once.

Routing is keyword-based on purpose. Classifying with an extra model call would add a
network round trip to save a local one, which is the wrong trade at both ends.

One rule outranks all of this: `citations.verify()` runs on every full-tier answer. A
fast reply carrying an invented IS clause is worse than a slow correct one, so no path
here may skip it.
"""
import re
import time
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------- intent gate
# Whole-message patterns only. "thanks" is chit-chat; "thanks, why is the base shear
# 1,240 kN" is not, and anchoring both ends is what keeps the two apart.
_GREETING_TOKEN = (
    r"(?:hi|hey|hello|yo|hiya|hola|namaste|greetings|"
    r"good\s+(?:morning|afternoon|evening|day|night)|"
    r"how\s+(?:are\s+you(?:\s+doing)?|r\s+u|do\s+you\s+do|is\s+it\s+going|are\s+things|is\s+everything)(?:\s+today)?|"
    r"how\'?s\s+(?:it\s+going|everything)|"
    r"what\'?s\s+up|wassup|sup|what\s+is\s+up|"
    r"(?:are\s+you|you)\s+(?:there|ready|online)|"
    r"nice\s+to\s+meet\s+you)"
)

_POLITE_TOKEN = (
    r"(?:thanks?|thank\s+you(?:\s+(?:so\s+much|very\s+much|a\s+lot))?|thx|cheers|many\s+thanks|"
    r"ok(?:ay)?|k|got\s+it|nice|great|cool|perfect|awesome|alright|all\s+right|sure|fine|understood|noted|"
    r"bye|goodbye|see\s+you(?:\s+later)?|see\s+ya|cya|talk\s+to\s+you\s+later|"
    r"have\s+a\s+(?:good|nice|great)\s+day|take\s+care|"
    r"good\s+job|great\s+job|well\s+done|nice\s+work)"
)

_CHITCHAT_TOKEN = rf"(?:{_GREETING_TOKEN}|{_POLITE_TOKEN})"
# A run of them, not just one: people write "ok thanks" and "ok, got it" as readily as
# "ok", and matching a single token classified those as real questions.
_CHITCHAT = re.compile(
    r"^\s*" + _CHITCHAT_TOKEN + r"(?:[\s!.,]+" + _CHITCHAT_TOKEN + r")*[\s!.,?]*$",
    re.IGNORECASE)

_CAPABILITY = re.compile(
    r"^\s*(what\s+(can|do)\s+you\s+do|who\s+are\s+you|what\s+are\s+you|"
    r"how\s+can\s+you\s+help|help|what\s+is\s+apt|"
    r"who\s+(?:made|created|built)\s+you|"
    r"tell\s+me\s+about\s+yourself)"
    r"[\s?!.]*$", re.IGNORECASE)

# A question earns the full payload when it names something the full payload contains.
_FULL_TERMS = (
    "clause", "is 456", "is 1893", "is 875", "nbc", "code", "derivation", "derive",
    "formula", "step by step", "step-by-step", "calculate", "calculation", "how did you",
    "why is", "why does", "why did", "base shear", "seismic", "foundation", "mix design",
    "structural", "module",
    "column", "beam", "slab", "rebar", "reinforcement", "carbon", "embodied",
    "far", "fsi", "setback", "coverage", "parking", "ecs", "compliance", "rule",
    "boq", "quantity", "quantities", "cost", "rate", "budget", "cash flow", "cashflow",
    "roi", "irr", "margin", "payback", "break-even", "break even", "feasibility",
    "programme", "schedule", "critical path", "float", "floor cycle", "curing", "prop",
    "optimis", "optimiz", "waste", "material", "solar", "tree", "plantation",
    "stp", "rwh", "tank", "water", "fire", "accessib", "lift", "stair",
    "revision", "version", "compare", "changed",
)
_NUMBER = re.compile(r"\d")

NONE, LIGHT, FULL = "none", "light", "full"


def classify(message: str) -> str:
    """Which context tier this message needs. Cheap, deterministic, no model call."""
    text = (message or "").strip()
    if not text:
        return NONE
    if _CHITCHAT.match(text) or _CAPABILITY.match(text):
        return NONE
    low = text.lower()
    if any(t in low for t in _FULL_TERMS) or _NUMBER.search(low):
        return FULL
    return LIGHT


def is_capability_question(message: str) -> bool:
    return bool(_CAPABILITY.match((message or "").strip()))


# Sub-patterns to route local chit-chat into appropriate natural replies
_HOW_ARE_YOU_TOKEN = (
    r"(?:how\s+(?:are\s+you(?:\s+doing)?|r\s+u|do\s+you\s+do|is\s+it\s+going|are\s+things|is\s+everything)(?:\s+today)?|"
    r"how\'?s\s+(?:it\s+going|everything)|"
    r"what\'?s\s+up|wassup|sup|what\s+is\s+up)"
)
_HOW_ARE_YOU = re.compile(r"\b" + _HOW_ARE_YOU_TOKEN + r"\b", re.IGNORECASE)
_THANKS = re.compile(r"\b(?:thanks?|thank\s+you|thx|cheers|many\s+thanks)\b", re.IGNORECASE)
_BYE = re.compile(r"\b(?:bye|goodbye|see\s+you|see\s+ya|cya|take\s+care|talk\s+to\s+you\s+later)\b", re.IGNORECASE)
_ACK = re.compile(r"^(?:\s*(?:ok(?:ay)?|k|got\s+it|nice|great|cool|perfect|awesome|alright|all\s+right|sure|fine|understood|noted)[\s!.,]*)+$", re.IGNORECASE)

# Fixed replies for the no-context path. Written here rather than generated because a
# model round trip to say "hello" is the thing this module exists to avoid.
GREETING = (
    "Ready. Ask me about any number in this project — where it came from, which IS or NBC "
    "clause governs it, or what would change if you moved an input."
)

GREETING_HOW_ARE_YOU = (
    "I'm doing well and ready to help! Ask me about any number in this project — "
    "where it came from, which IS or NBC clause governs it, or what would change if you moved an input."
)

THANKS_REPLY = (
    "You're welcome! Let me know if you want to explore any other numbers, clauses, or what-ifs in this project."
)

BYE_REPLY = (
    "Goodbye! Have a productive design session."
)

ACK_REPLY = (
    "Ready whenever you are. Ask about any numbers, compliance rules, or structural and cost details."
)

CAPABILITY = (
    "I answer from this project's own computed state. Things I can do:\n\n"
    "- **Explain a number** — trace it back to the inputs and the governing clause.\n"
    "- **Show a derivation** — the formula, the substitution and the arithmetic, step by step.\n"
    "- **Answer compliance questions** — which rule fails, by how much, and the smallest "
    "change that clears it, with the clause cited.\n"
    "- **Reason about what-ifs** — directionally, from the same formulas the engine uses. "
    "You must re-run the engine for an authoritative number.\n"
    "- **Compare revisions** — what changed and what it moved downstream.\n\n"
    "I explain the engine's output; I do not recalculate it, and I am not a substitute for "
    "a licensed engineer's sign-off."
)


def reply_for(message: str) -> str:
    """Return an appropriate instant, deterministic local reply for tier == NONE questions."""
    text = (message or "").strip()
    if is_capability_question(text):
        return CAPABILITY
    if _HOW_ARE_YOU.search(text):
        return GREETING_HOW_ARE_YOU
    if _THANKS.search(text):
        return THANKS_REPLY
    if _BYE.search(text):
        return BYE_REPLY
    if _ACK.match(text):
        return ACK_REPLY
    return GREETING


# ---------------------------------------------------------------- analysis cache
# Keyed on the project's own `updated_at`, so an unchanged project is analysed once no
# matter how many questions are asked about it. Small and bounded: this is a convenience
# for a conversation, not a store.
_CACHE: Dict[str, Tuple[Any, Any]] = {}
_CACHE_MAX = 32


def cache_key(project: Dict[str, Any]) -> str:
    return f'{project.get("_id") or project.get("id")}:{project.get("updated_at")}'


def cached_analysis(project: Dict[str, Any], analyse, analyse_engineering,
                    need_engineering: bool = True):
    """(analysis, engineering) for this project, computing only what is missing.

    The two halves are cached independently. `analyse_engineering` is the expensive one --
    fourteen modules -- so the light tier never computes it, and a later full-tier question
    on the same unchanged project adds it to the existing entry rather than redoing both.
    """
    key = cache_key(project)
    an, eng = _CACHE.get(key, (None, None))

    if an is None:
        an = analyse(project)
    if eng is None and need_engineering:
        eng = analyse_engineering(project, an)

    if key not in _CACHE and len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))       # oldest out; this is a convenience, not a store
    _CACHE[key] = (an, eng)
    return an, eng


# The assembled context is cached too, and this is the one that matters. Profiling the
# full tier: engine.analyse 1 ms, analyse_engineering 6 ms, aptcontext.build 384 ms -- of
# which 339 ms is the eleven optimisers it runs. Caching the two analysis calls alone saved
# about seven milliseconds of a four-hundred millisecond path. Same key, same invalidation,
# applied to the thing that is actually slow.
_CTX_CACHE: Dict[str, Any] = {}


def cached_context(project: Dict[str, Any], tier: str, build) -> Any:
    """The assembled context for this project and tier, built once per project version."""
    key = f"{cache_key(project)}:{tier}"
    if key in _CTX_CACHE:
        return _CTX_CACHE[key]
    ctx = build()
    if len(_CTX_CACHE) >= _CACHE_MAX:
        _CTX_CACHE.pop(next(iter(_CTX_CACHE)))
    _CTX_CACHE[key] = ctx
    return ctx


def clear_cache() -> None:
    _CACHE.clear()
    _CTX_CACHE.clear()


# ---------------------------------------------------------------- light context
def light_context(project: Dict[str, Any], an: Dict[str, Any]) -> Dict[str, Any]:
    """Project identity, headline metrics and whatever is failing.

    Enough to answer "how big is this project" or "is it compliant" without touching the
    engineering modules, the optimisers or the programme.
    """
    ar = an["areas"]
    co = an["compliance"]
    return {
        "project": {
            "name": project.get("name"), "client": project.get("client"),
            "location": project.get("location"),
            "plot_area_sqm": round(float(ar["plot_area_sqm"] or 0), 1),
            "builtup_area_sqm": round(float(ar["builtup_area_sqm"] or 0), 1),
            "total_units": ar["total_units"],
            "towers": len(project.get("towers") or []),
            "max_height_m": ar["max_height_m"],
            "far": ar["far"], "fsi": ar["fsi"],
            "ground_coverage_pct": ar["ground_coverage_pct"],
            "open_space_pct": ar["open_space_pct"],
        },
        "cost_inr": {"total": round(float(an["cost"]["total"] or 0)),
                     "per_unit": round(float(an["cost"]["per_unit"] or 0))},
        "parking": {"required": an["parking"]["required_slots"],
                    "provided": an["parking"]["provided_slots"],
                    "deficit": an["parking"]["deficit"]},
        "compliance": {
            "score_pct": co["score"], "passed": co["passed"], "failed": co["failed"],
            "failing_rules": [{"rule": r["label"], "threshold": r["threshold"],
                               "actual": r["actual"]}
                              for r in co["results"] if r["status"] == "fail"],
        },
        "note": ("This is the light summary. Ask about a specific module, number or "
                 "clause and the full project state is loaded."),
    }


class Timer:
    """Wall time around the work in front of the model, for the before/after numbers."""

    def __init__(self):
        self.t0 = time.perf_counter()
        self.marks: Dict[str, float] = {}

    def mark(self, name: str) -> None:
        self.marks[name] = round((time.perf_counter() - self.t0) * 1000, 1)

    @property
    def ms(self) -> float:
        return round((time.perf_counter() - self.t0) * 1000, 1)
