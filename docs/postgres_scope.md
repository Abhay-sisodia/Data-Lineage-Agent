# PostgreSQL — what is in scope, and what is refused

**Measured, not asserted.** Every row below comes from `scripts/probe_postgres.py`, which
runs 43 production-shaped constructs through `analyse_source` and buckets each one. Re-run it
after any change:

```
.\.venv\Scripts\python.exe scripts\probe_postgres.py
```

It exits non-zero if anything is SILENT or CRASHes, so a regression cannot pass unnoticed.

**Current result: 43 / 43 agree with expectation. 0 silent, 0 crashes.**

| bucket | count | meaning |
|---|---|---|
| EDGES | 34 | in scope, lineage produced |
| NO-SOURCE | 2 | in scope, counted, and correctly has nothing to trace |
| REFUSED | 6 | out of scope, refused by name with a stated reason |
| DECLARED | 1 | out of scope, a boundary names what stopped it |
| SILENT | **0** | never acceptable, in scope or out |
| CRASH | **0** | |

---

## The contract

1. **Inside the scope, nothing fails and nothing is refused.** Every construct listed as in
   scope produces lineage, or is counted and correctly found to have no upstream.
2. **Outside the scope, everything is refused or declared — never silent.** A refusal is a
   deliverable: it is counted against parse coverage and carries a reason.
3. **The boundary is known in advance.** This file is that boundary, and the probe is what
   keeps it true.

Silence is the only outcome that is never acceptable. A statement that is skipped before
being counted is invisible in parse coverage, so a table can acquire its entire contents and
be reported as having no writer — which reads as a finding rather than as a gap.

## In scope

**Core DML** — `INSERT … SELECT`, `INSERT … VALUES`, `INSERT` with a leading CTE,
`UPDATE … SET`, `UPDATE … FROM` (the PostgreSQL join-update), `DELETE … WHERE`,
`DELETE … USING`, `INSERT … ON CONFLICT DO UPDATE` (upsert), `ON CONFLICT DO NOTHING`,
`INSERT … RETURNING`, `MERGE` (PostgreSQL 15+), `CREATE TABLE AS SELECT`.

**Query shape** — CTEs chained to any depth, `WITH RECURSIVE`, `LATERAL`, `FULL OUTER JOIN`,
`UNION ALL` / `EXCEPT`, scalar subqueries in the select list, `EXISTS` / `NOT EXISTS` / `IN`,
schema-qualified names, `VALUES` used as a table source, `LIMIT` / `OFFSET`.

**Aggregation and windows** — `GROUP BY`, `HAVING`, `ROLLUP` / `GROUPING SETS`, window
functions with `ROWS`/`RANGE` frames, `LAG` / `LEAD`, `DISTINCT ON`, aggregate
`FILTER (WHERE …)`, `string_agg` with an inner `ORDER BY`.

**Expressions and types** — `CASE`, `COALESCE`, `::` casts, `date_trunc`, `EXTRACT`,
`INTERVAL`, JSON `->` and `->>`, `jsonb_build_object`, `unnest` / arrays, `generate_series`.

## Out of scope, and refused by name

| construct | code | why |
|---|---|---|
| PL/pgSQL routine body (`AS $$ … $$`) | `UNSUPPORTED_CONSTRUCT` | the body is a dollar-quoted string literal, not part of the SQL parse tree; no grammar chosen yet (ADR-0002 §5) |
| `DO $$ … $$` anonymous block | `UNSUPPORTED_CONSTRUCT` | same body, no name |
| `CREATE TRIGGER` | `UNSUPPORTED_CONSTRUCT` | a PostgreSQL trigger names a FUNCTION that holds the body — so the lineage is in a routine we cannot parse. **Oracle triggers are handled and deliberately not refused**, which is why this refusal is scoped to one dialect |
| `COPY … FROM/TO` | `SOURCE_UNAVAILABLE` | moves rows between a relation and something outside the database; the contents depend on data no static analysis can see |
| `TRUNCATE` | `UNSUPPORTED_CONSTRUCT` | empties a relation unconditionally — no column has a source to name, but the statement decides what the table contains afterwards, so it is recorded rather than skipped |
| data-modifying CTE (`WITH x AS (DELETE … RETURNING) …`) | declared `dangling_reference` | the CTE's rows come from a statement, not a relation |

## What this scope does NOT cover, by construction

**Bands 1 and 2.** A PL/pgSQL body is refused, so there is no variable def-use, no cursor
analysis and no trigger inheritance for PostgreSQL. Everything above is band 0. This is the
cost recorded in ADR-0002 §5 while the grammar decision is parked, and it is the single
largest thing a second phase would buy.

**Identifier case.** PostgreSQL folds unquoted identifiers to lower case and the analyser
follows (`dialects.fold`). A *quoted* mixed-case identifier — `"MyTable"` — is a different
object from `mytable` in PostgreSQL, and the analyser does not yet distinguish them. Oracle
has the same limitation with quoted identifiers, recorded as GL-002.

**Unit names.** A script has no named units, so every edge's origin is `<script>`. Nothing
depends on it, but it means origin cannot attribute a fact to a routine the way it does for
Oracle.
