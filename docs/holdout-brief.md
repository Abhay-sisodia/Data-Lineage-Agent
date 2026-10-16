# Brief: blind PostgreSQL lineage holdout

The brief sent to a separate agent to write a blind holdout (used for holdout4, 2026-10-16).
**Freeze it** (docs/handoff-2026-10-16.md, section 5): the same brief for every later holdout,
so blind numbers are comparable. Change only the number and the excluded domains.

---

You are building a realistic PostgreSQL 17 data-warehouse project, plus an answer key
written by hand. It will be used to test a column-level data-lineage tool. You will never
see the tool. Write SQL the way a strong data engineer writes production ETL. Do NOT
simplify anything to make it easier to parse.

## 1. Domain
- Pick ONE domain that is NOT insurance, NOT telecom or billing, NOT hospital operations,
  and NOT agent commissions. (Holdouts so far: insurance, telecom billing, hospital
  operations.)
- Good choices: e-commerce fulfilment and returns, retail banking (accounts, cards, loans),
  logistics and fleet, energy metering, airline operations.
- Use realistic table and column names. Include:
  - at least 3 mixed-case quoted identifiers (e.g. `"OrderNo"`, `"SKU"`);
  - one column whose name is a reserved word, quoted (e.g. `"order"`, `"user"`).

## 2. Layout (exactly this)
```
holdoutN/
  schema/00_schemas.sql      CREATE SCHEMA raw, ref, staging, core, mart, report, audit
  schema/01_raw.sql          raw.* source tables: at least one jsonb, one array, one range column
  schema/02_ref.sql          ref.* lookups: effective-dated rates/prices, calendars, run_control,
                             a hierarchy table (parent/child)
  schema/03_targets.sql      every staging/core/mart/report/audit target table, including:
                             one partitioned table with partitions, one table with a
                             GENERATED ALWAYS AS (...) STORED column, one with an identity
                             column, one enum type and one domain used by a column
  seed/01_seed.sql           small seed data, so every file runs and produces rows
  sql/01_staging/  sql/02_core/  sql/03_marts/  sql/04_reporting/   <- the ETL
  run_all.sh                 schema -> seed -> sql/0*/*.sql, each file in its own psql
                             session with -v ON_ERROR_STOP=1
  expectations.md            the answer key (format in section 6)
  GRAMMAR.md                 an inventory of the constructs used (format in section 7)
```

## 3. Size and shape
- **8–10 ETL files** under `sql/`, each **400–500 lines**, about 4,000 lines in total. The
  schema and seed files are not counted.
- Number the files globally (`01_...sql` to `10_...sql`). They run in that order.
- Each layer reads the layer before it. At least 4 hops from `raw` to `report`.
- **Files must depend on each other the way real projects do:**
  - at least 2 tables that one file creates with `CREATE TABLE ... AS` or SELECT INTO,
    and a LATER file reads;
  - at least 2 columns that one file adds or renames with ALTER TABLE, and a LATER file
    reads;
  - at least 2 functions defined in one file and called in a LATER file: one scalar
    function, and one set-returning function used in FROM or LATERAL;
  - at least 1 view or materialized view defined in one file and read in a later one.

## 4. Hard rules
1. `run_all.sh` MUST run cleanly on an EMPTY PostgreSQL 17 database. Run it yourself, and
   fix every error. Every statement must be valid PostgreSQL. No Spark, T-SQL, Oracle or
   MySQL syntax.
2. **Script contents.** Every file is a normal SQL script:
   - plain statements, CREATE FUNCTION / PROCEDURE / TRIGGER bodies, DO blocks and CALLs;
   - psql meta-commands (`\set`, `:'var'`, `\gset`) in at most two files.
3. **Qualified names.** Schema-qualify every table (`raw.x`, `core.y`), except temp
   tables, CTE names, and ONE file that sets `search_path` and then uses unqualified
   names on purpose.
4. **Answer key by hand.** Write `expectations.md` BY HAND, from reading the SQL, before
   any lineage tool is run. Never generate it with a tool.
5. **Comments.** No comments that explain lineage (e.g. "this column comes from ...").
   Normal ETL comments are fine.
