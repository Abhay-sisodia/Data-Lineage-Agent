# Batch B3 — DML writes: objective cards

**Status:** drafted 2026-10-02; decisions D-15..D-18 taken before any key; SQL and keys
written blind, then run. Results: `REPORT.md`.

**Scope (programme plan §1, row B3):** INSERT with SELECT, VALUES, DEFAULT VALUES and an
omitted column list; `ON CONFLICT … DO NOTHING / DO UPDATE SET … EXCLUDED … WHERE`;
UPDATE (simple, multi-column, row-valued `SET (a, b) = (subquery)`, correlated subquery in
SET, `UPDATE … FROM`, self-referencing); DELETE (simple, `USING`, subquery, unconditional);
RETURNING; data-modifying CTEs; MERGE (PostgreSQL 15+, including PostgreSQL 17's
`WHEN NOT MATCHED BY SOURCE` and `MERGE … RETURNING`); TRUNCATE and COPY.

Schema, outcome classes and test shapes are B1's. Every B1 and B2 convention applies
(D-2 … D-14).

## How the existing conventions apply to DML

These were already decided; they are stated so the keys can be checked against them.

- **UPDATE:** each SET expression's columns are values of the column it assigns.
  `SET amount = amount * 1.1` is a real self-edge (S3-07). A WHERE column is a filter on
  the updated relation.
- **`UPDATE t … FROM s WHERE t.k = s.k`** and **`DELETE FROM t USING s WHERE t.k = s.k`:**
  PostgreSQL joins the target to the FROM / USING list, so an equality between the target
  and a FROM relation is a join condition: structural under D-11. Every other WHERE column
  is a filter on the target.
- **DELETE:** WHERE columns are filters on the deleted relation (convention (c)). A
  DELETE with no WHERE gives nothing.
- **MERGE:** ON is structural (D-5). USING's WHERE and each arm's `AND` / WHERE condition
  are filters. SET / INSERT values are values. A GROUP BY or window in USING gives
  influence (S4-03).
- **TRUNCATE and COPY** are refused by name (they are in the refusal register). The
  refusal must stay local to its statement.

## Decisions taken 2026-10-02, before any B3 key was written

### D-15 · B3-QA — an ON CONFLICT target is structural

The conflict-target columns of `ON CONFLICT (k)`, or of the constraint behind `ON
CONFLICT ON CONSTRAINT`, match incoming rows against the target, exactly as a MERGE's ON
does. They give **no edge** (D-5), even under DO NOTHING, which drops the matched rows.
That is the semi/anti-join match shape left open in B2 (P5, P6), and it is treated the same
way here until that seam is decided.

A `DO UPDATE … WHERE` condition **is** a filter on the target, like a MERGE arm's
condition. In `DO UPDATE SET`, `EXCLUDED.c` is the value the INSERT proposed for `c`, so
its sources are whatever fed `c` in the INSERT.

### D-16 · B3-QB — RETURNING output lands in `<result>`

As D-8 does for a bare SELECT, a standalone DML's RETURNING columns are targets:
`<result>.<column>`. **A returned column's source is the written table's column**:
`RETURNING t.amount` gives `t.amount → <result>.amount`. For INSERT and UPDATE that is
the new value, which already has its own lineage into `t.amount`, so the chain composes.
For DELETE it is the deleted value.

Inside a WITH, a DML's RETURNING columns feed whatever reads the CTE, by the same rule.

### D-17 · B3-QC — a writable CTE's predicate filters every table its rows reach

In `WITH moved AS (DELETE FROM a WHERE a.status = 'X' RETURNING …) INSERT INTO b SELECT …
FROM moved`, `a.status` is a filter on **a**, because it decides which rows are deleted
(convention (c)). It is also a filter on **b**, because it decides which rows are copied
there. The same holds for an UPDATE or INSERT in a WITH whose returned rows feed another
write.

### D-18 · B3-QD — D-12 applies in a SET

`UPDATE t SET total = (SELECT SUM(p.amount) FROM payments p WHERE p.order_id = t.id)`:
the subquery's WHERE columns, both correlation operands included, are **values** of
`t.total`, aggregated because the subquery sums. They are not filters on `t`: no row of
`t` is skipped, and a row with no match simply gets NULL. The same applies to a row-valued
`SET (a, b) = (SELECT …)` and to a scalar subquery in an INSERT's VALUES list.

## The cards

| Test | Objective | Statements |
|---|---|---|
| **T1 Breadth** | every B3 construct once, including a TRUNCATE and a COPY whose refusals must stay local | ~16 |
| **T2 Depth** | DML fed by deep CTE chains and set operations; MERGE whose USING is a five-level query; data-modifying CTEs chained three deep | ~9 |
| **T3 Interaction** | DML meeting B1/B2 rules: an UPDATE … FROM a grouped derived table, a DELETE driven by a window (D-9), an upsert from an aggregate, a MERGE with filtered ranks, RETURNING feeding aggregation | ~10 |
| **T4 Adversarial** | target-alias shadowing, EXCLUDED vs a table named like it, a FROM-list table equal to the target under another alias, a SET referring to the column it overwrites, a column list in a different order from the table, an omitted column list | ~12 |
| **T5 Metamorphic** | must-equal pairs: INSERT … ON CONFLICT DO UPDATE vs the same upsert as MERGE; CTE-fed INSERT vs inlined; omitted vs explicit column list; `SET a = x, b = y` vs `SET (a, b) = (x, y)`; DELETE … WHERE IN vs DELETE … WHERE EXISTS. One SEAM pair: DELETE … USING vs DELETE … WHERE EXISTS (the B2 semi-join seam, left open). Plus one realistic nightly upsert-and-archive job | 6 pairs + 1 model |

**Corrected while designing T5:** the first draft paired `UPDATE … FROM` with `SET =
(correlated subquery)`. They are not equivalent: `UPDATE … FROM` leaves unmatched target
rows untouched, while the correlated SET writes NULL into them. A pair that computes
different rows tests nothing, so it was dropped.
