# Batch B10 — Lexical robustness: objective cards

**Status:** drafted 2026-10-04. Decisions D-35 to D-38 were taken before any key was
written. The SQL and keys are written blind, then run; results are in `REPORT.md`.

**Scope (programme plan §1, row B10):**

- dollar quoting, plain (`$$…$$`), tagged (`$q$…$q$`) and nested (one tag inside another);
- `E''` strings with backslash escapes, `U&''` strings and `U&""` identifiers (with and
  without UESCAPE), `B''` and `X''` bit strings;
- comments: `--` line comments and nested `/* /* */ */` block comments, in and between
  statements;
- operators: `OPERATOR(schema.op)` spellings of built-ins, and user-defined or extension
  operators (`<->`, `@@`, `mart.===`);
- `$n` parameters, PREPARE, EXECUTE, DEALLOCATE, and CREATE TABLE … AS EXECUTE (deferred
  here from B7);
- many statements per file, several statements on one line, empty statements;
- psql meta-commands (`\set`, `\echo`, `\pset`, `\timing`, `\i`, `\copy`, `\gset`) and psql
  variable interpolation (`:v`, `:'v'`, `:"v"`).

The schema is unchanged. Two prerequisites are assumed, as a real deployment would have
them: the `pg_trgm` extension (for `text <-> text`) and an operator `mart.=== (text, text)
→ boolean`. Neither is created in the scripts, because CREATE EXTENSION and CREATE OPERATOR
are not lineage. Every earlier convention applies, D-1 to D-34. Every node is
schema-qualified (D-25).

## How the existing conventions apply

- **A string literal carries no lineage, whatever its spelling or content** (B4). A dollar-
  quoted, `E''`, `U&''`, `B''` or `X''` literal is a literal. SQL text inside one, a column
  name, a `;`, a `$1` or a `--`, is text. A literal concatenated to a column makes the
  column derived.
- **A comment carries no lineage**, including a whole statement commented out.
- **`OPERATOR(pg_catalog.op)` is `op`**, so it must give exactly what `op` gives.
- **A statement boundary is a `;` outside every literal, quoted identifier and comment.**
  Several statements on one line are several statements; an empty statement is nothing.
- **A `U&""` identifier is the identifier it spells**: `U&"\0069d"` is `id`.
- **A query terminated by `\gset` writes psql variables** (D-37): it gives nothing, and in
  particular nothing in `<result>`. My application; overrule it if you see it differently.

## Decisions taken 2026-10-04, before any B10 key was written

### D-35 · B10-QA — a prepared statement's lineage is emitted at EXECUTE

`PREPARE p AS stmt` stores `stmt`. It is **counted, with no edges**, as a view definition
is. Each `EXECUTE p(…)` emits `stmt`'s edges **at the EXECUTE's line**, as REFRESH does
(D-32); a second EXECUTE emits them again. `CREATE TABLE t AS EXECUTE p(…)` is CREATE TABLE
AS over `stmt`'s query. `DEALLOCATE p` (or `DEALLOCATE ALL`) forgets it. An EXECUTE of a
name not prepared earlier in the script is **counted and declared `source_unavailable`**.
A prepared SELECT lands in `<result>`, as a bare SELECT does.

### D-36 · B10-QB — parameters and psql variables are external values, like literals

`$1` in a prepared statement, and a psql variable used as a value (`:v`, `:'v'`), read no
column: they give no edge, as a literal does. `WHERE o.status = $1` is still a filter from
`o.status`, and `o.amount * $2` is still derived from `o.amount`. An EXECUTE's arguments
are literals here. A psql variable used as an **identifier** (`FROM :tbl`, `:"col"`) cannot
be resolved, so that statement is refused or declared, never guessed.

### D-37 · B10-QC — psql meta-commands are skipped; `\i` is declared and `\copy` refused

A meta-command is not SQL. It is **skipped and not counted**, and it must never break the
SQL around it: a meta-command runs to the end of its line. `\i` and `\ir` (another file's
lineage) are **declared `source_unavailable`**, subject the file name. `\copy` is
**refused by name**, as COPY is (B3). `\gset` writes psql variables, not columns, so it
gives nothing.

### D-38 · B10-QD — a user-defined operator is read like an unknown function

`a <-> b` and `a OPERATOR(mart.===) b` are read as a call `op(a, b)`: **derived** from both
operands under the existing transform rules (D-7, D-19). In WHERE, a filter from both
operands. Only `OPERATOR(pg_catalog.op)` is the built-in `op` itself.

## A harness constraint, stated

`triage.py` attributes edges to statements by splitting the file at lines that end with
`;`. It does not lex. So no file in this batch ends a line with `;` inside a literal or a
comment: the adversarial `;` are mid-line. That keeps the per-statement view honest. It
does not weaken the test, because the analyser's own splitter is what is under test, and it
sees the `;` wherever it is.

## The cards

| Test | Objective | Statements |
|---|---|---|
| **T1 Breadth** | each lexical form once: plain, tagged and nested dollar quotes; `E''`, `U&''` (with UESCAPE), `B''` / `X''`; line and nested block comments; PREPARE / EXECUTE (twice) of an INSERT and of a SELECT; CREATE TABLE AS EXECUTE; DEALLOCATE then EXECUTE; `OPERATOR(pg_catalog.…)`; `<->`, `@@`, `mart.===`; `\set` with `:'v'`; `\echo`, `\timing`, `\pset`, `\i`, `\copy`, `\gset`; two statements on one line; an empty statement | ~27 |
| **T2 Depth** | the same forms nested 3–5 deep: a tagged literal in the innermost CTE; a PREPARE whose body is a four-deep CTE chain with parameters at two depths; comments nested five deep inside a statement; a psql variable three subqueries down; a custom operator under an aggregate; CREATE TABLE AS EXECUTE over a CTE chain; then a run of short statements | ~20 |
| **T3 Interaction** | forms combined: PREPARE with a parameter, a custom operator and a dollar literal, executed with a psql variable as argument; prepared UPDATE … FROM, MERGE and a writable CTE with RETURNING; E'' + U& + OPERATOR() in one WHERE; prepared statements re-prepared under a different body | ~10 |
| **T4 Adversarial** | traps: a dollar tag that spells a column; an `E''` escape that hides a column from a naive lexer, and a standard string ending in `\` that does not; `--` glued to an operand; a nested comment that a non-nesting lexer would end early; `$1` inside a literal; `::` and `[1:2]` beside psql variables; a quoted identifier containing `/*` and `--`; `U&""` spelling a column; EXECUTE before PREPARE; DEALLOCATE ALL; `:tbl` as an identifier | ~16 |
| **T5 Metamorphic** | must-equal pairs: `'…'` vs `$$…$$`; `'it''s'` vs `E'it\'s'`; a direct INSERT vs PREPARE + EXECUTE; `\|\|` vs `OPERATOR(pg_catalog.\|\|)`; heavily commented vs clean; a psql variable vs a literal; CTAS SELECT vs CTAS EXECUTE; `U&''` vs plain. Then one realistic psql deployment script | 8 pairs + 1 model |
