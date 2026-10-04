# Batch B8 — Procedural (PL/pgSQL): report

**Run 2026-10-04**, against the analyser at commit `b4d0b1f` (B10 complete), unchanged.
Every key was written before the analyser first saw its file, and decisions D-39 to D-42
were taken before any key. Sections 1–5 are the blind measurement, unchanged. **The fixes
(option A, as recommended in section 3) are in section 6.**

```
.\.venv\Scripts\python.exe stress/pg/triage.py b8_procedural
```

## 1. Headline

**Strict: 18 of 169 statement-level facts found. 0 false positives, 0 forbidden edges.
56 refusals.**

The programme plan predicted this batch would be "mostly refused" and asked whether the
refusals are honest. **They are**:
- every routine body, DO block and CREATE TRIGGER is refused by name, with the reason that
  no PL/pgSQL grammar has been chosen (ADR-0002 §5);
- no refused routine leaked a partial or guessed edge;
- every read of an in-script function from outside is declared.

The 18 facts found are the set-based statements at top level. One silent gap remains: CALL
is not counted (B8-02).

| Test | Statements | Seen | Refused | Strict, per statement |
|---|---|---|---|---|
| T1 Breadth | 25 | 23 | 19 | 1 / 49 |
| T2 Depth | 13 | 12 | 9 | 4 / 32 |
| T3 Interaction | 8 | 8 | 7 | 0 / 26 |
| T4 Adversarial | 15 | 15 | 14 | 0 / 11 |
| T5 Metamorphic | 14 | 13 | 7 | 13 / 51 |
| **B8 total** | **75** | **71** | **56** | **18 / 169 (11%)** |

By band, the misses are spread across all three: band 0 (static SQL in bodies, inlined
function reads), band 1 (variables, loops, cursors) and band 2 (dynamic SQL, triggers).
Nothing in bands 1 or 2 can be found until a body can be read.

## 2. Findings, by root cause

### Analyser defects

| ID | What | Where | Grade |
|---|---|---|---|
| **B8-01** | **No PL/pgSQL body is read.** Every `CREATE FUNCTION/PROCEDURE … LANGUAGE plpgsql`, every DO block and every CREATE TRIGGER is refused `UNSUPPORTED_CONSTRUCT` by name. Honest and local: the statements around each routine are analysed normally. But D-39 to D-42 all start from a body that can be read, so **151 of the 169 facts wait on this one capability**. | every routine and DO block (56) | R3, declared |
| **B8-02** | **CALL is silent**: not counted, not refused, nothing declared. D-39 says it adds no lineage, but it is still a statement absent from coverage. | T1 S4, S21; T2 end; T5 end (4) | R1, silent |
| **B8-03** | **A read of an in-script function in FROM is declared `dangling_reference`** ("row source is a function; its shape is its return type"). Honest, but D-40 inlines a RETURN QUERY function. And for the RETURN NEXT case, which D-40 *does* declare, the kind is `dangling_reference`, not the `source_unavailable` the key expects. | T1 S11, S22; T2 S11; T3 S4, S5; T4 S9; T5 P4 A | R3, declared |
| **B8-04** | **A scalar function used in a query is declared `source_unavailable`** ("could not be summarised"). The caller's other columns are analysed and correct. The function's contribution waits on B8-01 (rule 6). A top-level `SELECT f()` gets the same declaration. Under D-39 that call adds no edges, so once bodies are read this declaration should become a plain count. | T1 S2, S13; T2 S9 | R3, declared |

### Expected boundaries the key states, and why they are undeclared

| Subject | Why |
|---|---|
| `dynamic_sql EXECUTE:195` (T1), `:144` (T2), `:92` (T4), `:150` (T5) | each EXECUTE is inside a refused routine or DO block. The refusal covers it, so nothing finer can be said yet |
| `suppressed_error mart.fn_swallow:105` (T4) | inside a refused routine, likewise |
| `source_unavailable mart.fn_gen_regions` (T1 S22) | declared, but as `dangling_reference` (B8-03) |

### What held

- **No refused routine produced an edge.** Bodies were not half-analysed: the dollar-quoted
  body is never mistaken for SQL, even with `$body$` tags, `$$` inside a literal, or SQL
  text inside RAISE (T4 S12, S5).
- **Statements around each routine are unaffected**: a refusal is local, and the next
  top-level INSERT is analysed.
- **Every top-level set-based statement is correct**: the B halves of P1–P5, T1 S13's
  first column, T2 S10 (the view) and S9's first column.

### Metamorphic pairs (T5)

P1–P5 differ only because each A half sits in a refused routine or DO block. Every B half
is correct. P6 is "equal" only because both halves are empty, sitting in one DO block.

## 3. What fixing B8 means — a decision before any code

B8-02 to B8-04 are small. **B8-01 is not: it needs a PL/pgSQL front end.** The analyser
already has everything downstream of one:
- the Frontend interface: units, parameters, variables, CFG shapes;
- def-use with variable nodes and band 1;
- dynamic-SQL folding for band 2;
- trigger edges for band 2;
- interprocedural summaries;
- the B7 script catalog, for registering routines.

