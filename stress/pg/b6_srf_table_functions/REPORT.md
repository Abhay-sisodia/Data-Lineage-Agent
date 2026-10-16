# Batch B6 — Set-returning and table functions: report

**Run 2026-10-03**, against the analyser at commit `34f1e8a` (B9 complete), unchanged.
Every key was written before the analyser first saw its file, and decisions D-26 to D-28
were taken before any key. **Nothing has been fixed.** This is the review point.

```
.\.venv\Scripts\python.exe stress/pg/triage.py b6_srf_table_functions
```

## 1. Headline

| Test | Statements | Value TP/FP/FN | Filter TP/FP/FN | Influence TP/FP/FN | Per statement |
|---|---|---|---|---|---|
| T1 Breadth | 16 | 16 / 0 / 8 | 2 / 0 / 0 | 0 / 0 / 3 | 18 / 29 |
| T2 Depth | 7 | 12 / 0 / 2 | 1 / 0 / 1 | 7 / 0 / 2 | 20 / 25 |
| T3 Interaction | 8 | 13 / 0 / 1 | 4 / 0 / 1 | 3 / 0 / 2 | 20 / 24 |
| T4 Adversarial | 11 | 14 / 0 / 4 | 2 / 0 / 0 | 1 / 0 / 0 | 17 / 21 |
| T5 Metamorphic | 13 | 6 / 0 / 4 | 0 / 0 / 2 | 0 / 0 / 2 | 8 / 21 |
| **B6 total** | **55** | **61 / 0 / 19** | **9 / 0 / 4** | **11 / 0 / 9** | **83 / 120 (69%)** |

**No false positive, one refusal, 25 boundaries.** Every miss is declared somewhere,
except the ordinal's influence and filter, which D-26 has not yet given a home. The
boundaries are honest about their losses but wrong about their reasons: most say "row
source is a function", about built-ins whose shape is known.

**What held:**

- unnest in FROM, in a comma join and a CROSS JOIN LATERAL;
- two arrays bound by position, and unnest in the select list;
- a filter on an element read as a filter on its array;
- GROUP BY an element (influence), ORDER BY an element under LIMIT, and DISTINCT ON an
  element (D-9);
- an outer WHERE on a LEFT JOIN LATERAL element;
- an SRF inside a scalar subquery (D-12);
- TABLESAMPLE, with and without REPEATABLE: no edge, as decided;
- **D-27 already holds**: an unused unnest gives no filter and no influence;
- an element alias named like a source column, an SRF alias named like a table, a CTE
  named `unnest`, and an element named like its own array.

## 2. Findings, by root cause

### Analyser defects

| ID | What | Where | Grade |
|---|---|---|---|
| **B6-01** | **Built-in set-returning functions in FROM other than unnest are "row source is a function"**: `generate_series`, `string_to_table` and `regexp_split_to_table`. Their columns are lost, declared with a false reason. A **literal** series, which has no source at all, still raises a boundary, so a calendar table reads as a loss (T1 S7, T2 S2). | T1 S7, S8, S13; T2 S2; T3 S6; T4 S11; T5 P4 A, P6 A | R3, declared loss with a false reason |
| **B6-02** | **`ROWS FROM (…)` is not supported**: declared dangling, so every column is lost. | T1 S6; T2 S6; T5 P3 A | R3 |
| **B6-03** | **A single-column SRF aliased by table name only (`AS v`, or unaliased, giving a column called `unnest`)** is declared "cannot be bound to one array: 0 alias columns". In PostgreSQL the table alias names the one column. | T1 S4, S14; T4 S3; T5 P4 B | R3 |
| **B6-04** | **`regexp_split_to_table` in the select list is `source_unavailable`.** B4 deliberately kept it out of the built-ins until B6 settled set-returning functions; D-26 to D-28 now have. | T1 S12; T5 P5 A | R3 |
| **B6-05** | **WITH ORDINALITY inside a CROSS JOIN LATERAL refuses the whole statement** ("Unknown column: n") as soon as the ordinal is referenced. The comma spelling works. T5's realistic report lost every fact. | T5 R1 | **R2, unsound refusal** |
| **B6-06** | **An SRF over a scalar subquery** traces the subquery's columns from the wrong scope: `unnest((SELECT array_agg(p.brand) …))` declares `brand` unresolved, while `(SELECT max(p.brand) …)` works. | T4 S4 | R3 |

### Decided, not yet implemented (expected)

| Decision | Effect | Where |
|---|---|---|
| **D-26** the ordinal is influenced by its array | no influence edge reaches `position`. Its applications have no edge either: a filter on the ordinal (`n <= 3`, `n = 1`) is not a filter from the array, and a window ordered by it gives no influence | T1 S5, S6; T2 S3; T3 S2, S5; T5 R1 |
| **D-28** a series is derived from its bounds | blocked behind B6-01 for FROM. In the select list it works (T1 S9 and T5 P6 B pass) | as B6-01 |
| **D-27** an SRF in FROM is structural | already holds | T1 S1, S16; T4 S6 |

### Metamorphic pairs (T5)

P1 and P2 are equal. **P4** is equal only because both halves are lost (B6-01, B6-03).
**P3, P5 and P6 differ**, and in each the select-list or `unnest` spelling works while the
other loses everything (B6-02, B6-04, B6-01).

## 3. Recommended fix order (for agreement, not yet started)

| # | Item | Why here |
|---|---|---|
| 1 | **B6-05** ordinality in a LATERAL | the only unsound refusal; it takes a whole statement down |
| 2 | **B6-01, B6-03, B6-04** built-in SRFs | one mechanism: a built-in SRF's columns are its arguments' elements (derived), by position, named by the column list or by the table alias. `generate_series` follows D-28, and a literal series is silent. Fixes four pairs' worth of losses |
| 3 | **B6-02** ROWS FROM | the same mechanism, applied to each function in turn |
| 4 | **D-26** the ordinal | influence from the array(s), and the stated applications: a filter on the ordinal filters from the array, and ordering by it gives influence |
| 5 | **B6-06** SRF over a scalar subquery | narrow; trace the argument's subquery in its own scope |

