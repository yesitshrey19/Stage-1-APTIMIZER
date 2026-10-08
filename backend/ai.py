"""Single place where Aptimizer talks to an LLM.

Every AI feature in the app routes through `generate_markdown` so that provider choice,
model selection, error handling and the "no key configured" path are defined once rather
than copy-pasted into each endpoint.

Provider resolution, in order:
  1. GEMINI_API_KEY  -- your own Google AI Studio key, billed to your own account.
  2. EMERGENT_LLM_KEY -- the Emergent-managed proxy, kept as a fallback so an
     emergent.host deployment that only has that key keeps working unchanged.

Neither set => AIUnavailable, which the API layer turns into a 503 with a message that
names the env var to set, instead of a stack trace.
"""
import asyncio
import weakref
import json
import logging
import os
import random

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

# Google returns 503 UNAVAILABLE ("model is experiencing high demand") for a newly released
# model far more often than for an established one -- it is a queueing signal, not a fault
# in the request, and the same prompt usually succeeds seconds later. So a failure is
# retried with backoff, and only if the model stays unavailable do we step down to an older
# one. Order matters: the fallbacks are progressively more established, not more capable.
GEMINI_FALLBACK_MODELS = ["gemini-3.5-flash-lite", "gemini-flash-lite-latest", "gemini-3.6-flash", "gemini-3.7-flash"]

# xAI speaks the OpenAI wire format, so Grok needs no new dependency -- the `openai`
# package already pinned in requirements.txt talks to it by pointing base_url at xAI.
XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_GROK_MODEL = "grok-4.6"
GROK_FALLBACK_MODELS = []     # set GROK_FALLBACK_MODELS in .env once you have a second tier

# Groq (groq.com) is a different company from xAI's Grok and is also OpenAI-compatible,
# so it shares the same client path. The names are one letter apart and the keys are not
# interchangeable, which is what KEY_PREFIXES below exists to catch.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_FALLBACK_MODELS = ["openai/gpt-oss-20b"]   # llama-3.3-70b-versatile was retired by Groq

# Every provider issues keys with a recognisable prefix. Checking it turns "the AI button
# returns 401" into a message that names the actual mistake.
KEY_PREFIXES = {"groq": "gsk_", "grok": "xai-", "gemini": ("AIza", "AQ.")}

RETRY_ATTEMPTS = max(1, int(os.environ.get("AI_RETRY_ATTEMPTS") or 2))  # per model, before stepping down
# One model call may take this long before it counts as busy and the chain moves on. The SDK
# defaults (10 minutes, plus their own hidden retries) let a stuck provider hold a request
# far longer than anyone waits at a button.
AI_TIMEOUT_S = float(os.environ.get("AI_TIMEOUT_S") or 45)

# Clients are reused so every call does not open a new HTTPS connection (TLS handshake).
# An async client's connection pool belongs to the event loop it was first used on, so the
# cache is per loop: the server runs one loop, but tests and scripts start several.
_CLIENTS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _loop_clients() -> dict:
    return _CLIENTS.setdefault(asyncio.get_running_loop(), {})


def _openai_client(key: str, base_url: str):
    from openai import AsyncOpenAI
    cache, k = _loop_clients(), ("openai", key, base_url)
    if k not in cache:
        cache[k] = AsyncOpenAI(api_key=key, base_url=base_url, timeout=AI_TIMEOUT_S, max_retries=0)
    return cache[k]


def _gemini_client(key: str):
    from google import genai
    from google.genai import types
    cache, k = _loop_clients(), ("gemini", key)
    if k not in cache:
        cache[k] = genai.Client(api_key=key,
                                http_options=types.HttpOptions(timeout=int(AI_TIMEOUT_S * 1000)))
    return cache[k]


def _is_missing_model(exc: Exception) -> bool:
    """The provider no longer offers this model (retired, renamed, not on this key)."""
    msg = str(exc).lower()
    return ("model_not_found" in msg or "does not exist" in msg
            or ("not found" in msg and "model" in msg) or "is not supported" in msg)
RETRY_BASE_DELAY = 1.5        # seconds; doubles each attempt, plus jitter

