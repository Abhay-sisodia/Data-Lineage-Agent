# Runtime (dynamic) and hybrid static+runtime column lineage for PostgreSQL 17

Scope: runtime evidence as an oracle for, or complement to, a static column-level lineage analyser whose weak layer is PL/pgSQL and psql-script semantics.

Some facts below were **verified locally** on 2026-10-16. I ran a throwaway `postgres:17` container (reported `PostgreSQL 17.11 (Debian 17.11-1.pgdg13+2)`) with `shared_preload_libraries=auto_explain,pg_stat_statements`, `auto_explain.log_min_duration=0`, `log_nested_statements=on`, `log_verbose=on`, `log_format=json`, `pg_stat_statements.track=all` and `compute_query_id=on`. A second throwaway container ran with `wal_level=logical`.

The test script covered:
- a PL/pgSQL function with a static `INSERT ... SELECT` that uses local variables, plus an IF branch that is never taken;
- `EXECUTE format(...)` and `RETURN QUERY EXECUTE 'SELECT ... ' || quote_ident(..)`;
- a BEFORE trigger doing `NEW.z := NEW.z + 1` and an `INSERT ... VALUES (NEW.id, NEW.z)`;
- a DO block, a `BEGIN ATOMIC` SQL function, `BEGIN; INSERT ...; ROLLBACK;`, `COPY ... FROM STDIN`, and psql `\gset` followed by `:n` substitution.

These results are marked **[Local PG17.11 test]**. Both containers were stopped afterwards. Everything else is cited.

---

## Q1. auto_explain (log_nested_statements, log_verbose, log_format=json): what it logs inside routines, and whether column lineage can be derived from EXPLAIN VERBOSE

### Takeaway
auto_explain with `log_nested_statements=on` logs one plan for every executor-run statement, at any nesting depth. That covers plain SQL in PL/pgSQL functions, procedures, triggers and DO blocks, and EXECUTE'd dynamic SQL, which appears **with its fully built text**. Each entry carries a `CONTEXT` line naming the routine and the PL/pgSQL line. This makes it a strong statement-level oracle ("which SQL actually ran, from where").

It does not log utility statements (COPY FROM, DDL, DO itself). It does not log PL/pgSQL "simple expressions", so `NEW.x := ...` assignments are invisible. Its VERBOSE `Output` lists show expressions over range-table aliases, but PL/pgSQL variables and `NEW.*` fields appear as **folded constants** (custom plans) or `$n` (generic plans). Column lineage from plans alone is therefore partial: good for SELECT/INSERT...SELECT source columns, lossy for anything flowing through variables.

