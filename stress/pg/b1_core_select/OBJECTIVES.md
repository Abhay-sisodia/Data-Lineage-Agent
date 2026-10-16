# Batch B1 — Core SELECT: objective cards

**Status:** cards drafted, reviewed and built 2026-10-01. Results: `REPORT.md`. Two rows were corrected while the keys were written, and say so in place.

**Scope (programme plan §1, row B1):** projections and expressions; WHERE; every join type;
GROUP BY with ROLLUP, CUBE and GROUPING SETS; HAVING; aggregates including FILTER and
WITHIN GROUP; window functions (named windows, frames); DISTINCT and DISTINCT ON; ORDER BY,
LIMIT, OFFSET, FETCH.

**Out of scope here, deliberately:** subqueries and CTEs as a *subject* (B2), DML other
than `INSERT … SELECT` (B3), json and arrays (B4/B5), name-resolution traps beyond what T4
needs (B9). T2 uses derived tables and CTEs only as *containers* for B1 constructs. What it
tests is whether a B1 construct survives being nested, not the nesting itself.

**Schema:** `stress/pg/schema.sql`. Every relation is schema-qualified, sources are
`staging.*` and `core.*`, and targets are `mart.*`. **PostgreSQL version:** 17. Every
construct below exists in 9.6 or later unless its row says otherwise.

## How each test is shaped

- Each statement is `INSERT INTO mart.<t> (<cols>) SELECT …`, so every value lands in a
  named column the key can point at. T1 also has two bare SELECTs, to test decision D-8
  (`<result>.<column>`).
- Each statement opens with a comment naming the constructs it carries, so a miss in the
  results maps straight back to a checklist row.
- About 300 lines per file, 10–16 statements.
- **Outcome classes** used in the checklists:
  - **E** — an edge is expected;
  - **B** — a declared boundary is expected;
  - **R** — a refusal is expected;
  - **∅** — nothing is expected, and nothing being produced is the correct answer
    (a literal, `COUNT(*)`, an ORDER BY with no LIMIT).

## Labelling conventions the keys will follow

All of these were decided earlier and are restated here so this batch stands on its own:

1. A match is (source, target, flow, transform, phase).
2. **value** — the column supplies part of the output value.
3. **filter** — the column decides which rows exist. It targets the *relation* written,
   and carries a phase: pre-aggregation (WHERE, a join's surviving rows) or
   post-aggregation (HAVING), decided by what the filter *does*, not its keyword (D-6).
4. **influence** — the column governs a value without supplying it. GROUP BY keys
   influence every *aggregated* output column, never the keys themselves (D-2). A
   window's PARTITION BY and ORDER BY influence its output (D-4), and so does the ORDER BY
   inside an ordered aggregate.
5. A join's `ON` condition is structural, so it gives no edge (D-5). An equality in
   WHERE contributes **both** operands as filter.
6. **Transform ladder:** aggregated > conditional > derived > identity, ranked along the
   *path* (D-7).
7. A literal, `COUNT(*)` or `NOW()` gives no value edge.

## T1 — Breadth: is each construct supported at all?

**Objective.** Every B1 construct once, kept apart from the others: one statement, one
construct group, the simplest correct spelling. A miss here means "not supported", not
"broken by combination".

| # | Statement carries | Expected |
|---|---|---|
| 1 | identity, rename, arithmetic, `CASE`, `COALESCE`, `NULLIF`, `GREATEST`, cast (`::` and `CAST`), string functions, a literal column | E (one transform of each kind), ∅ for the literal |
| 2 | WHERE with `=`, `<>`, `IN (list)`, `BETWEEN`, `LIKE`/`ILIKE`, `IS NULL`, `IS DISTINCT FROM`, `AND`/`OR`/`NOT` | E filter, pre-aggregation |
| 3 | `INNER JOIN … ON` with a multi-column condition | E value from both sides; ∅ for the ON |
| 4 | `LEFT JOIN`: a right-side predicate in ON in one statement, and in WHERE in another | E; the WHERE one is a filter, the ON one is not |
| 5 | `RIGHT JOIN` | E |
| 6 | `FULL OUTER JOIN` with `COALESCE(a.k, b.k)` | E conditional from both keys |
| 7 | `CROSS JOIN` with a one-row dimension | E |
| 8 | `JOIN … USING (col)` and `NATURAL JOIN` — the merged column projected unqualified | E from the **left** input only — PostgreSQL's merged column for an inner join (corrected while keying; the card first said "both sides") |
| 9 | `GROUP BY` with `SUM`, `AVG`, `MIN`, `MAX`, `COUNT(x)`, `COUNT(DISTINCT x)`, `COUNT(*)`, plus `HAVING` | E aggregated; influence from the keys; post-aggregation filter |
| 10 | `ROLLUP`, `CUBE`, `GROUPING SETS`, and `GROUPING()` in the projection | E, with influence from every grouping column |
| 11 | aggregate `FILTER (WHERE …)`; `percentile_cont … WITHIN GROUP (ORDER BY …)`; `string_agg(x, ',' ORDER BY y)`; `array_agg` | E; D-10 |
| 12 | `ROW_NUMBER`, `RANK`, `DENSE_RANK`, `NTILE`, `LAG`, `LEAD`, `FIRST_VALUE`, `SUM … OVER (ROWS BETWEEN …)`, a named `WINDOW w` | E value from the arguments; influence from PARTITION BY and ORDER BY |
| 13 | `DISTINCT`, `DISTINCT ON (k) … ORDER BY k, ts DESC` | E; D-9 |
| 14 | `ORDER BY` with `LIMIT`/`OFFSET`, `FETCH FIRST n ROWS WITH TIES` | D-9 |
| 15–16 | two bare `SELECT`s (D-8), with disjoint column names | E into `<result>.*` |

## T2 — Depth: does support survive nesting?

**Objective.** The T1 constructs placed 3–6 levels deep, inside derived tables and CTEs,
renamed at every level. The question is whether a rule that works at the top level still
works at depth. The project has needed that question at least four times (S3-02, S4-04,
PG-13, PG-14).

| # | Statement carries | Expected |
|---|---|---|
| 1 | an alias chain: one column renamed at each of 5 levels | E identity, end to end |
| 2 | a window at depth 4, read by name at the top | E value, plus influence from PARTITION BY and ORDER BY carried up |
| 3 | re-aggregation: `SUM` at depth 3, then `AVG` of that sum at depth 1 | E aggregated; influence from **both** GROUP BYs |
| 4 | `HAVING` at depth 3, with a WHERE at the top over the aggregated column | post-aggregation at both levels (D-6) |
| 5 | `ROLLUP` in an inner CTE, with an outer filter on `GROUPING()` | E; the `GROUPING()` filter is **flagged, not labelled** — it selects on grouping structure, not data, and no convention exists (corrected while keying) |
| 6 | a join inside a derived table, joined again outside | E from all four relations; ∅ for both ON clauses |
| 7 | `FILTER (WHERE …)` aggregate at depth 3 | same answer as T1 #11, at depth |
| 8 | `DISTINCT ON` at depth 2 feeding an aggregate | D-9, at depth |
| 9 | a column referenced only by a WHERE at depth 5 and never projected | E filter on the top-level target |
| 10 | a column projected at depth 3 and never referenced above | ∅ — key forbids the edge |
| 11 | a named window defined at depth 2 and used twice | E |
| 12 | three sibling CTEs aggregating at different grains, then joined | E; each output's influence comes only from **its own** CTE's GROUP BY |

## T3 — Interaction: do the rules compose?

**Objective.** Constructs that each work alone, combined in one expression, where two
rules meet and have to agree.

| # | Statement carries | Expected |
|---|---|---|
| 1 | `RANK() OVER (ORDER BY SUM(x) DESC)` — a window over an aggregate | E; influence from x **through** the SUM, and from the GROUP BY |
| 2 | `x / SUM(x) OVER (PARTITION BY r)` — ratio to total | E aggregated from x; influence from r |
| 3 | `SUM(x) FILTER (WHERE c) OVER (PARTITION BY r)` | E; D-10 inside a window |
| 4 | `GROUPING SETS` with a window over the grouped result | E |
| 5 | `CASE` inside an aggregate inside a window | E aggregated, the highest rank on the path |
| 6 | `FULL JOIN` and then `GROUP BY COALESCE(a.k, b.k)` | influence from both keys |
| 7 | `percentile_cont WITHIN GROUP` with `FILTER`, under `GROUP BY` | E |
| 8 | `DISTINCT ON` with a window in the same SELECT | D-9, with a window present |
| 9 | `ORDER BY SUM(x) DESC LIMIT 10` | D-9, post-aggregation |
| 10 | `LAG(SUM(x)) OVER (ORDER BY month)` — growth over the previous period | E aggregated; influence from month |
| 11 | a mixed-grain projection: an aggregate beside a window beside a grouping key | each column gets exactly its own edges |
| 12 | `HAVING COUNT(DISTINCT x) > 1 AND SUM(y) > 0` | post-aggregation filter from x and y |

## T4 — Adversarial: does it get fooled?

**Objective.** Every statement is built so that a resolver that *guesses* gives a
confident, plausible, **wrong** edge. The forbidden-edge list in the key does as much work
here as the edge list does.

| # | Trap | What must not happen |
|---|---|---|
| 1 | unqualified `status`, `amount`, `created_at` with two relations in scope, where only one really has the column | binding to the wrong relation |
| 2 | a table alias spelled like another table (`FROM staging.orders AS customers`) | resolving `customers.id` to `staging.customers` |
| 3 | one alias reused in two subqueries for two different tables | mixing their columns (the earlier PG-21 shape) |
| 4 | an output alias equal to an input column, used in ORDER BY and in WHERE (PostgreSQL resolves the two differently) | the wrong one in each clause |
| 5 | `GROUP BY 1, 2` and `ORDER BY 2` — ordinals | ordinals treated as literals or dropped |
| 6 | keyword-like columns `year`, `month`, `level`, `position`, `name` — some quoted, some not | parse failure or a missed column |
| 7 | `'CONNECT BY'`, `'JSON_TABLE('` and `-- ROLLUP (` inside literals and comments | an invented refusal or influence edge |
| 8 | a self-join: two aliases of one table, each feeding a different column | the two sides crossing |
| 9 | `COUNT(*)` beside `COUNT(col)` beside `COUNT(1)` | an edge invented for `*` or for `1` |
| 10 | a projection of `NULL::numeric`, a constant and `now()` | any edge |
| 11 | a column used only inside an ON clause and nowhere else | a filter edge from it (D-5) |
| 12 | the same column feeding two targets via one window, with different transforms | the transforms crossing |
| 13 | a quoted mixed-case alias `"Region"` beside an unquoted `region` | case-folding merging them |

## T5 — Metamorphic and realistic: one computation, one answer

**Objective.** Pairs of statements that compute the same rows, so their lineage must match
exactly. This needs no hand-written truth: the pair is the test. It ends with one
production-shaped query of about 120 lines.

| Pair | Spelling A | Spelling B | Must be equal? |
|---|---|---|---|
| 1 | `JOIN … USING (k)` | `JOIN … ON a.k = b.k` | yes |
| 2 | named `WINDOW w` | inline `OVER (…)` | yes |
| 3 | `GROUP BY 1, 2` | `GROUP BY a, b` | yes |
| 4 | `DISTINCT a, b` | `GROUP BY a, b` | yes |
| 5 | `ROLLUP (a, b)` | the same groupings as `GROUPING SETS ((a, b), (a), ())` | yes |
| 6 | `HAVING SUM(x) > 0` | `WHERE s > 0` over a subquery that computes `SUM(x) AS s` | yes (D-6) |
| 7 | `COUNT(DISTINCT x)` | `COUNT(x)` over `SELECT DISTINCT x` | yes |
| 8 | `SUM(x) FILTER (WHERE c)` | `SUM(CASE WHEN c THEN x END)` | yes (D-10) |
| 9 | `FROM a JOIN b ON a.k = b.k` | `FROM a, b WHERE a.k = b.k` | yes (D-11) |
| 10 | `ROW_NUMBER() … = 1` in a subquery | `DISTINCT ON` | yes (D-9) |

**The realistic query:** a customer-summary model into `mart.customer_summary`. It
combines first and last order dates, lifetime value with FX conversion, average basket,
value rank and decile (`NTILE`), and a status from `CASE` over recency, across four joins,
two aggregation grains and three windows.

## Decisions taken 2026-10-01, before any key was written

Each of these was asked as a question (Q-A, Q-B, Q-C) and decided by Abhay. They are
project-wide conventions, not B1-only rules. They're numbered after D-8 and are also
recorded in `stress/FINDINGS.md`. **None is implemented yet.** The keys encode them, so
until the analyser follows them, every place they apply scores as a disagreement. That is
the point of writing the test first.

### D-9 · Q-A — a column that decides which row survives is a filter

Columns that choose the surviving row get a **filter** edge on the written relation:

- `DISTINCT ON (k) … ORDER BY k, ts DESC`: both the keys `k` and the tie-breaker `ts`.
- `ROW_NUMBER()` / `RANK()` `OVER (PARTITION BY p ORDER BY o)` when the result is then
  filtered (`= 1`, `<= n`): both `p` and `o`.
- `ORDER BY o … LIMIT / FETCH FIRST / OFFSET`: `o`.

The phase follows D-6: an ordering over an aggregated value is post-aggregation.

Three cases deliberately get **no** filter edge:

- An `ORDER BY` with no row limit removes no rows.
- A `LIMIT` with no `ORDER BY` keeps arbitrary rows, so no column decides which.
- A ranking window whose result is projected but never filtered stays plain D-4 influence.

A projected `k` still gets its value edge as well. Plain `DISTINCT a, b` gets no filter
edge, because the rows it removes are fully decided by the values already written.

### D-10 · Q-B (closes PG-15 as a decision) — `FILTER (WHERE c)` is read as `CASE WHEN c`

`c` produces exactly what it produces in `SUM(CASE WHEN c THEN x END)`: a **value** edge
into that one aggregated column, with the transform ranked along the path (D-7). It does
**not** produce a filter on the relation: `FILTER` changes one column's value, not which
rows of the table exist. T5 pair 8 must be equal.

### D-11 · Q-C — a join condition is structural wherever it is written

In WHERE, an **equality** between columns of **two different relations of the same FROM**
gives no edge, exactly as it would in `ON` (D-5), **when both relations are inner-joined**
(comma joins and INNER JOINs).

Two cases stay filters:

- A comparison that touches the null-supplying side of an outer join. After a LEFT JOIN,
  `WHERE b.x = a.x` removes rows the join kept, so it is a real filter, not a restatement
  of the join.
- A correlation to an outer query, which stays as D-5 already treats it.

Any other WHERE predicate is unchanged. T5 pair 9 must be equal.

**Corrected 2026-10-01, after implementation.** This entry first said "a comparison", which is wider than the option decided ("a column-equals-column condition"). The implementation followed the wider wording and moved `stress_01`'s self-join predicate `child.signup_date > parent.signup_date`, which that key labels as a real filter. Narrowed to equality. Whether a non-equality join condition is structural is an open question, B1-Q2.