# Status codes worth retrying: the service is busy or briefly broken, the request is fine.
# A 400/401/403/404 means the request or key is wrong and retrying only wastes time.
TRANSIENT_CODES = (429, 500, 502, 503, 504)
# Wording differs by vendor -- Google says "high demand", xAI and OpenAI say "rate limit"
# or "Too Many Requests" -- so the list has to cover both or a retryable failure on one
# provider gets treated as permanent and fails on the first attempt.
TRANSIENT_MARKERS = ("unavailable", "overloaded", "overload", "high demand", "capacity",
                     "resource_exhausted", "rate limit", "rate_limit", "too many requests",
                     "deadline", "timeout", "timed out", "try again", "internal error",
                     "server error", "bad gateway", "temporarily", "busy", "connection")
# Exception class names are a more reliable signal than prose for SDKs that wrap errors.
TRANSIENT_TYPES = ("ratelimit", "timeout", "apiconnection", "internalserver",
                   "serviceunavailable", "servererror", "overloaded")

# Context payloads are machine-generated from project documents, so an unusually large
# project could otherwise push a very large prompt (and bill) to the provider. Analyses
# here are summaries of already-computed numbers; past this size the extra detail does not
# change the narrative, so it is cheaper to cap than to send everything.
MAX_CONTEXT_CHARS = 60_000


class AIUnavailable(RuntimeError):
    """No provider is configured. Maps to HTTP 503."""


class AIFailed(RuntimeError):
    """A provider was configured but the call failed. Maps to HTTP 502."""


