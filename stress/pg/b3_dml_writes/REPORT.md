# Batch B3 — DML writes: report

**Run 2026-10-02**, against the analyser at commit `9fd6223` (B2 complete), unchanged.
Every key was written before the analyser first saw its file; decisions D-15 to D-18 were
taken before any key. **Nothing has been fixed.** This is the review point.

```
.\.venv\Scripts\python.exe stress/pg/triage.py b3_dml_writes
```

## 1. Headline

| Test | Statements | Seen / analysed / refused | Value TP/FP/FN | Filter TP/FP/FN | Influence TP/FP/FN |
|---|---|---|---|---|---|
| T1 Breadth | 19 | 14 / 9 / 5 | 13 / 1 / 25 | 12 / 6 / 11 | 0 / 0 / 4 |
| T2 Depth | 8 | 6 / 4 / 2 | 6 / 0 / 10 | 6 / 3 / 22 | 6 / 0 / 4 |
| T3 Interaction | 8 | 6 / 6 / 0 | 13 / 1 / 12 | 8 / 6 / 11 | 6 / 0 / 11 |
| T4 Adversarial | 12 | 8 / 7 / 1 | 12 / 0 / 5 | 4 / 2 / 7 | — |
| T5 Metamorphic | 13 | 11 / 9 / 2 | 9 / 2 / 9 | 9 / 0 / 4 | 2 / 0 / 5 |
| **B3 total** | **60** | **45 / 35 / 10** | **53 / 4 / 61** | **39 / 17 / 55** | **14 / 0 / 24** |

**246 labelled facts: 106 found (43%), 140 missed, 21 false positives.** This is the
weakest first run of the three batches by a wide margin. B1 started at 77% and B2 at 82%.

**Statements seen is 45 of 60.** Every UPDATE is uncounted, which is the open finding PG-01
measured again. Of the ten refusals, two are correct by name (TRUNCATE, COPY), and the
INSERT written after them is analysed normally, so **refusals stay local**. The other eight
refuse statements that have readable lineage.

What held: ON CONFLICT DO NOTHING (D-15's structural target already behaves), simple
UPDATE with a self-reference, DELETE with OR and IN, an unconditional DELETE, VALUES of
literals, a MERGE whose USING is five levels deep, EXCLUDED read as the proposed row
(T4 S2), SET reading the old row (T4 S4), an explicit column list out of table order
(T4 S5), and a MERGE whose USING alias is the target's table name (T4 S8). Pairs P1 (ON
CONFLICT vs MERGE), P2 (CTE vs inlined) and P5 (DELETE IN vs EXISTS) are equal.

## 2. Findings, by root cause

### Analyser defects