The Oracle path feeds all of these from an ANTLR parse. The missing piece is a
**PostgreSQL implementation of the procedural half of that Frontend interface**. ADR-0002
§5 left the grammar open. The three options:

| Option | What | For | Against |
|---|---|---|---|
| **A. A hand-written statement-level reader** | Split a body with the B10 lexer. Parse each embedded SQL statement with SQLGlot, as every other PostgreSQL statement is. Read the PL/pgSQL statements themselves (DECLARE, `:=`, IF, LOOP / FOR / FOREACH / WHILE, EXECUTE, RETURN [QUERY / NEXT], PERFORM, GET DIAGNOSTICS, RAISE, EXCEPTION) with a small recursive-descent parser into the existing Frontend shapes | no new dependency; reuses SQLGlot and the B10 lexer; PL/pgSQL's statement grammar is small and stable | a parser to own; anything it does not know must be refused, not skipped (the B10 guard pattern) |
| **B. ANTLR PostgreSQL grammar** (grammars-v4, which includes PL/pgSQL) | the same machinery as the Oracle front end | symmetry with Oracle; a full grammar | a large generated parser; the grammars-v4 PostgreSQL grammar is known to be slow and loose, and its SQL half would compete with SQLGlot |
| **C. pglast / libpg_query** | PostgreSQL's own parser, including `plpgsql` parsing to JSON | exactly PostgreSQL's behaviour | a C-extension dependency (Windows wheels to be checked); a second SQL AST beside SQLGlot's |

**My recommendation is A**:
- it keeps one SQL parser (SQLGlot) for every statement, inside a routine or not, so every
  fix of B1–B10 applies inside bodies too;
- its failure mode is the one this programme has been building: refuse what you do not
  read.

It is a substantial piece of work, staged, with the batch re-measured after each stage:
- (1) routines and DO blocks with straight-line bodies;
- (2) variables, SELECT INTO, assignments;
- (3) loops and cursors;
- (4) EXECUTE (D-42);
- (5) triggers (D-41);
- (6) RETURN QUERY inlining (D-40) and scalar summaries (rule 6).

**The smaller fixes, whichever option is chosen:**
- B8-02: count CALL;
- B8-03: the right kind for a function read whose body is unreadable;
- B8-04: a plain count, not a declaration, for a top-level `SELECT f()`, once bodies are
  read.

## 4. Coverage checklist (programme plan §1, row B8)

| Construct | Exercised |
|---|---|
| functions and procedures, parameters, RETURNS TABLE | T1 R1–R25; T2 R1–R7; T4 A1–A11; T5 |
| DO blocks | T1 S14–S16; T2 S12, S13; T5 P3, P5, P6 |
| variables, assignments, SELECT INTO, records | T1 S5–S10; T2 S1–S6; T3 S6, S7; T4 S10, S11; T5 R1–R6 |
| RETURN QUERY / RETURN NEXT | T1 S11, S22; T2 S11; T3 S4, S5; T4 S9; T5 P4 |
| FOR rec IN loops, explicit cursors | T1 S8, S9; T2 S6; T3 S1, S5, S7; T5 P2, R3 |
| dynamic SQL via EXECUTE / format() | T1 S15–S17; T2 S12, S13; T3 S1; T4 S6; T5 P3, P6, R5 |
| triggers (NEW, OLD, WHEN, two tables, unbound) | T1 S18, S19; T3 S3; T4 S2, S3 |
| CALL, PERFORM, exception blocks, GET DIAGNOSTICS | T1 S2, S4, S21, S23–S26; T4 S7, S8; T5 R6 |

**Deferred:**
- `SELECT … INTO` a whole record (`%ROWTYPE`). Loop records are transparent but scalar
  variables are nodes, and which rule a record variable follows is a convention of its
  own.
- OUT / INOUT parameters, and procedures returning values.
- `RETURN QUERY EXECUTE`.
- Overloaded functions.
- `CREATE OR REPLACE` of a routine whose old body was already analysed.
- psql `\if`, carried over from B10.

## 5. Corrections while designing, stated

- **Function parameters are variable nodes, not external values.** When I put the B8
  questions, I wrote that parameters would be external values "like D-36". Reading the
  Oracle keys while drafting OBJECTIVES.md showed the older convention: parameters and
  variables are nodes (`variable:P_REGION`). It is applied, and stated as rule 1. D-36 is
  unchanged for psql variables and `$n`.
- **The harness changed:**
  - keyspecs gained `origin <unit> <band>` and `var:<name>`, and keygen compares a
    `repeat` by full node identity;
  - triage prints bands 1 and 2.

  Regenerating every earlier key with the new keygen changed no byte of any of them.
- **The files are shaped for triage.** Each routine opens with a DECLARE ending in `;`, and
  loop queries keep their continuation lines from starting with SELECT, so the
  per-statement view lands each write on its own line. The analyser sees them as ordinary
  PL/pgSQL.

## 6. Fixes applied, 2026-10-04 — option A