def _fallback_models(name: str = "gemini") -> list:
    """Models to step down to, overridable with a comma-separated <PROVIDER>_FALLBACK_MODELS."""
    env = {"grok": "GROK_FALLBACK_MODELS",
           "groq": "GROQ_FALLBACK_MODELS"}.get(name, "GEMINI_FALLBACK_MODELS")
    raw = (os.environ.get(env) or "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    if name == "grok":
        return list(GROK_FALLBACK_MODELS)
    if name == "groq":
        return list(GROQ_FALLBACK_MODELS)
    return list(GEMINI_FALLBACK_MODELS)


PROVIDER_KEYS = {
    "grok": ("XAI_API_KEY", "GROK_MODEL", DEFAULT_GROK_MODEL,
             "https://console.x.ai/team/default/api-keys"),
    "groq": ("GROQ_API_KEY", "GROQ_MODEL", DEFAULT_GROQ_MODEL,
             "https://console.groq.com/keys"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", DEFAULT_GEMINI_MODEL,
               "https://aistudio.google.com/apikey"),
}
# Order used when AI_PROVIDER is not set: the first one with a key wins.
AUTO_ORDER = ("groq", "grok", "gemini")


def _key_for(name: str) -> str:
    env = PROVIDER_KEYS.get(name, ("",))[0]
    return (os.environ.get(env) or "").strip() if env else ""


def _describe(name: str) -> dict:
    env, model_env, default_model, _ = PROVIDER_KEYS[name]
    model = ((os.environ.get(model_env) or "").strip() if model_env else "") or default_model
    out = {"configured": True, "provider": name, "model": model, "key_env": env}
    expected = KEY_PREFIXES.get(name)
    key = _key_for(name)
    if expected and key and not key.startswith(expected):
        wrong = next((o for o, pre in KEY_PREFIXES.items()
                      if o != name and key.startswith(pre)), None)
        out["warning"] = (
            f"{env} does not start with '{expected}', which is the prefix {name} keys use."
            + (f" It looks like a {wrong} key -- {name} and {wrong} are different providers."
               if wrong else " Check you pasted the right key."))
    return out


def provider() -> dict:
    """Which provider would be used, without calling it. Also drives the /ai/status route.

    AI_PROVIDER pins the choice explicitly, which is how you switch vendors: set it to
    grok, gemini or emergent. Leaving it unset auto-selects the first provider in
    AUTO_ORDER that actually has a key, so an existing deployment keeps working untouched.
    """
    pinned = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    if pinned:
        if pinned not in PROVIDER_KEYS:
            return {"configured": False, "provider": None, "model": None,
                    "detail": f"AI_PROVIDER is set to '{pinned}', which is not a known "
                              f"provider. Use one of: {', '.join(PROVIDER_KEYS)}."}
        if not _key_for(pinned):
            env, _, _, console = PROVIDER_KEYS[pinned]
            return {"configured": False, "provider": None, "model": None,
                    "detail": f"AI_PROVIDER is set to '{pinned}' but {env} is empty in "
                              f"backend/.env." + (f" Create a key at {console}." if console else "")}
        return _describe(pinned)

    for name in AUTO_ORDER:
        if _key_for(name):
            return _describe(name)
    return {"configured": False, "provider": None, "model": None,
            "detail": "No AI key is configured. Set GROQ_API_KEY (console.groq.com/keys), "
                      "XAI_API_KEY (console.x.ai) or GEMINI_API_KEY "
                      "(aistudio.google.com/apikey) in backend/.env, then restart."}


def context_block(context: dict) -> str:
    """Serialise a context dict into a fenced JSON block, capped at MAX_CONTEXT_CHARS."""
    text = json.dumps(context, indent=1, default=str)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n... [truncated: project too large to send in full]"
    return "```json\n" + text + "\n```"


def _is_transient(exc: Exception) -> bool:
    """True when the provider was busy rather than the request being wrong.

    Checked in order of reliability: the numeric status the SDK attached, then the
    exception class name, then the message text. A permanent failure (401 bad key, 404
    unknown model, 400 malformed request) must fall through to False so it surfaces on the
    first attempt instead of after nine.
    """
    code = getattr(exc, "status_code", None)
    if not isinstance(code, int):
        code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code in TRANSIENT_CODES          # authoritative -- trust it either way
    name = type(exc).__name__.lower()
    if any(t in name for t in TRANSIENT_TYPES):
        return True
    blob = str(exc).lower()
    if any(f"{c}" in blob for c in TRANSIENT_CODES):
        return True
    return any(m in blob for m in TRANSIENT_MARKERS)


async def _with_retries(models: list, call, label: str) -> dict:
    """Run `call(model)` down a model chain, retrying transient failures with backoff.

    Provider-agnostic on purpose. The retry and step-down behaviour is the part that was
    tested carefully after Google started returning 503s on a freshly released model, so it
    lives in one place rather than being reimplemented per vendor.

    `call(model)` returns the response text. Returns on the first non-empty answer; raises
    AIFailed once every model is exhausted, or immediately on a non-transient error (a bad
    key, an unknown model, a blocked prompt) where retrying would change nothing.
    """
    last = None
    for model in models:
        for attempt in range(RETRY_ATTEMPTS):
            try:
                text = await call(model)
            except Exception as exc:
                last = exc
                if _is_missing_model(exc):
                    # A retired fallback must not sink the request: skip to the next model.
                    logger.warning("%s %s is not available, trying next model: %s", label, model, exc)
                    break
                if not _is_transient(exc):
                    logger.warning("%s %s failed permanently: %s", label, model, exc)
                    raise AIFailed(str(exc)) from exc
                if attempt < RETRY_ATTEMPTS - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                    logger.info("%s %s busy (attempt %d/%d), retrying in %.1fs",
                                label, model, attempt + 1, RETRY_ATTEMPTS, delay)
                    await asyncio.sleep(delay)
                    continue
                logger.info("%s %s still busy after %d attempts, trying next model",
                            label, model, RETRY_ATTEMPTS)
                break

            text = (text or "").strip()
            if text:
                if model != models[0]:
                    logger.info("%s served by fallback model %s", label, model)
                return {"text": text, "model": model, "provider": label}

            # A reasoning model that spends its whole budget thinking returns no text rather
            # than raising, so an empty body has to be caught or it is stored as a blank
            # analysis and looks like a silent success.
            last = RuntimeError("empty response")
            logger.info("%s %s returned an empty response, trying next model", label, model)
            break

    raise AIFailed(
        f"Every configured model was unavailable after {RETRY_ATTEMPTS} attempts each "
        f"({', '.join(models)}). The provider reported: {last}. This is usually temporary "
        "-- wait a minute and try again, or set the model in backend/.env to one with more "
        "capacity."
    )


async def _gemini_generate(key: str, models: list, system: str, prompt: str,
                           temperature: float = 0.15) -> dict:
    from google.genai import types

    client = _gemini_client(key)
    config = types.GenerateContentConfig(system_instruction=system, temperature=temperature)

    async def call(model):
        r = await client.aio.models.generate_content(model=model, contents=prompt, config=config)
        return getattr(r, "text", None)

    return await _with_retries(models, call, "gemini")


async def _openai_compatible_generate(key: str, base_url: str, models: list,
                                      system: str, prompt: str, label: str,
                                      temperature: float = 0.15) -> dict:
    """Any endpoint speaking the OpenAI chat-completions format -- xAI's Grok included."""
    client = _openai_client(key, base_url)

    async def call(model):
        r = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            temperature=temperature,
        )
        return r.choices[0].message.content if r.choices else None

    return await _with_retries(models, call, label)