### Cited Findings
- Loading: `LOAD 'auto_explain'` (superuser), or `session_preload_libraries` / `shared_preload_libraries`. All of its parameters are superuser-only — [PG17 docs: auto_explain](https://www.postgresql.org/docs/17/auto-explain.html)
- `auto_explain.log_nested_statements`: "Causes nested statements (statements executed inside a function) to be considered for logging. When it is off, only top-level query plans are logged." Default off — [PG17 docs](https://www.postgresql.org/docs/17/auto-explain.html)
- `log_verbose` is equivalent to `EXPLAIN VERBOSE`. `log_format` accepts text/xml/json/yaml. `log_min_duration=0` logs all plans. `log_parameter_max_length` controls logging of query parameter values (-1 = full, the default) — [PG17 docs](https://www.postgresql.org/docs/17/auto-explain.html)
- `log_analyze` warning: "per-plan-node timing occurs for all statements executed, whether or not they run long enough to actually get logged. This can have an extremely negative impact on performance." For lineage you do not need `log_analyze`. Use `log_verbose` and leave `log_analyze` off, or turn on `log_analyze` with `log_timing=off` only if you want actual row counts — [PG17 docs](https://www.postgresql.org/docs/17/auto-explain.html)
- `sample_rate`: "In case of nested statements, either all will be explained or none." — [PG17 docs](https://www.postgresql.org/docs/17/auto-explain.html)
- Source (REL_17_STABLE): auto_explain installs only `ExecutorStart/Run/Finish/End` hooks and **no ProcessUtility hook**, so utility commands are not explained. Gating macro: `auto_explain_log_min_duration >= 0 && (nesting_level == 0 || auto_explain_log_nested_statements) && current_query_sampled`. Parameters are printed through `ExplainQueryParameters(es, queryDesc->params, auto_explain_log_parameter_max_length)` — [auto_explain.c REL_17_STABLE](https://raw.githubusercontent.com/postgres/postgres/REL_17_STABLE/contrib/auto_explain/auto_explain.c)
- EXPLAIN VERBOSE "includes the output column list for each node in the plan tree, schema-qualify table and function names, always label variables in expressions with their range table alias" — [PG docs: EXPLAIN (current)](https://www.postgresql.org/docs/current/sql-explain.html)

**[Local PG17.11 test] observations:**
- Static SQL inside PL/pgSQL is logged with the **source text as written**, still containing variable names, e.g. `"Query Text": "INSERT INTO tgt(id,x,y) SELECT s.id, s.a + v, upper(s.b) FROM src s WHERE s.a > p"`. It is followed by `CONTEXT: SQL statement "..."` and `PL/pgSQL function f_static(integer) line 4 at SQL statement`. That gives routine and line attribution.
- Dynamic SQL is logged with the **resolved string**. `EXECUTE format('INSERT INTO %I(id,z) SELECT id, a*3 FROM src', tbl)` logged as `INSERT INTO tgt2(id,z) SELECT id, a*3 FROM src`, and `RETURN QUERY EXECUTE 'SELECT z FROM ' || quote_ident(tbl)` logged as `SELECT z FROM tgt2`.
- Plan JSON per node has `Node Type`, `Operation`, `Relation Name`, `Schema`, `Alias`, `Output` and `Filter`, plus a top-level `Query Identifier` because compute_query_id was on. Example:
  - Seq Scan on `src` alias `s` has `"Output": ["s.id", "(s.a + 10)", "upper(s.b)"]` and `"Filter": "(s.a > 5)"`.
  - The `ModifyTable` node (Insert into `public.tgt`) has **no Output list and no target-column names**. Mapping subplan outputs to target columns is positional, against the statement's insert column list, which has to be parsed from the Query Text.
  - For `UPDATE tgt SET y='zz' WHERE id=1` the subplan output is `["'zz'::text", "ctid"]`, again without the target column name.
- **Variables are folded.** In the first 5 calls of `f_static(p)`, the variable `v` appeared as a constant (`s.a + 10`, `s.a + 12`, ...). From the 6th call onward the plan cache switched to a generic plan and it appeared as `(s.a + $3)`. Either way the plan does not say that `$3`/`10` is variable `v` derived from parameter `p`.
- **Trigger NEW fields are folded to constants.** `INSERT INTO audit_t VALUES (NEW.id, NEW.z)` had plan `Result` with `"Output": ["1", "31"]`. The lineage NEW.z → audit_t.v is not recoverable from the plan, only from the text.
- **Trigger assignment `NEW.z := NEW.z + 1` produced no log entry.** PL/pgSQL evaluates it as a simple expression that does not pass through the executor hooks.
- **Ordering.** Nested plans are logged at ExecutorEnd, so inner statements (trigger bodies, function bodies) appear before their calling statement.
- **DO block:** its inner `UPDATE` was logged; the `DO` itself was not, because it is a utility statement.
- **BEGIN ATOMIC SQL function:** the inner statement was logged with `"Query Text": ""` (empty), `"Query Parameters": "$1 = '1'"` and `CONTEXT: SQL function "f_atomic" statement 1`. The plan, `Seq Scan on src, Output: ["a"], Filter: (src.id = $1)`, is the only usable lineage evidence.
- **ROLLBACK:** an INSERT inside `BEGIN ... ROLLBACK` was logged like any other. There is no rollback marker.
- **Not logged:** `COPY tgt FROM STDIN` and the untaken IF branch (`INSERT INTO tgt2 ...` under `IF p > 1000`).
- **psql `\gset`:** psql substitutes variables client-side, so the server log shows `INSERT INTO tgt2 VALUES (1, 1);` with the value already in place.

### Inferences
- auto_explain alone is the best single "what ran" oracle. Uses:
  - (a) Cross-check that every statement the static model attributes to a routine was actually executed, and spot executed statements the static model missed. Join on routine name + PL/pgSQL line number from CONTEXT.
  - (b) Get the resolved text of dynamic SQL, then feed it to the existing static SQL lineage parser. This is the key hybrid win for `EXECUTE`/`RETURN QUERY EXECUTE`.
- Column lineage derivation: parse `Output` expressions of scan/join nodes. They are alias-qualified Vars, which you can resolve via the `Alias`→`Relation Name` map of scan nodes. Then map positionally to target columns from the statement text. Expect gaps:
  - Var references through subquery/CTE scans need tracing the plan tree.
  - Hash/Sort nodes reference child outputs.
  - Folded PL/pgSQL variables lose their origin.
  - `ModifyTable` lacks target column names.
- Because static SQL text keeps PL/pgSQL variable names, the static analyser should use the logged text + CONTEXT line as a key, not the plan.
- auto_explain's ordering (inner before outer) plus CONTEXT stacks (multi-line CONTEXT for deeper nesting) lets you rebuild the call tree.

### Gaps
- I found no published project or blog that derives column lineage from auto_explain / EXPLAIN VERBOSE JSON. Searches for "auto_explain lineage" and "EXPLAIN VERBOSE column lineage" returned only performance-tuning material ([search results incl. pgMustard](https://www.pgmustard.com/auto-explain)).
- Not tested: procedures with COMMIT inside, cursors/FOR-IN-query loops (each FETCH batch), `RETURN NEXT`, OUT-param functions in LATERAL, set-returning SQL functions that get inlined (an inlined SQL function would probably appear inside the caller's plan rather than as a nested statement). These need a local test.
- Overhead of `log_min_duration=0` + verbose JSON was not measured. Expect log volume roughly proportional to statements × plan size.

---

## Q2. pg_stat_statements (track=all) and query-log lineage (OpenMetadata, DataHub, tokern): what is lost

### Takeaway
`pg_stat_statements.track=all` does record nested statements, including resolved dynamic SQL, with `toplevel=false`. But it **normalises constants to `$n`**, deduplicates by query shape, has no call-site, line, order or rollback information, and evicts entries (default max 5000). It is a set-of-shapes oracle, weaker than auto_explain. OpenMetadata builds its Postgres usage/lineage on it and documents these limits.

### Cited Findings
- `track`: "`top` to track top-level statements ..., `all` to also track nested statements (such as statements invoked within functions)". The `toplevel` column is "True if the query was executed as a top-level statement" — [PG17 docs: pg_stat_statements](https://www.postgresql.org/docs/17/pgstatstatements.html)
- Normalisation: constants are "replaced by a parameter symbol, such as `$1`", and "the rest of the query text is that of the first query that had the particular `queryid`". Queries are combined when they have "identical query structures according to an internal hash calculation" — [PG17 docs](https://www.postgresql.org/docs/17/pgstatstatements.html)
- `pg_stat_statements.max` defaults to 5000. "Information about the least-executed statements is discarded." `track_utility` (default on) covers non-DML commands — [PG17 docs](https://www.postgresql.org/docs/17/pgstatstatements.html)
- OpenMetadata: "For the usage and lineage workflow, OpenMetadata relies on the pg_stat_statements extension to read query history". It "is **not a query log**, it is a fixed-size, in-memory statistics collector". "Queries are deduplicated by shape, literal values are replaced with placeholders". It recommends `pg_stat_statements.track = 'all'` to capture queries inside procedures. The connector advertises column-level lineage, which it gets by parsing query text — [OpenMetadata Postgres connector docs](https://docs.open-metadata.org/latest/connectors/database/postgres)
- Other query-history lineage tools on Postgres: tokern/data-lineage ("Generate and Visualize Data Lineage from query history", Postgres/Redshift/Snowflake) — [GitHub tokern/data-lineage](https://github.com/tokern/data-lineage). contiamo/datahub-postgres-lineage covers view lineage only — [GitHub](https://github.com/contiamo/datahub-postgres-lineage). DataHub Postgres source — [DataHub docs](https://docs.datahub.com/docs/generated/ingestion/sources/postgres)
- **[Local PG17.11 test]** With `track=all`, nested entries appeared with `toplevel=f`:
  - `INSERT INTO tgt(id,x,y) SELECT s.id, s.a + v, upper(s.b) FROM src s WHERE s.a > p` (PL/pgSQL variable names kept);
  - `INSERT INTO tgt2(id,z) SELECT id, a*$1 FROM src` (dynamic SQL resolved, then normalised `3`→`$1`);
  - `SELECT z FROM tgt2`;
  - `UPDATE tgt SET y = $1 WHERE id = $2` (from the DO block);
  - `INSERT INTO audit_t VALUES (NEW.id, NEW.z)` (trigger, calls=2).

  Top-level entries included `COPY tgt FROM STDIN`, the DO text, and `SELECT f_static($1)`. The inner statement of the `BEGIN ATOMIC` function did **not** appear as an entry.

### Inferences
- pg_stat_statements does not lose function bodies when `track=all`, contrary to common belief. It loses:
  - literals (a problem for lineage where a constant decides the table name? No: identifiers are not normalised, only constants);
  - the calling routine and line;
  - execution order and per-execution multiplicity beyond `calls`;
  - transaction outcome;
  - entries evicted beyond `max`.
- For a test-corpus oracle, take a pre/post snapshot (`pg_stat_statements_reset()` before `run_all.sh`). That gives a cheap "set of distinct statements executed" with row counts per shape. Use it as a secondary check alongside auto_explain.

### Gaps
- Not verified how OpenMetadata maps `toplevel=false` statements back to the calling procedure. Its docs do not say.

---

## Q3. Server logging and audit/monitoring extensions: log_statement, debug_print_*, pgaudit, pg_stat_monitor, pg_tracing, pg_query_state

### Takeaway
- `log_statement=all` logs only **client-sent** statements, not nested ones.
- `debug_print_parse/rewritten/plan` do print nested statements' Query trees. These include `resorigtbl/resorigcol`, but only for plain column references, and only when a statement is (re)parsed. The plan cache means each PL/pgSQL statement prints once per session. The output is huge and unstable.
- **pgaudit** session logging records statements *and substatements*, including dynamic SQL text inside DO/functions, with STATEMENT_ID/SUBSTATEMENT_ID, and `log_relation` gives a per-relation entry. It is table-level, not column-level, and it is **not in the official postgres:17 image**.
- pg_stat_monitor (PG17 supported) is a pg_stat_statements alternative with optional actual parameter values and relations per query.
- pg_tracing's README lists PG14–16 only.

### Cited Findings
- `log_statement` values none/ddl/mod/all. "Statements that contain simple syntax errors are not logged even by the log_statement = all setting". The `%Q` query-id escape "always reports a zero identifier for lines output by log_statement" — [PG17 docs: runtime-config-logging](https://www.postgresql.org/docs/17/runtime-config-logging.html)
- In PG17 source, the log_statement check is made in `exec_simple_query` (client protocol path), so statements run via SPI inside functions are not logged by it — [postgres.c REL_17_STABLE](https://raw.githubusercontent.com/postgres/postgres/REL_17_STABLE/src/backend/tcop/postgres.c). The docs do not state this explicitly ([PG17 docs](https://www.postgresql.org/docs/17/runtime-config-logging.html)), so this is a source-code reading.
- `debug_print_parse/rewritten/plan` "print the resulting parse tree, the query rewriter output, or the execution plan for each executed query" at LOG level — [PG17 docs](https://www.postgresql.org/docs/17/runtime-config-logging.html). In source, `Debug_print_parse` and `Debug_print_rewritten` are checked in `pg_rewrite_query()` and `Debug_print_plan` in `pg_plan_query()`. Those functions are also used by SPI/plancache, which is why nested statements print — [postgres.c REL_17_STABLE](https://raw.githubusercontent.com/postgres/postgres/REL_17_STABLE/src/backend/tcop/postgres.c)
- **[Local PG17.11 test]**
  - `SELECT s.a + 1 AS k, s.b FROM src s` with `debug_print_parse=on` printed TargetEntries `:resname k` and `:resname b`. Only `b` carried a non-zero origin, `:resorigtbl 16384 :resorigcol 3` (16384 = `src`). The expression column has no origin.
  - Calling `f_static()` printed parse trees for the nested statements on the first call only. Three calls in one session printed 6 parse trees, versus 5 for a single call. That is consistent with plan-cache reuse.
  - The untaken-branch statement was never parsed.
- pgaudit:
  - Session audit classes include "FUNCTION: Function calls and DO blocks".
  - Example: for a DO block that runs `EXECUTE 'CREATE TABLE import' || 'ant_table (id INT)'`, "not only is the DO block logged, but substatement 2 contains the full text of the CREATE TABLE".
  - Record fields: AUDIT_TYPE, STATEMENT_ID, SUBSTATEMENT_ID, CLASS, COMMAND, OBJECT_TYPE, OBJECT_NAME, STATEMENT (+ PARAMETER).
  - `pgaudit.log_relation` creates "a separate log entry for each relation (TABLE, VIEW, etc.) referenced in a SELECT or DML statement".
  - Object audit is driven by grants to an audit role, and column grants (e.g. `grant select (password) on public.account to auditor`) restrict *which statements* get logged. The log shows only the relation name, not the columns.
  - pgAudit v17.x targets PG17. Licence: PostgreSQL License. It must be in `shared_preload_libraries`.

  Sources: [GitHub pgaudit/pgaudit](https://github.com/pgaudit/pgaudit) and [README](https://raw.githubusercontent.com/pgaudit/pgaudit/main/README.md)
- pgaudit caveat: "a statement is logged when it executes, so an entry may be written even if its transaction later rolls back". Logging is best-effort, not transactional — [pgaudit README](https://raw.githubusercontent.com/pgaudit/pgaudit/main/README.md)
- **[Local PG17.11 test]** The official `postgres:17` image ships no pgaudit, pg_stat_monitor, pg_tracing or wal2json. Only contrib (auto_explain, pg_stat_statements, test_decoding, pgoutput) is present. pgaudit would need an extra install step, e.g. PGDG `postgresql-17-pgaudit` (package name not verified here).
- pg_stat_monitor:
  - Supports PostgreSQL 14+, "tested compatibility for versions 14, 15, 16, 17, and 18".
  - Can show queries with placeholders or actual parameter values.
  - Has "Tables Access Statistics for a Statement".
  - PostgreSQL licence.

  Source: [GitHub percona/pg_stat_monitor](https://github.com/percona/pg_stat_monitor)
- pg_tracing (DataDog):
  - Produces spans for planner/executor, plan nodes, "Nested queries: Statements invoked within another statement (like a function)" and BEFORE/AFTER triggers.
  - Query text is shown with `$n` placeholders.
  - The README says it "only supports PostgreSQL 14, 15 and 16 for the moment" and is "still in early development and may be unstable".
  - MIT licence. Needs `shared_preload_libraries`.

  Source: [GitHub DataDog/pg_tracing](https://github.com/DataDog/pg_tracing)

### Inferences
- For column information, debug_print_* Query trees are the richest runtime artefact. They contain full Var nodes (varno/varattno) for every expression, not just `resorigtbl`. But they are post-parse trees of the *same text* the static analyser already has, so they are useful only for dynamic SQL, and auto_explain already gives that text more cheaply. They are emitted only on (re)parse and are very large. Not recommended except as a debugging aid.
- pgaudit adds little over auto_explain for this purpose. It does record utility statements and the DO/function call itself, which auto_explain misses. It could fill the "COPY/DDL/CALL happened" gap, but `log_statement=all` covers top-level utility statements anyway.

### Gaps
- pg_query_state (Postgres Pro): not researched. My understanding is that it needs core patches and shows the plan of a running query; I have no citation, so treat it as unverified.
- Whether pg_tracing has since added PG17 support: the README I fetched says 14–16, and I did not check releases.
- pg_stat_monitor nested-statement behaviour was not documented on the README page I read.

---

## Q4. Executor-hook / OpenLineage / "pg_lineage" / eBPF approaches that capture column-level data flow at execution time

### Takeaway
I found **no maintained PostgreSQL extension that emits column-level lineage from executor or planner hooks**, no OpenLineage PostgreSQL server-side integration, and no eBPF-based column lineage for Postgres. Runtime lineage in OpenLineage comes from orchestrators and engines (Spark, Airflow, dbt). Its column lineage for SQL comes from static parsing of the SQL text.

### Cited Findings
- No "pg_lineage" extension surfaced. Search results gave only DataHub/tokern/contiamo query-history or view-lineage tools — [GitHub topics: data-lineage](https://github.com/topics/data-lineage?o=asc&s=updated), [tokern/data-lineage](https://github.com/tokern/data-lineage)
- OpenLineage column lineage arrived via the Spark integration (0.9.0) as the `columnLineage` dataset facet — [OpenLineage blog: The Current State of Column-level Lineage](https://openlineage.io/blog/column-lineage/), [Column Lineage facet spec](https://openlineage.io/docs/spec/facets/dataset-facets/column_lineage_facet/)
- An academic prototype "Lineage implementation in PostgreSQL" (UZH master project report, Andrin Betschart) exists — [UZH PDF](https://www.ifi.uzh.ch/dam/jcr:ffffffff-96c1-007c-ffff-ffff8e58fa74/ReportLeimerBetschartMScPA.pdf). I did not read its content or check its PG version.
- PgCuckoo shows the planner hook can inject externally built plans, which proves plan-tree access via hooks is practical — [GitHub kryonix/pg_cuckoo](https://github.com/kryonix/pg_cuckoo)

### Inferences
- A custom ~300-line C extension is feasible. It would use an `ExecutorStart_hook` (like auto_explain) and walk `queryDesc->plannedstmt`: rtable (relid, inh), each plan node's targetlist Vars, `ModifyTable.resultRelations`, and the INSERT/UPDATE target attnos from the `PlannedStmt`/subplan `TargetEntry.resno`. It would emit JSON with real attnums instead of deparsed text, which fixes auto_explain's missing target-column names.
- Even so, it inherits the same blind spots: PL/pgSQL variable folding, simple expressions, NEW/OLD values, COPY. Given the cost of writing and maintaining C, parsing auto_explain JSON + Query Text is the pragmatic option.

### Gaps
- No evidence found either way for eBPF/uprobe-based lineage on Postgres. Marquez/Egeria PG collectors: none found. Egeria not researched further.

---

## Q5. Provenance systems for PostgreSQL (ProvSQL, Perm, GProM, Smoke, Titian, DBTaint): capabilities and PG17 viability

### Takeaway
- **ProvSQL** is the only actively maintained PostgreSQL provenance extension (MIT). It supports semiring/how-provenance, an experimental **where-provenance** (cell/column level), and data-modification tracking. It claims PostgreSQL ≥ 11, but PG17 was not verified here. It works on queries over provenance-enabled tables, not on PL/pgSQL control flow.
- **Perm** (PostgreSQL 8.x fork) and **GProM** (middleware, Oracle-centred, transaction reenactment) are research systems, not usable on PG17 out of the box.
- **Smoke** and **Titian** are not PostgreSQL.
- **DBTaint** (PostgreSQL taint tracking via composite types) is the closest concept to value-level information flow.

### Cited Findings
- ProvSQL:
  - Requires "PostgreSQL ≥ 11". MIT License.
  - Supports m-semiring provenance, probabilities, Shapley values, and a "Where mode that hover-highlights the source cells".
  - Build prerequisites: C++17, PG dev headers, uuid-ossp, Boost.

  Source: [GitHub PierreSenellart/provsql](https://github.com/PierreSenellart/provsql)
- ProvSQL docs list "Data Modification Tracking" (INSERT/UPDATE/DELETE), "Temporal Features", "Undoing Updates", aggregation/GROUP BY/HAVING, CTEs, window functions, LATERAL, set operations, and an "Unsupported SQL Features" section — [ProvSQL docs](https://provsql.org/docs/)
- ProvSQL where-provenance:
  - Enabled with `SET provsql.provenance = 'where'`. It identifies "the specific cell (table, row, and column) in the database from which [each value] originated", via `where_provenance(provenance())`.
  - Adds `project` and `eq` gates.
  - It is "an experimental feature": aggregates' output cells "may not be annotated correctly". "Where-provenance adds overhead to every query."

  Source: [ProvSQL docs: where-provenance](https://provsql.org/docs/user/where-provenance.html)
- Perm "is implemented on top of PostgreSQL extending its SQL dialect with provenance features that are implemented as query rewrites" (2009 era) — [Perm overview](https://permdbms.sourceforge.net/), [GA09 paper](https://permdbms.sourceforge.net/pdfs/GA09.pdf)
- GProM:
  - Apache-2.0. Offers "Retroactive provenance capture for transactions using reenactment", including UPDATEs. Row-level.
  - The README says it "can produce code for multiple backends (currently Oracle)", with SQLite used in examples.

  Source: [GitHub IITDBGroup/gprom](https://github.com/IITDBGroup/gprom). PostgreSQL backend support is not confirmed by the page I read.
- Smoke: Psallidas & Wu (VLDB 2018). An in-memory engine that integrates lineage capture into physical operators, not PostgreSQL — [arXiv 1801.07237](https://arxiv.org/html/1801.07237), [The Morning Paper summary](https://blog.acolyer.org/2018/09/24/smoke-fine-grained-lineage-at-interactive-speed/)
- Titian: Interlandi et al. (UCLA). Data provenance in Apache Spark with capture overheads "rarely exceeding 30%" — [Titian summary](https://rmarcus.info/dbscholar/papers/he1a99e1a18687d16), [GitHub UCLA-SEAL/Titian](https://github.com/UCLA-SEAL/Titian)
- DBTaint (Davis & Chen, USENIX WebApps 2010):
  - "extending database datatypes to maintain and propagate taint bits on each value". Stores `(value, taint_value)` composite types in PostgreSQL.
  - Reports less than 10–15% overhead.

  Sources: [USENIX page](https://www.usenix.org/conference/webapps-10/dbtaint-cross-application-information-flow-tracking-databases), [paper](https://www.cs.ucdavis.edu/~hchen/paper/webapps2010.pdf)
- "Tracing Lineage Beyond Relational Operators" (Mingwu Zhang, Xiangyu Zhang et al., Purdue) uses dynamic program analysis to trace lineage through non-relational/user-defined operators — [PDF](https://www.cs.purdue.edu/homes/sunil/pub/BlackBox.pdf). The PDF could not be text-extracted, so venue (believed VLDB 2007) and details are unverified.

### Inferences
- ProvSQL could serve as a *query-level* oracle for column lineage of individual SELECT/INSERT...SELECT statements via where-provenance. It is experimental, weak on aggregates/expressions, and it does not track through PL/pgSQL variables. Its value for this project's weak layer (routine semantics) is low.
- The DBTaint idea (carry a taint tag with each value) is in principle the "right" runtime column-lineage mechanism, but it needs type and function rewriting of the whole corpus. Perturbation (Q7) approximates the same thing without changing types.

### Gaps
- No PG17 build of ProvSQL was attempted. The README says ≥ 11, and CI coverage for 17 was not confirmed.
- "pg_provenance": no project by that name was found.
- I found no 2020–2026 PG-native provenance system beyond ProvSQL.

---

## Q6. Logical decoding / CDC (pgoutput, test_decoding, wal2json, Debezium) as a table-level oracle

### Takeaway
Logical decoding is a reliable **table-level "was this table actually written, and committed?" oracle**. Rolled-back work is absent, and dynamic SQL inside DO/functions is captured. It gives row images, not which columns were assigned or why. Unlogged and temp tables are invisible, and DDL is not decoded.

### Cited Findings
- Logical decoding decodes WAL "changes on a storage level" into tuples. It needs `wal_level=logical`. Changes are streamed in the order they were made on the origin — [PG17 docs: logical decoding concepts](https://www.postgresql.org/docs/17/logicaldecoding-explanation.html)
- **[Local PG17.11 test, test_decoding]**
  - `INSERT` → `table public.t: INSERT: id[integer]:1 a[integer]:1 b[text]:'x'`.
  - `BEGIN; INSERT ...; ROLLBACK;` → nothing emitted.
  - A no-op `UPDATE t SET a = a` → a full new row image with no "changed columns" indicator.
  - A zero-row UPDATE → nothing.
  - INSERT into an UNLOGGED table and `CREATE TEMP TABLE ... AS` → empty BEGIN/COMMIT pairs with no rows.
  - A DO block running `EXECUTE 'UPDATE t SET b = ''w'''` → `UPDATE: ... b[text]:'w'`, captured.
  - `pg_create_logical_replication_slot` failed when run in the same transaction as writes ("cannot create logical replication slot in transaction that has performed writes"), so create it in a separate statement before running the corpus.

### Inferences
- Use it as the ground truth for "target tables written by a committed run". Static lineage that claims a write to table T can be checked against it.
- Column-level: compare old vs new images. That requires `REPLICA IDENTITY FULL` for old values (stated from general knowledge, not verified here), or snapshot diffing. The perturbation method in Q7 gives column-level evidence more directly.
- wal2json/Debezium add nothing over test_decoding/pgoutput for this use, and need extra install.

### Gaps
- wal2json and Debezium PG17 compatibility were not checked. The test_decoding and pgoutput built into PG17 are sufficient.

---

## Q7. Hybrid static+runtime approaches and PERTURBATION (mutation) as a ground-truth generator

### Takeaway
Perturbation-based column lineage works like this: run the corpus, mutate one source column, re-run, and diff the target columns. I found **no published description of it for SQL/PL/pgSQL lineage**. The closest analogues are:
- perturbation-based explanation in ML;
- mutation analysis of SQL/data-quality tests;
- taint tracking (DBTaint);
- black-box lineage learning, which notes that full black-box lineage is impossible without exhaustive input pairs.

It is feasible and cheap on small corpora. It yields *sound-but-incomplete* evidence: a detected change proves a dependency, while no change proves nothing. It needs careful handling of filters, aggregates, nondeterminism and control flow.

### Cited Findings
- Perturbation methods in ML "explore DNN models by perturbing their input and observing changes in the output" — [Survey, Pattern Recognition Letters 2021](https://www.sciencedirect.com/science/article/pii/S0167865521002440)
- "If an operation is a complete black box, it is theoretically impossible to determine the lineage unless every possible input-output container pair is generated". Lineage "can be learned over black-box operations with reasonable uncertainty" — [Learning Lineage Constraints for Data Science Operations (arXiv 2506.18252)](https://arxiv.org/html/2506.18252)
- Retrospective lineage inference for data workflows — [VLDB vol.14 p2795 (Rehman et al.)](https://vldb.org/pvldb/vol14/p2795-rehman.pdf). Schema-level data-dependency annotation validation/inference — [arXiv 1807.09899](https://arxiv.org/pdf/1807.09899). Neither paper was read in full.
- DBTaint value-level taint propagation in PostgreSQL — [USENIX WebApps 2010](https://www.usenix.org/conference/webapps-10/dbtaint-cross-application-information-flow-tracking-databases)
- Static column lineage systems that do not execute queries (contrast): LINEAGEX notes the need "to curate lineage information from static analysis of queries without executing the queries" — [LINEAGEX arXiv 2505.23133](https://arxiv.org/pdf/2505.23133)
- Industry view: DataHub argues ETL lineage should be "column-level, cross-platform, and captured at runtime" — [DataHub blog](https://datahub.com/blog/what-is-data-lineage-in-etl/). This is an opinion piece.

### Inferences (design and limits of perturbation for this project)
- **Procedure:**
  1. Load schema+seed into a template DB.
  2. Run the baseline: `CREATE DATABASE run0 TEMPLATE seed`, then `run_all.sh`, then dump every target table sorted by key or all columns.
  3. For each source column c: clone the template, apply a mutation to c, re-run, dump, and diff per (table, column).
  4. A column whose values differ is a dependent of c.

  Cost is (#source columns + 1) × corpus runtime. Cloning a template DB is fast for small seeds.
- **Mutation design** (to separate value flows from filter/control flows):
  - **(a) Value-preserving-predicate mutation:** apply an injective transform that keeps the order and equality structure, e.g. `x → x*1000+7` for ints, or add a suffix for text. Filters and joins then behave the same, so a changed target value implies a value flow.
  - **(b) Predicate-breaking mutation:** e.g. NULL or a sentinel. Row-count or membership changes imply filter/influence flows.
  - **(c) Sentinel values**, such as unique strings or primes. Search for them verbatim in targets to detect direct copies (where-provenance) without diffing.
- **Limits:**
  - Zero-effect cases: rows filtered out, columns overwritten later, count(*)/EXISTS that ignore values, CASE branches not reached by seed data, empty tables, and functions that are not injective (e.g. `x/1000`, `date_trunc`, `bool` casts) can mask a change. These are all false negatives.
  - Seed coverage bounds everything, just as with auto_explain (untaken branches are invisible).
  - Nondeterminism (`now()`, `random()`, sequences/serials, `ctid` order, ORDER BY-less LIMIT, hash aggregation order) produces false positives. Freeze them: fixed seeds, `SET timezone`, compare as multisets keyed on stable columns, and mask serial ids.
  - Mutation can change control flow: a branch or dynamic table name depends on data. The effect is then real but indirect (influence), which conflates edge types.
  - Constraint violations: mutations may break FKs/CHECKs and abort the run. Mutate consistently across FK pairs, or treat the abort itself as evidence.
  - Multi-column dependencies (e.g. `a+b` where a mutation in a cancels) are unlikely with injective transforms, but possible.
- **Positioning:** perturbation gives sound *positive* evidence of column→column dependency for the executed paths. Static analysis gives coverage of unexecuted paths. A hybrid is the natural combination:
  1. Treat static edges confirmed by perturbation as verified.
  2. Treat perturbation-only edges as "static model missed something". Locate it via auto_explain CONTEXT.
  3. Treat static-only edges as either unexercised paths (check auto_explain to see whether the statement ran) or masked mutations.

### Gaps
- I found no paper or tool that explicitly describes "perturb a source column, re-run, diff targets" as column-lineage ground truth for SQL or stored procedures. "Causal lineage testing" and "data-flow testing via fuzzing" searches gave nothing specific.
- SQL mutation analysis literature (e.g. SQLMutation / Tuya et al.) was surfaced only indirectly by search and not read. No citation is given.

---

## Q8. Practical recipe on PG17 Docker: capturing every executed statement (nested and dynamic, with line attribution) and per-statement column lineage

### Takeaway
Combine four sources, all available in the stock `postgres:17` image:
1. auto_explain (nested, verbose, JSON, `log_min_duration=0`) for every executor-level statement with resolved dynamic text and PL/pgSQL `CONTEXT` line;
2. `log_statement=all` (or psql echo) for top-level utility statements, COPY and psql-substituted text;
3. pg_stat_statements `track=all` as a dedup and count cross-check;
4. test_decoding for committed table writes.

Then derive column lineage by feeding each logged Query Text to the existing static SQL parser, which handles dynamic SQL, and use perturbation runs for column-level ground truth.

### Cited Findings (settings and their documented behaviour)
- auto_explain settings and superuser/preload requirements — [PG17 auto_explain](https://www.postgresql.org/docs/17/auto-explain.html)
- `log_line_prefix` `%Q` gives the query id when `compute_query_id` is on, but it is zero for `log_statement` lines — [PG17 logging](https://www.postgresql.org/docs/17/runtime-config-logging.html)
- `pg_stat_statements.track=all`, `toplevel` — [PG17 pg_stat_statements](https://www.postgresql.org/docs/17/pgstatstatements.html)
- **[Local PG17.11 test]** The configuration that worked: `docker run postgres:17 -c shared_preload_libraries=auto_explain,pg_stat_statements -c auto_explain.log_min_duration=0 -c auto_explain.log_nested_statements=on -c auto_explain.log_verbose=on -c auto_explain.log_format=json -c pg_stat_statements.track=all -c compute_query_id=on`, then `CREATE EXTENSION pg_stat_statements`. On Git-Bash/Windows, `MSYS_NO_PATHCONV=1` was needed for `docker exec ... psql -f /tmp/x.sql`.

### Inferences (recipe and pitfalls)
- **Server config** (add to the above):
  - `log_statement=all` and `log_line_prefix='%m [%p] %Q %a '`;
  - `log_destination=jsonlog` with `logging_collector=on` for machine-parseable logs (PG15+ feature; not tested here);
  - `wal_level=logical` plus a test_decoding slot created *before* the run.
- **Line attribution for psql scripts:**
  - Run each script with `psql -X -v ON_ERROR_STOP=1 -e` (echo) or `-L session.log`, so client-side text after `\gset`/`:var` substitution is recorded.
  - Optionally set `application_name` per file (`PGAPPNAME=file.sql`) so `%a` tags the server log lines.
  - psql does not send script line numbers. Map statements back by order of appearance, or inject `\echo`/`SELECT set_config('application_name', 'file:line', false)` markers. Markers produce their own log lines, so filter them out.
- **Line attribution inside routines:** parse the auto_explain `CONTEXT` lines (`PL/pgSQL function f(args) line N at SQL statement`, `SQL function "f" statement K`, `EXECUTE`). Multi-level stacks give the call chain.
- **Column lineage per statement:**
  - Preferred: logged Query Text → existing static parser. For PL/pgSQL statements the text still contains variable names, so the static routine model must bind them. For EXECUTE'd SQL the text is fully resolved, so the problem disappears.
  - Secondary: plan `Output` lists for alias.column source references, plus `Relation Name`/`Alias` maps, mapped positionally to INSERT column lists.
  - For BEGIN ATOMIC functions, where the Query Text is empty, use the plan or `pg_get_functiondef`/`prosqlbody`.
- **Pitfalls verified locally:**
  - auto_explain logs rolled-back statements. Cross-check with test_decoding to decide whether a write took effect.
  - COPY FROM STDIN, DDL, DO/CALL are utility statements, invisible to auto_explain. Take them from `log_statement=all` or the psql echo.
  - Trigger `NEW.x := ...` assignments are invisible. Trigger statements that use NEW appear with folded constants.
  - Plan caching: after 5 executions PL/pgSQL switched to generic plans (`$n` instead of folded constants). Both forms must be handled. Setting `plan_cache_mode=force_generic_plan` or `force_custom_plan` makes the behaviour uniform; force_generic keeps `$n` placeholders, which hide constant values but are stable.
  - debug_print_* fires only on (re)parse, so nested trees print once per session.
  - Branches not taken and empty-data loops produce no runtime evidence. Runtime evidence covers only what the seed exercises.
  - pg_stat_statements normalises constants and merges repeats.
  - Nested plans are logged inner-first (at ExecutorEnd).

### Gaps
- `log_destination=jsonlog` behaviour combined with auto_explain JSON (a JSON plan nested inside a JSON log message) was not tested.
- Procedures with transaction control (`COMMIT` inside CALL), cursors, `RETURN NEXT` and `FOR ... IN EXECUTE` loops were not exercised in the local test. Their logging behaviour should be confirmed with one more test script before relying on it.
- No overhead measurements were taken. For small blind-test corpora, overhead is unlikely to matter, but this is unmeasured.
