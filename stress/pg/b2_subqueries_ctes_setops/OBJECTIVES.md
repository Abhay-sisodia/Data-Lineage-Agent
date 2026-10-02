# Batch B2 — Subqueries, CTEs and set operations: objective cards

**Status:** drafted 2026-10-01; SQL and keys written blind, then run; fixed 2026-10-02 under D-12..D-14. Results and fixes: `REPORT.md`.

**Scope (programme plan §1, row B2):** scalar subqueries (uncorrelated, correlated,
aggregated, lookup, ordered-pick); EXISTS / NOT EXISTS; IN / NOT IN; ANY / ALL; a scalar
subquery inside a WHERE; derived tables that aggregate and join back; LATERAL (including a
top-N per row); deep CTE chains; `MATERIALIZED` hints; recursive CTEs; UNION, UNION ALL,
INTERSECT, EXCEPT (and ALL), parenthesised and nested, inside CTEs and derived tables, and
with an ORDER BY / LIMIT over the whole set operation.

**Deliberately out:** `SEARCH` / `CYCLE ... TO` decorations on a recursive CTE fail the
parse for the whole file (PG-18, registered). A parse failure takes every statement in its
file with it, so they are not placed in a batch file where they would hide everything
else; they stay on PG-18's own record.

Schema, file shape, outcome classes and the five test shapes are B1's
(`../b1_core_select/OBJECTIVES.md`). Every B1 convention applies, including D-9 to D-11.

## Decisions taken 2026-10-01, before any B2 key was written

### D-12 · B2-QA — a scalar subquery's predicate is a VALUE of the column it feeds

In a scalar subquery in a SELECT list, `(SELECT SUM(r.amount) FROM r WHERE r.k = o.k AND
r.status = 'OK')`, every column of its WHERE, correlated or not, both operands of a
correlation included, is a **value** source of the one column the subquery feeds. None is
a filter on the written relation: the subquery decides which rows feed that column's
value, and removes no row from the target. The transform is ranked along the path:

- `aggregated` when the subquery's projection aggregates;
- `conditional` otherwise. A lookup, `(SELECT c.name FROM c WHERE c.id = o.cid)`, selects
  which value arrives, the same way a CASE condition does.

This is D-10's reasoning, applied to D-10's twin: a non-correlation predicate (`status =
'OK'`) is a value of the column in both the FILTER spelling and the subquery spelling.

**Corrected while keying T5:** this entry first said the two spellings "must give the same
lineage". They need not. The correlation (`r.k = o.k`) corresponds to a JOIN plus a GROUP BY
in the FILTER spelling, and the grouping key is *influence* under D-2, not a value. Only the
non-correlation predicates agree. The difference is one of the B2 seams in the report.

**It reverses the s7 convention for this one position.** A subquery in WHERE (EXISTS, IN,
ANY/ALL, a scalar comparison) decides which rows land. It is unchanged, and its columns
stay filters. A join inside the scalar subquery stays structural (D-5).

### D-13 · B2-QB — an ordered pick inside a scalar subquery is INFLUENCE

`(SELECT p.amount FROM p WHERE ... ORDER BY p.paid_at DESC LIMIT 1)` gives `paid_at` an
**influence** edge on that one column. It decides which value the column receives, without
supplying any of it: D-4's argument for a window's ORDER BY. It is not a D-9 filter,
because no target row is removed.

### D-14 · B2-Q1, decided after B2's first run — a predicate that cannot remove a result row is not a filter

Applied **only where a predicate cannot remove a row from the result**:

- inside a derived table, CTE or LATERAL reached through the **null-supplying side of an
  outer join** (the right of a LEFT JOIN, the left of a RIGHT JOIN, either side of FULL);
- inside a **LATERAL that returns exactly one row per outer row** (an aggregate without
  GROUP BY), whatever the join.

There, a WHERE column is a **value** of each column fed from that side (`aggregated` if the
side aggregates, else `conditional`), and a DISTINCT ON / ORDER BY … LIMIT column is
**influence** on them: D-12 and D-13, extended from the scalar subquery to its row-source
twins.

**Deliberately not applied to semi-joins and anti-joins.** Their match keys CAN remove rows
in every spelling (IN, EXISTS, EXCEPT, inner JOIN). Labelling them consistently would
reverse either D-5 or the correlation rule, and that is a separate decision. The P5 and P6
seams stay open, and P8's match key with them.

## The cards

| Test | Objective | Statements |
|---|---|---|
| **T1 Breadth** | every B2 construct once, kept apart from the others | 16 |
| **T2 Depth** | subqueries inside subqueries; CTE chains 6–8 deep; set operations inside CTEs inside derived tables; correlation reaching across two levels | 10 |
| **T3 Interaction** | subqueries meeting B1's rules: a scalar subquery inside an aggregate, a set operation feeding a window, EXISTS over a grouped CTE, recursion feeding a GROUP BY | 10 |
| **T4 Adversarial** | correlation name capture (inner alias shadowing outer), the same alias in subquery and outer, a CTE named like a real table, set-operation arms with different column names, NOT IN, a subquery projection named like an outer column | 12 |
| **T5 Metamorphic** | pairs: EXISTS vs IN; IN vs JOIN + DISTINCT; scalar subquery vs FILTER (D-12); scalar vs LEFT JOIN of a grouped derived table; CTE vs inlined derived table; UNION vs UNION of DISTINCTs; EXCEPT vs NOT EXISTS; ordered pick vs DISTINCT ON | 8 pairs + 1 model |

### Expected in advance, flagged rather than settled

- **Scalar subquery vs LEFT JOIN LATERAL aggregate** (T5). Both compute the same column,
  but a LATERAL is a row source, and its WHERE is a filter today (PG-03's tests pin that).
  D-12 is worded for the SELECT-list position only, so this pair is expected to **differ**.
  If the report shows it differing, deciding it is a new question; it is not settled
  quietly in a key.