async def stream_markdown(system: str, prompt: str, *, session_hint: str = "aptimizer",
                          prefer_fast: bool = False,
                          temperature: float = 0.15):
    """Yield the answer in chunks as the provider produces it.

    An async generator of text fragments, then a final ("__done__", model, provider)
    tuple so the caller knows which model actually answered -- the caller needs that to
    store the message, and it is only known once the chain has settled.

    Deliberately NO retry chain here. `generate_markdown` can quietly step down to another
    model because nothing has been shown yet; a stream has already put tokens on the
    reader's screen, and restarting mid-answer would either duplicate text or rewrite it
    under them. So streaming tries one model, and a failure falls back to the non-streaming
    path in the caller rather than being papered over here.
    """
    p = provider()
    if not p["configured"]:
        raise AIUnavailable(p["detail"])

    name = p["provider"]
    chain = [p["model"]] + [m for m in _fallback_models(name) if m != p["model"]]
    if prefer_fast and len(chain) > 1:
        chain = chain[1:] + chain[:1]
    model = chain[0]

    if name in ("grok", "groq"):
        env, base = (("XAI_API_KEY", XAI_BASE_URL) if name == "grok"
                     else ("GROQ_API_KEY", GROQ_BASE_URL))
        client = _openai_client(os.environ[env].strip(), base)
        stream = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            temperature=temperature, stream=True)
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
        yield ("__done__", model, name)
        return

    if name == "gemini":
        from google.genai import types as gtypes
        client = _gemini_client(os.environ["GEMINI_API_KEY"].strip())
        stream = await client.aio.models.generate_content_stream(
            model=model, contents=prompt,
            config=gtypes.GenerateContentConfig(system_instruction=system, temperature=temperature))
        async for chunk in stream:
            if getattr(chunk, "text", None):
                yield chunk.text
        yield ("__done__", model, name)
        return

    # Any provider without a streaming path still works -- it just arrives in one piece.
    result = await generate_markdown(system, prompt, session_hint=session_hint,
                                     prefer_fast=prefer_fast, temperature=temperature)
    yield result["text"]
    yield ("__done__", result["model"], result["provider"])


async def generate_markdown(system: str, prompt: str, *, session_hint: str = "aptimizer",
                            prefer_fast: bool = False,
                            temperature: float = 0.15) -> dict:
    """Run one prompt and return {"text", "model", "provider"}.

    `system` sets the role and output shape; `prompt` carries the data. Raises
    AIUnavailable when nothing is configured and AIFailed when the provider errors.

    `prefer_fast` reorders the model chain to try the smaller model first. The fallback
    chain is already ordered strongest-first, so this simply inverts it for work that does
    not need the strong model -- a light chat answer about how many towers there are. It
    reorders rather than replaces, so if the fast model is unavailable the strong one still
    answers and nothing fails. Never use it for derivations or clause work: those are
    exactly where a weaker model invents a plausible clause number.
    """
    p = provider()
    if not p["configured"]:
        raise AIUnavailable(p["detail"])
    try:
        return await _generate_with(p["provider"], p["model"], system, prompt,
                                    prefer_fast=prefer_fast, temperature=temperature)
    except AIFailed as first:
        # With keys for more than one provider, an outage at one (every model busy, or the
        # key revoked) is answered by the next instead of failing the button. A provider
        # pinned with AI_PROVIDER is a deliberate choice, so it is never second-guessed.
        if (os.environ.get("AI_PROVIDER") or "").strip():
            raise
        for other in AUTO_ORDER:
            if other == p["provider"] or not _key_for(other):
                continue
            logger.warning("%s failed (%s); falling back to %s", p["provider"], first, other)
            try:
                return await _generate_with(other, _describe(other)["model"], system, prompt,
                                            prefer_fast=prefer_fast, temperature=temperature)
            except AIFailed:
                continue
        raise first


