"""Clause-text retrieval over a corpus of code documents the operator supplies.

The registry in iscodes is 62 entries of code, clause number and topic. That is enough to
check a citation and never enough to answer with. When an engineer asks what the
span/depth rule actually says, the only honest answers are the clause text or nothing --
and a model asked to supply the text from memory will supply something: fluent, plausible,
and not the code. So the assistant is handed the passage or it is handed nothing, and it
is told which of the two happened.

BIS and NBC text is copyrighted, so none of it ships here. The corpus is whatever the
operator legitimately holds, dropped into codes_corpus as .txt or .md. An absent corpus is
the normal state of a fresh checkout, so every entry point below returns empty rather than
raising: retrieval that is not configured must look like a feature that has nothing to
say, not like a broken server.

Chunks are clauses, not windows of tokens. A splitter that cuts every 500 characters will
one day hand back the second half of a load table with its heading in the previous chunk,
and the reader has no way to tell. So the split follows the document's own numbering --
the same designations citations.py parses on the way back out, imported from there rather
than copied, because two regexes for one thing drift and then the guard rejects the very
text the index served. Tables are never cut and never folded away: a load table sliced
mid-row is worse than a load table that was not found.

Scoring is three signals rather than one. Embeddings are good at "what stops a slab
deflecting too much" and bad at "Cl. 23.2.1", which is what engineers type all day, so
keyword overlap and an explicit designation match carry the queries the vectors handle
worst. With no API key there are no vectors at all; the remaining two weights are
renormalised over their own sum so RELEVANCE_FLOOR keeps meaning the same thing, because
a floor that a keyword-only deployment can never clear would answer nothing and blame the
corpus. Hits from that mode are flagged degraded so the caller can say so out loud.

A silently mismatched index is the failure worth guarding hardest: vectors from one
embedding model scored against queries from another return confident nonsense in perfect
ranking order. The manifest records the model and the dimension, and a mismatch refuses
the vectors and drops to keyword-only rather than pretending. The same manifest is stat-ed
on every query, because the second way to serve a stale index is to load a good one and
never notice it was rebuilt underneath.

A query scores a candidate set rather than the corpus. A chunk outside it shares no token
with the query and answers to none of its designations, so two of the three signals are
zero and the vector term alone decides it -- and a chunk that cannot reach RELEVANCE_FLOOR
on that term alone cannot reach it at all. That makes the set an exact reformulation of the
full scan and not a recall/latency trade: whatever is skipped was going to be dropped. It
stops being exact the moment a signal can score above zero without a token, a designation
or a vector, so a fourth signal has to be added to the candidate union as well as the loop.

The keyword normaliser divides by every query token, including the ones the corpus has
never seen, at a default idf. That reads like an accident and is load-bearing: it makes the
keyword score a measure of how much of the question was covered. Restricted to tokens the
corpus holds, a question whose one familiar word is "water" scores a perfect 1.0 against
the first passage that mentions water, and the empty answer this module exists to make
possible stops being reachable.

Nothing here computes. Retrieval finds the passage; the engine owns every number in it.
"""
import argparse
import hashlib
import heapq
import json
import logging
import math
import os
import random
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from glob import glob
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import numpy as np

import ai
import citations
import iscodes as C

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- tunables
# Everything in this block is a judgement call, not a derived quantity. Change one and
# retrieval behaves differently; nothing recalculates itself to match.

DEFAULT_EMBED_MODEL = "gemini-embedding-001"     # GEMINI_EMBED_MODEL overrides it

_HERE = os.path.dirname(os.path.abspath(__file__))
# Defaults only. CODES_CORPUS_DIR and CODES_INDEX_DIR are read on every call rather than
# captured at import, so a test can point the module at a fixture directory with
# monkeypatch.setenv without reloading it.
CORPUS_DIR = os.path.join(_HERE, "codes_corpus")     # CODES_CORPUS_DIR overrides
INDEX_DIR = os.path.join(_HERE, "codes_index")       # CODES_INDEX_DIR overrides

# Vectors lead because most questions are phrased rather than numbered. The other two
# exist because the numbered ones are exactly what a vector model is worst at.
W_VECTOR, W_KEYWORD, W_DESIGNATION = 0.55, 0.25, 0.20

RELEVANCE_FLOOR = 0.15    # below this the corpus does not answer the question; say so
BM25_K1, BM25_B = 1.2, 0.75   # saturation and length normalisation, the usual defaults
QUERY_CACHE_SIZE = 256    # distinct query vectors kept; a repeat question re-embeds nothing
MIN_CHUNK_CHARS = 200     # shorter than this is a cross-reference, not a passage
MAX_CHUNK_CHARS = 1500    # longer than this and the passage has stopped being one idea
EMBED_BATCH = 64          # chunks per embedding request, not one request per chunk

HEADING_MAX_CHARS = 100   # past this, the rest of a clause line is its first sentence
TABLE_RUN_LINES = 3       # rows it takes before a block counts as tabular
CODE_HEADER_LINES = 40    # how far into a file to look for the standard it names

VECTORS_FILE = "vectors.npy"
CHUNKS_FILE = "chunks.json"
MANIFEST_FILE = "manifest.json"

# Gemini embeddings are asymmetric: the document and the question are embedded with
# different task types and the pair scores better than either used for both.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_QUERY = "RETRIEVAL_QUERY"


def _corpus_dir() -> str:
    return (os.environ.get("CODES_CORPUS_DIR") or "").strip() or CORPUS_DIR


def _index_dir() -> str:
    return (os.environ.get("CODES_INDEX_DIR") or "").strip() or INDEX_DIR


def _embed_model() -> str:
    return (os.environ.get("GEMINI_EMBED_MODEL") or "").strip() or DEFAULT_EMBED_MODEL


# ---------------------------------------------------------------- text shapes
_ALNUM = re.compile(r"[^a-z0-9]+")
_TOKEN = re.compile(r"[a-z0-9]+(?:\.[a-z0-9]+)*")
_WIDE_GAP = re.compile(r"\S {2,}(?=\S)")
# A clause line that names no keyword: "23.2.1 Control of Deflection", or the number alone
# with its heading on the next line. citations' pattern deliberately demands the word
# "Cl."/"Table" first, so that a stray "1.5" in running prose is never read as a citation;
# at the head of a line in a code document that same string is the clause, which is the one
# case the guard cannot be reused for.
_NUMBERED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,3}){0,5})[.)]?(?:\s+(\S.*))?$")
# Bare dotted numbers, for a query typed as "23.2.1" with no "Cl." in front of it.
_DOTTED = re.compile(r"\b\d{1,2}(?:\.\d{1,3}){1,5}\b")

_KEYWORD_KINDS = {
    "cl": ("clause", "Cl. "), "clause": ("clause", "Cl. "),
    "table": ("table", "Table "),
    "annex": ("annex", "Annex "), "annexure": ("annex", "Annex "),
    # A figure caption in a text dump is followed by prose about the figure, not by rows,
    # so it is chunked as a clause and only its label says otherwise.
    "fig": ("clause", "Fig. "), "figure": ("clause", "Fig. "),
}

