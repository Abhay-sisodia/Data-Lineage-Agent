# Batch B10 — Lexical robustness: report

**Run 2026-10-04**, against the analyser at commit `5661247` (B7 complete), unchanged.
Every key was written before the analyser first saw its file, and decisions D-35 to D-38
were taken before any key. Sections 1–5 are the blind measurement, unchanged. **The fixes,
agreed after review, are in section 6.**

```
.\.venv\Scripts\python.exe stress/pg/triage.py b10_lexical
```

## 1. Headline

**Strict: 41 of 198 statement-level facts found. 0 false positives, 0 forbidden edges,
1 refusal.** The analyser saw **26 of the 103 SQL statements** in the batch. Nothing it
emitted was wrong. Almost everything it missed was lost **before parsing**, in the
statement splitter (`parsing/sqlscript.split_statements`).

The splitter does not know three lexical rules: `E''` backslash escapes, nested block
comments, and psql meta-command lines. Each makes it cut the script in the wrong place,
and **one mistake takes a run of statements with it**. A single `E'it\'s \\ '` in T1 S4 glued
16 statements (S4–S19) into one 117-line fragment. SQLGlot read that fragment as a `Block`, which
band 0 skips **silently**: no edge, no refusal, no boundary (B10-01).

To see what lies behind the splitter, each statement was also run **alone**: cut at
triage's boundaries, with meta-command lines blanked. This uses the scratch tool
`iso10.py` and is a diagnostic, not a score.

| Test | Statements | Seen | Strict, per statement | Each statement alone |
|---|---|---|---|---|
| T1 Breadth | 27 | 9 | 12 / 42 | 27 / 42 |
| T2 Depth | 17 | 4 | 10 / 32 | 23 / 32 |
| T3 Interaction | 15 | 0 | 0 / 29 | 5 / 29 |
| T4 Adversarial | 19 | 10 | 14 / 23 | 17 / 23 |
| T5 Metamorphic | 25 | 3 | 5 / 72 | 27 / 72 |
| **B10 total** | **103** | **26** | **41 / 198 (21%)** | **99 / 198 (50%)** |

In isolation, every remaining miss is one of four things:
- D-35 not implemented (PREPARE / EXECUTE);
- D-36 not implemented (psql variables in SQL);
- B10-02, the nested comment in T1 S7;
- B10-05, `U&""`.

**What held, by the isolated view.** SQLGlot itself lexes this batch's literals and
comments correctly. So did the analyser on every statement the splitter handed over
intact:
- plain, tagged and nested dollar quotes, including a tag that spells a column (T4 S1);
- `E''` with `\'` and `\\`, including the escape that hides a column (T4 S2) and `E'a\\'`
  (T4 S18);
