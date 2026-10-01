# Batch B1 — Core SELECT: report

**Run 2026-10-01**, against the analyser at commit `90dbcab`, unchanged. Every key was
written before the analyser first saw its file. **Nothing has been fixed.** This report is
the stopping point the plan set: agree the findings and the fix order before any code moves.

Reproduce:

```
.\.venv\Scripts\python.exe stress/pg/triage.py b1_core_select      # every miss / FP, by statement, + pairs
.\.venv\Scripts\python.exe stress/run_stress.py pg/b1               # the harness grids
```

## 1. Headline

| Test | Statements | Parse coverage | Value TP/FP/FN | Filter TP/FP/FN | Influence TP/FP/FN |
|---|---|---|---|---|---|
| T1 Breadth | 24 | 100% | 100 / 8 / 9 | 16 / 4 / 5 | 46 / 2 / 1 |
| T2 Depth | 13 | 100% | 26 / 0 / 16 | 3 / 2 / 6 | 12 / 0 / 14 |
| T3 Interaction | 13 | 100% | 41 / 2 / 10 | 2 / 2 / 8 | 36 / 0 / 9 |
| T4 Adversarial | 15 | 100% | 42 / 1 / 1 | 12 / 4 / 2 | 11 / 0 / 0 |
| T5 Metamorphic | 21 | 100% | 25 / 2 / 9 | 1 / 3 / 7 | 11 / 0 / 18 |
| **B1 total** | **86** | **100%** | **234 / 13 / 45** | **34 / 15 / 28** | **116 / 2 / 42** |

**499 labelled facts: 384 found (77%), 115 missed, 30 false positives, 0 refusals.**

The parser is not the problem in this scope: all 86 statements parsed and were analysed,
with no refusals.

**One defect accounts for 84 of the 115 misses** (B1-01, below). Without it, recall would
be about 94%.

**T4, the adversarial file, did well.** Of its 13 traps, only the quoted-identifier trap
caught the analyser (B1-05); the D-11 comma join is a decision not yet built, not a trap.
No unqualified-name guess, alias-shadowing trap, reused alias, self-join crossing, ordinal,
keyword identifier, or construct-name-in-a-literal produced a wrong edge or a refusal.

## 2. Findings, by root cause

Every miss and every false positive is assigned to exactly one cause; the counts add up to
the totals above. **No key errors were found.** Every disagreement traces to the analyser
or to one of the conventions decided for this batch but not yet implemented.

### Analyser defects

| ID | What | Evidence | FP | FN | Grade |
|---|---|---|---|---|---|
| **B1-01** | **A `WITH … INSERT` loses every edge that passes through its CTEs.** The CTE is declared `dangling_reference` ("relation not in dictionary"). The same query written `INSERT … WITH` works. `WITH … INSERT` is PostgreSQL's usual spelling of a CTE-fed write. Minimal reproduction: `WITH c AS (SELECT s.region AS r FROM core.dim_store s) INSERT INTO mart.kpi_rollup (region) SELECT c.r FROM c` produces no edges. | T2 S2, S4, S12, S13; T3 S13; T5 R1 | 0 | **84** | R3 — declared, but declared as the wrong thing, and it hits the most common form |
| **B1-02** | `JOIN … USING` / `NATURAL JOIN`: the merged column is read as `COALESCE(left, right)` for **every** join type. PostgreSQL defines it as the left input for INNER and LEFT, the right input for RIGHT, and COALESCE only for FULL. The result is a `conditional` edge from both sides where the truth is one `identity` edge. | T1 S8a, S8b; T5 P1 (the pair differs) | 6 | 2 | R0 (proposed) — wrong claim |
| **B1-03** | Ordered-set aggregates (`percentile_cont` / `percentile_disc … WITHIN GROUP (ORDER BY x)`) are classified `derived`, not `aggregated`. Probable cause: the ordered column sits in the WITHIN GROUP's ORDER BY, which is a sibling of the aggregate node rather than inside it, so the path walk from the column never meets the aggregate. | T1 S11; T3 S7 | 4 | 4 | R0 (proposed) — wrong transform |
| **B1-04** | `cardinality()` (a built-in) is treated as an unknown routine: `source_unavailable … could not be summarised - its source is not in this analysis`. That loses the value of `array_agg(…)` and its grouping influence. The same shape as PG-04 (`jsonb_build_object`): the PostgreSQL built-in function catalogue is incomplete. | T1 S11 | 0 | 2 | R3 — declared, misclassified |
| **B1-05** | **A quoted mixed-case identifier is matched case-insensitively.** With `"Region"` (= segment) and `region` (= region) both projected, `x.region` resolves to `"Region"`. The result is a confident wrong edge, `dim_customer.segment → kpi_rollup.segment`, with no boundary. Probable cause: `band0._projection_named` compares `.upper()` on both sides, which is right for unquoted names and wrong for quoted ones. | T4 S13 | 1 | 1 | **R0 (proposed) — silent wrong edge** |
| **B1-06** | Every statement that uses a CTE also gets a `context_dependent_binding` boundary that treats the CTE name as an unqualified schema object, even when every real relation is schema-qualified. It isn't scored, but it is noise in the one list a reader is told to trust. | T2, T3, T5 (every CTE statement) | — | — | R4 — measurement distortion |

