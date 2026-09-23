# `dictionary_postgres.json` — synthetic, and what that costs

**Not a capture.** There is no PostgreSQL database in this project. `corpus/dictionary.json`
was read out of a live Oracle XE with `capture()`; this one was written by hand.

Stated here rather than left to be inferred, because it lowers the authority of every
PostgreSQL stress result by exactly one step: the Oracle stress packages bind against
objects that were **observed** to exist, and these bind against objects that were
**asserted** to exist. `Dictionary` has `extra="forbid"`, so the note cannot live in the
JSON itself.

**What that does and does not cost.** Name *resolution* is not what these packages test.
The dictionary is deliberately complete, so `NAME_NOT_RESOLVED` never becomes the answer and
a dangling reference in a result is a real finding rather than a gap in this file. What is
tested is construct handling and edge correctness, and neither depends on whether the schema
was observed. The two facts this file could get wrong and hide — a column that does not
exist, a column list in the wrong order — would show up as a missing edge or a wrong
positional binding, not as a pass.

**Lower case throughout.** PostgreSQL folds unquoted identifiers down and `dialects.fold`
follows it. An UPPER key here would never match anything, and would fail *silently* as a
dangling reference — the shape `tests/test_identifier_folding.py` exists for.

**Empty by design, not by omission:**

| field | why empty |
|---|---|
| `synonyms` | PostgreSQL has none — see `PostgresCatalogue.synonyms`, which returns `{}` and explains why that is an answer rather than a gap |
| `view_text` | views are not in the measured scope of `docs/postgres_scope.md` |
| `triggers` | `CREATE TRIGGER` is refused for PostgreSQL; a trigger here would assert band-2 behaviour the scope explicitly does not claim |

The last two are the rule these packages are built on: **a stress package may only use
constructs the scope has measured.** Putting a view in this file would quietly invite one in.