| ID | What | Where | Grade |
|---|---|---|---|
| **B3-01** | **The target's schema is lost when the INSERT target has an alias (`INSERT INTO mart.t AS x`) or no column list.** `mart.top_customers` is looked up as `core.top_customers`, and the whole statement is refused with NAME_NOT_RESOLVED. Both are ordinary upsert spellings. | T1 S1, S5; T4 S6; T5 P3 A, R1 | R3, a declared but false refusal |
| **B3-02** | `INSERT … DEFAULT VALUES` is refused ("INSERT from NoneType"). It has no lineage, and the correct answer is nothing, not a refusal. | T1 S2b | R2, unsound refusal |
| **B3-03** | **Data-modifying CTEs are not understood.** `WITH x AS (DELETE/UPDATE/INSERT … RETURNING …) INSERT … FROM x`: `x` is declared "relation not in dictionary", or the statement is refused; the DML inside the CTE is never analysed. **A writable CTE that nothing reads (T4 S10b) and one read by a bare SELECT (S10a) produce nothing at all, with no boundary.** PostgreSQL executes those DELETEs regardless. | T1 S14; T2 S4, S5; T3 S5; T4 S10a, S10b; T5 R1 | **R1, silent loss** (S10a/b); R3 elsewhere |
| **B3-04** | **`UPDATE … FROM`:** the FROM list's columns are not resolved ("unresolved country_code / email / id"), so the values are lost, and the target–FROM join condition is emitted as a **filter** (D-11 not applied to UPDATE). The def-use UPDATE path does not see the FROM list. | T1 S7; T2 S1, S7; T3 S1; T4 S1, S3 | **R0, wrong claims**, plus R3 losses |
| **B3-05** | **`WITH … UPDATE / DELETE / MERGE`:** the leading WITH is not attached, so the CTE becomes a "dangling" relation or a bogus source (`stale.product_id`, `dormant.customer_id` emitted as columns). B1-01 was fixed for INSERT only. | T2 S1, S6, S8; T3 S8 | **R0** (invented `cte.column` sources), R3 |
| **B3-06** | **Row-valued SET (`SET (a, b) = (x, y)` / `= (SELECT …)`) writes to a target column with an EMPTY name** (`column:dim_store.`, `column:dim_warehouse.`). The edges are malformed and the real columns get nothing. | T1 S8; T5 P4 B | **R0, wrong edges** |
| **B3-07** | **`DELETE … USING`:** the USING join condition is emitted as filters (D-11 not applied), and a USING CTE's own predicate (HAVING) is not traced. | T1 S11; T3 S8; masks T5 P6 | **R0**, R1 |
| **B3-08** | **`ON CONFLICT … DO UPDATE` SET and WHERE are not analysed at all.** A column assigned only in DO UPDATE (`growth_pct`) gets nothing; the DO UPDATE WHERE gives no filter. | T3 S3; T4 S11; T1 S5 (also refused) | **R1, silent loss** |
| **B3-09** | **MERGE arm conditions are not filters.** `WHEN MATCHED AND s.status = 'CLOSED'` and `WHEN NOT MATCHED BY SOURCE AND t.is_current` (PostgreSQL 17) give nothing. | T1 S15 | **R1, silent loss** |
| **B3-10** | **The phase of an ordering aggregate written inline in a window** (`ROW_NUMBER() OVER (… ORDER BY SUM(x))` with `rn <= 10`) comes out pre-aggregation. D-9's selection phase reads the traced column, not the SUM in the window. | T3 S4 | R0, wrong phase |
| **B3-11** | **The B2-06 phase rule is missing from the UPDATE and DELETE paths:** `WHERE (SELECT SUM(q) …) > 1000` and `p.list_price < (SELECT AVG(…))` give pre-aggregation. | T4 S9; T2 S6 | R0, wrong phase |
| **B3-12** | A scalar subquery in an INSERT's VALUES loses columns (`COUNT(DISTINCT c.id)` declared "unresolved id in VALUES"). | T1 S3 | R3 |
| **PG-01** | (existing) UPDATE is analysed but never counted: 15 of 60 statements are missing from `statements_seen`. | all | R4 |

### Decided, not yet implemented (expected)

| Decision | Effect | Where |
|---|---|---|
| **D-16** RETURNING → `<result>` | no `<result>` edges for any RETURNING | T1 S13; T2 S8; T3 S7; T4 S7 |
| **D-17** writable-CTE predicate filters every table | blocked behind B3-03 | T1 S14; T2 S4, S5; T3 S5 |
| **D-18** D-12 in a SET / VALUES | SET-subquery correlations come out as filters, values missing | T1 S8, S9; T3 S6 |

### Metamorphic pairs (T5)

| Pair | Result | Why |
|---|---|---|
| P1 ON CONFLICT vs MERGE | equal | |
| P2 CTE vs inlined | equal | |
| P3 omitted vs explicit column list | **DIFFER** | B3-01: the omitted list is refused |
| P4 `SET a, b` vs `SET (a, b)` | **DIFFER** | B3-06: the row form writes to an empty column name |
| P5 DELETE IN vs EXISTS | equal | |
| P6 DELETE USING vs EXISTS (seam) | equal, **by accident** | B3-07 makes USING emit its join keys as filters, matching EXISTS. Once B3-07 is fixed, this pair shows the open semi-join seam as expected |

## 3. Recommended fix order (for agreement, not yet started)

