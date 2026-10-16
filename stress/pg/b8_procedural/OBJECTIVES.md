# Batch B8 — Procedural (PL/pgSQL): objective cards

**Status:** drafted 2026-10-04. Decisions D-39 to D-42 were taken before any key was
written. The SQL and keys are written blind, then run; results are in `REPORT.md`.

**Scope (programme plan §1, row B8):**

- functions and procedures (`LANGUAGE plpgsql`), parameters, `RETURNS TABLE`;
- DO blocks;
- variables, assignments, `SELECT … INTO`, explicit cursors with FETCH, loop records;
- RETURN QUERY (and RETURN NEXT, for the declared case);
- `FOR rec IN query LOOP`;
- dynamic SQL: `EXECUTE` of constant text, of `format()`, and of text built from a
  parameter;
- triggers: trigger functions, NEW and OLD, `CREATE TRIGGER … WHEN`;
- CALL, PERFORM, `SELECT f()`; exception blocks; GET DIAGNOSTICS.

The programme plan put B8 late because it was expected to be **mostly refused**. This
batch therefore measures two things:
- whether the lineage is right where the analyser produces it;
- whether every routine it cannot read is **refused or declared, never partial or silent**.

The keys state the true lineage, so a refusal scores as misses. The report says which
refusals were honest.

The schema is unchanged. Routines are created by the scripts, always schema-qualified
(`mart.fn_…`). Every earlier convention applies, D-1 to D-38.

## How the existing conventions apply to procedural code

These come from the Oracle keys (`stress/stress_01`…`04`, conventions headers), which have
keyed PL/SQL since phase 0. Each is stated so the keys can be checked against it.

1. **Variables and parameters are nodes**, `variable:<name>`, as the Oracle keys name them:
   - bare and lower-case here, because PostgreSQL folds identifiers;
   - every variable and parameter name is unique within its file, so no two routines share
     a node.

   This **corrects what I said when asking the B8 questions**: there, I wrote that function
   parameters would be external values "like D-36". The Oracle convention is older and
   says otherwise. D-36 (psql variables, `$n` of PREPARE) is unchanged: those are values
   supplied from outside the script, while a routine's parameter is a named input whose
   flow the Oracle keys trace.
2. **`SELECT … INTO v`** writes a variable. Then:
   - each column read is a **value** of `v`, with the transform its expression gives;
   - a WHERE column is a **filter on the relation read**, because the statement writes no
     relation (the Oracle "second clause");
   - a variable in that WHERE is a filter on the relation read too.
3. **A variable in a DML statement:**
   - used as a value, it is `variable → column`;
   - used in WHERE, it is a filter on the written relation;
   - an assignment `v2 := v1 * 2` is `variable → variable`, derived;
   - literals, `now()` and GET DIAGNOSTICS give no edge.
4. **A loop record is transparent.** In `FOR r IN query LOOP … r.f …`, `r.f` is traced to
   the query's column (Oracle's cursor FOR loop). Then:
   - the query's WHERE is a filter on each relation the body writes;
   - its GROUP BY gives influence as usual.

   An explicit cursor FETCHed INTO variables follows rule 2: values bind positionally, and
   the cursor's WHERE filters the relation read.
