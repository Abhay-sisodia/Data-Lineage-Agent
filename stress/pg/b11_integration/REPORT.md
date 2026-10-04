# Batch B11 — Integration: report

**Run 2026-10-04**, against the analyser at commit `460996f` (B8 complete), unchanged.
Every key was written before the analyser first saw its file, and decisions D-43 and D-44
were taken before any key. Sections 1–5 are the blind measurement, unchanged. **The fixes,
agreed after review, are in section 6.**

```
.\.venv\Scripts\python.exe stress/pg/triage.py b11_integration
```

## 1. Headline

**Strict: 189 of 191 statement-level facts found, 0 false positives, 0 forbidden edges.**

Every expected boundary is declared. All six metamorphic pairs give identical lineage, with
both halves non-empty. The one refusal is T4's `\copy`, made on purpose, and it stays local.

The parts hold together. Facts survived every combination the pipelines put them in:
- views inlined inside CTEs inside a CTAS;
- a CTAS table read as a node and grouped;
- a ranking filter over a SUM through a view;
- a materialized view and its REFRESH;
- a temp table feeding an UPDATE … FROM and a lookup subquery with ORDER BY … LIMIT;
- a MERGE and a grouped loop inside a procedure, with a parameter;
- a partition loaded from jsonb extractions, and its parent read back;
- unnest WITH ORDINALITY in a LATERAL;
- a trigger on a fact;
- quoted mixed-case names through a session path, PREPARE / EXECUTE, `\i` and `\copy`;
- constant dynamic SQL;
- a watermark variable in an incremental load.

| Pipeline | Statements seen | Strict, per statement |
|---|---|---|
| T1 Models | 8 | 66 / 66 |
| T2 SCD2 | 7 | 28 / 28 |
| T3 Semi-structured | 9 | 28 / 30 |
| T4 Legacy deploy | 10 (1 refused, `\copy`) | 22 / 22 |
| T5 Twice | 19 | 45 / 45 |
| **B11 total** | **53** | **189 / 191 (99%)** |

## 2. Findings

| ID | What | Where | Grade |
|---|---|---|---|
| **B11-01** | **`jsonb_to_recordset(…) AS x(col type, …)` under `CROSS JOIN LATERAL` loses its columns.** Each is declared `unresolved_identifier` ("no matching projection in subquery"). The same call in a comma join works (B5 T1 S11's shape, re-checked on this schema), and so does `unnest` under CROSS JOIN LATERAL (T3 S3). So the defect is the interaction of the explicit LATERAL join with a column-definition list. | T3 S5: `shipments.tracking → carrier_id, weight_kg` | R3, declared |
| **B11-02** | **`nextval('seq')` is declared `source_unavailable`**, as "a call that could not be summarised". It is a built-in that reads no column, missing from the built-ins list. No edge is wrong, but the declaration is false. | T2 S3 | R4, a false declaration |

### What else the run declared, honestly

- **`fusion_hazard` on `core.dim_customer` (T2) and `core.fct_shipment` (T3).** A table is
  written by one unit and read by another: the script and `mart.pr_publish_customers`; the
  script and the trigger function. The scratch-table rule declares that, as it does for
  Oracle units. Not a fact the keys state.

## 3. Recommended fix order (for agreement, not yet started)

| # | Item | Why |
|---|---|---|
| 1 | B11-01 | make an explicit `CROSS JOIN LATERAL` row source with a column-definition list bind its columns as the comma-join form already does: one code path, not two |
| 2 | B11-02 | add `nextval` (and its siblings `currval`, `setval`, `lastval`) to the PostgreSQL built-ins as no-source calls |

## 4. Coverage (programme plan §1, row B11)

Every scope B1–B10 appears in at least one pipeline:

| Scope | Where |
|---|---|
| B1 core SELECT | T1 S4, S5; T3 S6; T5 R2 |
| B2 subqueries, CTEs | T1 S3, S4; T2 S1, S3; T5 P1 |
| B3 DML | T2 S2–S4; T5 P3, R2 |
| B4 expressions | T1 S1–S3; T2 S3; T5 R1 |
| B5 semi-structured | T3 S2, S5, S7; T5 P4, P5 |
| B6 SRFs | T3 S3, S5 |
| B7 DDL | T1 S1–S3, S6, S7; T2 S1; T3 S1; T5 S0, P6 |
| B8 procedural | T2 S4, S5; T3 S4; T4 S7; T5 P2, R1–R3 |
| B9 names | T4 S1–S5 |
| B10 lexical | T4 throughout; T5 P3 |

**Avoided by D-43**, and still open:
- semi-joins (B2 P5 / P6);
- non-equality join conditions (B1-Q2);
- a single-relation predicate in an inner join's ON;
- the loop-UPDATE WHERE (B8);
- GROUP BY influence onto a projected ranking over an aggregate.

## 5. Corrections while designing, stated

- **One fact was re-keyed before any run.** T2 S1's `dim_customer.valid_from` reaches its
  comparison through `MAX`. Checking B2 T1's `AVG` precedent showed that is
  post-aggregation (D-6, per column), so the key states it so.
- **Projected rankings over an aggregate were removed from T1 S4 and S5 while keying.**
  Whether GROUP BY influence reaches a ranking computed over an aggregate is not decided.
  It is listed under D-43.

## 6. Fixes applied, 2026-10-04 — in the agreed order

| # | Item | Change | Where |
|---|---|---|---|
| 1 | B11-01 | a `Lateral` wrapping a function call is restated exactly as the comma-join `Table` form, through the same `_table_calls`. LATERAL changes nothing for a function in FROM: PostgreSQL lets it read the items before it either way. Before, only a LATERAL `unnest` was read, so the column-definition list of `jsonb_to_recordset` (and of any JSON / hstore SRF) went unbound | `srf._restated` |
| 2 | B11-02 | `nextval`, `currval`, `setval` and `lastval` are PostgreSQL built-ins: their argument names a sequence in a literal, so they read no column and declare nothing | `builtins.SCALAR` |

### Result

**B11: 191 of 191 per statement, 0 false positives, 0 forbidden edges, every expected
boundary declared, all six pairs equal.** The one refusal is the intended `\copy`. Every
band is pinned in `tests/test_pg_batches.py`, with the `\copy` refusal listed as on purpose.

The regression tests are in `tests/test_b11_integration.py`. Its two fix tests fail on the
unfixed source. The third, the comma-join control, passes on both, as it must.

B5 and B6, whose SRF code item 1 touches, still score 100% per statement.

**Oracle:** unchanged. Against `460996f`, the Oracle stress edges and `run_stress.py`
scores are identical.