6. **Dead code is allowed but must be marked.** A function nobody calls, a view nobody
   reads, a commented-out block: each must say so in a comment.

## 5. PostgreSQL grammar to cover
- Each bullet below appears AT LEAST ONCE.
- Spread them across files, inside real logic. No toy one-liners.
- Add your own idioms beyond this list too.

**DML and DDL that moves data**
- INSERT ... SELECT with a column list in a different order from the table
- INSERT multi-row VALUES
- INSERT ... DEFAULT VALUES
- INSERT ... RETURNING into a data-modifying CTE that feeds another INSERT
- INSERT ... ON CONFLICT DO UPDATE using EXCLUDED, with a WHERE on the update
- INSERT ... ON CONFLICT ON CONSTRAINT ... DO NOTHING
- INSERT ... OVERRIDING SYSTEM VALUE
- UPDATE ... FROM with a multi-table join
- UPDATE SET (a, b) = (SELECT ...)
- UPDATE SET (a, b) = ROW(...)
- UPDATE with a correlated subquery
- UPDATE ... RETURNING
- DELETE ... USING
- DELETE ... RETURNING feeding an archive INSERT
- MERGE:
  - WHEN MATCHED AND ... THEN UPDATE / DELETE
  - WHEN NOT MATCHED [BY TARGET] THEN INSERT
  - WHEN NOT MATCHED BY SOURCE
  - one MERGE ... RETURNING merge_action(), *
- CREATE TABLE AS
- CREATE TABLE AS ... WITH NO DATA, then a later INSERT into it
- SELECT INTO
- CREATE TEMP TABLE ... ON COMMIT DROP
- CREATE UNLOGGED TABLE
- CREATE VIEW:
  - with a column list;
  - with WITH CHECK OPTION;
  - an INSERT or UPDATE THROUGH an updatable view.
- CREATE RECURSIVE VIEW
- CREATE MATERIALIZED VIEW, then REFRESH MATERIALIZED VIEW [CONCURRENTLY]
- Partitions: ATTACH PARTITION and DETACH PARTITION; also an INSERT into the partitioned
  parent
- TRUNCATE ... RESTART IDENTITY before a reload
- COPY ... FROM STDIN with inline data, and COPY (query) TO STDOUT
- At the top level, not inside PL/pgSQL:
  - PREPARE ... AS INSERT ... and EXECUTE it with parameters;
  - DECLARE a cursor, FETCH from it, CLOSE it, all inside BEGIN/COMMIT.

**Queries**
- WITH RECURSIVE with SEARCH DEPTH FIRST BY ... SET and CYCLE ... SET
- MATERIALIZED / NOT MATERIALIZED CTEs
- Several chained CTEs
- LATERAL subqueries
- LATERAL set-returning functions, including your own function from another file
- LATERAL (VALUES ...)
- Joins:
  - INNER, LEFT, RIGHT, FULL, CROSS;
  - NATURAL and USING (...), including USING ... AS alias (PostgreSQL 14+);
  - self-joins.
- Range / effective-dated joins: BETWEEN, half-open intervals, daterange && and @>,
  and a multirange.
- Subqueries: EXISTS, NOT EXISTS, IN, NOT IN, = ANY(array), <> ALL(...), scalar
  subqueries in SELECT / WHERE / ORDER BY.
- Set operations:
  - UNION, UNION ALL, INTERSECT, EXCEPT;
  - one with ORDER BY / LIMIT on the whole set;
  - one INSERT whose SELECT is a UNION ALL.
- DISTINCT and DISTINCT ON
- GROUP BY:
  - GROUPING SETS, ROLLUP, CUBE, and GROUPING();
  - GROUP BY DISTINCT.
- HAVING
- Aggregates:
  - FILTER (WHERE ...);
  - ordered-set aggregates (percentile_cont, mode() WITHIN GROUP);
  - hypothetical-set aggregates (rank(...) WITHIN GROUP);
  - string_agg / array_agg / jsonb_agg with ORDER BY.
