"""Clause retrieval tests: the chunker, the three scoring signals and the refusals.

Retrieval is the only thing standing between an engineer and a fluently invented clause,
and every way it can fail quietly ends in the same place: an answer in the app's own
confident voice with a clause number attached to it. A table cut between two rows still
reads as a table. A sub-clause folded into its parent and not recorded still reads as
prose. A question the corpus cannot answer, answered anyway from the five nearest
passages, reads exactly like a question it could. So most of what is asserted below is
what retrieval must NOT do, and the empty list is checked as carefully as the hit.

Nothing here needs an API key or a network. The one embedding call in codesearch is
replaced by a five-axis vector space small enough to read on one screen, in which every
similarity is a number this file chose. That is not a compromise: the corpus is invented,
so a real embedding of it would only be a differently arbitrary set of numbers, and a
test that needs a provider is a test that is skipped on the machine where it matters.

The corpus under tests/fixtures/codes_corpus is invented too, for the copyright reasons
its README sets out. Clause numbers, headings, tables and constants there are fabrications
in the shape of an Indian standard, which is all a retrieval test needs.
"""
import sys, os, json, re, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from typing import Any, Dict, List, Sequence

import pytest

import ai
import codesearch

CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "codes_corpus")

# Named so that no test depends on whatever the deployment's real embedding model is, and
# so the mismatch test has a second name to switch to that is obviously not a typo.
FIXTURE_MODEL = "fixture-embed-001"
OTHER_MODEL = "fixture-embed-002"
FIXTURE_DIM = 5

# Two questions the whole file turns on. The first shares not one token with the clause it
# must retrieve -- IS 456 Cl. 7.1 is about steam curing and says none of these words -- so
# only the vectors can reach it, and it is out of reach the moment they are refused. The
# second is a fair question about a subject this corpus simply does not cover.
SEMANTIC_QUESTION = "accelerated hardening of factory-made floor units"
UNANSWERABLE_QUESTION = "What insulation thickness does a chilled water pipe need?"

# Axis 0 is "a passage this space has no opinion about" and axis 1 is the same for a
# question. They are kept apart so that an unmapped question scores zero against an
# unmapped passage instead of one against every one of them.
_NO_OPINION_PASSAGE, _NO_OPINION_QUESTION = 0, 1
_TOPIC_AXIS = {"curing": 2, "cover": 3, "pressurisation": 4}

# Marker phrases, each of which appears in exactly one chunk of the fixture corpus.
_PASSAGE_TOPICS = {"presteaming delay": "curing",              # IS 456 Cl. 7.1
                   "electromagnetic covermeter": "cover",      # IS 456 Cl. 12.4
                   "barometric damper": "pressurisation"}      # NBC 4 Cl. 4.7.4
# A bare designation is deliberately absent from this table: an embedding of "Cl. 7.1"
# lands nowhere in particular, which is the whole reason the designation signal exists.
_QUESTION_TOPICS = {"accelerated hardening": "curing"}


class _Embedder:
    """codesearch's embedding call with a lookup table where the provider would be.

    Both the document and the query task type go through here, so a test can say which
    passage a question is nearest and then assert on the ranking that follows. It also
    records what it was asked to embed, which is how the mismatch test proves the refused
    vectors were never consulted rather than merely outvoted.
    """

    def __init__(self) -> None:
        self.passages: List[str] = []
        self.questions: List[str] = []

    def __call__(self, texts: Sequence[str], task_type: str) -> List[List[float]]:
        question = task_type == codesearch.TASK_QUERY
        (self.questions if question else self.passages).extend(texts)
        topics = _QUESTION_TOPICS if question else _PASSAGE_TOPICS
        fallback = _NO_OPINION_QUESTION if question else _NO_OPINION_PASSAGE
        out: List[List[float]] = []
        for text in texts:
            low = text.lower()
            topic = next((t for marker, t in topics.items() if marker in low), None)
            axis = _TOPIC_AXIS[topic] if topic else fallback
            out.append([1.0 if i == axis else 0.0 for i in range(FIXTURE_DIM)])
        return out


def _fixture(name: str) -> str:
    with open(os.path.join(CORPUS, name), encoding="utf-8") as fh:
        return fh.read()


def _chunk_fixture(name: str) -> List[Dict[str, Any]]:
    """One fixture file through the chunker, resolved the way build() resolves it."""
    stem = os.path.splitext(name)[0]
    text = _fixture(name)
    code_id, code = codesearch._resolve_code(stem, text)
    return codesearch.chunk_text(text, code_id=code_id, code=code, stem=stem)