### Decisions taken for this batch, not yet implemented (expected)

| Decision | Effect on B1 | FP | FN | Grade |
|---|---|---|---|---|
| **D-9** row selection is a filter | DISTINCT ON keys and tie-breakers, filtered ranks, and ORDER BY + LIMIT/FETCH give no filter edge. "The latest row per customer" is invisible. | 0 | 14 | R1 — silent loss |
| **D-10** FILTER reads as CASE | The FILTER columns come out as a filter on the whole relation instead of a value of one column. T5 P8 differs. | 7 | 8 | R0 (proposed) — claims a column decides which rows exist when it does not |
| **D-11** a join in WHERE is structural | Comma-join conditions come out as filters. T5 P9 differs. | 6 | 0 | R0 (proposed) — the same |

### Open convention question

| ID | Question | FP (unlabelled) |
|---|---|---|
| **B1-Q1** | **What does `GROUPING(a, b)` produce?** It reports which grouping set a row belongs to and carries no column's value. Today the analyser emits `a, b → target` as an *aggregated value* plus influence, and treats `WHERE GROUPING(…) = 1` as a post-aggregation filter on a and b. Neither is labelled in the keys and neither is forbidden, so these six edges count as false positives until the question is decided. | 6 |

### The grade scale needs one more step: R0

The R1–R5 scale was written for *losses*. B1 is the first batch whose worst defects are
*wrong claims*: an edge that is false, or a filter on a column that decides nothing. Under
this project's own rule (a wrong edge reads as a finding about the data), such a claim is
worse than a silent loss. **Proposed: R0 = a false edge or a false transform, emitted
silently.** It needs your agreement before it enters the register's grading.

## 3. Metamorphic pairs (T5)

| Pair | Result | Why |
|---|---|---|
| P1 USING vs ON | **DIFFER** | B1-02: A gives `conditional` from both sides, B gives `identity` from the left |
| P2 named vs inline window | equal | |
| P3 GROUP BY ordinals vs names | equal | |
| P4 DISTINCT vs GROUP BY | equal | |
| P5 ROLLUP vs GROUPING SETS | equal | |
| P6 HAVING vs WHERE over an aggregate | equal | D-6 holds |
| P7 COUNT(DISTINCT) vs COUNT over DISTINCT | equal | |
| P8 FILTER vs CASE | **DIFFER** | D-10 not built: A is a relation filter, B a column value |
| P9 JOIN ON vs comma join | **DIFFER** | D-11 not built: B gives two filters |
| P10 ROW_NUMBER() = 1 vs DISTINCT ON | equal, **but both wrong** | D-9 not built: both lose the same two filters, which the key catches |

Seven of ten agree. Each disagreement has exactly one cause, already listed above.

## 4. What held — worth carrying forward

- **Parsing:** 86 of 86 statements, every B1 construct, no refusals.
- **Influence (D-2, D-4) composes through depth:** re-aggregation across two GROUP BYs
  (T2 S3), windows over aggregates (T3 S1, S4, S10, S11), and a named window two levels
  down (T2 S11). **Not yet shown:** the sibling-CTE grain test (T2 S12) and the deep
  HAVING test (T2 S4) are both `WITH … INSERT`, so B1-01 removed them before they could
  say anything. They are the first thing to read after B1-01 is fixed.
