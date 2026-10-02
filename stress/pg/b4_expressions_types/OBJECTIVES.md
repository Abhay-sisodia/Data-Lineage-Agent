# Batch B4 — Expressions and types: objective cards

**Status:** drafted 2026-10-02. Decisions D-19 to D-22 were taken before any key was
written. The SQL and keys were written blind and then run; results are in `REPORT.md`.

**Scope (programme plan §1, row B4):**

- CASE, searched and simple, and nested;
- COALESCE, NULLIF, GREATEST and LEAST;
- casts: `::`, `CAST`, typed literals and type modifiers;
- string, date and math functions;
- IS DISTINCT FROM;
- regular expressions: `~`, `~*`, SIMILAR TO and the `regexp_*` functions;
- row constructors and row comparison;
- composite field access `(t).f` and whole-row references;
- arrays: `ARRAY[]`, subscripts and slices, `= ANY`, `@>` / `&&`, `array_agg`, `array_*`;
- ranges: constructors, `lower` / `upper`, `@>` / `&&`.

The schema, outcome classes and test shapes are B1's, unchanged. The schema has no range or
composite column, so ranges are built from columns, and composite access uses whole-row
references to table aliases, which is valid PostgreSQL on any table. Every earlier convention
applies (D-2 to D-18).

## How the existing conventions apply to expressions

These were already decided. They are stated here so the keys can be checked against them.

- Every column read by a projection expression is a **value** of the column it feeds,
  including CASE conditions (D-10).
- The transform is ranked along the path from source to target (D-7):
  aggregated > conditional > derived > identity. Anything between the column and the target
  makes it at least derived, **a cast included**.
- A column read in WHERE is a **filter**, whatever expression wraps it. That includes both
  operands of a comparison, every column in `= ANY(…)`, `@>` and `~`, and every column in a
  row comparison.
- A literal, `now()`, `current_date` or `random()` gives no edge (B1 rule 7).
- An expression in a join's ON is structural (D-5). An expression in GROUP BY gives
  influence from its columns (D-2), and so does one in a window's PARTITION BY or ORDER BY
  (D-4).

## Decisions taken 2026-10-02, before any B4 key was written

### D-19 · B4-QA — a conditional form is `conditional` only when two columns decide it

CASE, COALESCE, NULLIF, GREATEST, LEAST, IF and DECODE are **conditional** when the node
reads **two or more distinct columns**, across its tests and its results together. With one
column and constants it is **derived**. That covers a null guard, a clamp, an ABS-like CASE
and a bucketing CASE:

| Expression | Transform |
|---|---|
| `COALESCE(x, 0)` | derived |
| `GREATEST(x, 0)` | derived |
| `NULLIF(x, 0)` | derived |
| `CASE WHEN x IS NULL THEN 0 ELSE x END` | derived |
| `CASE WHEN x > 1000 THEN 'HIGH' ELSE 'LOW' END` | derived |
| `COALESCE(a, b)` | conditional, for both columns |
| `CASE WHEN s = 'A' THEN x ELSE 0 END` | conditional, for both `s` and `x` |

This extends the existing COALESCE rule (`transforms.is_conditional`) to every conditional
form, so equivalent spellings agree. The phase-0 NVL label is unaffected.

**How it is read, stated so the keys can be checked:**

- **Distinct columns** means distinct column references by relation instance: `a.amount`
  and `b.amount` in a self-join are two columns, while `o.amount` written twice is one.
- **They are counted in the node's own scope.** In `COALESCE(x.net, 0)`, where `x.net` is
  a derived-table column built from two base columns, the node reads one column. It is
  derived, and the two base columns reach the target through it.
- **The rule applies per node, along the path (D-7).** A conditional node anywhere between a
  column and its target makes that path conditional. A single-column guard on the path does
  not.

### D-20 · B4-QB — a whole-row reference expands, field-exact

- `(o).amount` **is** `o.amount`. It is identity when projected bare and has every
  consequence `o.amount` has.
- A function over a whole row (`row_to_json(o)`, `to_jsonb(o)`, `ROW(o.*)`, or a bare `o`
  passed or projected as a value) reads **every column of `o`**. Each column is a
  **derived** value of the target, expanded through the dictionary as `*` is.
- In WHERE, a whole-row comparison (`o IS NOT NULL`, `ROW(o.*) = …`) is a filter from every
  column of `o`.

### D-21 · B4-QC — dead reads keep their edges

Lineage is syntactic. A column that is read is a source, even where it cannot change the
result: `o.amount * 0`, `CASE WHEN false THEN o.amount END`, `COALESCE(1, o.amount)`. No
constant folding is done. T4 checks that these edges are present, with the transform the
expression's shape gives.

### D-22 · B4-QD — a parameter column is a derived value

A column used as a function's parameter rather than as its operand is a **derived value**
of the output, like any other column read by the projection. That covers a precision, a
format, a unit, a width or a pattern: `round(x, s.scale)`, `to_char(ts, c.fmt)`,
`date_trunc(c.grain, ts)`, `lpad(code, c.width, '0')`, `x ~ c.pattern`. It is not
influence. This is today's behaviour, and it needs no per-function parameter list.

## The cards

| Test | Objective | Statements |
|---|---|---|
| **T1 Breadth** | every B4 construct family once, kept apart from the others | ~18 |
| **T2 Depth** | expressions carried through 3–6 levels of CTEs and derived tables: a cast, then a guard, then a CASE, then an aggregate; D-19 counted in the node's own scope at each level; whole-row references through a derived table | ~8 |
| **T3 Interaction** | expressions meeting B1/B2 rules: GROUP BY and PARTITION BY expressions (influence), CASE inside an aggregate inside a window, an expression in ON (structural), array and range predicates in WHERE and HAVING, a scalar subquery inside a CASE | ~9 |
| **T4 Adversarial** | dead reads (D-21); one column guarded twice (one distinct column); a self-join GREATEST (two); columns named like functions or keywords (`year`, `month`, `position`, `level`); a column name inside a string literal or a format string; `extract(year FROM …)` beside `dim_date.year`; cast chains; `(o).amount` where `o` is also a column name | ~14 |
| **T5 Metamorphic** | must-equal pairs: `COALESCE(x,0)` vs its CASE; `GREATEST(x,0)` vs its CASE; `NULLIF` vs its CASE; `COALESCE(a,b)` vs its CASE; `::` vs `CAST`; `\|\|` vs `concat`; `extract` vs `date_part`; `IN` vs `= ANY(ARRAY[…])`; `IS DISTINCT FROM` vs its expanded form; `(o).amount` vs `o.amount`. Then one realistic pricing and banding model | 10 pairs + 1 model |

Each file opens with its own card: the constructs it exercises, and for each, the expected
outcome class (an edge, a declared boundary or a refusal).
