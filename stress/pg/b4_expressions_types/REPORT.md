# Batch B4 — Expressions and types: report

**Run 2026-10-02**, against the analyser at commit `e9d7dae` (B3 complete), unchanged.
Every key was written before the analyser first saw its file, and decisions D-19 to D-22
were taken before any key. **Nothing has been fixed.** This is the review point.

```
.\.venv\Scripts\python.exe stress/pg/triage.py b4_expressions_types
```

## 1. Headline

The harness scores each file as a set of facts. A fact stated by two statements of the
same file is **one** fact, because the match key has no origin. B4 repeats facts more than
earlier batches did, so both views are given below. The per-statement view is the honest
one (§2, B4-04).

| Test | Statements | Value TP/FP/FN | Filter TP/FP/FN | Influence TP/FP/FN | Per statement |
|---|---|---|---|---|---|
| T1 Breadth | 17 | 64 / 5 / 21 | 17 / 0 / 0 | 1 / 0 / 0 | 85 / 109 |
| T2 Depth | 8 | 37 / 1 / 12 | 7 / 0 / 3 | 16 / 0 / 0 | 61 / 76 |
| T3 Interaction | 9 | 47 / 1 / 1 | 10 / 0 / 0 | 30 / 0 / 0 | 88 / 89 |
| T4 Adversarial | 14 | 33 / 3 / 15 | 13 / 0 / 0 | — | 52 / 67 |
| T5 Metamorphic | 21 | 40 / 3 / 2 | 7 / 0 / 0 | 18 / 0 / 0 | 91 / 99 |
| **B4 total** | **69** | **221 / 13 / 51** | **54 / 0 / 3** | **65 / 0 / 0** | **377 / 440 (86%)** |

**Per file: 340 of 394 facts found, 13 false positives.** All 69 statements were counted and
analysed, with no refusals and 3 boundaries (all in T1).

**What held.** These all came out right on the first run:

- casts, string, date and math functions, `||`, `IS DISTINCT FROM`, the regex operators,
  `= ANY`, `@>` and `&&` on arrays, subscripts and slices;
- every filter and every influence, including expression GROUP BY keys, PARTITION BY
  expressions, CASE inside SUM inside a window, and a rank over SUM;
- FILTER with compound predicates, and a DISTINCT ON an expression;
- scalar subqueries inside a CASE (D-12), and expressions in ON staying structural;
- dead reads (D-21) and parameter columns (D-22), which already behave as decided;
- the keyword-named columns (`year` and `month` beside `EXTRACT(year FROM …)`), column
  names inside string literals and format strings, and cast chains.

**Pairs:** P4 to P9 are equal. P1 and P10 differ (§2). P2 and P3 are equal only because both
halves are wrong in the same way (B4-02).

## 2. Findings, by root cause

### Analyser defects

| ID | What | Where | Grade |
|---|---|---|---|
| **B4-01** | **Whole-row references and composite field access produce nothing, and declare nothing.** A statement made only of `(c).id`, `(c).full_name` and `row_to_json(c)` has no edges and no boundary. The same holds for `to_jsonb(c)` through a derived table, `md5(orders::text)`, the filter `WHERE (c).status = …`, and the whole-row test `WHERE c IS NOT NULL`. The table appears to have no writer. | T1 S10; T2 S2, S8; T4 S7; T5 P10 A | **R1, silent loss** |
| **B4-03** | **Three built-ins have no summary and are declared `source_unavailable`:** `ROW(…)`, `regexp_match(…)` and `tstzrange(…)`. Their columns are lost as values: `full_name`/`email` in S9, `channel` in S8, and both range columns in S13. The loss is declared. **The declaration depends on position:** the same `tstzrange` call inside a CTE (T2 S6) passes its columns as values with no boundary. The siblings (`daterange`, `numrange`, `int4range`, `tsrange`, `regexp_split_to_array`) are probably affected the same way but were only exercised in WHERE, where filters survive. | T1 S8, S9, S13 | R3, declared loss |
| **B4-04** | **Measurement: the per-file score hides statement-level misses.** A fact repeated in a later statement counts once, so a miss there scores as a hit. Example: T1 S13 loses `delivered_at`/`shipped_at → transit_days` behind the `tstzrange` boundary, but T1 S5 states the same fact, so the harness reports it found. The scratch per-statement check used for this report found **7 misses the per-file score cannot see**. Four are T5 pair halves, which the pair check does flag (P1, P10). **Three are in T1, and nothing flags them:** `(c).id` in S10, and the two range columns in S13. | all of B4; likely earlier batches too | R4, measurement distortion |

### Decided, not yet implemented (expected)

