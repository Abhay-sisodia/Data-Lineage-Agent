# Batch B7 — DDL that creates lineage: objective cards

**Status:** drafted 2026-10-03. Decisions D-31 to D-34 were taken before any key was
written. The SQL and keys were written blind and then run; results are in `REPORT.md`.

**Scope (programme plan §1, row B7):**

- CREATE TABLE AS (with a column list, WITH NO DATA, IF NOT EXISTS) and SELECT INTO;
- views (column lists, views on views, CREATE OR REPLACE, DROP);
- materialized views and REFRESH;
- temp tables (and pg_temp shadowing, deferred here from B9);
- generated columns, and identity columns with `OVERRIDING SYSTEM VALUE` (deferred here
  from B3);
- partitions (PARTITION OF, ATTACH and DETACH), inheritance (INHERITS) and LIKE.

The schema is unchanged: every object B7 needs is created by the test scripts themselves,
which is the point. Every node is schema-qualified (D-25), and every earlier convention
applies.

## How the existing conventions apply

- **CREATE TABLE AS and CREATE VIEW are writes into the object they create**, like INSERT …
  SELECT. Columns are named by the column list or by the projections. A filter in the
  query is a filter on the new object.
- **SELECT … INTO t is CREATE TABLE t AS SELECT** (PostgreSQL's own definition). It is not a
  query into `<result>`.
- **A temp table lives in `pg_temp`, so its node is `pg_temp.<name>`** (my application of
  D-25; overrule it if you see it differently). Because pg_temp is searched first, an
  unqualified name matching a temp table is that temp table, with certainty, and no
  assumption is declared.
- **WITH NO DATA moves no rows**: no edges, counted (my application; overrule it if you see
  it differently). A materialized view created WITH NO DATA still registers its
  definition, so a later REFRESH emits it (D-32).
- **CREATE TABLE IF NOT EXISTS … AS on a table that already exists does nothing**: no
  edges. PostgreSQL skips it with a notice.
- **Identity columns and DEFAULTs** read no column. `OVERRIDING SYSTEM VALUE` changes
  nothing about lineage; it lets the INSERT supply the identity value.

## Decisions taken 2026-10-03, before any B7 key was written

### D-31 · B7-QA — in-script objects are registered; views are inlined

An object created earlier in the script is visible to every later statement, in script
order:

- **A VIEW** is a stored query with no data of its own, so reading it is **inlined**:
  lineage runs from the view's base columns to the target, as it already does for views in
  the dictionary. CREATE OR REPLACE replaces the definition from that point on, and DROP
  removes it.
- **A TABLE, temp table or materialized view** holds data, so it is a **node**: base →
  object, then object → target.
- **A temp table shadows a same-named table** for unqualified names, because pg_temp comes
  first on the search path.

### D-32 · B7-QB — REFRESH re-derives a known definition

`REFRESH MATERIALIZED VIEW m` is the write. If m's definition is known (created earlier in
the script), its edges are emitted again at the REFRESH line. If not, the REFRESH is
counted and declared `source_unavailable`.

### D-33 · B7-QC — a plain CREATE TABLE is counted; generated columns derive

A CREATE TABLE without AS moves no data. It is **counted, with no lineage and no
refusal**. A `GENERATED ALWAYS AS (expr) STORED` column gives **derived** edges, within the
table, from every column its expression reads (`mart.g.a → mart.g.b`). The ordinary
transform rules apply, D-19 included. `LIKE t` copies structure only and gives no edges.

### D-34 · B7-QD — a partition or child table flows into its parent, identity

Rows of a partition or an inheriting child are rows of the parent. So `CREATE TABLE c
PARTITION OF p`, `ALTER TABLE p ATTACH PARTITION c` and `CREATE TABLE c (…) INHERITS (p)`
each give `c.col → p.col` **identity** for every column the two share. This is the rule the
analyser already applies to Oracle's EXCHANGE PARTITION. DETACH moves nothing.

## The cards

| Test | Objective | Statements |
|---|---|---|
| **T1 Breadth** | each DDL form once: CTAS (column list, WITH NO DATA), SELECT INTO TEMP, a temp table read, a view and its read, a view with a column list, a view on a view, a materialized view with REFRESH and a read, CREATE TABLE with generated columns and an insert into it, LIKE, PARTITION OF, ATTACH, INHERITS, DETACH, an identity column with OVERRIDING SYSTEM VALUE | ~21 |
| **T2 Depth** | a view chain three deep with CTEs inside; CTAS from a CTE chain over a view; a materialized view over a view; a temp table built from a temp table built from a view | ~6 |
| **T3 Interaction** | a grouped view filtered on its aggregate (B2-06); UPDATE and MERGE reading a view; a UNION view; a generated column over a jsonb document (D-29); INSERT into a partitioned parent vs into a partition | ~7 |
| **T4 Adversarial** | CREATE OR REPLACE changing a view mid-script; DROP VIEW then a table of the same name; a temp table shadowing a real table (unqualified vs qualified); a view selecting `*`; CTAS IF NOT EXISTS on an existing table; a generated column whose CASE reads two columns (D-19) | ~9 |
| **T5 Metamorphic** | must-equal pairs: CTAS vs CREATE TABLE + INSERT … SELECT; SELECT INTO vs CTAS; a view read vs the same query inlined as a derived table; PARTITION OF vs CREATE TABLE + ATTACH; a view column list vs projection aliases. Then one realistic staging-to-mart build | 5 pairs + 1 model |
