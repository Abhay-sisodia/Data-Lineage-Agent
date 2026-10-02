# Batch B2 — Subqueries, CTEs and set operations: report

**Run 2026-10-01**, against the analyser at commit `766c625` (B1 complete), unchanged. Every
key was written before the analyser first saw its file. **Nothing has been fixed.** This is
the review point: agree the findings and the fix order first.

```
.\.venv\Scripts\python.exe stress/pg/triage.py b2_subqueries_ctes_setops
```

## 1. Headline

| Test | Statements | Value TP/FP/FN | Filter TP/FP/FN | Influence TP/FP/FN | Boundaries |
|---|---|---|---|---|---|
| T1 Breadth | 16 | 41 / 6 / 17 | 30 / 11 / 4 | 4 / 0 / 1 | 2 |
| T2 Depth | 10 | 28 / 1 / 13 | 19 / 13 / 2 | 8 / 0 / 2 | 13 |
| T3 Interaction | 10 | 28 / 3 / 12 | 18 / 7 / 3 | 16 / 0 / 0 | 6 |
| T4 Adversarial | 11 | 28 / 2 / 4 | 17 / 8 / 0 | 4 / 0 / 0 | 1 |
| T5 Metamorphic | 17 | 21 / 3 / 7 | 25 / 1 / 0 | 6 / 0 / 1 | 0 |
| **B2 total** | **64** | **146 / 15 / 53** | **109 / 40 / 9** | **38 / 0 / 4** | **22** |

**359 labelled facts: 293 found (82%), 66 missed, 55 false positives.** All 64 statements
parsed and were analysed, with no refusals.

**D-12, decided for this batch and not built, accounts for 48 of the 66 misses and 34 of
the 55 false positives.** What remains is ten analyser defects (below) and four
convention seams the metamorphic pairs put on the record.

What held, without qualification: recursive CTEs (T1 S11, T2 S6, T3 S4), the six-CTE chain
with MATERIALIZED hints, every UNION / INTERSECT / EXCEPT form (convention (a) held
everywhere it was reached), EXISTS / NOT EXISTS / IN / NOT IN / ANY / ALL in WHERE,
derived-table aggregation, a CTE joined to itself, positional binding under swapped
aliases, a CTE named like a real table, and innermost-first resolution of unqualified
names (T4 S2). No invented sources: every false positive is about a column the statement
really reads.

## 2. Findings, by root cause

Every miss and false positive is assigned to exactly one cause; the counts add to the
totals. **No key errors were found** in this batch's first run.

### Decided, not yet implemented

| Decision | Effect | FP | FN |
|---|---|---|---|
| **D-12** a scalar subquery's predicate is a value | its WHERE columns come out as filters on the relation | 34 | 48 |
| **D-13** an ordered pick is influence | appears as the influence misses below, and inside B2-02 | — | 3 |

### Analyser defects

| ID | What | Where | FP | FN | Grade |
|---|---|---|---|---|---|
| **B2-01** | A column a scalar subquery passes through **unchanged** is reported `derived`, not `identity`. `(SELECT c.full_name FROM … WHERE …)` derives nothing; the subquery wrapper is counted as a computation on the path. | T1 S3, S4; T3 S10; T5 P8, R1 | 6 | 5 | R0, wrong transform |
| **B2-02** | **A scalar subquery's `ORDER BY` column is reported as a VALUE source.** `paid_at` in `… ORDER BY p.paid_at DESC LIMIT 1` appears as `paid_at → lifetime_value [value]`. That's false whatever D-13 says: the ordering supplies no part of the value. It is S2-12's defect again, in a new position. | T1 S4; T2 S9; T5 P8 | 3 | — | **R0, wrong claim** |
| **B2-03** | **A join inside a scalar subquery leaks its keys as VALUE sources** (`orders.id`, `payments.order_id → lifetime_value [derived]`), and **the D-9 code added in B1 does the same for an ordering by such a subquery** (T4 S6). D-5 says an ON is structural wherever it sits. | T1 S2; T3 S6; T4 S6 | 8 | — | **R0, wrong claim** |
| **B2-04** | D-9 is not applied to an `ORDER BY … LIMIT` over a **whole set operation**. The B1 implementation reads SELECT scopes only. | T1 S12d; T3 S8 | — | 4 | R1, silent loss |
| **B2-05** | Inside a **LATERAL**: D-9's ORDER BY … LIMIT is not applied, and its GROUP BY's influence does not reach the column. Probably the LATERAL scope is neither a derived table nor a CTE to the scope walk. | T1 S9; T2 S5 | — | 3 | R1, silent loss |
| **B2-06** | **The phase of a filter column that reaches its predicate through an aggregate inside a subquery.** `x > (SELECT AVG(y) …)` gives `y` pre-aggregation (D-6 says post). And a HAVING inside an IN subquery is also swept up by the outer WHERE as pre. | T1 S7; T2 S2, S8 | 2 | 2 | R0, wrong phase |
| **B2-07** | `x IN (SELECT a … INTERSECT SELECT b …)`: the IN's projected key is lost from the first arm, and an `unresolved_identifier` boundary names the arm's columns. | T3 S5 | — | 1 | R3, declared |
| **B2-08** | **An inner alias that shadows an outer one is resolved OUTWARD.** In `FROM orders o WHERE EXISTS (SELECT 1 FROM payments o WHERE o.order_id = o.id)`, `o.id` is bound to the outer orders. PostgreSQL resolves innermost first, and the analyser does get unqualified names right (T4 S2) but not shadowed qualifiers. It also declares `ORDERS.ORDER_ID not a column of orders`. | T4 S1 | 1 | — | **R0, wrong claim** |
| **B2-09** | **An EXISTS subquery's ORDER BY is swept into the outer WHERE's filters.** Whether a row exists does not depend on which is first. | T4 S9 | 1 | — | **R0, wrong claim** |
| **B2-10** | **`unresolved_identifier` boundaries on columns the analyser DID trace**: inside recursive CTEs, nested subqueries, and set operations in subqueries. 22 boundaries across the batch, and every statement carrying one except T3 S5 (B2-07) still scored its edges. Noise in the one list a reader is told to trust. | T1 S11; T2 S1, S2, S6, S7, S10; T3 S4, S5, S9 | — | — | R4, measurement |

