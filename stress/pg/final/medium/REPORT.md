# Final suite, medium tier (M1–M5): report

**Run 2026-10-07**, against the analyser at commit `f46f8e0`, unchanged. The SQL and the
keys were written before the analyser first saw these files. Scoring is closed world, as in
the easy tier.

```
.\.venv\Scripts\python.exe stress/pg/final/run.py --tier medium
```

## 1. The score

| Test | Lines | CTEs | Facts | TP | FP | FN | Result |
|---|---|---|---|---|---|---|---|
| M1 procurement savings chain | 256 | 13 | 114 | 114 | 0 | 0 | **PASS** |
| M2 shipment exception board | 170 | 8 + 2 laterals | 117 | 117 | 0 | 0 | **PASS** |
| M3 hierarchies, islands, sessions | 166 | 11 (3 recursive) | 46 | 44 | 0 | 2 | FAIL (FM-Q1, FM-Q2) |
| M4 semi-structured documents | 176 | 15 | 47 | 46 | 0 | 1 | FAIL (FM-01) |
| M5 names under load | 137 | 14 | 42 | 42 | 0 | 0 | **PASS** |
| **Total** | 905 | | **366** | **363** | **0** | **3** | **3 of 5** |

- **No false positive** anywhere. No refusal, no boundary, and none of the 19 forbidden
  edges was produced.
- Statements seen: 5 of 5. No key error this tier.
- **Size against the brief.** The files run 137–256 lines, not ~300. Each was grown until its
  logic composed, not padded with lines. Fact density is about twice the easy tier's.

All three misses are on readings this tier's keys **flagged before the run**. One is an
analyser defect, and two are conventions for you to decide.

## 2. Findings

| ID | Finding | Where | Grade |
|---|---|---|---|
| **FM-01** | **An ordinality column used as an ORDERING is dropped, on the row-deciding path and on the null side alike.** `WITH ORDINALITY` gives the ordinal no value, only influence from its array or document (D-26). When that ordinal orders a DISTINCT ON or a LIMIT, the influence is not followed. Isolated (stress/pg/final medium probes, section 4): `DISTINCT ON (h.shipment_id) ... ORDER BY h.shipment_id, c.n` on the main path gives the filter from `shipment_id` only, and `header_attrs` is lost. `ORDER BY c.n LIMIT 5` over `jsonb_array_elements`, or `ORDER BY u.n LIMIT 5` over `unnest(tags)`, gives **no filter at all**. On the null side (M4's `ship_to_contact`) the DISTINCT ON's ordinal influence is lost. The same ordinal in a WHERE cut (`WHERE c.n = 1`) is handled right in both positions, and so is a ROW_NUMBER used as the ordering, or an ordinal inside a scalar subquery's ORDER BY … LIMIT. Nothing is declared. | M4 `ship_to_email`; probes | **R1** silent loss |
| **FM-Q1** | **CYCLE's own guard is not a filter.** PostgreSQL's `CYCLE org_id SET is_cycle USING path` stops the recursive arm from extending a row that closed a cycle. That is the same guard as M3's explicit `WHERE NOT ch.customer_id = ANY (r.chain)`, which the analyser *does* report as a filter on `customer_id`. `pg_grammar`'s restatement adds the mark and path columns, correctly derived from `org_id`, but not the guard, so `organization.org_id` is not a filter. **Decision for you:** is CYCLE's implicit guard a filter on the cycle column (the key's reading)? If yes, this is an R1 silent loss in `pg_grammar._restate_search_cycle`. | M3 `org_tree` | R5, then R1 |
| **FM-Q2** | **COUNT(\*) over an already-grouped CTE does not inherit the inner GROUP BY.** `island_count = COUNT(*)` over `islands`, whose `GROUP BY customer_id, grp_key` (with `grp_key` derived from `ship_date`) decides how many island rows exist. The analyser gives influence from the outer GROUP BY (`customer_id`) only. `longest_island = MAX(island_days)`, over the same CTE, does carry `ship_date`, because `island_days` carries it. **Decision for you:** does an inner GROUP BY influence a COUNT(\*) of its groups (the key's reading, D-2's "the grouping decides the aggregate")? | M3 `island_stats` | R5 |

