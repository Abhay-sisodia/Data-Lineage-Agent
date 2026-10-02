# PostgreSQL — what is in scope, and what is refused

**Measured, not asserted.** Every row below comes from `scripts/probe_postgres.py`, which
runs 44 production-shaped constructs through `analyse_source` and buckets each one. Re-run it
after any change:

```
.\.venv\Scripts\python.exe scripts\probe_postgres.py
```

It exits non-zero if anything is SILENT or CRASHes, so a regression cannot pass unnoticed.

**Current result: 45 / 45 agree with expectation. 0 silent, 0 crashes.**

| bucket | count | meaning |
|---|---|---|
| EDGES | 36 | in scope, lineage produced |
| NO-SOURCE | 3 | in scope, counted, and correctly has nothing to trace |
| REFUSED | 6 | out of scope, refused by name with a stated reason |
| DECLARED | 0 | out of scope, a boundary names what stopped it |
| SILENT | **0** | never acceptable, in scope or out |
| CRASH | **0** | |

---

## The contract

**It has two levels, and only the first is measured by the probe.** That distinction was not
here when this file was written, and adding it is finding PG-06: the probe asks whether a
STATEMENT produced edges, and three constructs answer yes while losing a COLUMN. See
"Partial: in scope, and incomplete" below — that section is the honest part of this page.

| level | question | measured by | status |
|---|---|---|---|
| statement | did anything fall through unowned? | `probe_postgres.py` | **holds** — 44/44, 0 silent |
| column | did every column with an upstream get an edge? | a ground-truth key | **three constructs partial** |

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

**A query with no target at all** — a standalone `SELECT`, which is the shape of every dbt
and Databricks model, of a view body, and of most BI SQL. Its own projections are the
target: edges land on `<result>.<column>` by default, or on a relation you name
(`analyse_source(..., result_target=...)`) so that a directory of model files chains
together. **Silent until 2026-09-24**, then briefly refused while the target was an open
model decision (PG-09).

**Core DML** — `INSERT … SELECT`, `INSERT … VALUES`, `INSERT` with a leading CTE,
`UPDATE … SET`, `UPDATE … FROM` (the PostgreSQL join-update), `DELETE … WHERE`,
`DELETE … USING`, `INSERT … ON CONFLICT DO UPDATE` (upsert), `ON CONFLICT DO NOTHING`,
`INSERT … RETURNING`, `MERGE` (PostgreSQL 15+), `CREATE TABLE AS SELECT`, data-modifying
CTEs (`WITH x AS (DELETE … RETURNING) …`, since B3: D-17). A `DELETE … USING` whose WHERE
is only the target-to-USING join is counted and empty: that equality is the join (D-11).

**Query shape** — CTEs chained to any depth, `WITH RECURSIVE`, `LATERAL`†, `FULL OUTER JOIN`,
`UNION ALL` / `EXCEPT`, scalar subqueries in the select list, `EXISTS` / `NOT EXISTS` / `IN`,
schema-qualified names, `VALUES` used as a table source, `LIMIT` / `OFFSET`.

**Aggregation and windows** — `GROUP BY`, `HAVING`, `ROLLUP` / `GROUPING SETS`, window
functions with `ROWS`/`RANGE` frames, `LAG` / `LEAD`, `DISTINCT ON`, aggregate
`FILTER (WHERE …)`, `string_agg` with an inner `ORDER BY`, and a named `WINDOW w AS (…)`
clause shared by several functions (added under PG-14, where it was silently losing every
`PARTITION BY` and `ORDER BY`).

**Expressions and types** — `CASE`, `COALESCE`, `::` casts, `date_trunc`, `EXTRACT`,
`INTERVAL`, JSON `->` and `->>`, `jsonb_build_object`†, `unnest` / arrays†,
`generate_series`†.

† and `LATERAL` above: **partial**. The statement is analysed and a column inside it is
lost. See "Partial: in scope, and incomplete" below — do not read these three as covered.

## Partial: in scope, and incomplete

**Measured 2026-09-23 by `stress/stress_pg_02_deep_nesting.sql`, whose key is the first
thing in this project able to see these.** All three are in the probe's EDGES bucket and
were listed above without qualification until that run. The statement is analysed; a column
inside it is not.

| construct | what is produced | what is lost | finding |
|---|---|---|---|
| `LATERAL` subquery | the correlation's filter edges, correctly | every column PROJECTED through the LATERAL alias — declared `unresolved_identifier` | PG-03 |
| `jsonb_build_object` | nothing from it | its arguments — declared `source_unavailable`, so the refusal is honest and the classification is wrong | PG-04 |
| ~~`unnest(...)` in `FROM`~~ | **fixed 2026-10-01** — the element traces to its array, by position, as `SELECT unnest(...)` always did | an alias list that does not map one-to-one onto the arrays, and a bare `AS t` with no column list — both declared `unresolved_identifier` | PG-05 |

**PG-05 was the one to know about**, and is fixed. The other two announce themselves;
`unnest` did not, so the target column read as having no upstream — a finding about the data
rather than a gap in the analysis. It was the only known column-level silence in the
PostgreSQL path, and no other is known now. That is a statement about what has been
measured: the probe asks per statement and cannot see a per-column silence (PG-06).

## Out of scope, and refused by name

| construct | code | why |
|---|---|---|
| PL/pgSQL routine body (`AS $$ … $$`) | `UNSUPPORTED_CONSTRUCT` | the body is a dollar-quoted string literal, not part of the SQL parse tree; no grammar chosen yet (ADR-0002 §5) |
| `DO $$ … $$` anonymous block | `UNSUPPORTED_CONSTRUCT` | same body, no name |
| `CREATE TRIGGER` | `UNSUPPORTED_CONSTRUCT` | a PostgreSQL trigger names a FUNCTION that holds the body — so the lineage is in a routine we cannot parse. **Oracle triggers are handled and deliberately not refused**, which is why this refusal is scoped to one dialect |
| `COPY … FROM/TO` | `SOURCE_UNAVAILABLE` | moves rows between a relation and something outside the database; the contents depend on data no static analysis can see |
| `TRUNCATE` | `UNSUPPORTED_CONSTRUCT` | empties a relation unconditionally — no column has a source to name, but the statement decides what the table contains afterwards, so it is recorded rather than skipped |

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