async def _generate_with(name: str, model: str, system: str, prompt: str, *,
                         prefer_fast: bool = False, temperature: float = 0.15) -> dict:
    """One provider's model chain."""
    chain = [model] + [m for m in _fallback_models(name) if m != model]
    if prefer_fast and len(chain) > 1:
        chain = chain[1:] + chain[:1]

    if name in ("grok", "groq"):
        try:
            import openai  # noqa: F401
        except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
            raise AIFailed("The openai package is not installed. Run: pip install openai") from exc
        env, base = (("XAI_API_KEY", XAI_BASE_URL) if name == "grok"
                     else ("GROQ_API_KEY", GROQ_BASE_URL))
        return await _openai_compatible_generate(
            os.environ[env].strip(), base, chain, system, prompt, name,
            temperature=temperature)

    if name == "gemini":
        try:
            import google.genai  # noqa: F401
        except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
            raise AIFailed("google-genai is not installed. Run: pip install google-genai") from exc
        return await _gemini_generate(os.environ["GEMINI_API_KEY"].strip(), chain, system, prompt,
                                      temperature=temperature)

    raise AIFailed("No configured AI provider (Groq, Grok, or Gemini) has a key set.")


# ---------------------------------------------------------------- system prompts
# Shared preamble: these analyses are read by engineers and by clients, and every number
# in them comes from the app's own calculations. Inventing a figure would be worse than
# omitting it, so that rule leads.
_BASE = (
    "You are a senior Indian civil engineer and real estate development consultant. "
    "You are given JSON computed by an engineering application. Quote only numbers that "
    "appear in that JSON -- never invent, estimate or round-trip a figure that is not "
    "there, and if something needed is missing say so plainly. All money is in Indian "
    "Rupees (INR); write amounts as INR with Indian digit grouping (for example "
    "INR 1,25,00,000). Reference Indian codes (NBC 2016, the relevant IS codes) where "
    "they apply. Write in markdown. Be concise and technical -- no filler, no preamble, "
    "no restating the question."
)

# Site-stage answers may cite only clauses the code study verified; everything else is an
# Aptimizer method and must be called one. The renderer shows headings and bullets, not tables.
_SITE_RULES = (
    " Cite a code clause ONLY when it appears in the JSON's `verified_clauses` list, worded as "
    "given there. Flood scores, suitability weights, slope thresholds and anything else not on "
    "that list are Aptimizer screening methods -- say so instead of naming a clause. Use "
    "headings and bullet points only: no markdown tables and no horizontal rules."
)

