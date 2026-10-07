"""Resolve IS/NBC citations in model output against the real clause registry.

Models state clause numbers with total confidence and get them wrong often enough that an
unchecked citation is worse than none: a plausible "IS 456 Cl. 26.5.1.1" sends an engineer
to a clause that does not say what they were told it says, and the app's authority is what
made them believe it.

So every citation-shaped string in a reply is extracted and looked up. Three outcomes:
  * resolved   -- the code and clause both appear in iscodes.CLAUSES
  * code_only  -- the standard is real and in the registry, the clause number is not
  * unverified -- neither matches anything the app knows

Deleting the bad ones silently would be worse, not better: the reader would see a clean
answer with a hole in it. They are returned alongside the text so the UI can mark them,
and the reader can see exactly which reference to check before relying on it.

The registry has two tiers. The 66 curated entries are the ones that ship, and they are a
list of clause numbers and topics, not of code text. Whatever corpus the operator has
loaded is the second: codesearch knows every clause it holds the text of, and a citation to
one of those is as real as a citation gets -- often it is the app quoting a passage it just
retrieved, and flagging that would discredit the flag on the citations that matter. So the
corpus tier widens what resolves and narrows nothing; with no index built the behaviour is
exactly what it was. An unresolved citation is still reported rather than stripped, for the
reason above and one more: neither tier is the whole of any code, so "not found here" is a
prompt to check, never a finding that the clause does not exist.
"""
import re
from typing import Any, Dict, List, Set, Tuple

import iscodes as C

# "IS 456:2000", "IS 875 (Part 3):2015", "NBC Part 4", "Cl. 7.6.2", "Clause 23.2.1",
# "Table 2". Deliberately loose on the standard number and strict on shape -- a false
# positive costs one lookup, a miss ships an unchecked citation.
_STANDARD = re.compile(
    r"\b(?:IS|SP)\s*[:\s]?\s*(\d{2,5})\s*(?:\(\s*Part\s*(\d+)\s*\))?\s*(?::\s*(\d{4}))?",
    re.IGNORECASE)
_NBC = re.compile(r"\bNBC\b[^.,;)\n]{0,40}?Part\s*(\d+)", re.IGNORECASE)
# The designation is usually numeric ("Cl. 7.6.2", "Table 2"), but annexes are
# lettered ("Annex E") and missing those made real registry entries fail their own
# check. The single-letter alternative is case-sensitive via (?-i:) -- under
# IGNORECASE it would otherwise match the "o" in "Table of contents".
_CLAUSE = re.compile(
    r"\b(?:Cl\.?|Clause|Table|Fig\.?|Figure|Annex(?:ure)?)\s*"
    r"("
    r"[A-Z]?[\d]+(?:\.\d+)*[a-z]?"        # numeric: 7.6.2, 26.5.1.1a
    r"(?:\s*\([a-z]\))?"                    # optional parenthesized sub-item: (a)
    r"(?:\s+Note\s+\d+)?"                   # optional note suffix: Note 2
    r"|(?-i:[A-Z])(?:\.\d+(?:\.\d+)*)?"    # lettered annex: E, E.1, E.1.2
    r")", re.IGNORECASE)

# codesearch chunks the corpus on the same designations this guard parses, so the two can
# never drift apart. The private names keep working; these are the ones to import.
STANDARD_RE = _STANDARD
NBC_RE = _NBC
CLAUSE_RE = _CLAUSE


def _base(code: str) -> str:
    """'is1893p1' -> 'is1893'. Engineers write "IS 1893:2016" as often as
    "IS 1893 (Part 1):2016", and treating the shorter form as unverifiable would flag
    correct citations -- which discredits the flag itself, and then the ones that matter
    get ignored too.

    IS codes only. An NBC part is not an optional refinement of the standard, it is which
    volume you are in -- collapsing 'nbcp47' to 'nbc' would wave through a part that does
    not exist (the NBC has 12).
    """
    if not code.startswith("is"):
        return code
    return re.sub(r"p\d+$", "", code)


