# Final suite, easy tier (E1–E5): report

**Run 2026-10-07**, against the analyser at commit `f46f8e0`, unchanged. The SQL and the
keys were written before the analyser first saw these files.

```
.\.venv\Scripts\python.exe stress/pg/final/run.py --tier easy
```

Scoring is **closed world**: every emitted edge (value, influence, filter) must be in the
key, and every boundary the analyser declares must be expected, both ways. A test passes
only with FP 0, FN 0, no forbidden edge, and boundaries equal.

## 1. The score

| Test | Lines | Facts | TP | FP | FN | Boundaries | Result |
|---|---|---|---|---|---|---|---|
| E1 latest version per key | 106 | 39 | 39 | 0 | 0 | 0 = 0 | **PASS** |
| E2 supplier spend aggregation | 78 | 58 | 57 | 1 | 1 | 0 = 0 | FAIL (FE-01) |
| E3 shipment joins | 85 | 37 | 37 | 0 | 0 | 0 = 0 | **PASS** |
| E4 item and shipment expressions | 85 | 43 | 43 | 0 | 0 | 1 = 1 | **PASS** |
| E5 entity activity windows | 88 | 53 | 53 | 0 | 0 | 0 = 0 | **PASS** |
| **Total** | 442 | **230** | **229** | **1** | **1** | | **4 of 5** |

There were no refusals. One boundary was declared and expected: E4's
`source_unavailable NORMALISE_UOM` (D-53). No forbidden edge was produced (13 forbids).
Statements seen: 5 of 5.

The one false positive and the one miss are a single fact, `po_header.created_at → <result>`,
reported with the wrong phase. It is one finding, not two.

## 2. Findings

| ID | Finding | Where | Grade |
|---|---|---|---|
| **FE-01** | **A sort key's filter phase is decided per clause, not per column.** `ORDER BY vy.spend_year DESC, vy.total_spend DESC LIMIT 100` over an aggregated CTE reports *both* sort keys as post-aggregation. `spend_year` is the grouping expression `EXTRACT(YEAR FROM created_at)`, and it does not reach the LIMIT through an aggregate, so D-6 (decided per column, PG-10) makes it pre-aggregation. Isolated, the analyser agrees: `ORDER BY vy.spend_year LIMIT 10` gives pre. It flips only when an aggregate sort key sits beside it, in either order. A WHERE with the same two conjuncts is handled per column, correctly. The edge itself is right; only its phase is wrong. | E2, final ORDER BY … LIMIT | R4 (phase misclassification, 1 fact) |

**A convention question sits next to FE-01, and it is yours (R5).** D-6 says pre-aggregation
here, because the sort key does not reach the LIMIT through an aggregate. But a LIMIT over an
aggregated query removes *groups*, like HAVING, and could be argued post-aggregation for every
key. If you prefer that reading, the analyser's single-key behaviour (pre) becomes the bug
instead.

### Key errors (mine)

| ID | What | Effect |
|---|---|---|
| KE-01 | E2 `currencies = string_agg(DISTINCT currency_code, ',' ORDER BY currency_code)`: the blind key stated the value but missed the **influence** of the aggregate's own ORDER BY column. B1 S11 established that convention, and D-47 lets the same column be both. | One false positive in the first run. The key was corrected and marked `KE-01, ADDED AFTER THE FIRST RUN`. |

## 3. What held

Every construct on the five objective cards produced exactly the key's facts:

- **Dedup shapes (E1).** These produced filters on the row-deciding spine (D-9):
  - `ROW_NUMBER() = 1` over `SELECT x.*`;
  - the same dedup through a named window;
  - DISTINCT ON with two tie-breakers beside a WHERE.

  On the null side, a `ROW_NUMBER` dedup reached through a LEFT JOIN gave **influence** on
  exactly the columns that side feeds. That is the reading this key flagged as a convention
  reading (D-13/D-45, as torture_names read an ordinal cut); the analyser agrees. A window
  `ORDER BY 1` was correctly read as a constant.
- **Aggregation (E2):**
  - GROUP BY on an expression;
  - FILTER (WHERE) and SUM(CASE), both handled under D-10;
  - percentile_cont, with no influence from its ORDER BY, which is the value itself;
  - HAVING as post-aggregation;
  - a ROLLUP whose `GROUPING()` predicate carries nothing (D-50), read through a scalar
    subquery that still brings its grouping influence;
  - RANK over an aggregate, with the GROUP BY behind it (D-46).
- **Joins (E3):**
  - USING and NATURAL, structural;
  - an INNER join's one-relation ON condition, a filter (D-48);
  - LEFT-join ON conditions, conditional values (D-48);
  - a LEFT-join range ON, values with both operands (D-49);
  - a CTE's WHERE reached only through a LEFT join, values (D-45);
  - a RIGHT join's null-side condition, which reaches only the null side's column;
  - a FULL join of two aggregates;
  - a WHERE non-equality, filters with both operands (D-49);
  - IN and NOT EXISTS, filters with both match columns (D-52).
- **Expressions (E4).** Every D-19 count was right, one column against two:
  - COALESCE/NULLIF;
  - CASE, both searched and simple;
  - GREATEST/LEAST.

  jsonb `->>`/`#>>`/`?`/`@>`, the arrays, casts and date functions, and md5 over
  concatenation were all derived. The unknown UDF was claimed as derived and declared (D-53).
- **Windows (E5):**
  - an inherited named window;
  - ROWS and RANGE INTERVAL frames;
  - LAG/LEAD/FIRST_VALUE as derived;
  - ranking functions as influence only;
  - NTILE ordered by `COUNT(*)`, influenced only by the GROUP BY behind it (D-46);
  - influence carried through `amount - COALESCE(prev_amount, 0)`;
  - the ranking cut `activity_seq <= 10` as filters (D-9).

## 4. Coverage check

Every bullet on the five objective cards was exercised. None was deferred.

None of the Step 0 findings came up: the easy tests use no construct from LIMITATIONS.md
§1–2, and none of the F0-03 built-ins.

## 5. Next

Per the plan, nothing is fixed now. FE-01 joins the final findings list, with F0-01..F0-08.
Next is the medium tier (M1–M5, about 300 lines each, 8–15 CTEs).