- a standard string ending in `\` (T4 S3);
- `U&''` with and without UESCAPE, and `B''` / `X''`;
- `--` glued to an operand (T4 S4);
- nested block comments inside a statement, five deep (T2 S2, T4 S5);
- quoted identifiers containing `/*` and `--` (T4 S9);
- an identifier containing `$` (T4 S19);
- `::` and `[1:2]` beside a psql variable (T4 S8, once the `\set` is blanked);
- `OPERATOR(pg_catalog.…)` spellings;
- `<->`, `@@` and `OPERATOR(mart.===)`, read as D-38 says;
- two statements on one line, and empty statements.

## 2. Findings, by root cause

### Analyser defects

| ID | What | Where | Grade |
|---|---|---|---|
| **B10-01** | **The splitter does not know `E''` backslash escapes.** In `E'it\'s'` it closes the literal at `\'`. Every quote after that is read inverted, until another odd quote happens to restore it. The statements in between are glued into one fragment. When SQLGlot parses that fragment as a `Block`, **band 0 skips it with nothing declared**. T1: S4–S19, 16 statements, lost silently in one 117-line fragment. T2: S8–S12, 10 statements in 53 lines, silent. In T3 (S9 to the end of the file) and T5 (P2 B to the realistic script) the fragment failed to parse, so the loss is declared `parse_failure`, but the statements are still gone. | T1 S4; T2 S8; T3 S9; T5 P2 B | **R1, silent loss** (T1, T2); R3 elsewhere |
| **B10-02** | **The splitter does not nest block comments.** It ends `/* a /* b */ … */` at the first `*/`. A `;` after that point splits the comment: T1 S7 is cut in two, and in T4 the commented-out INSERT between S5 and S6 becomes two unparseable fragments, the second of which carries S6's PREPARE. Both are declared `parse_failure`. Either fragment could parse as a statement and emit an edge from text inside a comment; none did here. | T1 S7; T4 after S5 | R3 (R0 if a fragment parses) |
| **B10-03** | **psql meta-commands are not recognised.** A `\set`, `\echo`, `\timing`, `\pset`, `\i` or `\copy` line is glued onto the statement after it, which then fails to parse. **That statement is lost, not the meta-command.** `\i` is not declared. `\copy` is refused, but the refusal spans the statement it is glued to (T1 S22): the refusal is not local. | T1 S21, S22, S23; T2 S6; T3 S1; T4 S8, S16; T5 R-script | R3 (declared) for each lost statement; R1 for `\i` (silent) |
| **B10-04** | **PREPARE, EXECUTE and DEALLOCATE are dropped silently.** SQLGlot parses them as `Command` (DEALLOCATE as `Alias`). Band 0 does not count them, does not refuse them and declares nothing. With D-35 that is every EXECUTE's lineage; without it, it is still a statement absent from coverage. BEGIN and COMMIT are also uncounted, which is right: they move no data. | every PREPARE / EXECUTE / DEALLOCATE (38 statements) | **R1, silent** |
| **B10-05** | **A `U&""` identifier is read as a column `u`.** `c.U&"\0069d"` is refused "Unknown column: u". SQLGlot does not lex `U&"…"` as an identifier. | T4 S10 | R3 (refused) |

### Decided, not yet implemented (expected)

| Decision | Effect | Where |
|---|---|---|
| **D-35** PREPARE / EXECUTE | no EXECUTE emits anything (B10-04): 15 EXECUTEs and 3 CREATE TABLE … AS EXECUTE, 87 facts, plus T2 S10's 2, which read S9's table | T1 S9, S10, S12, S13; T2 S4, S9 (and S10, which reads S9's table); T3 every EXECUTE; T4 S7, S13; T5 P3 B, P7 B, R1, R2 |
| **D-36** psql variables | `:'v'` does not parse in SQLGlot: the statement is a declared `parse_failure` even when isolated. Unquoted `:v` parses (T4 S8, isolated). `FROM :tbl` parses as a relation `tbl` and is **declared** `dangling_reference`, not guessed, which D-36 accepts | T1 S21; T2 S6; T5 P6 A; T4 S16 |
| **D-37** meta-commands | B10-03 | — |
| **D-38** custom operators | **already holds**: `<->`, `@@` and `OPERATOR(mart.===)` give the keyed edges in isolation | T1 S18–S20; T2 S7 |

### Metamorphic pairs (T5)

P1 is equal, with both halves correct. **P2 differs**: the `E''` half is the start of
B10-01's fragment. **P3–P8 are reported equal only because both halves are empty**:
everything from P2 B to the realistic script is inside that fragment. In isolation, P1, P2,
P4, P5 and P8 are equal and correct. P3 and P7 differ (D-35), and P6 differs (D-36).

### What the run did not show

- **No false positive and no forbidden edge.** Text inside a literal or a comment never
  became lineage, and no `\echo` or commented-out INSERT leaked.
- **The refusal count understates the damage**: one refusal and 14 boundaries stand for 77
  unseen statements.

## 3. Recommended fix order (for agreement, not yet started)

| # | Item | Why here |
|---|---|---|
| 1 | **B10-01, B10-02**: teach the splitter `E''` escapes (the `E` prefix, `\'` and `\\`) and nested `/* */` | one function, and it is upstream of everything. Fixes most of the batch, including both silent fragments |
| 2 | **A glued fragment is never silent** | whatever the splitter does, a statement that SQLGlot parses as a `Block` (or any multi-statement result) must be declared, not skipped. This is the guard that makes the next splitter defect R3 instead of R1 |
| 3 | **B10-03, D-37**: meta-commands | a line whose first non-blank character is `\` (outside a literal or comment) is a meta-command, running to the end of the line. It is blanked, same length. `\i` / `\ir` are declared `source_unavailable` (subject: the file); `\copy` is refused, alone; `\gset` ends the query before it, as `;` would, and that query gives nothing |
| 4 | **D-36**: psql variables | `:'v'`, `:"v"` and `:v` (not `::`, not inside literals, comments or `[ : ]`) become a literal placeholder of the same length. As a relation they are already declared |
| 5 | **B10-04, D-35**: PREPARE / EXECUTE / DEALLOCATE | through the B7 script catalog: PREPARE stores the statement and is counted, EXECUTE re-analyses it at its own line (as REFRESH does), `CREATE TABLE … AS EXECUTE` is CTAS over it, DEALLOCATE [ALL] forgets, and an unknown EXECUTE is counted and declared |
| 6 | **B10-05**: `U&""` identifiers | decode to the plain quoted identifier before parsing |

## 4. Coverage checklist (programme plan §1, row B10)

| Construct | Exercised |
|---|---|
| dollar quoting, tagged and nested | T1 S1–S3; T2 S1, S5; T4 S1, S17; T5 P1 |
| `E''`, `U&''`, `B''` / `X''` strings; `U&""` identifiers | T1 S4–S6; T2 S8; T3 S3, S9; T4 S2, S3, S10, S18; T5 P2, P8 |
| nested comments, line comments | T1 S7; T2 S2; T4 S4, S5, S9; T5 P5 |
| custom operators, `OPERATOR()` | T1 S16–S20; T2 S7, S11; T3 S1, S9, S12; T5 P4 |
| `$n` parameters, PREPARE / EXECUTE / DEALLOCATE | T1 S8–S15; T2 S3, S4, S8, S9; T3 throughout; T4 S6, S7, S11–S15; T5 P3, P7, R-script |
| many statements per file, several per line, empty statements | T1 S24, S25; T2 S12 |
| psql meta-commands and variables | T1 S21–S23; T2 S6; T3 S2, S12; T4 S8, S16; T5 P6, R-script |

**Deferred:**
- psql `\if` / `\elif` / `\else`: conditional execution needs a convention of its own
  (both branches, or neither).
- `:"v"` as a quoted identifier: exercised only as `:tbl`.
- Dynamic SQL inside a DO block: B8.
- `standard_conforming_strings = off`: the files assume the default.

## 5. Corrections while designing, stated

- **The analyser was not probed before the keys were written.** A probe script was drafted
  to inform the convention questions and stopped before it ran. The questions rest only on
  PostgreSQL semantics.
- **Targets were reassigned while keying T1**, so that no two statements in a file state
  the same fact (keygen rejects duplicates). Every reassignment was made before any run.
- **T2 S6 was simplified while keying**, before any run. As drafted, its psql variable sat
  in an IN subquery inside a scalar subquery, where D-12's reach is not decided. That would
  have tested D-12, not lexing.
- **One application is mine, stated in OBJECTIVES.md:** a query sent with `\gset` gives
  nothing, not even `<result>`.
- **The harness changed in one place:** `triage.py` now recognises PREPARE, EXECUTE and
  DEALLOCATE as statement starts. Without that, their edges would be attributed to the
  wrong line.

## 6. Fixes applied, 2026-10-04 — in the agreed order

| # | Item | Change | Where |
|---|---|---|---|
| 1 | B10-01, B10-02 | one lexer, `skip_lexeme`, for everything that must not be read as code. `E''` takes backslash escapes, `/* */` nests, and a `$tag$` opens only where a token can start (`a$1` is a name; a tag never starts with a digit, so `$1` is a parameter). The splitter uses it | `parsing/sqlscript` |
| 2 | the guard | text that SQLGlot reads as several statements (a `Block`) is counted and declared `parse_failure`, never skipped | `band0` statement loop |
| 3 | B10-03, D-37 | a psql pass, `parsing/psql.restate`, run first for PostgreSQL. It uses the same lexer, keeps the same lines, and handles each meta-command (below) | `parsing/psql`; `procedure.analyse_source` |
| 4 | D-36 | `:'v'` and `:"v"` lose the colon, becoming the literal or identifier psql substitutes; `:v` is left as SQLGlot's placeholder. `::`, slices and quoted text are untouched | `parsing/psql` |
| 5 | B10-04, D-35 | handled by the B7 script catalog (below) | `catalog._prepared_statement`; `band0` |
| 6 | B10-05 | `U&"…"` [UESCAPE] is decoded to the plain quoted identifier, padded to the same length | `parsing/psql` |

**How the psql pass handles each meta-command (item 3):**
- every meta-command is blanked from the `\` to the end of its line;
- `\g` / `\gx` end the query, as `;` does;
- `\gset` blanks the query it sends;
- `\copy` becomes `COPY …;` on its own, so the register refuses it alone;
- `\i` / `\ir` are returned and declared `source_unavailable`, subject the file.

**How the script catalog handles prepared statements (item 5):**
- PREPARE stores the body and is counted;
- EXECUTE re-analyses the body at its own line, and its kind is read from the body, so a
  prepared UPDATE is an UPDATE;
- `CREATE TABLE … AS EXECUTE` is CTAS over the body;
- DEALLOCATE [ALL] forgets;
- an unknown EXECUTE is counted and declared `source_unavailable`;
- leading comments, block comments included, are skipped before the statement is matched.

**One more defect, found by this batch and not lexical (B10-06).** In T3 S6, a MERGE arm
condition on an aggregate of the USING source (`WHEN MATCHED AND s.amt > $1`, `amt` a SUM)
was reported pre-aggregation. D-6 makes it post, as ON CONFLICT's `DO UPDATE WHERE` already
was. The same happens with a literal in place of `$1`. Both MERGE arm sites (the WHEN
condition and the arm's own WHERE) now take the phase from the traced path. Fixed in
`band0._analyse_merge`.

**B10-06 reaches Oracle, and six Oracle labels were corrected.**
- **What moved.** The Oracle check found six filter edges moving to post-aggregation, all
  from this fix, in `s3_incremental_merge` (stress_03) and `s4_merge_upsert` (stress_04).
  Each is a MERGE whose arm WHERE tests `revenue_total`, a correlated `SUM` in USING. The
  six are the tested column and both correlation operands, which reach the filter through
  the SUM.
- **Why the old labels were wrong.** Their labels said pre-aggregation, with the note "Not
  a HAVING, so the phase stays the default". They were written 2026-09-12, before D-6,
  and use the keyword test D-6 rejected.
- **The correction.** Decided with Abhay on 2026-10-04 to keep the fix and apply D-6 to
  those keys. Each of the six labels is now `phase: post-aggregation`, with a KEY
  CORRECTED comment beside it. MERGE arms now agree with ON CONFLICT `DO UPDATE WHERE`
  in both dialects.
- **Oracle, against `5661247`:**
  - **Edges:** identical except for those six phases. The lexer and psql changes moved no
    Oracle edge.
  - **`run_stress.py` scores:** identical with the corrected keys.
  - **With the old labels:** stress_03 alone would have dropped from 11 band-0 filter true
    positives to 8, with 3 false positives and 3 misses.

### Result

**B10: 198 of 198 per statement, 0 false positives, 0 forbidden edges, all four expected
boundaries declared, and all eight pairs equal**, with both halves non-empty this time.
- **Statements seen:** 27, 17, 15, 19 and 22, up from 9, 4, 0, 10 and 3. T5 counts 22 of
  25 because DROP, BEGIN and COMMIT move no data and are not counted.
- **The one refusal is `\copy`, on its own line**, and it is pinned as intended.
- **T4 S16 (`FROM :tbl`) is declared, not guessed** (`dangling_reference tbl`), as D-36
  allows.
- **Pins and tests:** pinned in `tests/test_pg_batches.py`. The regression tests are in
  `tests/test_b10_lexical.py`: 13 of its 14 fail on the unfixed source. The 14th is the
  control for `::`, `[1:2]` and a colon inside a literal, which must stay as they are.

### Not done, stated

- **psql `\if` / `\elif` / `\else`** are blanked like any other meta-command, so both
  branches are analysed. Deferred in section 4; it needs its own convention.
- **The psql pass assumes the default `standard_conforming_strings = on`.**