- Window functions:
  - ROWS, RANGE and GROUPS frames, with EXCLUDE;
  - a named WINDOW clause;
  - lag, lead, first_value, last_value, nth_value, ntile, percent_rank, cume_dist;
  - row_number() for dedup;
  - running totals.
- generate_series; unnest WITH ORDINALITY; ROWS FROM(f(...), g(...))
- TABLESAMPLE ... REPEATABLE
- FETCH FIRST ... WITH TIES, OFFSET
- FOR UPDATE OF alias SKIP LOCKED
- Table aliases with column lists (`AS t(a, b)`)
- A set-returning function in the SELECT list

**Expressions**
- CASE (searched and simple), COALESCE, NULLIF, GREATEST, LEAST
- `::` casts and CAST
- IS DISTINCT FROM; IS [NOT] TRUE; BETWEEN SYMMETRIC
- LIKE ... ESCAPE, ILIKE, SIMILAR TO
- Regex: ~ and ~*, regexp_replace, regexp_match, regexp_substr
- Date/time: date_trunc, EXTRACT, AT TIME ZONE, interval arithmetic, age(), make_date
- Row constructors and row comparison
- Arrays:
  - constructors, indexing, slicing, array_position;
  - ANY over an array column.
- String functions: format(), concat_ws, string_to_array, split_part
- COLLATE
- Calls to your own functions:
  - named arguments (`f(p_a => x)`);
  - a VARIADIC function;
  - an overloaded function name;
  - a function whose return value is computed from a local variable.

**JSON / XML**
- ->, ->>, #>, #>>
- @>, ?, ?|
- jsonb_set, jsonb_strip_nulls
- jsonb_array_elements, jsonb_each_text
- jsonb_build_object, jsonb_agg, jsonb_object_agg
- SQL/JSON:
  - JSON_OBJECT, JSON_ARRAYAGG;
  - IS JSON;
  - JSON_VALUE / JSON_QUERY / JSON_EXISTS;
  - JSON_TABLE with NESTED PATH and FOR ORDINALITY.
- jsonb_path_query, jsonb_path_exists
- One XMLTABLE or xpath() use

**PL/pgSQL**
- At least 4 functions or procedures and 2 DO blocks.
- Declarations:
  - %TYPE, %ROWTYPE, record;
  - initial values that read parameters.
- SELECT ... INTO [STRICT], including several INTO targets
- Assignments into variables and into record fields
- Control flow:
  - IF / ELSIF / ELSE;
  - a CASE statement;
  - a labelled nested block with EXIT label.
- Loops:
  - FOR rec IN query;
  - FOR i IN 1..n;
  - FOREACH ... SLICE over an array;
  - WHILE;
  - EXIT / CONTINUE WHEN.
- Returning rows: RETURN QUERY, RETURN QUERY EXECUTE, RETURN NEXT
- Parameters: OUT and INOUT, RETURNS TABLE, RETURNS SETOF a table type
- Exceptions:
  - an exception block catching a named SQLSTATE;
  - GET DIAGNOSTICS row_count;
  - GET STACKED DIAGNOSTICS.
- RAISE NOTICE / EXCEPTION, and ASSERT
- Dynamic SQL:
  - EXECUTE format(...) USING ...;
  - one dynamic statement whose text is fully constant.
- Cursors:
  - a cursor with parameters (OPEN / FETCH / MOVE / CLOSE);
  - a FOR loop over a cursor.
- A procedure with COMMIT and ROLLBACK inside, called with CALL using named arguments
- Routine forms:
  - one LANGUAGE sql function;
  - one SQL-standard body function (`BEGIN ATOMIC ... END`);
  - one SECURITY DEFINER function with SET search_path.
- A function called from a SELECT in a LATER file

**Triggers and rules**
- A BEFORE row trigger that sets NEW fields from other NEW fields and from a lookup
- An AFTER row trigger that writes an audit table from NEW / OLD, with WHEN (...)
- A statement-level trigger with REFERENCING NEW TABLE AS ... OLD TABLE AS ...
- An INSTEAD OF trigger on a view
- One CREATE RULE (DO ALSO or DO INSTEAD)

