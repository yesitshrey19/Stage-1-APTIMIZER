# Clause-retrieval fixture corpus — invented text only

Everything in `is456.txt`, `nbc4.txt` and `is1893.md` is **made up**. It was written for
these tests and describes nothing. The clause numbers, headings, prose, tables, constants
and worked example are all fabrications in the register of an Indian standard, and several
constants were chosen to differ deliberately from any published value so that no reader can
mistake a fixture for the code it imitates.

## Why no real text is here, and none may be added

BIS standards and the National Building Code are copyrighted works, sold by the Bureau of
Indian Standards. Committing their clause text to this repository would be redistribution,
whatever the intent, and would not stop being redistribution because the file lives under
`tests/`. The app already handles this correctly everywhere else: `backend/iscodes.py`
carries clause *numbers, titles and the numeric constants the engine needs*, which is the
minimum a citation guard can work from, and never the clause prose. This corpus follows the
same line.

So: do not paste a real clause here, do not paraphrase one from memory, and do not fetch one
to "make the fixture more realistic". A retrieval test needs text with the right *shape* —
numbered clauses, nested sub-clauses, a captioned pipe table, a lettered annex, one clause
long enough to split and one short enough to merge. It does not need text with the right
*content*, and buying realism with someone else's copyright is not a trade this repo makes.

If a future test needs a shape this corpus does not cover, invent another clause. That is
cheaper than a licence.

## What the files are for

The stems are real `CODE_LIBRARY` ids from `backend/iscodes.py` — `is456`, `nbc4`, `is1893` —
so a loader that maps filename to library id, and from there to `CODE_INDEX` and the
`citations.py` `_norm_code` forms (`is456`, `nbcp4`, `is1893p1`), is exercised by the corpus
itself rather than by a hand-written mapping in the test.

This README is documentation, not corpus. A loader should select the three files by stem
rather than globbing the directory, so that these paragraphs never end up in a retrieval
result.