| # | Item | Why here |
|---|---|---|
| 1 | **B3-04, B3-07** UPDATE … FROM / DELETE … USING | the widest R0: join keys reported as filters, and FROM values lost, across eight statements |
| 2 | **B3-05** WITH before UPDATE / DELETE / MERGE | R0 invented `cte.column` sources; B1-01's fix extended to every write |
| 3 | **B3-06** row-valued SET | R0 malformed edges; small and well bounded |
| 4 | **B3-03** data-modifying CTEs, with **D-16, D-17** | the largest missing capability, and the only silent R1 among these; RETURNING has to exist for either decision to mean anything |
| 5 | **B3-08, B3-09** ON CONFLICT DO UPDATE, MERGE arm conditions | silent losses in the two upsert forms |
| 6 | **B3-01, B3-02** target schema with alias / omitted list; DEFAULT VALUES | false refusals of common spellings |
| 7 | **D-18**, then **B3-10, B3-11, B3-12** | decided values; phase and VALUES fixes |
| 8 | **PG-01** count UPDATE | measurement; touches signed numbers, so it goes last and gets its own before/after |

## 4. Coverage checklist (programme plan §1, row B3)

| Construct | Exercised |
|---|---|
| INSERT SELECT / VALUES / DEFAULT VALUES / omitted list / out-of-order list | T1 S1–S3; T4 S5, S6; T5 P3 |
| ON CONFLICT DO NOTHING / DO UPDATE / EXCLUDED / WHERE / ON CONSTRAINT | T1 S4, S5; T2 S2; T3 S3, S7; T4 S2, S11; T5 P1, R1 |
| UPDATE simple / self / FROM / row-valued / correlated SET / WITH-first | T1 S6–S9; T2 S1, S7; T3 S1, S6; T4 S1, S3, S4, S9; T5 P4 |
| DELETE simple / USING / subquery / unconditional / WITH-first | T1 S10–S12; T2 S6; T3 S2, S8; T5 P5, P6 |
| RETURNING (standalone and in a WITH) | T1 S13, S14; T2 S4, S5, S8; T3 S5, S7; T4 S7, S10a; T5 R1 |
| MERGE incl. NOT MATCHED BY SOURCE, MERGE … RETURNING, WITH-first | T1 S15; T2 S3, S8; T3 S4; T4 S8; T5 P1 |
| TRUNCATE, COPY | T1 S16a, S16b |

**Deferred:** `INSERT … OVERRIDING SYSTEM VALUE` and identity columns (B7, DDL), `COPY … TO`,
and `UPDATE … WHERE CURRENT OF` (B8, procedural).

## 5. Corrections while designing, stated