## 4. Coverage checklist (programme plan §1, row B6)

| Construct | Exercised |
|---|---|
| unnest, one array and several | T1 S1–S4, S14–S16; T2 S1, S5; T3 S1, S3, S8; T4 S1–S3, S6, S8, S9; T5 P1–P4 |
| WITH ORDINALITY | T1 S5, S6; T2 S3; T3 S2, S5; T5 R1 |
| ROWS FROM | T1 S6; T2 S6; T5 P3 |
| generate_series (literal, column, timestamp, select list) | T1 S7–S9; T2 S2, S6, S7; T3 S6; T4 S5, S11; T5 P6, R1 |
| a function in FROM with a column-definition list | T1 S10 (user-defined: declared, correctly) |
| TABLESAMPLE | T1 S11; T4 S7 |
| SRFs in the select list (unnest, regexp_split_to_table, string_to_table) | T1 S9, S12, S15; T3 S7; T4 S4, S5, S10; T5 P1, P5, P6 |

**Deferred:** jsonb SRFs and built-in record functions with a column-definition list (B5);
`unnest` of a composite-typed array into its fields (the schema has no composite type);
set-returning functions in DML RETURNING.

## 5. Corrections while designing, stated

- **T3 S7's target moved before the run.** Its forbid (no filter from email on
  `customer_summary`) contradicted S4's filter on the same pair, because a forbid applies to
  the whole file.
- **T4 S6 had a malformed forbid** (a filter whose target was a column). It was removed
  before compiling.
- **T5 R1's key was corrected before the run.** As first written, it gave the line's
  `order_date` an influence through the calendar's GROUP BY key. The key is `mo.month`, the
  literal series, which has no source; the line's month reaches the result only through
  the structural join.
- **One application is mine, stated in OBJECTIVES.md:** a filter on the ordinal is a filter
  from its array, and ordering a window by it gives influence (D-26 with D-9 and D-4).
  Overrule it and T2 S3, T3 S2, T3 S5 and T5 R1 lose those facts.
- **The files run to 90–149 lines**, well below the plan's ~300: the scope is narrower than B1's, and the constructs are each covered (§4).

## 6. Fixes applied, 2026-10-03 — in the agreed order

**One restatement carries most of it.** Every built-in set-returning function in FROM is
rewritten, before `qualify`, as the LATERAL subquery that computes the same rows, with the
functions in its select list. That form was already traced correctly: an element is
derived from its array, a series from its bounds. Several SRFs in one select list zip by
position, which is exactly what `ROWS FROM` and a multi-array unnest do. An ordinal
becomes `ROW_NUMBER() OVER (ORDER BY <the arrays>)`, so D-26's influence, and a filter on
the ordinal, fall out of D-4 and D-9 unchanged.

| # | Item | Change | Where |
|---|---|---|---|
| 1 | B6-05 | A LATERAL unnest with ordinality is restated like every other one. SQLGlot's `Lateral` dropped the ordinal column before `qualify` saw it | `analysis/srf.py` |
| 2 | B6-01, B6-03, B6-04 | `generate_series`, `string_to_table`, `regexp_split_to_table` and `unnest` in FROM are restated. A table alias names a single column. A literal series is silent. `string_to_table` and `regexp_split_to_table` are built-ins in the select list | `srf.restate`; `builtins.py` |
| 3 | B6-02 | `ROWS FROM` gives one select-list call per function | `srf._table_calls` |
| 4 | D-26 | The ordinal is the ROW_NUMBER above. A window ordered by an ordering column that is itself a window now carries that window's ordering too (D-4, composed), which is what T3 S2 needed | `srf`; `band0._window_influence`, `_projection_behind` |
| 5 | B6-06 | A select-list unnest over a subquery (`exp.Explode`) becomes a plain call, which SQLGlot's scope builder descends into | `srf.restate` |

**Also fixed:** a boundary on a user-defined set-returning function named its *argument*
(`TABLE(O.PAYLOAD)`). Its subject builder assumed Oracle's `TABLE(f(x))` wrapper, so
PostgreSQL's direct call now names the function (`TABLE(ANALYTICS.EXPLODE_PAYLOAD)`),
which is what the key expected.

### Result

**B6: 120 of 120 per statement and per file, 0 false positives, 0 refusals, all six pairs
equal.** The only boundary is the user-defined SRF's, correctly named. Pinned in
`tests/test_pg_batches.py`. The regression tests are in `tests/test_b6_srf.py`: 10 of 11
fail on the unfixed source, and the one that passes is the D-27 control (an unused
unnest gives nothing). The full suite gives 1084 passed and 2 skipped (both need Oracle).

### What the fixes reached outside this batch

* **Two PG-05 unit tests changed, both by decision:**
  * the ordinal is now influenced by its array (D-26), where it had "no source at all";
  * `SELECT t FROM c, unnest(c.tags) AS t` is now the element (B6-03), where it was
    declared.
* **Oracle stress: scores identical to HEAD, with one more prediction.** In
  `s4_window_cascade`, the composed window rule also derives `STG_ORDERS.CUST_ID →
  FCT_REVENUE_STAGE.ORDER_COUNT` influence from line 419. `month_rank` orders by
  `running_gross`, a window partitioned by `cust_id`. The key already holds that fact, so
  it is a second claim of a fact already scored, and no number moves.
* B1–B4 and B9 are unchanged: every pin passes, and every per-statement total is complete.