| Decision | Effect | Where |
|---|---|---|
| **D-19 (B4-02)** two columns make a conditional | Every one of the 13 false positives. A single-column `GREATEST(x, 0)`, `LEAST(x, c)`, `NULLIF(x, 0)`, a one-column CASE (a mapping, a guard, a clamp, `CASE WHEN false THEN x END`), and `COALESCE(x, x)` / `GREATEST(x, x, 0)` all come out `conditional`. `COALESCE(x, 0)` alone is already derived (the existing rule). | T1 S1, S2, S15; T2 S4; T3 S6; T4 S1, S2; T5 P1 B, P2, P3 |
| **D-20** whole row, field-exact | blocked behind B4-01 | as B4-01 |

D-21 (dead reads) and D-22 (parameter columns) needed nothing: the analyser already behaves
as decided.

### Metamorphic pairs (T5)

| Pair | Result | Why |
|---|---|---|
| P1 `COALESCE(x,0)` vs CASE | **DIFFER** | D-19: COALESCE is derived (existing rule), the CASE is conditional |
| P2 `GREATEST(x,0)` vs CASE | equal, **both wrong** | both conditional; D-19 makes both derived |
| P3 `NULLIF(x,0)` vs CASE | equal, **both wrong** | as P2 |
| P4 `COALESCE(a,b)` vs CASE | equal | two columns: conditional, correctly |
| P5 to P9 | equal | |
| P10 `(c).f` vs `c.f` | **DIFFER** | B4-01: spelling A produces nothing |

## 3. Recommended fix order (for agreement, not yet started)

| # | Item | Why here |
|---|---|---|
| 1 | **B4-01, with D-20** whole-row and field access | the only R1. A table looks unwritten. `(c).f` resolves to `c.f`; a whole-row use expands through the dictionary as `*` does, in projections and in WHERE |
| 2 | **B4-02, D-19** in `transforms.is_conditional` | 13 false positives and two pair failures. **It moves earlier keys:** the B1–B3 keys, the Oracle stress keys and possibly phase-0 labels where a one-column NULLIF, GREATEST or CASE was labelled conditional. Each move is relabelled by decision and listed, as B2 did for D-12 |
| 3 | **B4-03** built-ins | add `ROW`, `regexp_match`, the range constructors and their siblings to `analysis.builtins`. Then find out why the same call is declared at the top level and passed inside a CTE |
| 4 | **B4-04** per-statement scoring in `triage.py` | promote the scratch checker, so later batches report statement-level misses without anyone hand-checking. The harness's per-file score is left unchanged (ADR-0001), and the triage report gains the second view |

## 4. Coverage checklist (programme plan §1, row B4)

| Construct | Exercised |
|---|---|
| CASE: searched, simple, nested | T1 S1; T2 S1, S4; T3 S3, S6, S9; T4 S2, S10, S12; T5 P1–P4, R1 |
| COALESCE, NULLIF, GREATEST, LEAST | T1 S2, S16; T2 S1, S3, S4; T3 S6; T4 S1–S3, S11; T5 P1–P4, R1 |
| casts (`::`, CAST, type modifier, typed literal) | T1 S3; T4 S6; T5 P5 |
| string, date and math functions | T1 S4–S6, S15, S17; T2 S6; T4 S4, S14; T5 P6, P7 |
| IS DISTINCT FROM | T1 S7; T4 S9; T5 P9 |
| regular expressions | T1 S4, S8 |
| row constructors and row comparison | T1 S9; T4 S9 |
| composite field access, whole row | T1 S10; T2 S2, S8; T4 S7; T5 P10 |
| arrays | T1 S11, S12; T2 S5; T3 S2; T4 S8; T5 P8 |
| ranges | T1 S13; T2 S6; T3 S2 |

**Deferred:** `COLLATE` (to B9, names), `OVERLAPS`, user-defined composite and enum types (the
schema has none; B7 owns DDL), custom operators (B10), and jsonb operators (B5).

## 5. Corrections while designing, stated

- **The files are 180–260 lines**, not the ~300 the plan names. Each family is covered
  (§4), and adding statements to reach the count would only repeat facts the per-file key
  cannot tell apart (B4-04).
- **Facts repeated within a file were removed from the key mechanically** after it was
  written. The harness rejects a key that states one match key twice. Each removal is left
  as a `# Recurs …` comment naming where the fact was first stated, and the per-statement
  check reads those comments back. No fact was changed.
- **T4 S13's target moved from `channel_mix` to `kpi_rollup`** before the first run. Its
  filter `orders.amount → channel_mix` contradicted S5's forbid of that same filter, because
  a forbid applies to the whole file.
- **T3 S9 was simplified before the run.** As first written, `d.region` was both a CASE-test
  value of `level` and a grouping-key influence on it. No decision settles whether one column
  can be both to the same target, so the CASE test now reads only the SUM. **That question
  is open (B4-Q1)**; it did not need answering for this batch.