- **T5's first pair list included a non-equivalence.** `UPDATE … FROM` and `SET = (correlated
  subquery)` write different rows, because unmatched target rows are untouched in one and set
  to NULL in the other. That pair was dropped (OBJECTIVES.md).
- **The schema gained unique keys** on three mart tables so that `ON CONFLICT (k)` is valid
  PostgreSQL. They add no column, and the generated dictionary is byte-identical.
- **T5 R1 combines D-14 and D-17.** The archived refunds reach the summary only through a
  LEFT JOIN, so their predicate is labelled as a value of `lifetime_value` (D-14), and as a
  filter only on the table it deletes from. The register can record this as how the two
  decisions compose, unless you want D-17 to override D-14 there.
- **One placeholder line in the T3 keyspec** was caught and replaced before compiling. No key
  was scored with it.

## 6. Fixes applied, 2026-10-02 — in the agreed order

**One idea carries most of it: a PostgreSQL write is restated as the read that decides it.**
`analysis/pg_dml.py` turns each write into one or more `INSERT … SELECT` parts that band 0
already analyses correctly, and band 0 analyses each part. The restatement keeps every rule
from B1 and B2 (D-9 to D-14, the phase rules, scope-correct tracing) on the write paths
without copying them. Def-use stands aside for PostgreSQL UPDATE, DELETE and INSERT. The
Oracle path is untouched.

| # | Item | Change | Where |
|---|---|---|---|
| 1 | B3-04, B3-07 | `UPDATE t … FROM s` is read as `SELECT … FROM t, s WHERE …`, and `DELETE … USING` likewise. The target–FROM equality is then an ordinary WHERE join (D-11), and the FROM list resolves | `pg_dml._read_of`, `_update_as_insert`, `_delete_as_insert` |
| 2 | B3-05 | the statement's WITH is carried onto every part, so a CTE before UPDATE / DELETE / MERGE resolves as it does before INSERT | `pg_dml._read_of`; `band0._analyse_merge` |
| 3 | B3-06 | a row-valued SET is split per column: a tuple by position, `= (SELECT a, b …)` as one scalar subquery per column | `pg_dml._assignments` |
| 4 | B3-03, D-16, D-17 | each writable CTE becomes its own part, and its body in the WITH becomes a read of its RETURNING. Its predicate therefore filters its own table and every table its rows reach. A RETURNING on the outer write is a separate SELECT into `<result>`; a CTE's RETURNING is not | `pg_dml._split_writable_ctes`, `expand` |
| 5 | B3-08, B3-09 | ON CONFLICT DO UPDATE is a second part, an update of the target from the proposed rows (EXCLUDED renamed to the query's output), with its WHERE. The conflict target stays structural (D-15). Each MERGE arm's `AND` condition is a filter, in both dialects | `pg_dml._insert_parts`; `band0._analyse_merge` |
| 6 | B3-01, B3-02 | the target keeps its schema behind an alias. An omitted column list binds the first N table columns, N being the width of the query. DEFAULT VALUES is counted and gives nothing | `band0._target_of`; `pg_dml._insert_parts` |
| 7 | D-18, B3-11, B3-12 | follow from the restatement: a SET or VALUES scalar subquery is a SELECT-list scalar subquery, so D-12 and B2-06 apply. VALUES rows become `SELECT … UNION ALL`, and DEFAULT becomes NULL | `pg_dml._insert_parts` |
| 7 | B3-10 | a filtered window ordered or partitioned by an aggregate written inline cuts aggregated rows, so the cut is post-aggregation | `band0` D-9 window path |
| 8 | PG-01 (PostgreSQL) | UPDATE goes through band 0 and is counted | `band0.analyse_source` |

### Result

**246 of 246 facts, 0 false positives, 0 boundaries.** The only refusals are TRUNCATE and
COPY, both correct by name. 60 of 60 statements are counted: 58 analysed, 2 refused. The
pair check reports P6 differing by the semi-join match keys, which is the seam left open by
decision. P1 to P5 are equal. B1 and B2 are unchanged. Pinned in `tests/test_pg_batches.py`,
which now also allows T1's two deliberate refusals by code. Regression tests are in
`tests/test_b3_dml_writes.py`: 17 of 18 fail on the unfixed source, and the one that passes
is the plain-column rank control.

### What the fixes reached outside this batch

* **Oracle stress: identical to HEAD.** It was measured from a clean HEAD worktree, not by
  stashing.
* **The PostgreSQL scope contract moved twice, both by decision** (`docs/postgres_scope.md`,
  `scripts/probe_postgres.py`):
  * The data-modifying CTE was declared `dangling_reference` and is now in scope.
  * `DELETE … USING` whose WHERE is only the target join is now counted and empty, under
    D-11. The probe gained a filter so it still tests a USING that gives edges.
* **Found and fixed during this step:** an omitted column list was first read as *all*
  columns, which refused a short INSERT for arity (T4 S6). PostgreSQL binds to the first N.

### Still open

* **D-18 on the Oracle UPDATE path.** Oracle's UPDATE still goes through def-use, which reads
  a SET scalar subquery's correlation as a filter. The Oracle stress has no such case, so
  nothing moved. The decision is implemented for PostgreSQL only. Recorded as B3-13.
* **PG-01 for Oracle.** An Oracle UPDATE is still not counted. Counting it changes signed
  phase-0 numbers, so it waits for the phase-0 re-measurement that is already owed.
* **The semi-join seam** (P6, with B2's P5 and P6) is unchanged by decision.