STOPWORDS = frozenset("""
a an and are as at be by do does for from how i if in into is it its of on or per shall
so that the their there this to was what when where which who why will with
""".split())

# Civil-engineering colloquialisms mapped to BIS standard terminology. A query using
# everyday language should find the clause that uses the standard's own words.
SYNONYMS: Dict[str, List[str]] = {
    "rebar": ["reinforcement", "bar"],
    "concrete cover": ["nominal cover", "clear cover"],
    "rcc": ["reinforced concrete"],
    "dead load": ["permanent load", "self weight"],
    "live load": ["imposed load"],
    "wind load": ["wind pressure", "design wind"],
    "earthquake": ["seismic"],
    "quake": ["seismic"],
    "deflection": ["span to effective depth", "control of deflection"],
    "crack width": ["cracking", "crack control"],
    "mix design": ["binder content", "water binder ratio"],
    "durability": ["exposure class", "nominal cover"],
    "slenderness": ["unsupported length", "effective length"],
    "footing": ["foundation", "isolated footing"],
    "raft": ["raft foundation", "mat foundation"],
    "shear wall": ["structural wall"],
    "beam design": ["flexural member"],
    "slab design": ["one way slab", "two way slab"],
    "rainwater": ["rainwater harvesting", "rwh"],
    "stp": ["sewage treatment"],
    "fire escape": ["protected route", "travel distance"],
    "staircase pressure": ["pressurisation", "stair enclosure"],
}

# Precomputed: for each single token that appears in a synonym key, the set of
# expansion tokens to add.  Built once at import so _expand_query is a fast lookup.
#
# NOTE: a multi-word key is registered under EACH of its tokens, so "crack width" fires on
# the bare word width and "staircase pressure" fires on staircase. That is almost certainly
# wrong -- but on a sparse corpus the surplus vocabulary is also acting as a recall crutch,
# and narrowing it to whole-phrase matching made a stair-width query retrieve nothing at
# all in a bench corpus. Do not change it without measuring on the real corpus first.
_SYN_LOOKUP: Dict[str, Set[str]] = {}
for _syn_key, _syn_vals in SYNONYMS.items():
    _key_tokens = _TOKEN.findall(_syn_key.lower())
    _expansion = set()
    for _v in _syn_vals:
        _expansion |= {t for t in _TOKEN.findall(_v.lower())
                       if len(t) > 1 and t not in STOPWORDS}
    for _kt in _key_tokens:
        if _kt not in STOPWORDS and len(_kt) > 1:
            _SYN_LOOKUP.setdefault(_kt, set()).update(_expansion)


def _expand_query(tokens: Set[str]) -> Set[str]:
    """Widen query tokens with domain synonyms so colloquial terms match code prose."""
    expanded = set(tokens)
    for t in tokens:
        extra = _SYN_LOOKUP.get(t)
        if extra:
            expanded |= extra
    return expanded


def _squash(text: str) -> str:
    """'IS 875 (Part 3)' and 'is875_3' both flatten to the same key."""
    return _ALNUM.sub("", (text or "").lower())


def _tokens(text: str) -> Set[str]:
    return {t for t in _TOKEN.findall((text or "").lower())
            if len(t) > 1 and t not in STOPWORDS}


def _column_gaps(line: str) -> int:
    """How many column separators a line carries.

    A pipe, or a run of two-or-more spaces between two non-space runs. One wide gap is a
    sentence with an awkward space in it; two means somebody laid out columns.
    """
    if "|" in line:
        return line.count("|")
    return len(_WIDE_GAP.findall(line))