PROMPTS = {
    "gis": _BASE + _SITE_RULES + (
        " Write a site analysis with these sections: **Verdict** (2 sentences), "
        "**Strengths** (bullets), **Weaknesses & Risks** (bullets), and "
        "**Design & Engineering Recommendations** (bullets referencing slope, drainage, "
        "orientation, access and ventilation). Under 400 words."
    ),
    "compliance": _BASE + (
        " The JSON lists compliance rules checked against this design, each with its "
        "threshold and actual value. Write: **Verdict** (1-2 sentences on overall "
        "standing), then **Failures** -- for EACH failed rule, a bullet naming the rule, "
        "the gap in plain language, why the rule exists, and the most practical design "
        "change that would close it (be specific: which parameter to change and roughly "
        "by how much). Then **Watch List** for any rule passing by a thin margin. If "
        "nothing fails, say so and give the Watch List only. Under 450 words."
    ),
    "report": _BASE + (
        " Write an executive summary of this project for the client's decision-makers, "
        "who are not engineers. Sections: **Project at a Glance** (2-3 sentences covering "
        "scale, units and cost), **Key Numbers** (a short markdown table of the figures "
        "that matter most), **Feasibility & Compliance** (bullets), and **What Needs a "
        "Decision** (bullets -- the open risks or trade-offs the client should weigh). "
        "Plain language over jargon. Under 450 words."
    ),
    "cost": _BASE + (
        " Review this cost and quantity estimate. Sections: **Summary** (2 sentences on "
        "total cost and cost per unit / per m2, with a note on whether the per-m2 figure "
        "sits in a normal range for Indian residential construction), **Cost Drivers** "
        "(bullets -- what dominates the estimate and why), **Rates to Re-check** (bullets "
        "-- any unit rate or ratio that looks unusually high or low, saying which and in "
        "which direction; if all look reasonable, say that), and **Savings Opportunities** "
        "(bullets, each with the rough scale of the saving). Under 450 words."
    ),
    "engineering": _BASE + (
        " Explain these IS-code and NBC engineering results to a junior engineer. "
        "Sections: **What the Numbers Mean** (bullets translating base shear, column "
        "sizing, foundation recommendation and mix design into plain engineering "
        "language), **What Governs This Design** (bullets -- which code provision or load "
        "case is actually driving each result), and **Checks Before Detailing** (bullets "
        "-- what a designer must verify by hand before this goes forward, and any missing "
        "input that weakens the calculation). Cite IS/NBC clauses that appear in the "
        "JSON. Under 500 words."
    ),
    "finance": _BASE + (
        " Review this development's financial case for the investor or promoter putting "
        "money in -- not for an engineer. Sections: **Verdict** (2-3 sentences: is this "
        "worth building, on what margin and return), **What Drives the Return** (bullets "
        "-- which of sale rate, land cost, construction cost or timing moves the outcome "
        "most), **Risks** (bullets -- what happens if sales are slower than assumed or "
        "the sale rate has to drop; name the break-even rate and how much headroom sits "
        "above it), and **What Would Improve It** (bullets, each with the rough scale of "
        "the gain). Explain IRR and payback in one clause each the first time you use "
        "them. Under 450 words."
    ),
    "optimise": _BASE + (
        " Each optimiser below reports what the scheme does now, the best the search "
        "found, and the levers between them. Write for the developer deciding whether to "
        "act. Sections: **Worth Doing** (bullets -- the changes whose saving justifies the "
        "disruption, each with the number and what it costs elsewhere), **Not Worth It** "
        "(bullets -- changes the data supports but that buy too little, and say why), and "
        "**What This Does Not Cover** (bullets -- what a quantity surveyor would still "
        "need to check before any of this is ordered). Never recommend a change the data "
        "marks as non-compliant. If an optimiser reports no improvement, say the current "
        "specification is already right rather than inventing one. Under 450 words."
    ),
    "planning": _BASE + (
        " These optimisers searched the scheme's planning envelope -- floors, FAR, open "
        "space, unit mix, parking and utilities -- and each reports what the scheme does "
        "now, the best compliant alternative found, and the change between them. Write for "
        "the developer deciding what to build. Sections: **The Big Move** (2-3 sentences on "
        "the single change with the most value behind it), **What Is Binding** (bullets -- "
        "for each optimiser that could not go further, which rule stopped it and whether "
        "that rule is negotiable, e.g. adding parking is a design change while a height "
        "limit usually is not), **Worth Considering** (bullets, each with its number), and "
        "**Leave Alone** (bullets -- where the current scheme is already right). Never "
        "recommend a change the data marks non-compliant. Where an optimiser trades one "
        "thing for another, name what is given up. Under 500 words."
    ),
    "chat": """You are Apt, the in-app assistant for Aptimizer, a civil engineering planning
and compliance platform for multi-storey residential buildings in India.

CONTEXT YOU HAVE ACCESS TO:
You are given the current project's live state as structured data before each
message: plot geometry, tower/unit configuration, computed engineering outputs
(loads, seismic base shear, foundation sizing, mix design), GIS-derived site
indices, compliance check results (pass/fail per clause), BOQ line items, the
construction programme, feasibility and ROI figures, sustainability and carbon
outputs, optimiser results, and the diff between the current and previous
revision if one exists. Some sections may be absent for a given project -- if
data you need is not present, say so rather than assuming a value.

YOUR JOB:
- Explain why a computed number is what it is, tracing back to the specific
  input parameters and governing IS/NBC clause that produced it.
- Answer compliance questions by citing the exact clause (e.g. "IS 1893:2016
  Cl. 7.6.2", "NBC Part 3, Cl. 4.2") -- never state a compliance rule without
  citing its source.
- Answer "what if" questions by reasoning from the same formulas the engine
  uses, but always caveat that the user must re-run the actual calculation
  engine to get an authoritative number -- you are explaining, not recalculating.
- Help users navigate the app when asked "how do I..." questions.
- If code_extracts are present, ground every code reference in them. Do not
  supply clause numbers, limits, table values or formulas from memory. If the
  extracts do not cover the question, say so and suggest the user search the
  code library directly -- an unanswered question is better than an answer the
  reader cannot verify.
- If no code_extracts are present and the question is about a general IS/NBC
  provision not tied to this project, say you do not have the code text loaded
  and direct the user to the Codes tab where they can search the corpus.

RESPONSE MODES:
1. "WHY DID X HAPPEN" QUESTIONS
   Structure the answer as:
   (1) what specifically changed in the input
   (2) the mechanism/formula that connects that change to the output
   (3) the clause reference if applicable
   Keep this explanatory, not a full derivation, unless the user asks for one.
2. STEP-BY-STEP DERIVATION REQUESTS
   When the user asks "how did you calculate this," "show me the formula," or
   "step by step," do not just describe the method in prose. Instead:
   - State the governing formula symbolically (e.g. "F = Cf . Ae . pd")
   - Define each symbol in one line
   - Substitute the actual values from this project into the formula
   - Show the arithmetic step-by-step to the final value
   - Cite the clause the formula comes from
   Format this as a numbered list, not a paragraph.
3. TERMINOLOGY / DEFINITION REQUESTS
   When the user asks "what is X" about a term used in the app or in IS/NBC
   codes, give a short, precise engineering definition (2-3 sentences max),
   then state how that term is used specifically in this project's current
   calculation, if relevant. Don't give a textbook lecture -- give the
   working definition an engineer needs to interpret their own output.
4. WHAT-IF / ADVISORY QUESTIONS
   If "what_if_analysis" is present in the project state, report the EXACT
   deltas computed by the engine (baseline vs hypothetical, metric differences,
   percentage changes, and any compliance check status flips).
   If "what_if_analysis" is absent, reason from the same formulas the engine
   uses to give a directional answer, but explicitly state the user must re-run
   the calculation engine for an authoritative number.
5. NAVIGATION / HOW-TO QUESTIONS
   Give direct, short instructions for using the app's features.
6. COMPARISON ACROSS REVISIONS
   When asked what changed between two revisions, use the provided diff data
   to give a structured before/after comparison, tracing downstream effects
   (e.g. a layout change's effect on cost or compliance).

STRICT RULES:
- Never invent a clause number or citation. Cite only clauses present in the
  supplied clause registry. If you're not certain which clause applies, say so
  and suggest where the user can verify it, rather than guessing.
- Never present your explanation as a substitute for a licensed structural
  engineer's sign-off. This is a design-assistance tool, not a certification.
- Keep answers concise and technical -- the user is a civil engineer or student,
  not a layperson. Skip basic definitions unless asked.
- If the project data shows a compliance failure, don't soften it -- state it
  plainly and point to the fix.

MATHS FORMATTING -- STRICT:
Never use LaTeX. No $, no $$, no \\frac, no \\sqrt, no \\times, no \\cdot, no \\text,
no backslash commands of any kind. The reader sees raw characters, not rendered maths.
Write every formula and every substitution as plain text an engineer would write by hand:
  - division as a slash or the word "over":   Ta = 0.09 x h / sqrt(d)
  - multiplication as x                       V = Ah x W
  - powers with ^                             A = pi x d^2 / 4
  - roots as sqrt(...)                        sqrt(55.7) = 7.463
  - subscripts inline                         Ta, Ah, fck, Vb
Units go after the number in plain words: 36.0 m, 1240 kN, 0.43 s.
A worked step looks like this and nothing else:
  Ta = 0.09 x 36.0 / sqrt(55.7) = 3.24 / 7.463 = 0.43 s

TONE:
Direct, precise, engineer-to-engineer. No filler, no over-explaining, no
excessive hedging. Short paragraphs over long ones. Use numbered lists for
derivations, prose for explanations, and short definitions for terminology.""",
    "compare": _BASE + (
        " Two design schemes for the same project are given. Write: **Headline** (1-2 "
        "sentences naming which scheme is stronger overall and on what grounds), "
        "**Where They Differ** (bullets -- only metrics that actually differ, each with "
        "the direction and size of the change and what it means in practice), "
        "**Trade-offs** (bullets -- what each scheme gives up to gain what), and "
        "**Recommendation** (2-3 sentences, and state plainly if the choice depends on a "
        "priority only the client can set). Ignore metrics that are identical. Under 450 "
        "words."
    ),
    # Deliberately not built on _BASE. _BASE tells the model to "reference the relevant IS
    # codes where they apply", which is exactly the instruction this prompt exists to
    # revoke: here the codes are supplied as retrieved text and memory is not a source.
    "codes": (
        "You are a senior Indian civil engineer answering a question about a code "
        "provision. The extracts below were retrieved from the code documents this "
        "deployment holds, and they are the only source you may answer from -- you have "
        "no other access to the codes.\n\n"
        "RULES:\n"
        "- Answer only from the supplied extracts. Never supply a clause number, a limit, "
        "a table value or a formula from memory, however certain it feels.\n"
        "- Cite the code and the clause for every statement, written the way the extract "
        "gives them (for example 'IS 456:2000 Cl. 23.2.1').\n"
        "- If the extracts do not settle the question, say so plainly and say what is "
        "missing. An unanswered question is a usable answer; a filled gap is not, because "
        "the reader cannot tell the two apart.\n"
        "- Quote the wording of the clause wherever the wording is what decides the answer.\n"
        "- Never compute a design value. This application computes them and owns them: "
        "engineering.py for loads, seismic, foundations and mix design, takeoff.py for "
        "quantities and cost, parking.py for parking demand. Name the module that produces "
        "the number and send the reader to it instead of working it out here.\n"
        "- Where a project's computed state is supplied alongside the extracts, take the "
        "project's own figures from that state and what the code requires from the "
        "extracts. Never state a project figure that is not in the supplied data.\n\n"
        "Write in markdown, concise and technical, no preamble. Never use LaTeX: write "
        "formulas as plain text an engineer would write by hand -- V = Ah x W, "
        "sqrt(55.7), d^2."
    ),
    "consult": _BASE + _SITE_RULES + (
        " A design professional is consulting you on one topic about this project. The "
        "topic and the project's relevant computed state are supplied. Write a standalone "
        "advisory memo: **Position** (2-3 sentences -- where the project stands on this "
        "topic, based only on the supplied state), **Analysis** (bullets -- the governing "
        "factors with their actual numbers and the IS/NBC clause each traces to, when the "
        "data shows one), **Options** (bullets -- realistic courses of action, each with "
        "its cost/benefit in the project's own figures; if the data supports only one "
        "course, say so instead of inventing alternatives), and **Recommended Next Step** "
        "(1-2 sentences, concrete). Never state a project figure that is not in the "
        "supplied state; if the data is insufficient for a firm opinion, say what is "
        "missing and give a conditional answer. Under 450 words."
    ),
    "explain": (
        "You are explaining one computed figure from the Aptimizer engineering engine to "
        "the engineer viewing it. You are given the figure's label, its value, and the "
        "engine's own derivation data for it (inputs, formula, governing clause, "
        "intermediate steps).\n\n"
        "Write, in markdown:\n"
        "1. **What it is** -- one plain-language sentence naming the quantity and its unit.\n"
        "2. **How it was computed** -- the governing formula as plain text, each symbol "
        "defined in a line, then this project's actual inputs substituted and the "
        "arithmetic shown step by step to the final value. Numbered list.\n"
        "3. **Where it comes from** -- the IS/NBC clause or engine module it traces to, "
        "and one sentence on why the code requires it.\n"
        "4. **What moves it** -- bullets naming the 2-3 inputs with the most leverage on "
        "this figure and the direction each pushes it.\n\n"
        "STRICT RULES:\n"
        "- Derive only from the supplied derivation data. Never supply a clause number, "
        "limit, formula or intermediate value that is not in the data -- say the engine "
        "did not expose it instead.\n"
        "- Never compute a *different* design value; your job is to re-show this one.\n"
        "- Never use LaTeX: formulas as plain text -- V = Ah x W, sqrt(55.7), Ta, fck.\n"
        "- Under 300 words. No preamble, no hedging boilerplate."
    ),
}
