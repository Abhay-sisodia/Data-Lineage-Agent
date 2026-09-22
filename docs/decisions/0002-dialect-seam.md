# ADR-0002 · The dialect seam, and what a second dialect costs

**Date:** 2026-09-23 · **Status:** accepted (Phase A complete); one decision deferred · **Task:** port

Phase 0 was measured on Oracle, so every dialect-specific fact was written where it was
needed. That was right — a constant is the correct way to write a fact you have measured
exactly once — but it meant the cost of a second dialect was spread across the whole tree.
This ADR records where that cost actually sat, what the seam holds, and the one question
still open.

---

## 1 · What was measured before anything was built

A survey, not an estimate:

| coupling | count | where |
|---|---|---|
| SQLGlot dialect constant | 27 sites | 5 modules, each with its own `DIALECT = "oracle"` |
| identifier folding | 144 `.upper()` | 21 modules |
| ANTLR grammar classes | 14 context names | 5 analysis modules |
| catalogue queries | 6 | `resolution/dictionary.py` |

And, more usefully, what turned out **not** to be coupled:

* **Band 0 entirely.** `band0.py` has 71 SQLGlot references and **zero** references to the
  Oracle grammar. Traversal, scopes, joins, CTEs, windows, the transform ladder and filter
  phase are all expressed against SQLGlot's typed tree, which SQLGlot normalises per dialect.
* **`transforms.py`.** It matches typed nodes (`exp.Sum`, `exp.Case`, `exp.Nvl2`), not
  function-name strings. `NVL` arrives as `Coalesce` whichever dialect wrote it.
* **The IR, the match key, bands, flows, phases, boundaries, the refusal taxonomy and the
  scoring harness.** None of them mentions a dialect, and none should start.

**Consequence, and it is the load-bearing one: set-based lineage ports for the cost of a
string.** The procedural half is where the work is.

## 2 · A dialect is four members

```
name       the dialect's own name, which is also its SQLGlot dialect name
fold       how the catalogue stores an unquoted identifier
frontend   parses a program and answers ~24 structural questions about it
catalogue  reads a Dictionary out of a live database - 6 methods
```

Two invariants are **checked at registration**, because both fail silently and totally:

1. **The registry key IS the SQLGlot name.** A1 stored the key on `Dictionary` and handed
   it to `sqlglot.parse_one`. For Oracle they coincide, so nothing distinguished them until
   a test registered under a different key and every statement refused with *"Unknown
   dialect"*.
2. **`fold` must agree with SQLGlot's `normalize_identifiers` for that name.** A dialect
   with `name="oracle"` and `fold=lower` cannot exist: the dictionary is keyed one way and
   the parse tree the other, so nothing binds — zero edges, no error, every relation
   reported as a dangling reference.

## 3 · The folding rule, and how it nearly failed

SQLGlot already folds per dialect after `qualify` (Oracle → `CUST_ID`, PostgreSQL →
`cust_id`, MySQL preserved). So the codebase's `.upper()` calls were a **no-op on Oracle**
and would have re-folded PostgreSQL names upward into something no catalogue contains.

70 of 144 sites became `fold`; the other 74 are listed with reasons in
`lineage/dialects/base.py`. Because Oracle's `fold` *is* `upper`, every one of those edits
was provably a no-op on the signed measurement — which is what made the refactor checkable.

**`Node._normalise` upper-cased every name in the IR**, silently undoing all seventy
conversions at once. On Oracle nothing detectable happened. It was found only by a test that
runs the analysis under a dialect folding the *other* way — with a control pinned beside it,
because an empty edge list satisfies "nothing escaped folding" perfectly, and did, twice.

## 4 · What does not survive the port

Handled by saying so, not by pretending:

* **Synonyms** do not exist in PostgreSQL or MySQL. Those catalogues return `{}` and
  `resolve` never redirects — honestly different from redirection we failed to read.
* **Packages** do not exist in either. Package-level state is band 1's hardest case.
* **Global temporary tables** have no exact analogue; under-reporting loses edges rather
  than inventing them, which is the right direction for a flag whose purpose is stopping
  lineage being composed through session-private state.
* **`MERGE`** is PostgreSQL 15+ and absent from MySQL.
* **`:NEW`/`:OLD`** is Oracle's spelling; PostgreSQL triggers are separate functions bound
  by `CREATE TRIGGER … EXECUTE FUNCTION`, so trigger→body is a second lookup.

## 5 · DEFERRED — which PL/pgSQL grammar, and whether to vendor one

**Status: open. Parked 2026-09-23. Not a blocker.**

A PL/pgSQL body arrives as a dollar-quoted string literal inside
`CREATE FUNCTION … AS $$ … $$` — it is **not part of the SQL parse tree**. Reading it needs
an extraction stage Oracle never required, and then a grammar. Three options:

1. **Vendor grammars-v4's `plpgsql`** alongside the Oracle one. Highest coverage, a second
   generated parser to maintain, and its real capability is unknown — the Oracle grammar's
   own limits (GL-002, the named `WINDOW` clause) were only found by probing it.
2. **Hand-write a front end for the subset the analysis asks about.** The protocol is ~24
   methods and most are mechanical. Smaller, no new dependency, and its limits would be ours
   rather than a third party's.
3. **Defer the procedural half entirely** and ship band 0 for PostgreSQL first.

**Deferred because it gates only the `frontend` member.** `name`, `fold` and `catalogue`
need none of it, and band 0 needs nothing from a front end beyond statement boundaries — so
option 3 is available immediately and is what Phase B does first. The choice between 1 and 2
should be made on evidence, by probing what grammars-v4's `plpgsql` actually handles, the way
`scripts/probe_parsers.py` did for Oracle. That probe is the next step whenever this is
picked up.

**What it costs while deferred:** PL/pgSQL function bodies are not analysed, so PostgreSQL
gets band 0 and not bands 1 and 2. That must be **declared per unit**, not left silent — a
body we can see and cannot read is a boundary, not an absence.