- **One reading of D-19 is mine, stated in OBJECTIVES.md:** distinct columns are counted in
  the node's own scope, so `COALESCE(x.net, 0)` is derived even when `x.net` was built from
  two columns (T2 S3). Overrule it and T2 S3's `net_amount` facts become conditional.

## 6. Fixes applied, 2026-10-02 — in the agreed order

Your OK to the fix order also accepted my reading of D-19: columns are counted where the
node is written.

| # | Item | Change | Where |
|---|---|---|---|
| 1 | B4-01, D-20 | **The cause:** SQLGlot's `qualify` turns a whole-row reference into `exp.TableColumn`, which is not an `exp.Column`, so every column walk stepped over it. **The fix:** after qualifying, `(a).f` becomes `a.f`. `(x.c).f` through a derived table or CTE gets a hidden projection inside `x`, so it stays field-exact. Every other whole row becomes the tuple of its columns, taken from the dictionary or the projection. A row whose columns can't be listed becomes an ordinary column, so it is declared unresolved instead of dropped | `analysis/whole_rows.py`, called from `band0._qualify` (every entry point) |
| 2 | B4-02, D-19 | `is_conditional` applies one rule to every conditional form: two or more distinct column references in the node (counted by relation instance) make it conditional. The old COALESCE-only rule is a special case of it | `analysis/transforms.py` |
| 3 | B4-03 | `ROW`, `regexp_match`, `regexp_split_to_array`, the range and multirange constructors, the range predicates and a few numeric built-ins are listed as PostgreSQL built-ins. `regexp_split_to_table` is left out because it returns a set (B6) | `analysis/builtins.py` |
| 4 | B4-04 | Keyspecs gain a `repeat` directive. It restates a fact a later statement also produces, must name a fact the file states, and adds nothing to the YAML. `triage.py` now prints a per-statement line and every **hidden miss** | `stress/pg/keygen.py`, `stress/pg/triage.py` |

### Result

**B4: 394 of 394 facts per file and 440 of 440 per statement, 0 false positives, 0
boundaries, 0 refusals. All ten pairs are equal.** Pinned in `tests/test_pg_batches.py`.
The regression tests are in `tests/test_b4_expressions.py`: 13 of 19 fail on the unfixed
source, and the 6 that pass are controls (two-column conditionals, a self-join, a bare name
that is a column and not a whole row, and a user routine still being declared). The full
suite gives 1022 passed and 2 skipped (both need Oracle).

### What the fixes reached outside this batch

* **D-19 relabelled ten earlier labels, every one a one-column form, each marked in its key
  with a comment:**
  * PostgreSQL, 4 labels: B1 T1 (2), B1 T4 (1) and B3 T3 (1), all `NULLIF(x, 0)`.
  * Oracle stress, 6 labels:
    * a one-column CASE in a MERGE source and in a view chain (stress_01);
    * `DECODE(status_code, 'A', 1, 0)` and `NVL(NULLIF(region, 'XX'), …)` (stress_02);
    * a one-column CASE (stress_03);
    * `v_cust_l2`'s one-column CASE (stress_04). **This reverses S4-06's 2026-09-12 key
      correction, which applied the old rule.**

  After the relabels, the Oracle stress output is identical to HEAD. The two-column CASEs in
  the same packages stayed conditional. Phase-0 labels are unaffected, as far as the unit
  suite shows; the phase-0 re-measurement is still owed.
* **Unit tests:** eight pinned the old rule (one-column `DECODE`, `NULLIF`, `NVL2`, `CASE`
  as conditional). They now assert derived. Two-column versions were added, so the
  conditional path keeps its coverage, including in band 1.
* **Per-statement triage across B1–B4:** no hidden miss in any batch. It first flagged four
  facts in the seam pairs (B2 P6 and P8, B3 P6). Those keys stated spelling B's facts at
  spelling A's line. They were moved under their own `@` blocks with the labels unchanged.
  The per-file score ignores origin, so no number moved.

### New, recorded for your decision

* **B4-05: a user-defined function is declared at the top level and passed silently one level
  down.** `SELECT myschema.calc(o.amount)` gives a `source_unavailable` boundary and no
  edge. The same call inside a CTE or derived table gives `orders.amount → … derived`, with
  no boundary. This is an existing inconsistency, not caused by B4, and B4-03 is what
  exposed it. Making the nested case follow the top-level rule may move Oracle numbers
  wherever package functions sit inside CTEs, so it is left open for a decision. Proposed
  grade: R0 (a claim the top-level rule withholds).
* **B4-Q1** (one column as both value and grouping influence of the same target) is still
  open. B4 did not need it.
