# Final suite, hard tier (H1–H5): report

**Run 2026-10-07**, against the analyser at commit `f46f8e0`, unchanged. All five SQL files
and all five keys were written before the analyser first saw any of them. Scoring is closed
world. H5 also runs 20 metamorphic groups, whose spellings must receive identical lineage.

```
.\.venv\Scripts\python.exe stress/pg/final/run.py --tier hard
```

## 1. The score

| Test | Lines | Statements | Facts | TP | FP | FN | Result |
|---|---|---|---|---|---|---|---|
| H1 shipment margin fact (bare SELECT, 30 CTEs) | 473 | 1 | 202 | 194 | 0 | 8 | FAIL (FH-01, FH-04) |
| H2 PO fact load (upsert, CTAS, INSERT, UPDATE, DELETE, MERGE, SELECT, INSERT) | 482 | 8 | 156 | 154 | 0 | 2 | FAIL (FH-03) |
| H3 entity pipeline at depth (60 stages; view, matview, INSERT, REFRESH) | 549 | 4 | 74 | 74 | 0 | 0 | **PASS** |
| H4 nightly load script (temp tables, writable CTE, 2 MERGEs, SCD2, TRUNCATE) | 392 | 18 + 4 control | 160 | 159 | 0 | 1 | FAIL (FH-02) |
| H5 adversarial + 20 metamorphic groups | 393 | 46 | 181 | 181 | 0 | 0 | **PASS** |
| **Total** | 2,289 | 77 | **773** | **762** | **0** | **11** | **2 of 5** |

- **No false positive, and no forbidden edge** (30 forbids).
- **Refusals:** exactly one, H4's TRUNCATE at line 196, as expected. It is followed by 11 more
  statements, all analysed.
- **Metamorphic:** all 20 groups identical, among them IN / ANY / EXISTS, NOT IN / `<> ALL` /
  NOT EXISTS, DISTINCT ON / ROW_NUMBER, CTE / derived table, and USING / ON.
- **Traps:** every adversarial trap held:
  - SQL inside string, escape-string and dollar-quoted literals, and inside comments with `;`
    and `$$`;
  - CTEs named like base tables;
  - ORDER BY resolving to an output alias;
  - GROUP BY resolving to an input column.
- Statements seen: 77 of 77.
- **No key error this tier.**
- **Size:** H3 is over 500 lines. H1 and H2 are about 480, and H4 and H5 about 390, because
  they spread their logic over many statements rather than one.

## 2. Findings: four, all silent losses, all isolated

| ID | Finding | Isolated as | Where | Grade |
|---|---|---|---|---|
| **FH-01** | **Under `WITH RECURSIVE`, a CTE named like the base table it reads loses all its lineage.** `item_master AS (SELECT ... FROM ONLY lgx.item_master AS im)` in a `WITH RECURSIVE` list is evidently taken for a recursive self-reference. A schema-qualified name can never refer to a CTE. Every column read through the CTE vanishes, and nothing is declared. Without RECURSIVE, or with another CTE name, the same query is right. | `WITH RECURSIVE item_master AS (SELECT ... FROM lgx.item_master)` then `LEFT JOIN item_master` gives no `item_code` edge, with and without ONLY | H1: 7 facts (item_code ×3, std_cost ×3, the NOT EXISTS filter on item_id) | **R1** |
| **FH-02** | **`MERGE ... WHEN NOT MATCHED THEN INSERT VALUES (...)` with no column list produces nothing.** PostgreSQL maps the VALUES to the target's columns by position. The same MERGE with a column list, and `INSERT INTO t SELECT ...` with none, are both right. | the bare MERGE gives 0 edges, 0 boundaries | H4 S13: `entity_id` (the only column the UPDATE arm does not also set) | **R1** |
| **FH-03** | **A ranking cut in an INNER JOIN's ON is not a filter.** `JOIN r ON r.key = d.key AND r.rn = 1`, where `rn = ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)`, gives no filter from the partition or the ordering, in a SELECT and in an UPDATE ... FROM alike. Moved into WHERE, the same cut gives both filters (D-9), and D-48 says a non-key ON condition of an inner join filters as WHERE does. | SELECT / UPDATE probes, ON against WHERE | H2 S4: 2 filters | **R1** |
| **FH-04** | **GROUP BY influence is dropped for the arms of a set operation that is not the top-level query.** A UNION ALL of grouped arms keeps each arm's GROUP BY influence at the top level, or as an INSERT's source. Inside a derived table or a CTE it keeps the values and drops every arm's influence. | `SELECT ... FROM (grouped UNION ALL grouped) u` gives no influence; the same union at the top gives it | H1 `cust_net_margin`: hdr_charges' `shipment_id` (customer_id survived only through a window partition) | **R1** |

## 3. What held (selected)

- **H3, depth:**
  - `p.*` carried through 60 stage CTEs;
  - a column added at stage 1 read at the end;
  - 15 repetitions of each stage shape collapsing to one fact each;
  - the view inlined into the matview and the INSERT;
  - the matview read as a node;
  - REFRESH re-deriving exactly the matview's facts.
- **H2, the write mechanics:**
  - EXCLUDED, the GREATEST self-edge, the DO UPDATE WHERE (target and EXCLUDED sources);
  - CTAS read back as a node, twice;
  - UPDATE ... FROM self-edges;
  - DELETE ... USING;
  - a MERGE with three arms;
  - ON CONFLICT DO NOTHING;
  - one target column reached with derived and conditional facts at once (D-7).
- **H4:**
  - temp tables shadowing by bare name (D-31), and the writable CTE (D-16, D-17);
  - two MERGEs, one MATCHED AND condition on an aggregate as post-aggregation (D-6);
  - TRUNCATE refused, with every later statement still analysed;
  - SCD2 close and open: an aggregated self-read plus ROW_NUMBER influence;
  - BEGIN, SET LOCAL, ANALYZE and COMMIT giving nothing.
- **H1:**
  - D-45's exception (site_geo's WHERE is a filter through the NOT IN, and adds no value
    through its LEFT join);
  - a ranking cut on a LEFT-joined aggregate, read as influence;
  - a VALUES grid joined on a range on a null side;
  - literal relations giving nothing;
  - nested WITH;
  - five levels of derived tables;
  - three chained named windows;
  - WITH TIES.

## 4. Probes behind the findings (run after the tier)

```
FH-01  WITH RECURSIVE item_master AS (... FROM ONLY lgx.item_master)  -> LEFT JOIN item_master   item_code lost
       WITH           item_master AS (... FROM ONLY lgx.item_master)  -> same                     right
       WITH RECURSIVE items       AS (... FROM ONLY lgx.item_master)  -> same                     right
FH-02  MERGE ... WHEN NOT MATCHED THEN INSERT VALUES (s.a, s.b)        0 edges
       MERGE ... WHEN NOT MATCHED THEN INSERT (a, b) VALUES (s.a, s.b)  right
       INSERT INTO t SELECT a, b ...                                    right
FH-03  JOIN r ON r.k = d.k AND r.rn = 1                                 no filter   (SELECT and UPDATE ... FROM)
       JOIN r ON r.k = d.k WHERE r.rn = 1                               both filters
FH-04  SELECT ... FROM (grouped arm UNION ALL grouped arm) AS u        no influence
       WITH u AS (grouped arm UNION ALL grouped arm) SELECT ... FROM u  no influence
       grouped arm UNION ALL grouped arm   (top level, or INSERT source) influence kept
```