- **D-6 holds where it was reached:** HAVING against WHERE over an aggregate agree (P6).
- **Name resolution under adversarial load:** 12 of 13 traps clean.
- **No invented sources:** the 30 false positives are misclassified or redundant claims
  about columns the statement really reads. No edge came from a column the statement
  never touches.

## 5. Recommended fix order (for agreement, not yet started)

| # | Item | Why this position |
|---|---|---|
| 1 | **B1-05** quoted identifiers | The only silent wrong edge from a defect. Small fix: compare quoted names exactly |
| 2 | **B1-01** `WITH … INSERT` | 84 of 115 misses; the standard PostgreSQL write form. Expected to be one seam where the CTE attached to the INSERT is not handed to the SELECT |
| 3 | **D-10, D-11** | Decided; both currently emit false filters (R0). D-10 also closes PG-15's xfail |
| 4 | **D-9** | Decided; 14 silent misses |
| 5 | **B1-02, B1-03** | Wrong transforms on two well-bounded constructs |
| 6 | **B1-04** with PG-04 | One item: a PostgreSQL built-in function catalogue |
| 7 | **B1-06** | Measurement noise; fix once B1-01 shows where CTE names are being treated as relations |
| — | **B1-Q1** | Needs your decision, not code |

After each fix: rerun this batch. **A fix is done when its rows here move and nothing else
does**, the same receipt-by-measurement rule as before.

## 6. Coverage checklist (programme plan §1, row B1)

| Construct | Exercised |
|---|---|
| projections, expressions, CASE / COALESCE / NULLIF / GREATEST, casts | T1 S1; throughout |
| WHERE operators | T1 S2 |
| INNER / LEFT / RIGHT / FULL / CROSS / USING / NATURAL joins | T1 S3–S8b; T3 S6; T4 S14 |
| GROUP BY, ROLLUP, CUBE, GROUPING SETS, GROUPING() | T1 S9–S10c; T2 S5; T3 S4; T5 P5 |
| HAVING | T1 S9, S16; T2 S4; T3 S12, S13 |
| FILTER, WITHIN GROUP, string_agg … ORDER BY, array_agg | T1 S11; T2 S7; T3 S3, S7 |
| windows: ranking, offset, value, aggregate; named; ROWS frames | T1 S12, S15; T2 S2, S11, S13; T3 throughout |
| DISTINCT, DISTINCT ON | T1 S13a/b; T2 S8; T3 S8; T5 P4, P10, R1 |
| ORDER BY, LIMIT, OFFSET, FETCH … WITH TIES | T1 S14a–d; T3 S9; T4 S4, S5 |

**Deferred, with reasons:** `RANGE` / `GROUPS` frame modes and frame `EXCLUDE`, `GROUP BY
DISTINCT` (PostgreSQL 14+), `NULLS FIRST/LAST`, and `array_agg(… ORDER BY …)`. All are
B1-scoped, and none would change a lineage rule that the batch had not already tested
through a sibling construct. They belong in a B1 follow-up file once the fixes above land,
so they are measured against a working baseline.

## 7. Deviations from the approved plan, stated

- **`stress/dictionary_postgres.{json,md}` were kept, not removed.** Four unit tests (the
  D-6, D-7, equivalence and standalone-SELECT regression guards) and the frontend load
  them. Removing them would have deleted fixes' regression guards to tidy a folder. The
  five old packages and their pinned test module were removed as planned; they remain in
  git history.
- **Two tools were added** under `stress/pg/`:
  - `keygen.py` compiles a one-fact-per-line `.keyspec` into the harness's YAML. A
    300-line file carries 70–180 facts, and eight-line YAML entries at that volume are
    where key errors hide.
  - `triage.py` lists every miss and false positive by statement, and runs the pair check.
  The harness itself is unchanged.
- **T1 is 437 lines, not ~300**, because 24 statements, one construct group each, each
  with its own header, would not fit. T2–T5 are 242–347 lines.
- **Card corrections while keying**, marked in place in `OBJECTIVES.md`: the USING merged
  column comes from the left input only (the card first said both sides), and the
  `GROUPING()` filter is flagged rather than labelled.
- **SQL validity is by reading only**, as decided. Nothing in this batch failed to parse,
  so the "suspect SQL" triage class was never needed.

## 8. Fixes applied, 2026-10-01 — in the recommended order