def _mostly_rows(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < TABLE_RUN_LINES:
        return False
    rows = sum(1 for ln in lines if _column_gaps(ln) >= 2)
    return rows >= TABLE_RUN_LINES and rows * 2 >= len(lines)


def _heading_of(rest: str) -> str:
    """The remainder of a clause line, when that remainder is a title rather than prose.

    Code documents do not punctuate headings, so a trailing full stop and anything longer
    than a title means the clause body simply began on the same line as its number. The
    text keeps that line either way -- only the `heading` field is left empty.
    """
    r = rest.strip().lstrip("-–—:.").strip()
    if not r or len(r) > HEADING_MAX_CHARS or r.endswith((".", ";", ",")):
        return ""
    return r


def _boundary(line: str) -> Optional[Tuple[str, str, str]]:
    """(kind, designation, heading) when this line opens a new clause, else None.

    False boundaries are cheap and missed ones are not: a spurious split produces one
    oddly-labelled chunk that the merge rule usually folds straight back into its parent,
    while a missed split welds two clauses together and puts the first one's number on the
    second one's text. So the test leans permissive.
    """
    s = line.strip()
    if not s or _column_gaps(line) >= 2:      # a table row, not a heading
        return None
    m = citations.CLAUSE_RE.match(s)
    if m:
        word = s[:m.start(1)].strip().rstrip(".").lower()
        kind, label = _KEYWORD_KINDS.get(word, ("clause", "Cl. "))
        return kind, label + m.group(1).rstrip("."), _heading_of(s[m.end():])
    m = _NUMBERED_HEADING.match(s)
    if not m or m.group(1).startswith("0"):
        # A designation opening with 0 is the leading digit of a figure ("0.6 Vz2 ...").
        return None
    rest = m.group(2)
    # A bare integer on its own line is a page number far more often than a clause, and a
    # number followed only by more numbers is a table row that happened to fit under the
    # two-separator test -- splitting there would cut the table mid-row.
    if not (("." in m.group(1) or rest) and (rest is None or re.search(r"[A-Za-z]", rest))):
        return None
    return "clause", "Cl. " + m.group(1), _heading_of(rest or "")


def _slug(clause: str) -> str:
    """The chunk_id tail. 'Cl. 23.2.1' -> '23.2.1', 'Table 5' -> 'table-5'.

    The kind stays in the slug for tables and annexes because Table 2 and Cl. 2 are
    different passages and would otherwise claim the same id.
    """
    n = citations._norm_clause(clause)
    low = clause.lower()
    for prefix in ("table", "annex", "fig"):
        if low.startswith(prefix):
            return f"{prefix}-{n}"
    return n


def _designations(chunk: Dict[str, Any]) -> Set[str]:
    """Every clause number this chunk answers to: its own, plus the short children folded
    into it. A citation to a merged child must resolve, or the guard reports the app's own
    retrieved text as unverified."""
    out = {citations._norm_clause(chunk.get("clause", ""))}
    out.update(citations._norm_clause(m) for m in (chunk.get("merged") or []))
    return {d for d in out if d}


def _letters(i: int) -> str:
    """0 -> 'a', 25 -> 'z', 26 -> 'aa'. Split parts are lettered so that the clause number
    in the id stays readable as a clause number."""
    out = ""
    while True:
        out = chr(ord("a") + i % 26) + out
        i = i // 26 - 1
        if i < 0:
            return out


# ---------------------------------------------------------------- chunking
def _depth(clause: str) -> int:
    n = citations._norm_clause(clause)
    return n.count(".") + 1 if n else 0


def _parent_for(done: List[Dict[str, Any]], child: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The chunk a too-short clause belongs under.

    Preferring a true dotted ancestor over the merely-previous chunk matters because a
    two-line clause is usually a sub-item of the clause above it, and attaching 23.2.1.1 to
    Cl. 22 would file it under a rule it has nothing to do with. Tables are never candidates
    -- prose appended to a table is prose read as a row.
    """
    own = citations._norm_clause(child.get("clause", ""))
    usable = [c for c in done if c["kind"] != "table"]
    if not usable:
        return None
    if own:
        for c in reversed(usable):
            other = citations._norm_clause(c.get("clause", ""))
            if other and own.startswith(other + "."):
                return c
        for c in reversed(usable):
            if 0 < _depth(c.get("clause", "")) < _depth(child.get("clause", "")):
                return c
    return usable[-1]


def _blocks(text: str) -> List[str]:
    """Paragraphs, with tabular runs glued back together.

    A table that carries a blank line between its header and its rows is still one table,
    and splitting there is the mid-row cut this module exists to prevent.
    """
    raw = [b for b in re.split(r"\n\s*\n", text) if b.strip()]
    out: List[str] = []
    for b in raw:
        if out and _mostly_rows(out[-1]) and _mostly_rows(b):
            out[-1] = out[-1] + "\n\n" + b
        else:
            out.append(b)
    return out


def _split_long(chunk: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Break an over-long chunk at paragraph boundaries, never inside one."""
    if chunk["kind"] == "table" or len(chunk["text"]) <= MAX_CHUNK_CHARS:
        return [chunk]
    parts: List[List[str]] = []
    current: List[str] = []
    size = 0
    for block in _blocks(chunk["text"]):
        # A single paragraph over the cap goes out over the cap. An over-long passage is a
        # reading inconvenience; a sentence cut in half is a misquoted code provision.
        if current and size + len(block) + 2 > MAX_CHUNK_CHARS:
            parts.append(current)
            current, size = [], 0
        current.append(block)
        size += len(block) + 2
    if current:
        parts.append(current)
    if len(parts) < 2:
        return [chunk]
    # Every part keeps the parent's clause, heading and merged list: they are all the same
    # clause, and a citation to it must hit whichever part carries the answer.
    return [{**chunk, "chunk_id": f"{chunk['chunk_id']}({_letters(i)})",
             "text": "\n\n".join(p), "merged": list(chunk["merged"])}
            for i, p in enumerate(parts)]


def chunk_text(text: str, *, code_id: Optional[str], code: str,
               stem: str) -> List[Dict[str, Any]]:
    """Split one code document into clause-level chunks.

    Pure: no file access, no network, no index. Everything the caller needs to know about
    the document (which standard it is, what to call it) is passed in, so this is the one
    piece of the module a test can exercise on an invented string.
    """
    body = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    prefix = _squash(code_id or stem) or "doc"
    code_norm = citations._norm_code(code)

    def new(kind: str, clause: str, heading: str) -> Dict[str, Any]:
        return {"chunk_id": "", "code_id": code_id, "code": code, "code_norm": code_norm,
                "clause": clause, "heading": heading, "text": "", "kind": kind,
                "merged": []}

    chunks: List[Dict[str, Any]] = []
    lines: List[List[str]] = []
    current = new("clause", "", "")           # anything before the first designation
    buffer: List[str] = []
    for line in body.split("\n"):
        b = _boundary(line)
        if b is None:
            buffer.append(line)
            continue
        chunks.append(current)
        lines.append(buffer)
        kind, clause, heading = b
        current, buffer = new(kind, clause, heading), [line]
    chunks.append(current)
    lines.append(buffer)

    for chunk, buf in zip(chunks, lines):
        chunk["text"] = "\n".join(buf).strip()
        # A caption-less run of columns is still a table, and the length split has to leave
        # it alone whether or not the document bothered to number it.
        if chunk["kind"] == "clause" and _mostly_rows(chunk["text"]):
            chunk["kind"] = "table"

    # The preamble earns a chunk only if it is a passage. Otherwise it is a title page.
    if chunks and not chunks[0]["clause"]:
        head = chunks.pop(0)
        if len(head["text"]) >= MIN_CHUNK_CHARS:
            first = next((ln.strip() for ln in head["text"].split("\n") if ln.strip()), "")
            head["heading"] = first[:HEADING_MAX_CHARS]
            head["chunk_id"] = f"{prefix}#preamble"
            chunks.insert(0, head)

    kept: List[Dict[str, Any]] = []
    for chunk in chunks:
        if not chunk["text"]:
            continue
        # A table is never folded away, however short: two rows and a caption are the
        # answer to a load question, and appended to a parent they read as its prose.
        short = (len(chunk["text"]) < MIN_CHUNK_CHARS and bool(chunk["clause"])
                 and chunk["kind"] != "table")
        parent = _parent_for(kept, chunk) if short else None
        if parent is not None:
            parent["text"] = parent["text"].rstrip() + "\n\n" + chunk["text"].strip()
            parent["merged"].append(chunk["clause"])
            continue
        kept.append(chunk)

    out: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for chunk in kept:
        if not chunk["chunk_id"]:
            chunk["chunk_id"] = f"{prefix}#{_slug(chunk['clause']) or 'text'}"
        # A document that numbers two different passages the same way still has to produce
        # two addressable chunks.
        n = seen.get(chunk["chunk_id"], 0) + 1
        seen[chunk["chunk_id"]] = n
        if n > 1:
            chunk["chunk_id"] = f"{chunk['chunk_id']}-{n}"
        out.extend(_split_long(chunk))
    return out


# ---------------------------------------------------------------- corpus
def _id_lookup() -> Dict[str, str]:
    """Every spelling of a code id we might meet as a filename, mapped to its library id."""
    exact: Dict[str, str] = {}
    base: Dict[str, str] = {}
    ambiguous: Set[str] = set()
    for cid, entry in C.CODE_INDEX.items():
        norm = citations._norm_code(entry.get("code", ""))
        for key in (cid, _squash(cid), _squash(norm)):
            if key:
                exact.setdefault(key, cid)
        # 'is875' alone names three different parts, so it names none of them. A part-less
        # key is only usable where exactly one library entry claims it.
        b = _squash(citations._base(norm))
        if not b or b in exact:
            continue
        if base.setdefault(b, cid) != cid:
            ambiguous.add(b)
    for b, cid in base.items():
        if b not in ambiguous:
            exact.setdefault(b, cid)
    return exact


def _first_designation(text: str) -> str:
    head = "\n".join((text or "").splitlines()[:CODE_HEADER_LINES])
    m = citations.STANDARD_RE.search(head) or citations.NBC_RE.search(head)
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""


def _resolve_code(stem: str, text: str) -> Tuple[Optional[str], str]:
    """(code_id, display code) for a corpus file, from its name or from what it calls itself.

    An unrecognised file is still ingested. A corpus of amendments, state by-laws and
    office circulars is exactly what an operator has and exactly what nothing in
    CODE_LIBRARY names; refusing it would make the feature useless to the people with the
    most documents.
    """
    lookup = _id_lookup()
    s = stem.strip().lower()
    cid = s if s in C.CODE_INDEX else lookup.get(_squash(s))
    if cid:
        return cid, C.CODE_INDEX[cid]["code"]
    named = _first_designation(text)
    if named:
        norm = citations._norm_code(named)
        cid = lookup.get(_squash(norm)) or lookup.get(_squash(citations._base(norm)))
        if cid:
            return cid, C.CODE_INDEX[cid]["code"]
        return None, named
    return None, stem.upper()


# A corpus directory collects notes as well as code text -- an operator's own README
# saying where the PDFs came from, a CHANGELOG of what was re-exported. Ingesting those is
# not merely noise: _resolve_code reads a file's opening lines for a designation, so a note
# that happens to mention IS 456 is indexed AS IS 456 and served back as its clause text.
# Skipping them by name is cruder than a stem allow-list and keeps the "anything
# unrecognised is still ingested" promise for the documents that matter.
SKIP_STEMS = {"readme", "notes", "license", "licence", "changelog", "index", "contents"}


def _corpus_files(directory: str) -> List[str]:
    """Sorted so that chunk order, and therefore vector row order, is reproducible."""
    found = glob(os.path.join(directory, "*.txt")) + glob(os.path.join(directory, "*.md"))
    found = [p for p in found
             if os.path.splitext(os.path.basename(p))[0].strip().lower() not in SKIP_STEMS]
    return sorted(found, key=lambda p: os.path.basename(p).lower())


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()

# ---------------------------------------------------------------- embeddings

def _breadcrumb(chunk: Dict[str, Any]) -> str:
    """Enriched text for embedding: [Code > Clause > Heading] followed by raw text.

    The embedding model sees the structural hierarchy, so a query like
    "IS 456 deflection limits" lands closer to Cl. 23.2.1 even when the prose
    says "span to effective depth ratio" without repeating the standard name.
    The raw text stays in chunk["text"] for display and citation.
    """
    parts = [p for p in (chunk.get("code", ""),
                         chunk.get("clause", ""),
                         chunk.get("heading", "")) if p]
    prefix = " > ".join(parts)
    text = chunk.get("text", "")
    return f"[{prefix}]\n{text}" if prefix else text


def _embed_config(task_type: str) -> Any:
    try:
        from google.genai import types
        return types.EmbedContentConfig(task_type=task_type)
    except Exception:       # an SDK without the config type still embeds, just untyped
        return None


def _embed_call(client: Any, model: str, batch: List[str], config: Any) -> Any:
    """One embedding request, retried while the provider is merely busy.

    Deliberately no step-down to another model, which is where this parts company with
    ai._with_retries: a chat answer from a fallback model is still an answer, whereas
    vectors from a second model cannot be compared with the ones already in the index.
    Better to fail the build than to fill the matrix with two incompatible spaces.
    """
    last: Optional[Exception] = None
    for attempt in range(ai.RETRY_ATTEMPTS):
        try:
            if config is not None:
                return client.models.embed_content(model=model, contents=batch, config=config)
            return client.models.embed_content(model=model, contents=batch)
        except Exception as exc:
            last = exc
            if not ai._is_transient(exc):
                logger.warning("embed %s failed permanently: %s", model, exc)
                raise ai.AIFailed(f"Embedding with {model} failed: {exc}") from exc
            if attempt < ai.RETRY_ATTEMPTS - 1:
                delay = ai.RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5)
                logger.info("embed %s busy (attempt %d/%d), retrying in %.1fs",
                            model, attempt + 1, ai.RETRY_ATTEMPTS, delay)
                time.sleep(delay)
    raise ai.AIFailed(
        f"{model} was unavailable after {ai.RETRY_ATTEMPTS} attempts. The provider "
        f"reported: {last}. This is usually temporary -- wait a minute and build again.")


def _response_values(response: Any, expected: int) -> List[List[float]]:
    """Pull the vectors out of whatever shape the SDK returned them in."""
    rows = getattr(response, "embeddings", None)
    if rows is None and isinstance(response, dict):
        rows = response.get("embeddings")
    values: List[List[float]] = []
    for row in rows or []:
        v = getattr(row, "values", None)
        if v is None and isinstance(row, dict):
            v = row.get("values")
        if not v:
            raise ai.AIFailed("The embedding response carried no values.")
        values.append([float(x) for x in v])
    if len(values) != expected:
        raise ai.AIFailed(f"Asked for {expected} embeddings and got {len(values)}.")
    return values


def _embed(texts: Sequence[str], task_type: str) -> List[List[float]]:
    """Embed `texts` in batches of EMBED_BATCH.

    The key is read directly rather than through ai.provider(), because embeddings here are
    Gemini's alone: a Groq or xAI key configures the chat path perfectly well and cannot
    serve this request, and degrading silently on a key that is present would look like a
    broken index rather than a capability nobody enabled.
    """
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        raise ai.AIUnavailable(
            "GEMINI_API_KEY is not set in backend/.env, so code text cannot be embedded and "
            "retrieval falls back to keyword matching. Create a key at "
            "https://aistudio.google.com/apikey.")
    try:
        from google import genai
    except ImportError as exc:      # pragma: no cover - dependency is in requirements.txt
        raise ai.AIFailed("google-genai is not installed. Run: pip install google-genai") from exc

    model = _embed_model()
    client = _client(genai, key)
    config = _embed_config(task_type)
    out: List[List[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = list(texts[start:start + EMBED_BATCH])
        out.extend(_response_values(_embed_call(client, model, batch, config), len(batch)))
    return out


_CLIENTS: Dict[str, Any] = {}
_CLIENT_LOCK = threading.Lock()
_QUERY_VECTORS: "OrderedDict[Tuple[str, str], List[float]]" = OrderedDict()
_QUERY_LOCK = threading.Lock()


def _client(genai: Any, key: str) -> Any:
    """One SDK client per key, reused.

    Constructing it per call builds a fresh connection pool for a single request, which is
    a real share of the latency of embedding one short query -- the build amortises it over
    a whole corpus, a search cannot amortise it over anything.
    """
    cached = _CLIENTS.get(key)
    if cached is not None:
        return cached
    with _CLIENT_LOCK:
        if key not in _CLIENTS:
            _CLIENTS[key] = genai.Client(api_key=key)
        return _CLIENTS[key]


def _embed_query(query: str) -> List[float]:
    """The query vector, remembered for the last QUERY_CACHE_SIZE distinct questions.

    The same question arrives more than once in ordinary use: a retry, two panels on one
    screen, the APT path asking what the ask path just asked. Each repeat otherwise pays a
    network round trip before any ranking can begin. Keyed by model as well as text, so
    reconfiguring GEMINI_EMBED_MODEL can never serve one model's vector against another
    model's index -- the same mismatch _load refuses on the document side.
    """
    cache_key = (_embed_model(), query)
    with _QUERY_LOCK:
        hit = _QUERY_VECTORS.get(cache_key)
        if hit is not None:
            _QUERY_VECTORS.move_to_end(cache_key)
            return hit
    vector = _embed([query], TASK_QUERY)[0]         # outside the lock: this is network I/O
    with _QUERY_LOCK:
        _QUERY_VECTORS[cache_key] = vector
        _QUERY_VECTORS.move_to_end(cache_key)
        while len(_QUERY_VECTORS) > QUERY_CACHE_SIZE:
            _QUERY_VECTORS.popitem(last=False)
    return vector


def _normalise(matrix: np.ndarray) -> np.ndarray:
    """L2-normalise the rows, so cosine similarity is a plain dot product afterwards."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


# ---------------------------------------------------------------- index storage
_INDEX: Optional[Dict[str, Any]] = None
_INDEX_STAMP: Optional[Tuple[str, int, int]] = None
_INDEX_LOCK = threading.Lock()


def clear_cache() -> None:
    """Drop the cached index and the remembered query vectors."""
    global _INDEX, _INDEX_STAMP
    with _INDEX_LOCK:
        _INDEX = None
        _INDEX_STAMP = None
    with _QUERY_LOCK:
        _QUERY_VECTORS.clear()


def _write_json(path: str, payload: Any) -> None:
    """Write through a temporary file, so an interrupted build cannot leave chunks.json
    and manifest.json describing different indexes."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_raw(directory: str) -> Dict[str, Any]:
    """Whatever is on disk, with no compatibility judgement applied yet."""
    out: Dict[str, Any] = {"manifest": {}, "chunks": [], "vectors": None}
    try:
        with open(os.path.join(directory, MANIFEST_FILE), encoding="utf-8") as fh:
            out["manifest"] = json.load(fh) or {}
        with open(os.path.join(directory, CHUNKS_FILE), encoding="utf-8") as fh:
            out["chunks"] = json.load(fh) or []
    except Exception:
        return {"manifest": {}, "chunks": [], "vectors": None}
    vectors_path = os.path.join(directory, VECTORS_FILE)
    if out["manifest"].get("vectors") and os.path.exists(vectors_path):
        try:
            # allow_pickle=False explicitly: this file is read at import-adjacent time
            # from a directory an operator writes to, and a pickled .npy executes on load.
            out["vectors"] = np.load(vectors_path, allow_pickle=False)
        except Exception as exc:
            logger.warning("Could not read %s: %s", vectors_path, exc)
    return out


def _load(directory: str) -> Dict[str, Any]:
    """The index, with the vectors accepted or refused."""
    raw = _read_raw(directory)
    chunks, manifest, vectors = raw["chunks"], raw["manifest"], raw["vectors"]
    if not chunks:
        return {"available": False, "degraded": True, "chunks": [], "vectors": None,
                "manifest": manifest, "bags": [], "keys": [], "dl": [],
                "avgdl": 1.0, "idf": {}, "postings": {}, "by_clause": {}, "by_code": {},
                "by_code_id": {},
                "reason": (f"No clause index in {directory}. Put the code text in "
                           f"{_corpus_dir()} as .txt or .md and run: "
                           f"python -m codesearch build")}

    reason = None
    built_with = manifest.get("embed_model")
    wanted = _embed_model()
    if vectors is not None and built_with and built_with != wanted:
        # Scoring one model's vectors against another's queries produces a confident
        # ranking of the wrong passages, which is worse than having no ranking at all.
        logger.warning("Clause index was built with embedding model %r but the current "
                       "configuration is %r; refusing the vectors and falling back to "
                       "keyword-only. Rebuild with: python -m codesearch build --force",
                       built_with, wanted)
        reason = (f"Index was built with {built_with}, configuration is {wanted}. "
                  f"Vectors refused; rebuild with: python -m codesearch build --force")
        vectors = None
    if vectors is not None:
        dim = manifest.get("dimension")
        if vectors.ndim != 2 or len(vectors) != len(chunks) or (dim and vectors.shape[1] != dim):
            shape = "x".join(str(x) for x in getattr(vectors, "shape", ()))
            logger.warning("Clause index vectors are %s for %d chunks at dimension %s; "
                           "refusing them and falling back to keyword-only.",
                           shape or "unreadable", len(chunks), dim)
            reason = (f"Vector matrix ({shape or 'unreadable'}) does not match {len(chunks)} "
                      f"chunks at dimension {dim}. Rebuild with: "
                      f"python -m codesearch build --force")
            vectors = None
    if vectors is not None:
        # One contiguous float32 block is what the query dot product wants. A matrix that
        # arrives as float64 otherwise doubles the resident size of the index and the cost
        # of every query against it, for precision no cosine ranking can use.
        vectors = np.ascontiguousarray(vectors, dtype=np.float32)

    # Everything below is a pure function of the chunks, so it is built once per load
    # rather than per query. Rebuilding it on every search would make the keyword signal
    # cost the whole corpus each time.
    bags = [_tokens(" ".join((c.get("text", ""), c.get("heading", ""),
                              C.CODE_KEYWORDS.get(c.get("code_id") or "", ""))))
            for c in chunks]
    keys = [_designations(c) for c in chunks]

    # BM25 statistics, and the postings list that keeps a query from touching chunks that
    # share no word with it. Document frequency and postings come out of the same pass,
    # because they are the same walk over the same bags.
    document_frequency: Dict[str, int] = {}
    postings: Dict[str, List[int]] = {}
    for i, bag in enumerate(bags):
        for t in bag:
            document_frequency[t] = document_frequency.get(t, 0) + 1
            postings.setdefault(t, []).append(i)
    dl = [len(b) for b in bags]
    avgdl = sum(dl) / len(dl) if dl else 1.0
    N = len(chunks)
    idf = {t: math.log((N - df + 0.5) / (df + 0.5) + 1.0)
           for t, df in document_frequency.items()}

    # Which rows a designation or a standard could possibly match, so those two signals are
    # dict lookups over a handful of chunks instead of a walk over all of them. Codes are
    # filed under their part-less base as well, because _designation_score compares bases.
    by_clause: Dict[str, List[int]] = {}
    by_code: Dict[str, List[int]] = {}
    by_code_id: Dict[str, List[int]] = {}
    for i, chunk in enumerate(chunks):
        for designation in keys[i]:
            by_clause.setdefault(designation, []).append(i)
        code = chunk.get("code_norm") or ""
        if code:
            by_code.setdefault(code, []).append(i)
            base = citations._base(code)
            if base and base != code:
                by_code.setdefault(base, []).append(i)
        by_code_id.setdefault(chunk.get("code_id") or "", []).append(i)

    return {"available": True, "reason": reason, "degraded": vectors is None,
            "chunks": chunks, "vectors": vectors, "manifest": manifest,
            "bags": bags, "keys": keys, "dl": dl, "avgdl": avgdl, "idf": idf,
            "postings": postings, "by_clause": by_clause, "by_code": by_code,
            "by_code_id": by_code_id}


def _stamp(directory: str) -> Tuple[str, int, int]:
    """What identifies the index on disk, cheap enough to check on every query.

    build() replaces manifest.json through os.replace, so its modification time and size
    move with the index it describes. Without this check the process answering queries
    keeps serving whatever it loaded first, and a rebuild looks like it did nothing until
    somebody restarts the server -- which is indistinguishable, from the outside, from a
    build that silently failed.
    """
    try:
        st = os.stat(os.path.join(directory, MANIFEST_FILE))
        return (directory, int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (directory, 0, 0)


def _index() -> Dict[str, Any]:
    """The cached index, reloaded when the copy on disk changes. Never raises.

    Reached from a thread pool, so the load is done under a lock and published in one
    assignment: two requests arriving on a cold cache must not each build the postings and
    then race to install half of them.
    """
    global _INDEX, _INDEX_STAMP
    directory = _index_dir()
    stamp = _stamp(directory)
    cached, cached_stamp = _INDEX, _INDEX_STAMP     # one read each; no torn pair
    if cached is not None and cached_stamp == stamp:
        return cached
    with _INDEX_LOCK:
        if _INDEX is not None and _INDEX_STAMP == stamp:
            return _INDEX
        try:
            loaded = _load(directory)
        except Exception as exc:
            logger.warning("Could not load the clause index: %s", exc)
            loaded = {"available": False, "reason": f"Could not load the clause index: {exc}",
                      "degraded": True, "chunks": [], "vectors": None, "manifest": {},
                      "bags": [], "keys": [], "dl": [], "avgdl": 1.0, "idf": {},
                      "postings": {}, "by_clause": {}, "by_code": {}, "by_code_id": {}}
        _INDEX, _INDEX_STAMP = loaded, stamp
        return loaded


def index_status() -> Dict[str, Any]:
    """What the index is and whether it can answer. Never raises: this backs a status
    endpoint, and a retrieval feature nobody configured must not read as a server fault."""
    try:
        idx = _index()
        manifest = idx["manifest"]
        return {"available": bool(idx["available"] and idx["chunks"]),
                "reason": idx["reason"],
                "degraded": bool(idx["degraded"]),
                "embed_model": manifest.get("embed_model"),
                "dimension": manifest.get("dimension"),
                "chunk_count": len(idx["chunks"]),
                "built_at": manifest.get("built_at"),
                "files": manifest.get("files") or {}}
    except Exception as exc:        # pragma: no cover - _index already swallows failures
        return {"available": False, "reason": str(exc), "degraded": True,
                "embed_model": None, "dimension": None, "chunk_count": 0,
                "built_at": None, "files": {}}


def clause_pairs() -> Set[Tuple[str, str]]:
    """(code_norm, clause) for everything the corpus actually holds.

    The registry can vouch for a clause it carries a topic line for; the corpus can vouch
    for one it holds the text of. Handing these to the citation guard is what stops it
    flagging a clause the app just quoted in full. Indexed under the part-less code too,
    for the same reason citations does it: "IS 1893 Cl. 7.6.2" is how the clause gets
    written, and the file is is1893p1.
    """
    try:
        chunks = _index()["chunks"]
    except Exception:               # pragma: no cover - _index already swallows failures
        return set()
    pairs: Set[Tuple[str, str]] = set()
    for chunk in chunks:
        code = chunk.get("code_norm") or ""
        if not code:
            continue
        for designation in _designations(chunk):
            pairs.add((code, designation))
            pairs.add((citations._base(code), designation))
    return pairs


# ---------------------------------------------------------------- build
def build(corpus_dir: Optional[str] = None, out_dir: Optional[str] = None,
          force: bool = False) -> Dict[str, Any]:
    """Chunk the corpus, embed it and write the index. Synchronous; embeds inline.

    Unchanged files are reused rather than re-embedded. That is not only a speed
    optimisation: embedding is billed per token, and an operator who adds one circular to a
    corpus of twenty documents should pay for the circular.
    """
    source = corpus_dir or _corpus_dir()
    target = out_dir or _index_dir()
    model = _embed_model()
    files = _corpus_files(source)

    old = {} if force else _read_raw(target)
    old_manifest = old.get("manifest") or {}
    old_chunks = old.get("chunks") or []
    old_vectors = old.get("vectors")
    old_spans = old_manifest.get("chunk_spans") or {}
    old_hashes = old_manifest.get("files") or {}
    # Rows are only reusable if they came out of the same model. Anything else and the
    # matrix would hold two incomparable vector spaces at once.
    vectors_reusable = (old_vectors is not None
                        and old_manifest.get("embed_model") == model
                        and len(old_vectors) == len(old_chunks))

    chunks: List[Dict[str, Any]] = []
    reused: List[Optional[int]] = []          # old row index per chunk, None when new
    hashes: Dict[str, str] = {}
    spans: Dict[str, List[int]] = {}
    skipped: List[Dict[str, str]] = []
    warnings: List[str] = []
    unchanged = 0

    for path in files:
        name = os.path.basename(path)
        try:
            digest = _sha256(path)
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            skipped.append({"file": name, "reason": f"could not be read ({exc})"})
            continue
        hashes[name] = digest
        start = len(chunks)

        span = old_spans.get(name)
        if (not force and old_hashes.get(name) == digest and span
                and 0 <= span[0] <= span[1] <= len(old_chunks)):
            unchanged += 1
            for row in range(span[0], span[1]):
                chunks.append(old_chunks[row])
                reused.append(row if vectors_reusable else None)
            spans[name] = [start, len(chunks)]
            continue

        if not text.strip():
            skipped.append({"file": name, "reason": "empty"})
            continue
        stem = os.path.splitext(name)[0]
        code_id, code = _resolve_code(stem, text)
        produced = chunk_text(text, code_id=code_id, code=code, stem=stem)
        if not produced:
            skipped.append({"file": name,
                            "reason": "no clause designation and too little text to stand alone"})
            continue
        chunks.extend(produced)
        reused.extend([None] * len(produced))
        spans[name] = [start, len(chunks)]

    dimension = 0
    matrix: Optional[np.ndarray] = None
    embedded = 0
    fresh = [i for i, row in enumerate(reused) if row is None]
    carried = [i for i, row in enumerate(reused) if row is not None]
    if chunks and not fresh and carried:
        dimension = int(old_vectors.shape[1])
        matrix = np.vstack([old_vectors[reused[i]] for i in carried]).astype(np.float32)
    elif chunks and fresh:
        try:
            values = _embed([_breadcrumb(chunks[i]) for i in fresh], TASK_DOCUMENT)
            dimension = len(values[0]) if values else 0
            if carried and int(old_vectors.shape[1]) != dimension:
                # Same model, different width -- output_dimensionality changed under us.
                # The carried rows cannot share a matrix with the new ones, so they are
                # embedded again rather than silently left as zeros.
                logger.warning("Reusable vectors are %d wide but %s now returns %d; "
                               "re-embedding them rather than mixing widths.",
                               old_vectors.shape[1], model, dimension)
                values += _embed([_breadcrumb(chunks[i]) for i in carried], TASK_DOCUMENT)
                fresh, carried = fresh + carried, []
            matrix = np.zeros((len(chunks), dimension), dtype=np.float32)
            for i, row in zip(fresh, values):
                matrix[i] = row
            for i in carried:
                matrix[i] = old_vectors[reused[i]]
            embedded = len(fresh)
        except ai.AIUnavailable as exc:
            logger.info("Building a keyword-only index: %s", exc)
        except ai.AIFailed as exc:
            # The chunks cost minutes of work and a keyword-only index is a working index,
            # so the text is written anyway and the failure is reported rather than raised.
            logger.warning("Embedding failed, writing a keyword-only index: %s", exc)
            warnings.append(f"Embedding failed, so the index is keyword-only: {exc}")

    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    manifest = {"embed_model": model if matrix is not None else None,
                "dimension": dimension if matrix is not None else 0,
                "chunk_count": len(chunks), "built_at": built_at, "files": hashes,
                "vectors": matrix is not None, "chunk_spans": spans}

    os.makedirs(target, exist_ok=True)
    vectors_path = os.path.join(target, VECTORS_FILE)
    if matrix is not None:
        tmp = vectors_path + ".tmp.npy"
        np.save(tmp, _normalise(matrix))
        os.replace(tmp, vectors_path)
    elif os.path.exists(vectors_path):
        # A stale matrix paired with fresh chunks is the mismatch this module refuses on
        # load; better not to leave one lying next to a keyword-only index at all.
        os.remove(vectors_path)
    _write_json(os.path.join(target, CHUNKS_FILE), chunks)
    _write_json(os.path.join(target, MANIFEST_FILE), manifest)
    clear_cache()

    return {"corpus_dir": source, "index_dir": target, "files": len(files),
            "files_unchanged": unchanged, "chunks": len(chunks),
            "tables": sum(1 for c in chunks if c["kind"] == "table"),
            "annexes": sum(1 for c in chunks if c["kind"] == "annex"),
            "merged": sum(len(c["merged"]) for c in chunks),
            "embedded": embedded,
            "reused_vectors": len(carried) if matrix is not None else 0,
            "vectors": matrix is not None, "embed_model": manifest["embed_model"],
            "dimension": manifest["dimension"], "built_at": built_at,
            "skipped": skipped, "warnings": warnings}


# ---------------------------------------------------------------- retrieval
def _query_designations(query: str) -> Tuple[str, str]:
    """The normalised code and clause a query names, either of them empty."""
    m = citations.STANDARD_RE.search(query or "") or citations.NBC_RE.search(query or "")
    code = citations._norm_code(m.group(0)) if m else ""
    clause = citations._norm_clause(query or "")
    if not clause:
        # "23.2.1" typed on its own is a clause number to the person typing it, even though
        # citations will not call it one without the word in front.
        bare = _DOTTED.search(query or "")
        clause = bare.group(0).lower() if bare else ""
    return code, clause


def _designation_score(chunk: Dict[str, Any], keys: Set[str],
                       q_code: str, q_clause: str) -> float:
    """How squarely the query named this chunk.

    Exact identifiers are what semantic search is worst at and what engineers type
    constantly, which is why this signal exists at all. Note what it does NOT do: at
    W_DESIGNATION a perfect code-and-clause match contributes 0.20, so it cannot on its own
    outrank a strong vector match at 0.55. It wins the queries it is meant to win because a
    bare "IS 456 Cl. 7.1" is nearly contentless to an embedding model and no chunk scores
    highly on it. If a corpus turns out to bury exact-identifier queries under semantic
    near-misses, W_DESIGNATION is the lever -- it would have to exceed W_VECTOR.

    A code on its own is deliberately scored too low to clear RELEVANCE_FLOOR unaided:
    otherwise "IS 456 Cl. 99.9" would come back with five arbitrary passages from IS 456,
    when the honest answer to a clause the corpus does not hold is nothing at all.
    """
    code = chunk.get("code_norm") or ""
    code_hit = bool(q_code) and (code == q_code
                                 or citations._base(code) == citations._base(q_code))
    clause_hit = bool(q_clause) and q_clause in keys
    if code_hit and clause_hit:
        return 1.0
    if clause_hit:
        return 0.6
    return 0.3 if code_hit else 0.0


def search(query: str, k: int = 5, code_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """The best k passages for `query`, or [] when the corpus does not answer it.

    Synchronous on purpose -- this is the documented signature and it is called from
    scripts and tests as well as from the API. It can make a blocking embedding call for
    the query, so an async handler must reach it through
    fastapi.concurrency.run_in_threadpool rather than calling it inline.

    An empty list is not a failure. It is the honest "I do not have that clause" the whole
    feature exists to make possible.
    """
    idx = _index()
    chunks = idx["chunks"]
    if not chunks:
        return []
    allowed: Optional[Set[int]] = None
    if code_id is not None:
        rows = idx.get("by_code_id", {}).get(code_id) or []
        if not rows:
            return []
        allowed = set(rows)

    raw_q_tokens = _tokens(query)
    q_tokens = _expand_query(raw_q_tokens)
    q_code, q_clause = _query_designations(query)
    if not q_tokens and not q_code and not q_clause:
        return []

    degraded = idx["vectors"] is None
    similarity: Optional[np.ndarray] = None
    if idx["vectors"] is not None:
        try:
            vector = np.asarray(_embed_query(query), dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            if norm:
                # The whole matrix, not a fancy-indexed slice of it. Selecting rows first
                # copies the entire index on every unfiltered query -- tens of megabytes to
                # produce an operand BLAS was going to stream anyway -- and code_id is far
                # cheaper applied to the scores that come out than to the matrix going in.
                similarity = idx["vectors"] @ (vector / norm)
            degraded = similarity is None
        except (ai.AIUnavailable, ai.AIFailed) as exc:
            logger.info("Query embedding unavailable, answering keyword-only: %s", exc)
            degraded = True
        except Exception as exc:
            logger.warning("Query embedding failed, answering keyword-only: %s", exc)
            degraded = True

    if degraded:
        # Renormalised over their own sum, so that a keyword-only deployment scores on the
        # same 0-1 scale as a vector one and RELEVANCE_FLOOR keeps meaning what it means.
        total = W_KEYWORD + W_DESIGNATION
        w_vector, w_keyword, w_designation = 0.0, W_KEYWORD / total, W_DESIGNATION / total
    else:
        w_vector, w_keyword, w_designation = W_VECTOR, W_KEYWORD, W_DESIGNATION

    bags = idx["bags"]
    idf_map = idx.get("idf", {})
    dl_rows = idx.get("dl", [])
    avg_dl = idx.get("avgdl", 1.0) or 1.0
    k1, b = BM25_K1, BM25_B

    # Query-level constants, hoisted out of the scoring loop, which is where max_bm25 used
    # to live -- it never varied by chunk and was paid for once per chunk regardless.
    #
    # The normaliser deliberately sums over EVERY query token, absent ones included, at a
    # default idf. That looks like it under-rates the keyword signal and it is in fact the
    # load-bearing part: it makes the score a measure of how much of the question the chunk
    # covered. Restrict it to tokens the corpus holds and a question whose only familiar
    # word is "water" scores a perfect 1.0 against the first passage mentioning water, and
    # the honest empty answer for an unanswerable question stops being possible.
    scoring_tokens = [t for t in q_tokens if t in idf_map]
    max_bm25 = sum(idf_map.get(t, 0.5) * (k1 + 1) / (1 + k1) for t in q_tokens)

    # The candidate set. A chunk outside it shares no word with the query and answers to
    # none of its designations, so keyword and designation are both zero and the vector
    # term alone decides it; anything that cannot reach RELEVANCE_FLOOR on that term alone
    # cannot reach it at all. Skipping those rows is not an approximation of the full scan,
    # it is the same result without visiting the chunks that were always going to be
    # dropped -- which, on a real corpus and an ordinary question, is nearly all of them.
    candidates: Set[int] = set()
    postings = idx.get("postings", {})
    for t in scoring_tokens:
        candidates.update(postings.get(t, ()))
    if q_clause:
        candidates.update(idx.get("by_clause", {}).get(q_clause, ()))
    if q_code:
        by_code = idx.get("by_code", {})
        candidates.update(by_code.get(q_code, ()))
        candidates.update(by_code.get(citations._base(q_code), ()))
    if similarity is not None and w_vector > 0:
        candidates.update(np.flatnonzero(similarity >= RELEVANCE_FLOOR / w_vector).tolist())
    if allowed is not None:
        candidates &= allowed
    if not candidates:
        return []

    hits: List[Dict[str, Any]] = []
    for i in sorted(candidates):        # row order, so equal scores break the way they did
        chunk = chunks[i]
        # Each signal is already an absolute 0-1 measure and is used as it stands. Rescaling
        # them across the candidate set would hand the best chunk a 1.0 on every query,
        # including the ones the corpus cannot answer, and RELEVANCE_FLOOR would never fire
        # -- which is the one case it exists for.
        vector_score = 0.0
        if similarity is not None:
            vector_score = max(0.0, min(1.0, float(similarity[i])))
        keyword_score = 0.0
        bag = bags[i]
        if max_bm25 > 0 and bag:
            # Term frequency is 1 for every match, because the bag records presence, not
            # counts. That makes the per-term factor identical across terms, so it comes
            # out of the sum and the chunk costs one idf lookup per matched token.
            doc_len = dl_rows[i] if dl_rows else len(bag)
            saturation = (k1 + 1) / (1 + k1 * (1 - b + b * doc_len / avg_dl))
            matched = sum(idf_map[t] for t in scoring_tokens if t in bag)
            keyword_score = max(0.0, min(1.0, matched * saturation / max_bm25))
        designation_score = _designation_score(chunk, idx["keys"][i], q_code, q_clause)
        score = (w_vector * vector_score + w_keyword * keyword_score
                 + w_designation * designation_score)
        if score < RELEVANCE_FLOOR:
            continue
        hits.append({**chunk, "merged": list(chunk.get("merged") or []),
                     "score": round(score, 4),
                     "vector_score": round(vector_score, 4),
                     "keyword_score": round(keyword_score, 4),
                     "designation_score": round(designation_score, 4),
                     "degraded": degraded})
    # nlargest rather than a full sort: k is five and the passing set can be thousands.
    return heapq.nlargest(max(0, k), hits, key=lambda h: h["score"])


# ---------------------------------------------------------------- CLI
def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="codesearch",
        description="Build the clause index from the code corpus the operator supplies. "
                    "No code text is downloaded or bundled; the corpus is yours.")
    sub = parser.add_subparsers(dest="command")
    builder = sub.add_parser("build", help="chunk the corpus, embed it and write the index")
    builder.add_argument("--corpus", default=None, help=f"corpus directory (default {CORPUS_DIR})")
    builder.add_argument("--out", default=None, help=f"index directory (default {INDEX_DIR})")
    builder.add_argument("--force", action="store_true",
                         help="re-chunk and re-embed every file, including unchanged ones")
    args = parser.parse_args(argv)
    if args.command != "build":
        parser.print_help()
        return 1

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_HERE, ".env"))
    except Exception:
        pass            # the key is optional: a keyword-only index is still a working index

    summary = build(args.corpus, args.out, force=args.force)
    print(f"corpus    {summary['corpus_dir']}")
    print(f"index     {summary['index_dir']}")
    print(f"files     {summary['files']} seen, {summary['files_unchanged']} unchanged and reused")
    print(f"chunks    {summary['chunks']} written "
          f"({summary['tables']} tables, {summary['annexes']} annexes, "
          f"{summary['merged']} short clauses merged into their parents)")
    if summary["vectors"]:
        print(f"vectors   {summary['embedded']} embedded, {summary['reused_vectors']} reused "
              f"at dimension {summary['dimension']} with {summary['embed_model']}")
    elif not summary["chunks"]:
        # Nothing was embedded because nothing was chunked. Telling an operator to set a key
        # they may well have set already sends them to debug the wrong half of the system.
        print("vectors   none -- nothing to embed")
    elif not (os.environ.get("GEMINI_API_KEY") or "").strip():
        print("vectors   none -- retrieval will run keyword-only (set GEMINI_API_KEY to embed)")
    else:
        print("vectors   none -- retrieval will run keyword-only (see the warnings above)")
    for entry in summary["skipped"]:
        print(f"  skipped {entry['file']}: {entry['reason']}")
    for warning in summary["warnings"]:
        print(f"  warning {warning}")
    if not summary["chunks"]:
        print(f"\nNothing was indexed. Put .txt or .md code text in {summary['corpus_dir']}.")
    return 1 if summary["warnings"] or not summary["chunks"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