5. **Bands, per edge** (Oracle: "set-based 0, variable/cursor/loop 1, dynamic SQL and
   triggers 2"):
   - a static statement's column→column edges are **band 0**, even inside a routine;
   - an edge with a variable endpoint, or produced by a statement inside a loop, is
     **band 1**;
   - an edge from analysed dynamic SQL, or from a trigger body, is **band 2**.
6. **A scalar function used in a query** contributes the columns its **return value**
   depends on, not its argument (Oracle's interprocedural rule). They arrive band 1, with
   the transform of the path, and the argument column is not a source of that output.
7. **`EXCEPTION WHEN OTHERS THEN NULL`** is declared `suppressed_error`, subject
   `<unit>:<line>`, as the Oracle keys do. Statements inside a handler are analysed like
   any others.
8. **Origin:** a body statement's edges originate at **its own line in the file**, unit =
   the routine's name.
9. **Statements that give nothing:** RAISE and PERFORM give no lineage. Nor do COMMIT,
   ROLLBACK, IF and CASE *statements* (whose conditions are guards, not filter edges).

## Decisions taken 2026-10-04, before any B8 key was written

### D-39 · B8-QA — a routine's lineage is emitted at its definition

The body is analysed once, at its own lines, origin unit = the routine's name, as Oracle
routines are. A CALL, a PERFORM or a top-level `SELECT f()` adds no edges and is counted.
A routine that is never called still has its lineage. A DO block executes where it stands
and is analysed in place. A scalar function's return value reaches its caller through its
summary (rule 6).

### D-40 · B8-QB — a RETURN QUERY function read in FROM is inlined like a view

When a set-returning function's body produces its rows by RETURN QUERY (one or several),
reading it in FROM inlines the query, like a D-31 view. Lineage runs from the base columns
to the caller's target:
- the function's parameters take the call's arguments, which here are literals: no edge;
- a column the body filters on stays a filter on the caller's target.

**Two applications are mine; overrule them if you see them differently:**
- the RETURN QUERY gives nothing at the definition itself, because its rows go to its
  reader;
- an inlined function read is set-based, so it is **band 0**.

A body that builds its rows procedurally (RETURN NEXT in a loop) is declared
`source_unavailable`, subject the function, at the read.

### D-41 · B8-QC — a trigger's edges are emitted once, at its binding, band 2

`CREATE TRIGGER … ON t … EXECUTE FUNCTION f()` binds f's body to t. Then:
- `NEW.x` and `OLD.x` are `t.x`, and the body's writes give **band 2** edges, once;
- a trigger function never bound gives nothing, because NEW has no table;
- a function bound to two tables gives each table's edges;
- a `WHEN (NEW.x …)` condition is a filter from `t.x` on what the body writes.

**My application of where the edges are keyed:** at the line of the body statement that
writes, with unit = the function. That is where the write is in the file, which is what
rule 8 says. The binding decides *that* they exist, not where they are.

### D-42 · B8-QD — dynamic SQL is analysed when decidable

`EXECUTE` of text that folds to a constant is analysed like static SQL at the EXECUTE's
line, band 2. The text may be built from literals, `format()` of literals, `||`, and
variables assigned only constants before it.

Otherwise the EXECUTE is declared `dynamic_sql`, subject `EXECUTE:<line>` (Oracle:
`EXECUTE IMMEDIATE:<line>`), and gives no edge:
- text built from a routine parameter, a column or a loop variable is undecidable;
- `USING` values are external values.

## A harness note, stated

Keyspecs gained two directives for this batch:
- `origin <unit> <band>` sets the origin and band of the facts that follow;
- `var:<name>` names a variable node.

`triage.py` prints bands 1 and 2. It still attributes statements by lines ending in `;`,
so each routine opens with a DECLARE (ending in `;`) before its first statement. Loop
queries keep their continuation lines from starting with SELECT or WITH. Both are only so
the per-statement view lands each write on its own line.

## The cards

| Test | Objective | Routines / statements |
|---|---|---|
| **T1 Breadth** | one of each: a function with a static INSERT; `SELECT f()`; a procedure with a parameter and CALL; `SELECT … INTO` a variable then INSERT VALUES; assignments; a FOR loop record; an explicit cursor with FETCH; a RETURN QUERY function and its read; a scalar function and its use; a DO block; constant dynamic SQL; `format()`; undecidable EXECUTE; a trigger on INSERT (NEW) and one on UPDATE (OLD); a procedure with COMMIT; a RETURN NEXT function read; an exception block; GET DIAGNOSTICS | ~20 |
| **T2 Depth** | nested blocks three deep; a variable carried through four assignments; a loop inside a loop; a scalar function calling a scalar function; a RETURN QUERY function over a view over a CTE chain; dynamic SQL inside a loop with a constant text | ~8 |
| **T3 Interaction** | a loop whose body runs constant dynamic SQL; a trigger with WHEN whose body calls a scalar function; a RETURN QUERY function read under GROUP BY; a loop over a RETURN QUERY function; a SELECT INTO variable feeding an UPDATE's WHERE inside a loop | ~6 |
| **T4 Adversarial** | a function defined and never called (still lineage); a trigger function never bound (none); a function bound to two tables; a body with `SET search_path` binding unqualified names; SQL text inside RAISE; EXECUTE of a parameter; `WHEN OTHERS THEN NULL`; PERFORM of an INSERT-like query; a function DROPped and re-created before its read; a variable named like a column | ~12 |
| **T5 Metamorphic** | must-equal pairs: a static INSERT in a function vs at top level; a FOR loop vs INSERT … SELECT; constant dynamic SQL vs static; a RETURN QUERY read vs the derived table; a DO block vs the plain statement; `format('%I.%I')` vs `\|\|`. Then one realistic ETL procedure | 6 pairs + 1 model |