def _corpus_tier() -> Tuple[Set[str], Set[Tuple[str, str]]]:
    """The codes and (code, clause) pairs the built clause index can vouch for.

    codesearch imports this module, so the import has to happen here rather than at the top
    of the file: at import time the cycle leaves one of the two half-built. Every failure is
    swallowed and answered with empty sets -- no index, no numpy, an index written by some
    later version of the builder, anything. citations is imported by aptcontext and by the
    server on every request, and a retrieval feature the operator never configured must not
    be the reason the app fails to start.

    The index itself is cached in a module global on codesearch's side, so this costs a walk
    of an already-loaded set rather than a read from disk.
    """
    try:
        import codesearch
        pairs = codesearch.clause_pairs()
    except Exception:
        return set(), set()
    if not pairs:
        return set(), set()
    # clause_pairs() already files every chunk under the part-less code as well, so the
    # pairs go in as they come. The codes fall out of them, because a citation to a standard
    # that exists only in the corpus should read as a clause we cannot confirm, not as a
    # standard nobody has heard of.
    codes = {code for code, _clause in pairs}
    return codes | {_base(c) for c in codes}, pairs


def _registry() -> Dict[str, Any]:
    """Everything the app can vouch for, indexed for lookup.

    Indexed twice: by the exact code and by the code with its part number stripped, so a
    citation that omits the part still resolves against the part the registry holds.

    The curated entries are joined by whatever the clause index holds, which only ever adds
    to what resolves. `clauses` stays curated: nothing reads it, and the pair set is what
    verify() decides on.
    """
    codes, clauses, pairs = set(), set(), set()
    for entry in C.CLAUSES.values():
        code = _norm_code(entry["code"])
        codes.add(code)
        codes.add(_base(code))
        for cl in _all_clauses(entry.get("clause", "")):
            clauses.add(cl)
            pairs.add((code, cl))
            pairs.add((_base(code), cl))
    for entry in getattr(C, "CODE_LIBRARY", []):
        for field in ("code", "standard", "title"):
            v = entry.get(field)
            if isinstance(v, str):
                for m in _STANDARD.finditer(v):
                    n = _norm_code(m.group(0))
                    codes.add(n)
                    codes.add(_base(n))
    corpus_codes, corpus_pairs = _corpus_tier()
    return {"codes": codes | corpus_codes, "clauses": clauses,
            "pairs": pairs | corpus_pairs}


def _norm_code(text: str) -> str:
    """'IS 875 (Part 3):2015' and 'IS 875 Part 3' both normalise to 'is875p3'."""
    m = _STANDARD.search(text or "")
    if m:
        num, part, _year = m.group(1), m.group(2), m.group(3)
        return f"is{num}" + (f"p{part}" if part else "")
    n = _NBC.search(text or "")
    if n:
        return f"nbcp{n.group(1)}"
    return (text or "").strip().lower().replace(" ", "")


def _norm_clause(text: str) -> str:
    m = _CLAUSE.search(text or "")
    if not m:
        return ""
    raw = m.group(1).lower().rstrip(".")
    # Strip parenthesized sub-item and note suffix for registry lookup,
    # so "26.5.1.1(a)" resolves against "26.5.1.1" and "table 5 note 2"
    # resolves against "5".
    raw = re.sub(r"\s*\([a-z]\)$", "", raw)
    raw = re.sub(r"\s+note\s+\d+$", "", raw)
    return raw


def _all_clauses(text: str) -> List[str]:
    """Registry entries often carry two references -- "Table 3 / Annex E", "Cl. 6.4.2 /
    Fig. 2". Indexing only the first makes the second read as unverified."""
    return [m.group(1).lower().rstrip(".") for m in _CLAUSE.finditer(text or "")]