## 3. What held (selected)

- **M1, the PO savings chain:**
  - CASE over CASE over COALESCE, five CTE levels deep, with every source at the hardest
    transform across all paths (D-51);
  - COALESCE over two instances of *one* base column, read as conditional (D-19);
  - the same spine CTE (`poh`, `pol`) re-joined three times on the null side, adding nothing
    there (D-45's exception);
  - null-side `pole` dedups read twice, giving influence;
  - D-48 on `BLANKET`, and the LEFT-joined requisition CTE's WHERE as values;
  - a dimension's literal ON test.
- **M2, every subquery position:**
  - lookup and LIMIT-1 scalar subqueries (D-12/D-13);
  - an IN nested inside a counting subquery (aggregated, never a filter);
  - EXISTS in the SELECT list;
  - two one-row LATERALs (D-14), with `event_type` both projected and tested, one fact at the
    hardest (D-51);
  - LEFT-joined aggregates whose own WHERE reads a main-path CTE;
  - EXISTS and NOT IN … EXCEPT under a LEFT join, as values;
  - IN over UNION, EXISTS over INTERSECT, `NOT (= ANY)` and `>= SOME` in WHERE, as filters
    (D-52);
  - RANK over SUM with D-46;
  - the shadowed alias `cr` resolved correctly.
- **M3:**
  - three recursive CTEs, including SEARCH and CYCLE after restatement;
  - counters with no source;
  - a root carried down the recursion;
  - a recursive BOM explosion swept as aggregated values;
  - the explicit cycle guard as a filter;
  - gap-and-island and sessionisation influence carried through nested windows and two
    aggregations.
- **M4:**
  - jsonb operators and SQL/JSON JSON_VALUE (restated);
  - jsonb_to_recordset, JSON_TABLE with FOR ORDINALITY and EXISTS PATH;
  - jsonb_each and multi-array unnest WITH ORDINALITY ordering an aggregate;
  - generate_series between columns, filtered;
  - ROWS FROM over literals giving nothing;
  - XMLTABLE with xpath().
- **M5:**
  - a quoted keyword CTE name and quoted mixed-case, keyword and spaced aliases;
  - case folding (`Lines`, `LN`, `L4`, `SHIPPED_QTY`);
  - `x.*` through four levels;
  - a CTE named like a table, shadowed again by a nested WITH, with no leak;
  - USING with an unqualified merged column;
  - an unqualified column only one relation has;
  - a bare table name as qualifier;
  - system columns `ctid` and `xmin` giving nothing (TT-1);
  - nested scalar subqueries reusing one alias;
  - a UNION ALL renaming two columns to one;
  - a CTE named `status` with a column `status`.

## 4. Probes behind FM-01 (run after the tier, on the final suite's dictionary)

```
DISTINCT ON (h.shipment_id) ... ORDER BY h.shipment_id, c.n      main path   filter: shipment_id only   (header_attrs lost)
WHERE c.n = 1                                                     main path   filter: header_attrs       (right)
LEFT JOIN (... DISTINCT ON ... ORDER BY ..., c.n)                 null side   influence: shipment_id only (header_attrs lost)
LEFT JOIN (... WHERE c.n = 1)                                     null side   influence: header_attrs    (right)
(SELECT ... ORDER BY c.n LIMIT 1) in the SELECT list              value-deciding  influence: header_attrs (right)
ORDER BY c.n LIMIT 5   over jsonb_array_elements                  main path   NO filter                  (lost)
ORDER BY u.n LIMIT 5   over unnest(tags)                          main path   NO filter                  (lost)
DISTINCT ON ordered by a ROW_NUMBER                               null side   influence carried          (right)
```

## 5. Cumulative (easy + medium)

10 tests, 596 facts: 592 found, 1 false positive (FE-01's phase), 4 misses. 7 of 10 pass.

**Findings so far:**
- F0-01..F0-08 (Step 0 probes);
- FE-01 (phase, R4);
- FM-01 (R1).

**Open convention questions:**
- FE-01's alternative reading;
- FM-Q1 (CYCLE guard);
- FM-Q2 (COUNT over grouped CTE).

Nothing is fixed. Next: the hard tier, H1–H5, 500+ lines each, including the write
statements.
