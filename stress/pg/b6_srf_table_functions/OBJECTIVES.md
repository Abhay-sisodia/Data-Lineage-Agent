# Batch B6 — Set-returning and table functions: objective cards

**Status:** drafted 2026-10-03. Decisions D-26 to D-28 were taken before any key was
written. The SQL and keys were written blind and then run; results are in `REPORT.md`.

**Scope (programme plan §1, row B6):**

- `unnest` with one or several arrays;
- `WITH ORDINALITY`;
- `ROWS FROM (…)`;
- `generate_series` (numeric, date and timestamp; literal and column bounds);
- functions in FROM with a column-definition list;
- `TABLESAMPLE`;
- set-returning functions in the select list, including `regexp_split_to_table`, deferred
  here from B4, and `string_to_table`.

The jsonb set-returning functions (`jsonb_array_elements`, `jsonb_to_record(set)`,
`jsonb_each`) belong to B5 (semi-structured) and are not tested here.

The schema, outcome classes and test shapes are B1's, with B9's additive objects. Every
node is schema-qualified (D-25), and every earlier convention applies (D-2 to D-25).

## How the existing conventions apply

- **An element is derived from its array** (PG-05): `unnest(o.tags) AS t(tag)` gives
  `staging.orders.tags → … derived`, whether the unnest is in FROM, in a LATERAL or in the
  select list.
- **Several arrays bind by position**: `unnest(a, b) AS t(x, y)` gives x from a and y from
  b. `unnest(ARRAY[o.a, o.b])` has one element column, derived from both.
- **A filter on an element is a filter on its array**, as on any derived column.
- **A function nested in an SRF's argument is traced through it**:
  `unnest(string_to_array(o.csv, ','))` reads `csv`.
- **A user-defined set-returning function** in FROM has a body the analyser cannot see.
  It is declared, never guessed.
- **`TABLESAMPLE` decides which rows are read, by chance, from no column.** It gives no
  edge; the sampled table's other lineage is unchanged.
- **An ordinal used to select or order rows stands for its array** (my application of D-9
  and D-4 to D-26; overrule it if you see it differently): `WHERE t.n <= 3` is a filter
  from the array, as `WHERE rn <= 3` is a filter from a window's columns (D-9). A window
  `ORDER BY t.n` gives influence from the array, as any window ordering does (D-4).

## Decisions taken 2026-10-03, before any B6 key was written

### D-26 · B6-QA — the ordinal is influenced by its array

`unnest(o.tags) WITH ORDINALITY AS t(tag, n)`: the array's order decides each `n` without
supplying it, as a window's ORDER BY decides `ROW_NUMBER()` (D-4). So `n` gets
**influence** from the array, and no value. For `ROWS FROM (f(a), g(b)) WITH ORDINALITY`,
the ordinal is influenced by every array. This replaces PG-05's pinned "no source at all".

### D-27 · B6-QB — a set-returning function in FROM is structural, like a join

An SRF in FROM changes which rows exist: an empty or NULL array removes its source row,
and n elements repeat it n times. It is treated like a join's ON (D-5). Its argument gives
**no filter and no influence** unless the SRF's output is itself used, in a projection,
WHERE, GROUP BY or ORDER BY, where the ordinary rules apply to it.

### D-28 · B6-QC — a series is derived from its bound columns

`generate_series(start, stop[, step])`: each value is computed from the bounds, so every
column read in the arguments is a **derived value** of whatever the series feeds (D-22). A
series of literals has no source: no edge, and no boundary, because its shape (one column)
is known.

## The cards

| Test | Objective | Statements |
|---|---|---|
| **T1 Breadth** | every B6 construct once: unnest in FROM, LATERAL and the select list; two arrays; an ARRAY[] constructor; ordinality; ROWS FROM; generate_series with literal and column bounds; a user-defined SRF with a column-definition list; TABLESAMPLE; regexp_split_to_table; string_to_table; an unaliased unnest | ~16 |
| **T2 Depth** | SRFs through CTE chains: unnest of an array_agg, a calendar series joined to facts three levels up, ordinality carried up and filtered, nested SRF arguments | ~7 |
| **T3 Interaction** | SRFs meeting B1–B4 rules: GROUP BY an element (influence), a window over the ordinal, LIMIT ordered by an element (D-9), a LEFT JOIN LATERAL … ON true (D-14), INSERT … SELECT unnest, UPDATE … FROM unnest, a series feeding an aggregate | ~8 |
| **T4 Adversarial** | an alias named like a source column; an element alias reused across scopes; unnest of a literal array; an SRF over a scalar subquery; a series of literals only; SRFs whose output is never used (D-27); TABLESAMPLE … REPEATABLE; a column named `unnest` | ~12 |
| **T5 Metamorphic** | must-equal pairs: FROM unnest vs select-list unnest; comma vs CROSS JOIN LATERAL; ROWS FROM vs two-array unnest; string_to_table vs unnest(string_to_array); regexp_split_to_table vs unnest(regexp_split_to_array); a series in FROM vs in the select list. Then one realistic tag-and-calendar report | 6 pairs + 1 model |
