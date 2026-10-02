# Batch B9 — Names and resolution: objective cards

**Status:** drafted 2026-10-03. Decisions D-23 to D-25 were taken before any key was
written. The SQL and keys were written blind and then run; results are in `REPORT.md`.

**Scope (programme plan §1, row B9):**

- quoted and case-folded identifiers;
- `search_path` and schema qualifiers;
- alias shadowing and reuse across scopes;
- `*` and `t.*`;
- USING and NATURAL column merging;
- same-named columns across tables;
- `COLLATE`, deferred here from B4.

**Schema change (decision B9-QC, additive only).** These objects were added to
`stress/pg/schema.sql`:

- `staging."LegacyFeed"`: quoted mixed-case columns, with `"Amount"` beside a lower-case
  `amount` (two different columns), `"order"`, `"user"` and `"Status Code"`;
- `mart."FeedSummary"`: a quoted mixed-case target;
- `staging.ledger` / `core.ledger`: one name in two schemas, one of them the default;
- `staging.events_log` / `mart.events_log`: one name in two schemas, neither the default.

No existing object changed. The new names are deliberately not ones B1–B4 use, so the
earlier batches are untouched (their pins were re-run after the change).

## How PostgreSQL resolves names (the semantics the keys follow)

- An unquoted identifier folds to lower case. A quoted one is kept exactly, so `"Amount"`
  and `amount` are different columns, and `"segment"` is `segment`.
- A column name resolves in the innermost query that has it, then outward (B2-08). An
  alias hides the table name it stands for.
- In GROUP BY, a bare name that is both an input column and an output alias means the
  **input column**. In ORDER BY it means the **output column**.
- An unquoted `user` is the `CURRENT_USER` function, not a column.
- The merged column of `JOIN … USING` / `NATURAL JOIN` is the **left** input's column for
  INNER and LEFT, the **right** input's for RIGHT, and `COALESCE(left, right)` for FULL
  (B1-02). For FULL that reads two columns, so it is conditional (D-19).
- `SELECT *` lists a USING join's merged columns first, then the left input's other
  columns, then the right's. `t.*` is `t`'s columns in order.
- A CTE name hides a table of the same name for an unqualified reference only. A
  schema-qualified name always means the table.

## How the existing conventions apply

- `*` and `t.*` expand to one identity value edge per column, by position.
- `x COLLATE "C"` is **derived**: something stands between the column and the target, by
  the rule that makes a cast derived (B4). Overrule this here if you see it differently.
- An unqualified relation resolved by assumption is declared with the existing
  `context_dependent_binding` boundary, once per unit, at the first such reference (its
  subject is the unit and the first line of that statement's span).

## Decisions taken 2026-10-03, before any B9 key was written

### D-23 · B9-QA — `SET search_path` is followed, in script order

After `SET search_path TO a, b`, an unqualified relation resolves to the first schema on the
path that has it. The binding is certain, so no assumption is declared. Before any SET, and
after `RESET search_path` or `SET search_path TO DEFAULT`, today's behaviour applies: the
dictionary's default schema, with the assumption declared. A name on no schema of a known
path is dangling, as PostgreSQL would error.

### D-24 · B9-QB — an unqualified name with no known path binds only if unique

With no path known, an unqualified relation resolves in this order:

1. the default schema, if it has the name;
2. otherwise the **single** schema in the dictionary that has the name.

Either way the assumption is declared. If two or more non-default schemas have the name,
the reference is **ambiguous**: it is declared as `dangling_reference` with the bare name as
subject, and no edge is emitted from either table.

### D-25 · B9-QD — every PostgreSQL node is schema-qualified

A PostgreSQL column node is `schema.table.column`, and a relation node is `schema.table`:
`column:staging.ledger.amount`, `relation:mart.kpi_rollup`. Bare names merged
`staging.ledger` and `core.ledger` into one node. Oracle is unchanged. **Implementing it
relabels every B1–B4 key and test mechanically.** B9's keys are written qualified from the
start.

## The cards

| Test | Objective | Statements |
|---|---|---|
| **T1 Breadth** | each resolution rule once: the default schema, a unique name, an ambiguous name, SET / RESET search_path, path order, a qualifier overriding the path; quoted, folded and three-part names; `*`, `t.*`; USING (FULL and RIGHT), NATURAL; COLLATE | ~16 |
| **T2 Depth** | names through 3–5 levels: an alias reused at every level, a CTE named like a real table, `*` through three derived tables, a NATURAL join two levels down, unqualified names under a SET at depth | ~7 |
| **T3 Interaction** | names meeting B1–B4 rules: GROUP BY input-versus-alias, PARTITION BY a merged column, an unqualified UPDATE … FROM column, a MERGE source aliased like its target, quoted RETURNING, set-operation names | ~8 |
| **T4 Adversarial** | `"O"` versus `o`, unqualified `id` binding innermost, `"Amount"` versus `amount`, unquoted `user`, `"Status Code"`, an alias named like a schema, an alias named like a column, `*` with duplicate names, a quoted schema in SET | ~12 |
| **T5 Metamorphic** | must-equal pairs: qualified versus SET + unqualified (modulo the boundary), `"amount"` versus `amount`, upper versus lower case, `*` and `t.*` versus explicit lists, USING versus ON, NATURAL versus USING, a three-part name versus an alias, a CTE versus a derived table, FULL USING versus COALESCE. Then one realistic multi-schema load | 10 pairs + 1 model |