### The four seams (T5) — same rows, different lineage under today's conventions

| Pair | Spellings | Today | Seam |
|---|---|---|---|
| P5 | `IN (subquery)` vs `JOIN + DISTINCT` | **DIFFER** | the semi-join's match columns are filters through IN and structural through JOIN (D-5) |
| P6 | `EXCEPT` vs `NOT EXISTS` | **DIFFER** | the anti-join's outer key is a filter through NOT EXISTS, and only a value through EXCEPT (convention (a)) |
| P7 | scalar subquery vs `LEFT JOIN LATERAL` aggregate | equal *today*, **will DIFFER after D-12** | a single-row LATERAL's WHERE is a filter; the scalar subquery's will be a value |
| P8 | ordered pick vs `LEFT JOIN` to a `DISTINCT ON` derived table | **DIFFER** | D-13 makes the ordering influence; D-9 makes it a filter, though under a LEFT JOIN it removes no row |

**The four have one thing in common.** Each time, the "filter" spelling labels a predicate
as deciding which target rows exist, where the predicate **cannot remove a target row**:
a LEFT JOIN keeps every outer row, a single-row LATERAL always matches, and a semi-join's
key only matches. A single principle would settle all four (and B1-Q2): **a predicate is a
filter only if it can remove rows from the result; otherwise it is a value or influence of
the columns it feeds.** That is a decision, not a fix, and it is proposed here as **B2-Q1**
for you to take or reject. It would also revise the PG-03 LATERAL convention.

The other three pairs, P1–P4 (EXISTS vs IN, IN vs = ANY, CTE vs inlined, UNION vs UNION
of DISTINCTs), are **equal**.

## 3. Recommended fix order (for agreement, not yet started)

| # | Item | Why here |
|---|---|---|
| 1 | **B2-08** shadowed alias resolved outward | a silent wrong edge, from a scoping rule |
| 2 | **B2-02, B2-03, B2-09** columns that leak into the wrong role (an ordering as a value, a join key as a value, an EXISTS ordering as a filter) | three R0 wrong claims; likely one walk applied too broadly |
| 3 | **D-12, D-13** | decided; 82 of the 121 disagreements |
| 4 | **B2-01** scalar pass-through is identity | wrong transform |
| 5 | **B2-06** phase through a subquery aggregate | wrong phase |
| 6 | **B2-04, B2-05** D-9 over set operations and inside LATERAL | silent losses; completes D-9 |
| 7 | **B2-07, B2-10** | declared loss and boundary noise; likely one resolver gap |
| — | **B2-Q1** (and B1-Q1, B1-Q2) | needs your decision |

## 4. Coverage checklist (programme plan §1, row B2)

| Construct | Exercised |
|---|---|
| scalar subqueries: uncorrelated, correlated, aggregating, lookup, ordered pick | T1 S1–S4, S13; T2 S1, S9, S10; T3 S1, S6, S10; T4 S6, S8; T5 P7, P8, R1 |
| EXISTS / NOT EXISTS / IN / NOT IN / ANY / ALL / scalar comparison in WHERE | T1 S5–S7; T2 S2, S7, S8; T3 S3, S5, S7; T4 S1, S2, S5, S9; T5 P1, P2, P6, R1 |
| derived tables, LATERAL (incl. top-N per row) | T1 S8, S9; T2 S5; T3 S7; T5 P7 |
| CTE chains, MATERIALIZED hints, CTE self-join, CTE named like a table | T1 S10; T2 S3; T4 S3, S10; T5 P3 |
| WITH RECURSIVE | T1 S11; T2 S6; T3 S4 |
| UNION / UNION ALL / INTERSECT / EXCEPT (ALL), nested, in CTEs, with ORDER BY LIMIT | T1 S12a–d; T2 S3, S4, S7, S10; T3 S2, S5, S8, S10; T4 S4; T5 P3, P4, P6, R1 |