def _paragraphs(text: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _ok(resp, label: str) -> Dict[str, Any]:
    assert resp.status_code == 200, f"{label}: {resp.status_code} {resp.text[:300]}"
    try:
        return json.loads(resp.text)          # strict: rejects NaN/Infinity
    except ValueError as exc:
        pytest.fail(f"{label}: response is not valid JSON -- {exc}")


@pytest.fixture(autouse=True)
def _no_cached_index():
    """The loaded index lives in a module global that outlives the test.

    citations and every server request read it through the same global, so a fixture index
    left cached follows this file out of the worker and answers somebody else's query with
    invented clause text.
    """
    codesearch.clear_cache()
    yield
    codesearch.clear_cache()


@pytest.fixture
def embedder(monkeypatch) -> _Embedder:
    """The hand-built vector space, in place of the provider, for build and search alike."""
    stub = _Embedder()
    monkeypatch.setattr(codesearch, "_embed", stub)
    return stub


@pytest.fixture
def corpus_env(tmp_path, monkeypatch) -> str:
    """Point the module at the fixture corpus and at an index directory under tmp_path.

    Both are read on every call rather than captured at import, so this needs no reload --
    and the index has to go to tmp_path because a test that writes into the repository
    leaves the next run reading an index it did not build.

    The builder globs the corpus directory for .txt and .md, so the corpus README is
    indexed alongside the three invented standards and lands under IS 456, off the
    "is456" in its own prose. Nothing below asserts on a chunk count for that reason.
    """
    index_dir = str(tmp_path / "index")
    monkeypatch.setenv("CODES_CORPUS_DIR", CORPUS)
    monkeypatch.setenv("CODES_INDEX_DIR", index_dir)
    monkeypatch.setenv("GEMINI_EMBED_MODEL", FIXTURE_MODEL)
    return index_dir


@pytest.fixture
def indexed(embedder, corpus_env) -> Dict[str, Any]:
    """The fixture corpus, chunked and embedded through the stub into the tmp index."""
    return codesearch.build()


# ---------------------------------------------------------------- chunking
def test_chunks_break_where_the_document_numbers_its_clauses():
    """A chunker that splits on length instead of numbering welds the end of one clause
    onto the start of the next and files the pair under the first one's number. Every
    citation the app then serves from that chunk points at text the clause does not
    contain."""
    clauses: List[str] = []
    for chunk in _chunk_fixture("is456.txt"):
        if chunk["clause"] and chunk["clause"] not in clauses:
            clauses.append(chunk["clause"])
    assert clauses == ["Cl. 4.2", "Cl. 4.2.1", "Cl. 7.1", "Cl. 7.1.1", "Cl. 8.2",
                       "Table 5", "Cl. 12.4", "Cl. 23.2", "Cl. 23.2.1"]


def test_a_table_survives_as_one_chunk_with_its_caption_and_every_row():
    """A load table sliced mid-row is worse than a load table that was not found: the
    reader gets four exposure classes of the six, with the caption sitting in a chunk they
    were never shown, and nothing in the answer says a row is missing."""
    chunks = _chunk_fixture("is456.txt")
    tables = [c for c in chunks if c["kind"] == "table"]
    assert len(tables) == 1, [t["chunk_id"] for t in tables]
    table = tables[0]
    assert table["chunk_id"] == "is456#table-5" and table["clause"] == "Table 5"
    assert table["heading"].startswith("Nominal Cover, Free Water-Binder Ratio")

    rows = [ln for ln in _fixture("is456.txt").splitlines() if ln.startswith("|")]
    assert len(rows) == 7                     # a header and six exposure classes
    for row in rows:
        assert row in table["text"], f"row lost from the table chunk: {row[:40]}"


def test_a_short_sub_clause_is_folded_into_its_parent():
    """Two lines qualifying the clause above them are a cross-reference, not a passage,
    and retrieved on their own they answer nothing. Folding them in is only safe if the
    number they were folded under is recorded: a citation to the sub-clause has to keep
    resolving, or the guard reports the app's own retrieved text as unverified."""
    chunks = _chunk_fixture("is456.txt")
    assert all(c["clause"] != "Cl. 23.2.1.1" for c in chunks)

    parent = next(c for c in chunks if c["clause"] == "Cl. 23.2.1")
    assert parent["merged"] == ["Cl. 23.2.1.1"]
    assert "flanged beams" in parent["text"] and "0.83" in parent["text"]
    assert "23.2.1.1" in codesearch._designations(parent)


def test_an_over_long_clause_splits_between_paragraphs_never_inside_one():
    """A clause too long to be one passage still has to come back as whole sentences. A
    part that begins mid-paragraph is quoted as a code provision that starts with a
    subordinate clause, and the reader cannot tell what it was subordinate to."""
    chunks = _chunk_fixture("is456.txt")
    parts = [c for c in chunks if c["clause"] == "Cl. 12.4"]
    assert len(parts) > 1
    assert [c["chunk_id"] for c in parts] == ["is456#12.4(a)", "is456#12.4(b)"]
    assert {c["heading"] for c in parts} == {parts[0]["heading"]}

    source = _fixture("is456.txt")
    whole = source[source.index("12.4 Assessment"):source.index("23.2 Control of Deflection")]
    assert [p for c in parts for p in _paragraphs(c["text"])] == _paragraphs(whole)


# ---------------------------------------------------------------- retrieval
def test_a_clause_number_query_returns_that_clause_first(indexed):
    """"IS 456 Cl. 7.1" is what an engineer types and what a vector model is worst at: the
    stub gives a bare designation no opinion at all, exactly as an embedding of a clause
    number lands nowhere in particular. If the designation signal stops working the query
    returns arbitrary IS 456 passages, or nothing, and both look like the right answer."""
    hits = codesearch.search("IS 456 Cl. 7.1", k=5)
    assert hits, "the corpus holds Cl. 7.1 and the query names it"
    assert hits[0]["chunk_id"] == "is456#7.1"
    assert hits[0]["designation_score"] == 1.0 and hits[0]["vector_score"] == 0.0
    # Cl. 7.1.1 is a different passage whose number merely starts the same way.
    assert all(h["chunk_id"] != "is456#7.1.1" for h in hits)


def test_a_question_sharing_no_words_with_the_clause_still_finds_it(indexed):
    """The reason there are vectors at all. This question and IS 456 Cl. 7.1 have no token
    in common, so keyword overlap scores it zero; without the vector signal the clause that
    answers it is unreachable and the honest reply is that the corpus has nothing."""
    hits = codesearch.search(SEMANTIC_QUESTION, k=5)
    assert hits and hits[0]["chunk_id"] == "is456#7.1"
    assert hits[0]["keyword_score"] == 0.0 and hits[0]["designation_score"] == 0.0
    assert hits[0]["vector_score"] == 1.0 and hits[0]["degraded"] is False


def test_retrieval_still_answers_with_no_api_key_and_says_it_is_degraded(corpus_env, monkeypatch):
    """A deployment with no Gemini key is the normal one, not a broken one. The two
    remaining signals are renormalised over their own sum so that RELEVANCE_FLOOR keeps
    meaning the same thing -- a floor a keyword-only index could never clear would answer
    nothing and leave the operator blaming their corpus. Every hit says it is degraded so
    the caller can say so out loud rather than passing off a keyword match as a semantic
    one."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    summary = codesearch.build()
    assert summary["chunks"] and summary["vectors"] is False
    assert summary["embed_model"] is None

    hits = codesearch.search("travel distance to a protected stair")
    assert hits and hits[0]["chunk_id"] == "nbc4#4.6.2"
    assert all(h["degraded"] is True for h in hits)
    assert all(h["vector_score"] == 0.0 for h in hits)
    assert codesearch.index_status()["degraded"] is True


def test_a_question_the_corpus_cannot_answer_returns_nothing(indexed):
    """The empty list is the feature. This index is healthy and nothing in the corpus covers
    pipe insulation, so the nearest five passages to a question like that are five passages
    about something else -- handed to a model as context they become a fluent answer about
    pipe insulation with an IS number on it."""
    assert codesearch.search(UNANSWERABLE_QUESTION, k=5) == []
    status = codesearch.index_status()
    assert status["available"] is True and status["degraded"] is False


# ---------------------------------------------------------------- over HTTP
# Skipped as a body when MongoDB is not reachable, through the shared api_project fixture.
# There is exactly one TestClient in the process and it comes from tests/conftest.py: a
# second one closes the loop motor bound itself to, and every later request in the worker
# dies with "Event loop is closed".
def test_ask_refuses_a_question_the_corpus_cannot_answer_without_calling_the_model(
        api_project, indexed, monkeypatch):
    """The refusal is the feature, not a degraded path around it. Falling through to an
    unretrieved model answer returns exactly the invented clause the corpus exists to
    replace, and the reader has no way to tell which of the two they were handed -- so the
    provider must not be reached at all, which is what the exploding stub checks."""
    client, _ = api_project

    def _never_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the model was called for a question the corpus cannot answer")

    monkeypatch.setattr(ai, "generate_markdown", _never_called)
    data = _ok(client.post("/api/codes/ask", json={"question": UNANSWERABLE_QUESTION}),
               "codes ask")
    assert data["answered"] is False
    assert data["answer"] == "" and data["sources"] == []
    assert data["reason"] and data["degraded"] is False


def test_an_empty_corpus_is_a_feature_with_nothing_to_say_not_a_broken_server(
        api_project, tmp_path, monkeypatch):
    """An absent corpus is the normal state of a fresh checkout. Every entry point has to
    return empty rather than raise, and the status route has to carry the reason: a UI that
    can only see "no results" tells the operator their search failed, when what happened is
    that nobody has loaded a document yet."""
    client, _ = api_project
    missing = str(tmp_path / "corpus-that-was-never-created")
    monkeypatch.setenv("CODES_CORPUS_DIR", missing)
    monkeypatch.setenv("CODES_INDEX_DIR", str(tmp_path / "index"))
    codesearch.clear_cache()

    assert codesearch.search("span to effective depth ratio") == []
    assert codesearch.clause_pairs() == set()
    assert codesearch.index_status()["available"] is False

    data = _ok(client.get("/api/codes/index"), "codes index")
    assert data["available"] is False and data["chunk_count"] == 0
    assert missing in data["reason"]


# ---------------------------------------------------------------- index compatibility
def test_an_index_built_with_another_embedding_model_is_refused(indexed, embedder, monkeypatch):
    """The failure worth guarding hardest, because it has no symptom. Vectors from one
    model scored against queries from another are not noise -- they are a confident ranking
    of the wrong passages, in the same shape as a good one. Refusing them costs the semantic
    question the clause that answered it, which is the correct price: keyword-only and
    honest beats ranked and wrong."""
    assert codesearch.index_status()["embed_model"] == FIXTURE_MODEL
    monkeypatch.setenv("GEMINI_EMBED_MODEL", OTHER_MODEL)
    codesearch.clear_cache()

    status = codesearch.index_status()
    assert status["available"] is True and status["degraded"] is True
    assert FIXTURE_MODEL in status["reason"] and OTHER_MODEL in status["reason"]

    # Only the vectors could reach IS 456 Cl. 7.1 from this wording. Refused, it is gone,
    # and what comes back in its place is scored on shared words alone -- which is what
    # keyword-only retrieval looks like when it is being honest about what it is.
    semantic = codesearch.search(SEMANTIC_QUESTION, k=5)
    assert all(h["chunk_id"] != "is456#7.1" for h in semantic)
    assert all(h["vector_score"] == 0.0 and h["degraded"] is True for h in semantic)

    hits = codesearch.search("travel distance to a protected stair")
    assert hits and all(h["degraded"] is True for h in hits)
    assert embedder.questions == [], "a refused index must not embed the query either"


_BEFORE = """4.3.2 Travel Distance

The travel distance from any point on a floor to the nearest exit shall not exceed 22.5 m
in a residential occupancy fitted with sprinklers, measured along the actual path of travel
and not in a straight line across the floor plate.
"""

# Words that appear only in the clause the rebuild adds, and that no synonym key expands,
# so what this test measures is whether the new index was picked up, and nothing else.
_NEW_CLAUSE_QUERY = "handrail projection"

_AFTER = _BEFORE + """

4.3.3 Width of Exits

The clear width of any exit staircase shall be not less than 1000 mm for a residential
occupancy, measured between the finished faces of the enclosing walls and clear of any
handrail projection exceeding 100 mm.
"""


def test_a_rebuilt_index_is_picked_up_without_restarting_the_process(embedder, tmp_path,
                                                                     monkeypatch):
    """The index is cached for the life of the process, and a rebuild has to reach it.

    An operator who adds a standard and runs the builder gets no new answers until somebody
    restarts uvicorn -- and from the outside that is indistinguishable from a build that
    silently failed, which is the one thing this module works hardest never to look like.
    The load is keyed on the manifest that build() replaces, so the next query notices.

    The rebuild is staged into a second directory and copied over the live one, because
    that is what the case actually is: the builder runs as its own process, and the process
    holding the stale copy in memory is the one that never called clear_cache(). Calling it
    here would test the fixture instead of the behaviour.
    """
    corpus, live, staged = tmp_path / "corpus", tmp_path / "index", tmp_path / "staged"
    corpus.mkdir()
    monkeypatch.setenv("CODES_CORPUS_DIR", str(corpus))
    monkeypatch.setenv("CODES_INDEX_DIR", str(live))
    monkeypatch.setenv("GEMINI_EMBED_MODEL", FIXTURE_MODEL)

    (corpus / "nbc4.txt").write_text(_BEFORE, encoding="utf-8")
    codesearch.build()
    assert codesearch.search(_NEW_CLAUSE_QUERY, k=5) == []      # loads and caches the index

    (corpus / "nbc4.txt").write_text(_AFTER, encoding="utf-8")
    codesearch.build(out_dir=str(staged))                       # the builder, run elsewhere
    assert codesearch.search(_NEW_CLAUSE_QUERY, k=5) == []      # caches the live index again
    for name in os.listdir(staged):
        shutil.copy2(os.path.join(staged, name), os.path.join(live, name))

    hits = codesearch.search(_NEW_CLAUSE_QUERY, k=5)
    assert any(h["clause"] == "Cl. 4.3.3" for h in hits), \
        "the process is still answering out of the index it loaded first"