def extract(text: str) -> List[Dict[str, str]]:
    """Every citation-shaped span in the text, with its code and clause parts."""
    found: List[Dict[str, str]] = []
    seen = set()
    # A citation is a standard, optionally followed by a clause within the next stretch of
    # text -- "IS 1893:2016 Cl. 7.6.2" or "NBC Part 3, Cl. 4.2".
    for m in list(_STANDARD.finditer(text or "")) + list(_NBC.finditer(text or "")):
        tail = text[m.end(): m.end() + 60]
        cl = _CLAUSE.search(tail)
        raw = m.group(0).strip()
        if cl and cl.start() < 25:            # close enough to belong to this standard
            raw = (raw + " " + cl.group(0)).strip()
        key = raw.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append({"raw": raw, "code": _norm_code(m.group(0)),
                      "clause": _norm_clause(cl.group(0)) if cl and cl.start() < 25 else ""})
    return found


def verify(text: str) -> Dict[str, Any]:
    """Split the citations in `text` into resolved, code-only and unverified."""
    reg = _registry()
    resolved, code_only, unverified = [], [], []
    for c in extract(text):
        code, base = c["code"], _base(c["code"])
        if c["clause"] and ((code, c["clause"]) in reg["pairs"]
                            or (base, c["clause"]) in reg["pairs"]):
            resolved.append(c["raw"])
        elif code in reg["codes"] or base in reg["codes"]:
            # The standard is real; the clause number is not one the app carries. That is
            # not proof it is wrong -- the registry is not the whole code -- so it is
            # reported as unconfirmed rather than as an error.
            (code_only if c["clause"] else resolved).append(c["raw"])
        else:
            unverified.append(c["raw"])
    return {
        "resolved": resolved,
        "code_only": code_only,
        "unverified": unverified,
        "checked": len(resolved) + len(code_only) + len(unverified),
    }


_NUMBER = re.compile(r"\b(\d+(?:\.\d+)?)\b(?:\s*(mm|m|kN|MPa|Pa|kg|l|%|°C|N/mm|s))?")


def verify_with_extracts(text: str, extracts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extended verify() that also checks numerical claims against extract text.

    A number stated next to a citation should appear in the extract that citation
    came from. A number the extract does not contain is flagged as unconfirmed_value
    rather than silently passed -- the reader can then check the source.
    """
    base_result = verify(text)
    if not extracts:
        return {**base_result, "unconfirmed_values": []}

    # Build a lookup: code_norm -> set of all numbers in that code's extracts
    extract_numbers: Dict[str, Set[str]] = {}
    for ex in extracts:
        code = _norm_code(ex.get("code", ""))
        nums = extract_numbers.setdefault(code, set())
        for m in _NUMBER.finditer(ex.get("text", "")):
            nums.add(m.group(1))
        base_code = _base(code)
        if base_code != code:
            base_nums = extract_numbers.setdefault(base_code, set())
            base_nums.update(nums)

    # Check each citation's surrounding numbers against the extract
    unconfirmed = []
    for cite in extract(text):
        code = cite["code"]
        pos = text.lower().find(cite["raw"].lower())
        if pos < 0:
            continue
        # Check the text immediately following the citation
        window = text[pos + len(cite["raw"]): pos + len(cite["raw"]) + 100]
        code_nums = extract_numbers.get(code, set()) | extract_numbers.get(_base(code), set())
        if not code_nums:
            continue
        for nm in _NUMBER.finditer(window):
            val = nm.group(1)
            # Skip single digit numbers that might be punctuation/enumerations unless in code_nums
            if val not in code_nums:
                unconfirmed.append({"citation": cite["raw"],
                                    "value": nm.group(0).strip()})

    return {**base_result, "unconfirmed_values": unconfirmed}


def registry_context(limit: int = 0) -> List[Dict[str, str]]:
    """The clause entries to hand the model, so it cites from a list rather than memory."""
    # No internal key: it is a lookup name for the app, not something to cite.
    rows = [{"code": v["code"], "clause": v.get("clause", ""),
             "topic": v.get("topic", "")} for v in C.CLAUSES.values()]
    return rows[:limit] if limit else rows