Every item in §5 except the B1-Q1 decision, in that order. Each was rerun against this batch
before the next began, and the batch was the receipt.

| Item | Change | Where |
|---|---|---|
| B1-05 | exact name match first, case-blind only if unique — one helper for projections and set-operation arms | `band0._position_named` |
| B1-01 | the statement's WITH is hung on the query before analysis, merged ahead of any inner WITH | `band0._with_statement_ctes` |
| D-10 | an aggregate's FILTER `WHERE` is no longer read as a correlated predicate | `band0._correlated_filter_columns` |
| D-10 + B1-03 | a `Filter` / `WithinGroup` wrapping an aggregate counts as that aggregate on the transform path | `transforms.is_aggregate` |
| D-11 | a top-level equality between two inner-joined relations of one FROM is structural | `predicates._implicit_join_column_ids` |
| D-9 | DISTINCT ON keys and tie-breakers, ORDER BY under LIMIT/FETCH/OFFSET (output names and ordinals resolved), and filtered windows | `band0._row_selection_sources`, `_window_filter_sources` |
| B1-02 | `qualify`'s merged-column COALESCE replaced by the left/right input per join type; written COALESCE untouched | `band0._qualify`, `_merged_join_columns` |
| B1-04 + PG-04 | per-dialect catalogue of built-ins SQLGlot leaves untyped, including three aggregates | `analysis/builtins.py` |
| B1-06 | a statement's own CTE names are not search-path dependencies | `procedure._context_dependent_names` |

### Result

| Test | Value TP/FP/FN | Filter TP/FP/FN | Influence TP/FP/FN | Boundaries |
|---|---|---|---|---|
| T1 | 109 / 2 / 0 | 21 / 0 / 0 | 47 / 2 / 0 | 0 (was 1) |
| T2 | 42 / 0 / 0 | 9 / 2 / 0 | 26 / 0 / 0 | 0 (was 15) |
| T3 | 51 / 0 / 0 | 10 / 0 / 0 | 45 / 0 / 0 | 0 (was 8) |
| T4 | 43 / 0 / 0 | 16 / 0 / 0 | 11 / 0 / 0 | 0 |
| T5 | 34 / 0 / 0 | 8 / 0 / 0 | 29 / 0 / 0 | 0 (was 12) |

**501 of 501 facts found; 0 missed.** All ten metamorphic pairs agree. The six remaining
false positives are all `GROUPING()` (B1-Q1, left open by decision): four in T1 S10a, two
in T2 S5. They are unlabelled rather than wrong, and will move with whatever B1-Q1 decides.

### What the fixes reached outside this batch — checked, not assumed

The Oracle stress packages were rerun at HEAD and after, and diffed:

* **`stress_02` gained one true positive and nothing else** - D-9 labels
  `STG_ORDERS.GROSS_AMOUNT -> GTT_STAGE`, the ORDER BY under FETCH FIRST. Its key had
  written in advance that this label must move if the row-selection convention were ever
  revisited; the label was added with that note.
* **D-11 was first implemented wider than decided**, and this diff is what caught it. It
  covered every comparison operator, so `stress_01`'s self-join predicate
  `child.signup_date > parent.signup_date` stopped being a filter - and that key labels it
  a real predicate. The decision as taken was "column-equals-column"; the code and the
  written D-11 were both narrowed to equality. T4 S15's key had followed the wider
  wording too and forbade the `>=` operands - **two key errors of mine**, corrected and
  stated in the keyspec. Whether non-equality join conditions are structural is now
  **B1-Q2**, open.
* `stress_01`, `stress_03` and `stress_04`: byte-identical output.
* **Suite: 882 passed, 2 skipped (Oracle down), 0 xfailed.** The PG-15 xfail is now a
  plain assertion. Regression tests were added for every fix:
  `test_quoted_identifier_case`, `test_with_before_insert`, `test_using_merged_column`,
  `test_builtin_functions` and `test_row_selection_and_implicit_joins` (D-9, D-11, B1-03).
  **Run against the unfixed source, 25 of them fail**; the ones that pass there are the
  deliberate negatives and controls (an ORDER BY with no limit, a user function still
  declared, a written COALESCE left alone), which held before and must keep holding.

**Not re-measured:** phase 0, which needs the Oracle container.