**Deferred:** `SEARCH` / `CYCLE … TO`, which fail the whole file's parse (PG-18, already
registered). They need their own isolated file once PG-18 is worked on.

## 5. Deviations and corrections, stated

- **D-12's text overclaimed.** It said a correlated scalar subquery and its FILTER spelling
  "must give the same lineage". Only their non-correlation predicates agree: the
  correlation corresponds to a JOIN plus a GROUP BY, which is influence under D-2.
  Corrected in OBJECTIVES.md while keying T5.
- **Three key-internal contradictions** were caught by the harness while keying, before
  any run. In each, one statement forbade an edge another statement legitimately
  requires. Each was resolved by dropping the narrower forbid; none changed a label.
- T5 has **four seam pairs** instead of the planned "8 must-equal pairs". The seams turned
  up while keying, and labelling them as equal would have settled a convention inside a
  key.

## 6. Fixes applied, 2026-10-02 — in the agreed order, under D-14

**B2-Q1 was decided as D-14, scoped** (see OBJECTIVES.md): a predicate that cannot remove a
result row — inside an outer join's null-supplying side, or a one-row LATERAL — is a value
or influence of the columns it feeds. Semi-join and anti-join match keys were deliberately
left out, so the P5 and P6 seams stay open by decision. Three keys were relabelled under
D-14 before any code moved: T3 S7, T5 P7 and T5 P8.

| Item | Change | Where |
|---|---|---|
| B2-08, B2-09, B2-06 (nested HAVING) | every predicate column traced from the scope it was **written** in (innermost first); a nested ordering ignored unless it limits rows, and always under EXISTS | `band0._home_scope`, `_scope_index`, `_in_unlimited_nested_order` |
| B2-02, B2-03 | a nested query supplies values only through its projection — never its WHERE / HAVING / GROUP BY / ORDER BY / join conditions | `band0._in_non_value_clause` in `_value_columns`, and in D-9's `_ordered_sources` |
| D-12, D-13, D-14 | scopes split into **row-deciding** (filters) and **value-only** (a SELECT-list scalar subquery, an outer join's null side, a one-row LATERAL); value-only WHERE → values, ordered pick → influence. Also carried through `_trace`, so derived tables, CTEs and MERGE sources keep them | `band0._row_deciding_scopes`, `_value_only_edges`, `_scalar_predicate_values`, `_read_scopes` |
| B2-01 | a column carried unchanged through a subquery, derived table or set-operation arm is identity | `transforms._PASS_THROUGH` |
| B2-06 | a column reaching its predicate through an aggregate inside it is post-aggregation | `band0._filter_edges` |
| B2-04 | D-9 over a whole set operation, by output position, in every arm | `band0._set_operation_selection` |
| B2-05 | LATERAL read through its wrapper for D-9 and for GROUP BY influence | `_ROW_SELECTING_SCOPES`, `_grouping_influence` |
| B2-07, B2-10 | follow from scope-correct tracing; a recursive CTE's self-reference adds no source and declares nothing | `band0._is_recursive_self_reference` |

### Result

**361 of 361 facts, 0 false positives, 0 boundaries, 0 refusals.** The pair check reports
P5, P6 and P8's match key differing — the seam left open by decision — and nothing else.
B1 unchanged. Pinned in `tests/test_pg_batches.py`; regression tests in
`tests/test_b2_subquery_scoping.py` (12 of 14 fail on the unfixed source; the two that do
not are the controls).

### What the fixes reached outside this batch

* **Oracle stress — four keys relabelled, every move a decision, misses and false
  positives otherwise identical to HEAD.** `stress_01` (2 filters → 3 values, D-12/D-14),
  `stress_03` (+2 values, D-12), `stress_04` (5 filters → 12 values/influence, D-12/D-14 —
  including the S3-09 correlation whose filter-or-value tension that register entry
  recorded), `stress_02` (one filter's phase pre → post, B2-06). Each retired label is left
  as a comment saying why.
* **The diff caught a regression of mine before it shipped.** The first D-14 cut made a
  `MERGE … USING (… LEFT JOIN (… WHERE status = 'A') …)` predicate stop being a filter
  without becoming a value: a silent loss. Fixed by carrying D-14 values through `_trace`.
  It also caught that SQLGlot lists every visible CTE in `sources`, so the value closure
  swept unrelated CTEs' predicates in until it was restricted to what the query reads.
* **Phase-0 corpus — one key relabelled, by decision (option 1).**
  `ground_truth/sq_06_self_join.yaml`: the two correlation labels of its scalar subquery move
  from filter to value of `ROOT_NAME` under D-12. **The signed phase-0 numbers move with
  this and have not been re-measured** (the Oracle container is down); the obligation is
  recorded in FINDINGS. The corpus SQL itself is untouched, so the corpus fingerprint is too.
* **Five unit tests** pinned the old s7 convention and were updated to assert D-12/D-14.
  What they protect — each operand bound to the relation it names, no spurious boundary —
  is unchanged and still asserted.