**DDL and housekeeping mixed in**
- ALTER TABLE:
  - ADD COLUMN, DROP COLUMN, RENAME COLUMN, RENAME TO;
  - ALTER COLUMN TYPE ... USING;
  - SET DEFAULT.
- CREATE INDEX (partial, expression, INCLUDE)
- ANALYZE, VACUUM
- COMMENT ON
- GRANT / REVOKE
- CREATE POLICY with ENABLE ROW LEVEL SECURITY
- SET / SET LOCAL search_path
- BEGIN / COMMIT, SAVEPOINT / RELEASE / ROLLBACK TO
- LOCK TABLE
- NOTIFY / LISTEN

## 6. expectations.md (the answer key). Follow this format EXACTLY.
- Write **16–20 entries**. Each entry is ONE target column in a staging / core / mart /
  report / audit table.
- **Coverage. Required, and you must state where each requirement is met in a table at
  the top:**
  - at least one entry from every file 03 onward;
  - at least 6 entries 4+ hops from `raw`;
  - at least 3 through an effective-dated or range join;
  - at least 3 written inside PL/pgSQL;
  - at least 2 whose value passes through a PL/pgSQL VARIABLE (SELECT INTO, an
    assignment, or a function's local variable);
  - at least 2 through a function defined in an EARLIER file;
  - at least 2 through a table or column created or altered in an EARLIER file;
  - at least 1 each through:
    - a trigger;
    - MERGE;
    - a view or materialized view;
    - jsonb or SQL/JSON;
    - a data-modifying CTE (INSERT/UPDATE/DELETE ... RETURNING);
    - a recursive CTE or recursive view;
    - window-function dedup.
- **List only base columns:** every column you list is a `raw.` or `ref.` column. Never
  list an intermediate table; trace THROUGH it to the base.
- **Entry template** (keep the headings, the order and the indentation):
```
## 1. mart.some_table.some_column   (file 06)

values:
  raw.orders.amount                 aggregated
  ref.fx_rates.rate                 computed
filters:
  raw.orders.status
grouping / ordering:
  raw.orders.customer_id
join keys:
  raw.orders.customer_id
  ref.fx_rates.valid_from
note: <anything that is not a base column, e.g. JSON paths read>
```
- **What each heading means:**
  - **values:** base columns whose data reaches the output. Tag each one:
    - copied (unchanged, trimmed or cast);
    - computed (arithmetic);
    - aggregated (SUM / COUNT / ..., even after arithmetic);
    - chosen by a condition (CASE, ROW_NUMBER or a similar rule).

    A column that only steers a CASE goes in a `note:`.
  - **filters:** columns in WHERE / HAVING / FILTER / EXISTS / NOT EXISTS / dedup /
    quarantine predicates anywhere upstream that decide which rows arrive.
  - **grouping / ordering:** GROUP BY, PARTITION BY, DISTINCT ON keys, the ORDER BY of
    window functions and of LIMIT 1 lookups.
  - **join keys:** equality columns, and the date/range bounds of effective-dated joins.
    A non-equality predicate inside a join goes under filters.
- **Explain non-obvious entries.** When a column is listed ONLY because of where the
  output's ROWS came from, not where its value came from, add a `note:` line that says
  so. For example: filters upstream of rows that a DELETE ... RETURNING moved.
- **Repeats are fine:** one column may appear under several headings.
- **Quoted identifiers:** keep the quotes when the identifier is quoted,
  e.g. `raw.orders."OrderNo"`.
- **Fill every heading:** write `(none)` under an empty one.
- **Run control:** if `ref.run_control` selects the run row in a file, list it as a
  filter wherever run-control values are used.

## 7. GRAMMAR.md
- A table with the columns `construct | file:line(s)`.
- One row per bullet in section 5, plus a row for each extra construct you used.
- Then a second table, `cross-file dependency | defined at | used at`, for the section 3
  requirements.
- Say nothing about lineage.

## 8. Deliverable
- Put everything in `holdoutN/`.
- Confirm that `run_all.sh` passed on PostgreSQL 17, giving the `psql --version` and
  `SELECT version()` output.
- List any construct from section 5 you could not make valid, and why.