**One mechanism: desugar, analyse, restate.** `parsing/plpgsql` reads a body's procedural
structure (blocks, declarations, IF, loops, handlers, and the PL/pgSQL statements) and leaves
every embedded SQL statement as text. `analysis/plpgsql.desugar` then rewrites the script
before anything parses it. Each routine and DO block it can read becomes the SQL its body
runs, every statement at its own lines:

- **DML** stays itself, with variables, record fields and NEW/OLD read as NULL. Band 0
  analyses it exactly as at top level, so every B1–B10 rule applies inside a routine:
  catalog, paths, views and transforms.
- **`SELECT … INTO`, a FETCH, a table-reading assignment, and a FOR loop's query** each become
  a phantom `CREATE TEMP TABLE __plN AS <query>`. Band 0 traces it like any CTAS.
- **Constant dynamic SQL** becomes the text it folds to (D-42).
- **A trigger function** is emitted only when a CREATE TRIGGER binds it (D-41).
- **A RETURN QUERY function** is inlined where it is read (D-40).

`analysis/plpgsql.finish` then restates band 0's result in procedural terms:
- phantom columns become `variable:` nodes, and their filters target the relation read;
- each use of a variable, record field or NEW/OLD becomes an edge from its SQLGlot
  position (a VALUES slot, a projection, a SET, or a predicate);
- a loop's WHERE, or a trigger's WHEN, filters every relation the body writes;
- bands and units are set per edge;
- phantoms leave the statement count.

`summaries` turns each scalar function's RETURN into an interprocedural summary. Band 0 runs
again until the summaries stop changing, so a function calling a function gets its callee's
columns.

**What the reader does not know, it refuses whole.** The routine is left as written and
refused by name, exactly as before B8:
- a CASE statement;
- `RETURN QUERY EXECUTE`;
- OUT / INOUT parameters;
- `FOR … IN EXECUTE`;
- `OPEN … FOR`;
- a variable in a position it does not trace.

A test pins this.

| # | Item | Change | Where |
|---|---|---|---|
| 1 | B8-01 | the reader, desugaring and restating, as above | `parsing/plpgsql.py`, `analysis/plpgsql.py` (new); `procedure.analyse_source` |
| 2 | B8-02 | CALL is counted | `catalog.effect` |
| 3 | B8-03 | a RETURN QUERY function read is inlined; a RETURN NEXT one is declared `source_unavailable`, subject the function | `analysis/plpgsql._inline_tree` |
| 4 | B8-04 | a scalar function has a summary, so a call is analysed rather than declared | `analysis/plpgsql.summaries` |
| 5 | band 0, found by B8 | (a) a projection holding a user call kept ONLY the call's columns: `ol.unit_price - f(ol.product_id)` lost `unit_price`, and a grouped `SUM(f(x))` lost its GROUP BY influence. The call now reads as NULL and the rest of the projection is traced as usual. (b) The caller's expression around a call now counts toward the transform (D-7): `x - f(y)` is derived from f's columns, `SUM(f(y))` aggregated. A schema-qualified call's `Dot` wrapper is not a computation. (c) A summary column is split at its last dot, for `schema.table.column` | `band0._call_site_edges`, `_without_user_calls` |

### Result

**B8: 169 of 169 per statement, 0 false positives, 0 forbidden edges, 0 refusals, every
expected boundary declared, all six pairs equal.** All three bands are pinned in
`tests/test_pg_batches.py`, with a check that no band-1 or band-2 cell goes unpinned.

The regression tests are in `tests/test_b8_procedural.py`: 12 of its 13 fail on the
unfixed source. The 13th is the control: a routine with a form the reader does not know
stays refused whole, before and after.

### What the fixes reached outside this batch

- **The PostgreSQL scope contract moved deliberately**
  (`scripts/probe_postgres.py`, `docs/postgres_scope.md`, `tests/test_postgres_scope.py`):
  - a readable PL/pgSQL body is in scope;
  - a body or DO block that moves only literals is counted with nothing to trace;
  - a CREATE TRIGGER whose function is not in the script is still refused.

  The pinned counts went from 36 / 3 / 6 to 37 / 5 / 3 (edges / no-source / refused).
- **A new `fusion_hazard` declaration in T1.** `mart.customer_summary` is now written by
  two units (the script and `mart.fn_loop_cust`), which the existing scratch-table rule
  declares, as it does for Oracle units. Honest, and not a fact the key states.
- **Oracle:** unchanged. Against `b4d0b1f`, the Oracle stress edges and `run_stress.py`
  scores are identical. The band 0 call-site changes (item 5) reach Oracle code, but no
  Oracle package has a call inside a larger projection.

### Not done, stated

- **Conventions resting on an Oracle precedent.** A loop UPDATE's `WHERE t.k = r.f` gives
  filter edges on both sides, as the Oracle cursor-loop key does. Read as an UPDATE … FROM
  join, D-11 would call it structural. The keys follow the older precedent. Worth
  confirming.
- **Deferred in section 4:** record variables filled by `SELECT INTO`, OUT parameters,
  overloads, `CREATE OR REPLACE` of an analysed routine, and psql `\if`.
