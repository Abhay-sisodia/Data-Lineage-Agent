# PostgreSQL-native facilities and program-analysis tooling for routine semantics (column lineage)

Method note: besides web sources, many claims below were **verified by local experiment on 2026-10-16** against
PostgreSQL 17.11 (Debian, `postgres:17` Docker image; the project's `lineage-postgres` container, in a throwaway
`research_scratch` database that was dropped afterwards), a throwaway `postgres:17` container with
`postgresql-17-plpgsql-check` installed from PGDG apt, and the project venv's pglast 8.5 / psycopg 3.3.6.
These are tagged **[local experiment]** and are paired with the doc/source page that defines the facility. Scripts were in the
session scratchpad (t1..t10); nothing was left in the project.

---

## Q1. libpg_query `pg_query_parse_plpgsql` / pglast `parse_plpgsql`: what is exposed, what is missing

### Takeaway
The PL/pgSQL JSON gives a faithful statement tree (`PLpgSQL_stmt_*`), a flat `datums` array (vars, recs, rows, recfields) with **textual** type names only, INTO targets as `PLpgSQL_row` / `PLpgSQL_rec` / `PLpgSQL_recfield` references by `varno`, and embedded SQL as raw text (`PLpgSQL_expr.query` + `parseMode`). It runs with **no catalog**, so `%TYPE`/`%ROWTYPE` are not resolved, and, critically, **field assignment to a `%ROWTYPE` variable (`r.id := ...`) fails to parse**. Some fields you need (`retvarno` of RETURN NEXT, `out_param_varno`, `fn_rettype`/`fn_retset`, `rectypeid`, type OIDs) are not emitted.

### Cited Findings
- The JSON emitter writes, per datum: `PLpgSQL_var` → `refname, lineno, datatype, isconst, notnull, default_val, cursor_explicit_expr, cursor_explicit_argrow, cursor_options`; `PLpgSQL_rec` → only `refname, dno, lineno` (no `rectypeid`); `PLpgSQL_recfield` → `fieldname, recparentno`; `PLpgSQL_type` → only `typname` (no `typoid`); `PLpgSQL_row` → `refname, lineno, fields[{name,varno}]`; `PLpgSQL_expr` → `query, parseMode`; block → `lineno, label, body, exceptions`. — [libpg_query pg_query_json_plpgsql.c (17-latest)](https://github.com/pganalyze/libpg_query/blob/17-latest/src/pg_query_json_plpgsql.c)
- `dump_return_next` writes only `lineno` and `expr` — **not `retvarno`**; `dump_function` writes `new_varno` and `old_varno` but **not `out_param_varno`, `fn_rettype`, `fn_retset`**; unknown statement types hit `elog(ERROR, "unrecognized cmd_type")`. — [pg_query_json_plpgsql.c](https://github.com/pganalyze/libpg_query/blob/17-latest/src/pg_query_json_plpgsql.c)
- Catalog-free compilation: it walks the parse tree collecting every `CreateFunctionStmt` and `DoStmt` (so multi-function input and DO blocks work); detects triggers by the literal return type name `trigger` (then creates NEW/OLD rec datums); builds a datum per declared parameter from `stmt->parameters`, tracking OUT/INOUT modes into `out_param_varno`; types are built with `plpgsql_build_datatype(UNKNOWNOID, ...)`, i.e. no catalog lookup. — [libpg_query pg_query_parse_plpgsql.c](https://github.com/pganalyze/libpg_query/blob/17-latest/src/pg_query_parse_plpgsql.c)
- [local experiment] pglast 8.5 `parse_plpgsql(sql)` is literally `loads(parse_plpgsql_json(statement))` (pglast/__init__.py:156), i.e. the same libpg_query JSON as a Python list of `{"PLpgSQL_function": {...}}`. — [pglast repo](https://github.com/lelit/pglast)
- [local experiment] Per-form parse results with pglast 8.5 (PG17 libpg_query):
  - `r tgt%ROWTYPE; ... r.id := 1;` → **ParseError `"r.id" is not a known variable`** (one of the forms that broke the project).
  - `r tgt%ROWTYPE; ... RETURN NEXT r;` → OK. `r record; r.id := 1;` → OK. `v src.amt%TYPE;` → OK, datum type is the literal string `"typname": "src.amt%TYPE"` (unresolved).
  - `NEW.x := 1` in a non-trigger function → ParseError `"new.x" is not a known variable`; in a `RETURNS trigger` function → OK.
  - DO block, `CREATE PROCEDURE ... (INOUT a int)` → OK.
  — [pg_query_parse_plpgsql.c](https://github.com/pganalyze/libpg_query/blob/17-latest/src/pg_query_parse_plpgsql.c)
- [local experiment] Concrete JSON shapes observed (PG17 libpg_query via pglast 8.5):
  - OUT/INOUT params: `f(tbl text, INOUT io int, OUT o2 text)` yields datums `PLpgSQL_var tbl/io/o2` plus a synthetic `PLpgSQL_row {"refname":"(unnamed row)","lineno":-1,"fields":[{"name":"io","varno":1},{"name":"o2","varno":2}]}` — the OUT-row exists as a datum but the function node does not say it is the out-param row (no `out_param_varno`).
  - `SELECT id, amt INTO v1, v2 FROM src ...` → `PLpgSQL_stmt_execsql {"sqlstmt":{"PLpgSQL_expr":{"query":"SELECT id, amt             FROM src WHERE id = 1","parseMode":0}},"into":true,"target":{"PLpgSQL_row":{"fields":[{"name":"v1","varno":6},{"name":"v2","varno":7}]}}}` — the INTO clause is blanked out with spaces in the query text, and the target is a positional row of varnos.
  - `v1 := v2 * 2` → `PLpgSQL_stmt_assign {"varno":6,"expr":{"query":"v1 := v2 * 2","parseMode":3}}` (assignment expression includes the LHS; parseMode 3 = RAW_PARSE_PLPGSQL_ASSIGN1).
  - `RETURN QUERY EXECUTE format('SELECT id FROM %I', tbl) USING 0` → `PLpgSQL_stmt_return_query {"dynquery":{"PLpgSQL_expr":{"query":"format('SELECT id FROM %I', tbl)","parseMode":2}},"params":[{"PLpgSQL_expr":{"query":"0"}}]}` — the dynamic query is an *expression* to be evaluated, not SQL.
  - `EXECUTE 'select 1' INTO v1` → `PLpgSQL_stmt_dynexecute {"query":{...},"into":true,"target":{"PLpgSQL_row":...}}`.
  - `FETCH c INTO r` → `PLpgSQL_stmt_fetch {"target":{"PLpgSQL_rec":{"refname":"r","dno":5}},"curvar":8,"direction":0,"how_many":1}`.
  - `RETURN NEXT;` with OUT params → `{"PLpgSQL_stmt_return_next":{"lineno":9}}` (no indication it returns the OUT row).
  - Trigger: datums `[PLpgSQL_rec new (dno 0), PLpgSQL_rec old (dno 1), tg_name, tg_when, tg_level, tg_op, tg_relid, tg_relname, tg_table_name, tg_table_schema, tg_nargs, tg_argv (with "promise" codes), found, PLpgSQL_recfield {"fieldname":"label"} (recparentno 0 = NEW, omitted because zero), PLpgSQL_row {"fields":[{"name":"new.label","varno":13}]}]`; `NEW.label := upper(NEW.label)` → `PLpgSQL_stmt_assign {"varno":13, "expr":{"query":"NEW.label := upper(NEW.label)","parseMode":4}}`; `SELECT note INTO NEW.label FROM audit` → execsql whose target row has field `new.label` → varno 13. Function node shows `"old_varno":1` (new_varno 0 omitted).
  - Note: zero-valued fields are omitted from JSON (e.g. `recparentno: 0`, `new_varno: 0`, `dno: 0`), so consumers must default missing ints to 0. — [pg_query_json_plpgsql.c](https://github.com/pganalyze/libpg_query/blob/17-latest/src/pg_query_json_plpgsql.c)

### Inferences
- The JSON is a good **front end** for a canonical IR (statement kinds, explicit INTO targets as varno lists, recfield→parent links, OUT-row datum, NEW/OLD datums), but types/row shapes must come from elsewhere (catalog or own symbol table). The OUT-param row can be recovered heuristically as the `PLpgSQL_row` with `lineno: -1` built from parameter names, or by reading `proargmodes/proargnames` from `pg_proc`.
- Workaround for the `%ROWTYPE` field-assignment failure: textually rewrite `<var> <rel>%ROWTYPE` declarations to `<var> record` before calling `parse_plpgsql` (the `record` variant parses and gives `PLpgSQL_recfield` datums), and record the original rowtype in your own symbol table; resolve column lists from the catalog.
- Embedded SQL comes out as text with INTO blanked; it must still be parsed (pglast `parse_sql`) with PL/pgSQL variables treated as parameters — the same text PostgreSQL itself plans (see Q4).

### Gaps
- Did not inspect pglast docs pages for any extra post-processing; behaviour inferred from the installed 8.5 source line and output.
- Did not search libpg_query issue tracker for an open issue on `%ROWTYPE` field assignment; not found in a quick web search.

---

## Q2. PostgreSQL catalog facilities as ground truth (pg_proc, pg_depend, information_schema)

### Takeaway
For **views** and **SQL-standard-body (BEGIN ATOMIC / RETURN) functions**, a real PG17 records **column-granular dependencies** in `pg_depend` (`refobjsubid` = attnum), surfaced in `information_schema.view_column_usage` / `routine_column_usage` / `routine_table_usage`. These are a cheap, exact *read/write set* oracle, but they are **unordered sets per object**: no output→input mapping, no read-vs-write direction, no expression structure. Quoted-string SQL bodies and PL/pgSQL get **nothing** column-level from the catalog. `pg_proc` and `pg_get_function_*` give exact signatures (OUT/INOUT, RETURNS TABLE, SETOF) and the deparsed, fully-qualified ATOMIC body.

### Cited Findings
- SQL-standard body: "This form is parsed at function definition time ... therefore this form cannot support polymorphic argument types"; "This form tracks dependencies between the function and objects used in the function body, so DROP ... CASCADE will work correctly, whereas the form using string literals may leave dangling functions." Only `LANGUAGE SQL`. — [PG17 CREATE FUNCTION](https://www.postgresql.org/docs/17/sql-createfunction.html)
- "When there are OUT or INOUT parameters, the RETURNS clause can be omitted. If present, it must agree with the result type implied by the output parameters: RECORD if there are multiple output parameters, or the same type as the single output parameter." — [PG17 CREATE FUNCTION](https://www.postgresql.org/docs/17/sql-createfunction.html)
- `information_schema.routine_column_usage` "identifies all columns that are used by a function or procedure, either in the SQL body or in parameter default expressions"; works only for unquoted SQL bodies (not quoted bodies or other languages); a column is included only if its table is owned by a currently enabled role. The page exists for PG 14, 15, 16, 17, 18 (i.e. introduced with SQL-standard bodies in PG14). — [PG17 routine_column_usage](https://www.postgresql.org/docs/17/infoschema-routine-column-usage.html)
- [local experiment, PG17.11] For `f_atomic(p) BEGIN ATOMIC SELECT a+b FROM t WHERE c='z' AND a=p; END`, `pg_depend` rows (classid pg_proc) were: `pg_class t subid 1 (a)`, `subid 2 (b)`, `subid 3 (c)` plus namespace, all `deptype='n'`. For `f_atomic_dml() BEGIN ATOMIC INSERT INTO u(x,y) SELECT a,b FROM t; UPDATE u SET y = x+1 WHERE x>0; END` → t.a, t.b, u.x, u.y — target columns of INSERT/UPDATE are included, but nothing distinguishes read from written. For `f_quoted(p) LANGUAGE sql AS 'SELECT a FROM t WHERE b=p'` → **only the namespace dependency**. `routine_column_usage` / `routine_table_usage` returned exactly the same column/table sets; `routine_routine_usage` was empty (only built-in `upper` used). — [pg_depend docs](https://www.postgresql.org/docs/17/catalog-pg-depend.html)
- [local experiment] View `v AS SELECT a AS va, b+1 AS vb, upper(c) AS vc FROM t WHERE b > 0`: `pg_depend` rows with `classid='pg_rewrite'`, `objid = pg_rewrite.oid` where `ev_class='v'` → `t.a, t.b, t.c` (deptype n) plus internal self-dep (deptype i, refobjsubid 0). `information_schema.view_column_usage` returned (t,a),(t,b),(t,c) — note `b` is included although it is used both in a projection and in WHERE; no per-output-column mapping. — [PG17 view_column_usage](https://www.postgresql.org/docs/17/infoschema-view-column-usage.html)
- [local experiment] `pg_proc` for `f_out(IN p int, OUT o1 int, OUT o2 text)`: `proargmodes={i,o,o}`, `proallargtypes={integer,integer,text}`, `proargnames={p,o1,o2}`, `prorettype=record`, `proretset=f`, `prosqlbody IS NOT NULL`; `pg_get_function_result` → `record`; `pg_get_function_arguments` → `p integer, OUT o1 integer, OUT o2 text`. (`proargmodes` also uses `b` INOUT, `v` VARIADIC, `t` TABLE columns.) — [PG17 pg_proc](https://www.postgresql.org/docs/17/catalog-pg-proc.html); [PG17 system information functions](https://www.postgresql.org/docs/17/functions-info.html)
- [local experiment] `pg_get_function_sqlbody(oid)` returns the deparsed body with every column qualified and statements separated, e.g. `BEGIN ATOMIC INSERT INTO u (x, y) SELECT t.a, t.b FROM t; UPDATE u SET y = (u.x + 1) WHERE (u.x > 0); END`; parameters are rendered as `f_atomic.p`. `pg_get_functiondef` returns the full CREATE OR REPLACE. — [PG17 system information functions](https://www.postgresql.org/docs/17/functions-info.html)

### Inferences
- Loading each corpus into PG17 and reading `pg_depend`/`routine_column_usage`/`view_column_usage` gives a **ground-truth read/write column set** for views and ATOMIC functions — ideal as an *eval oracle* (every column your lineage claims as a source/target of a view or ATOMIC function must be in this set; any column in the set must be explained as value/filter/target). It cannot replace lineage computation because it lacks output-column attribution.
- The BEGIN-ATOMIC-splitting problem disappears if you read `pg_get_function_sqlbody` (or `prosqlbody`, a `pg_node_tree` of analysed Query nodes) from the live catalog: PG has already split statements and qualified columns. Re-parsing the deparsed body with pglast is safe and canonical.
- OUT/INOUT row-source shape (forms used in FROM/LATERAL) should be read from `proallargtypes/proargmodes/proargnames` rather than inferred from text.
- Quoted SQL bodies can be "upgraded" for analysis only: re-create them as BEGIN ATOMIC in a scratch DB to get dependencies (fails for polymorphic args — per docs).

### Gaps
- Did not verify how `pg_depend` represents whole-row references (e.g. `SELECT t FROM t`, `t.*`) or column refs reached via views inside ATOMIC bodies (expected: dependency on the view's column, not base columns — not tested).
- `prosqlbody` node-tree contents (e.g. whether `resorigtbl` is preserved) not inspected.

---

## Q3. plpgsql_check (okbob/plpgsql_check)

### Takeaway
plpgsql_check is MIT-licensed, actively released (PGDG `2.10.13` for PG17 on Debian trixie), supports PG 14–20, and is **not** in the stock `postgres:17` image (one `apt-get install postgresql-17-plpgsql-check`). Its dependency report is **relation/function-level only (no columns)** but it does see relations inside **constant** dynamic SQL (constant tracing). Its **tracer** is a powerful dynamic oracle: per-statement variable values in/out, including `NEW` fields in BEFORE triggers and nested trigger invocations.

### Cited Findings
- License: MIT-style ("Permission is hereby granted, free of charge ..."), author Pavel Stehule; "PostgreSQL 14 - 20 are supported." — [plpgsql_check README](https://github.com/okbob/plpgsql_check)
- `plpgsql_show_dependency_tb` displays functions, operators and relations used by a function; columns `type, oid, schema, name, params`; reports relations and functions only, not columns. — [plpgsql_check README](https://github.com/okbob/plpgsql_check)
- `plpgsql_check_function(funcoid regprocedure, relid regclass, ..., newtable, oldtable, fatal_errors, other_warnings, extra_warnings, performance_warnings, security_warnings, compatibility_warnings, anyelememttype, constant_tracing boolean DEFAULT true, without_warnings, all_warnings, use_incomment_options)`; output formats text, json, xml; `relid` is used to check DML trigger functions. — [plpgsql_check README](https://github.com/okbob/plpgsql_check)
- Limitations (README): cannot detect structure of referenced cursors (refcursor); temp tables created at runtime need a check-time definition (`-- @plpgsql_check_options` / `PERFORM plpgsql_check_pragma('table: name(col type, ...)')` or `'type: var (field type, ...)'`); "When the query text or a format width is unknown, it cannot reliably infer the result's record type." Profiler (per function/statement, coverage `plpgsql_coverage_statements/branches`; shared mode needs preload) and tracer (prints variables/arguments; requires superuser to set `plpgsql_check.enable_tracer`). — [plpgsql_check README](https://github.com/okbob/plpgsql_check)
- [local experiment] PGDG apt candidate `postgresql-17-plpgsql-check 2.10.13-1.pgdg13+1` (also Debian main 2.8.1); stock `postgres:17` image lists only `plpgsql` (and contrib) in `pg_available_extensions`, i.e. plpgsql_check must be installed. Installed functions include `plpgsql_check_function[_tb]`, `plpgsql_show_dependency_tb`, `plpgsql_check_pragma`, `plpgsql_profiler_function_tb`, `plpgsql_profiler_function_statements_tb`, `plpgsql_coverage_statements/branches`, `plpgsql_check_tracer`, `plpgsql_check_profiler`. — [apt.postgresql.org](https://apt.postgresql.org/pub/repos/apt/)
- [local experiment] Dependencies: for a function whose only table refs are `RETURN QUERY EXECUTE format('SELECT id, note FROM %I', 'audit')` and `EXECUTE 'INSERT INTO tgt(id) SELECT id FROM src'`, `plpgsql_show_dependency_tb` returned RELATION `audit`, `src`, `tgt` — i.e. constant dynamic SQL (including constant `format()`) is evaluated and analysed. For `EXECUTE format('DELETE FROM %I', t)` with `t` a parameter → **0 rows** (and no security warning, since `%I` is safe). For a trigger, `plpgsql_show_dependency_tb('trg()', 'tgt')` returned only `audit` (the trigger's own table is passed as relid, not reported).
- [local experiment] Tracer (`LOAD 'plpgsql_check'; SET plpgsql_check.enable_tracer=on; SET plpgsql_check.tracer=on; SET plpgsql_check.tracer_verbosity='verbose'`) emitted NOTICEs such as: `#0 ->> start of function trg() ... triggered by before row insert trigger`, `"new" => '(1,5,lbl)'`, `start of assignment NEW.label := upper(NEW.label)` with `"new"."label" => 'lbl'` before and `'LBL'` after; `start of SQL statement (query='SELECT note ...')` with `"new"."id" => '1'`; field-by-field `r.id := v1`, `r.total := v2 * 2` with before/after values; nested trigger frames (`#2 ... context: SQL statement "INSERT INTO tgt ..."`). It did **not** print the evaluated dynamic SQL text for `RETURN QUERY EXECUTE`.
- [local experiment] Profiler `plpgsql_profiler_function_tb('trg()')` returned per-line `lineno, stmt_lineno, queryids, cmds_on_row, exec_stmts, source`.

### Inferences
- As a **static oracle**, plpgsql_check is coarse (relation-level) but validates the routine compiles against the real catalog (types, `%ROWTYPE`, record fields, trigger NEW/OLD shape via `relid`) — a useful "is our symbol table right?" check and a source of "relations touched incl. constant dynamic SQL".
- As a **dynamic oracle**, the tracer gives per-statement def/use *values*; with seeded data it can confirm which variable/field each assignment wrote (e.g. NEW.x writes, field-by-field rowtype assembly) — useful for regression evals, not for production lineage (requires execution, superuser, and only covers executed paths).

### Gaps
- Did not check whether `plpgsql_check_function(..., format:='json')` exposes per-statement referenced relations (it returned no rows for a clean trigger function, i.e. it reports issues only).
- Maintenance cadence (commit dates) not fetched; inferred "active" from the 2.10.x PGDG series and PG 14–20 support statement.

---

## Q4. PostgreSQL's planner/analyser as a semantic oracle (EXPLAIN VERBOSE, auto_explain, debug_print_*, resorigtbl/resorigcol, RowDescription)

### Takeaway
Three mechanisms work on PG17 and give fully-resolved column references with zero hand-written rules: (1) `EXPLAIN (VERBOSE, GENERIC_PLAN)` (PG16+) on any statement with `$n` placeholders — static, no data needed; (2) `auto_explain` with `log_nested_statements` + `log_verbose` — logs every statement executed inside PL/pgSQL, **including dynamic SQL after format() substitution**, with JSON plans; (3) `debug_print_rewritten`/`debug_print_parse` — the analysed Query tree including `resorigtbl/resorigcol` and per-RTE `selectedCols/insertedCols/updatedCols`. `resorigtbl/resorigcol` (exposed on the wire in RowDescription) give origins only for **bare column references**, so they are not lineage for expressions.

### Cited Findings
- RowDescription per field: "If the field can be identified as a column of a specific table, the object ID of the table; otherwise zero" and "... the attribute number of the column; otherwise zero"; libpq exposes them as `PQftable` / `PQftablecol`. — [PG17 protocol message formats](https://www.postgresql.org/docs/17/protocol-message-formats.html); [PG17 libpq PQftable](https://www.postgresql.org/docs/17/libpq-exec.html)
- [local experiment, psycopg 3 `cursor.pgresult.ftable(i)/ftablecol(i)`]: `SELECT s.id AS sid, s.amt*2 AS dbl, x.label FROM src s JOIN tgt x USING (id)` → `sid=(src,1)`, `dbl=(0,0)`, `label=(tgt,3)`; through a subquery and a CTE the origin propagates (`(src,1)`, `(src,3)`); `UNION` → 0; `VALUES` → 0; set-returning SQL function `sqlf()` → 0; **a view column maps to the view itself** (`vid=(vw,1)`), not to the base table (origins are marked at parse analysis, before view rewrite). — [psycopg pq API](https://www.psycopg.org/psycopg3/docs/api/pq.html)
- [local experiment] `debug_print_rewritten=on` (with `client_min_messages=log`, `debug_pretty_print`) printed the rewritten Query node tree: `TARGETENTRY ... :resname sid :resorigtbl 16406 :resorigcol 1` for `s.id`, and `:resorigtbl 0 :resorigcol 0` for `s.amt * 2`; `RTEPERMISSIONINFO :relid 16406 :requiredPerms 2 :selectedCols (b 8 9) :insertedCols (b) :updatedCols (b)` — bitmapset members are attnums offset by `FirstLowInvalidHeapAttributeNumber` (8,9 → attnum 1,2 = id, amt). Vars carry `:varno :varattno :varlevelsup`, so the full analysed expression tree (functions as `funcid`, operators as `opno`) is available. The GUCs log at LOG level (to server log or client). — [PG17 runtime-config-logging (debug_print_parse / debug_print_rewritten / debug_print_plan / debug_pretty_print)](https://www.postgresql.org/docs/17/runtime-config-logging.html)
- [local experiment] `auto_explain` (`LOAD 'auto_explain'; SET auto_explain.log_min_duration=0; log_nested_statements=on; log_verbose=on; log_format='json'; log_level='notice'`) while running a PL/pgSQL SRF logged, for every executed statement: `"Query Text": "SELECT id, amt             FROM src WHERE id = 1"` (INTO blanked); for `RETURN QUERY EXECUTE format('SELECT id, amt, upper(name) FROM %I WHERE id > $1', tbl) USING 0` → `"Query Text": "SELECT id, amt, upper(name) FROM src WHERE id > $1"`, `"Query Parameters": "$1 = '0'"`, `"Output": ["id","amt","upper(name)"]` — i.e. **the post-format() dynamic SQL**; for `INSERT INTO tgt SELECT s.id, s.amt + 1, s.name FROM src s WHERE s.id = v1` → ModifyTable `Relation Name: tgt` with child `"Output": ["s.id", "(s.amt + '1'::numeric)", "s.name"]` and `"Filter": "(s.id = 1)"` (PL/pgSQL variable `v1` was inlined as its value in a custom plan). **Field-by-field record assignments (`r.total := v2 * 2`) and `RETURN NEXT r` produced no plan** (simple expressions bypass the executor's plan logging). — [PG17 auto_explain](https://www.postgresql.org/docs/17/auto-explain.html)
- [local experiment] `EXPLAIN (VERBOSE, COSTS OFF, GENERIC_PLAN) INSERT INTO tgt(label, id) SELECT s.name, s.id + $1 FROM src s WHERE s.amt > $2 RETURNING total` → `Insert on public.tgt / Output: tgt.total / -> Seq Scan on public.src s / Output: (s.id + $1), NULL::numeric, s.name / Filter: (s.amt > $2)` — the child output is in **target-table attnum order** (id, total, label) with `NULL::numeric` for the omitted column, so INSERT column mapping is recoverable positionally. For `UPDATE tgt SET total = s.amt * $1 FROM src s WHERE s.id = tgt.id` the JSON plan shows `"Output": ["(s.amt * $1)", "tgt.ctid", "s.ctid"]` — **the updated target column name (`total`) is not labelled** in EXPLAIN output (inferred, not doc-verified here: since the PG14 executor rework, UPDATE subplans emit only the new values of changed columns plus row identity). `GENERIC_PLAN` was added in PG16 and allows explaining statements with `$n` parameters without values. — [PG17 EXPLAIN](https://www.postgresql.org/docs/17/sql-explain.html)
- [local experiment] `EXPLAIN VERBOSE` also normalises joins (`USING`), qualifies all columns (`x.id, (x.total + '1'::numeric)`), and pushes projections down (`Seq Scan on public.src s Output: s.id`), so expressions are already resolved but **plan-shaped**, not query-shaped (outputs may be reordered, projected away or pushed into scans; `Hash Cond`/`Filter` carry filter-lineage).

### Inferences
- **Static route (recommended to evaluate):** for each SQL statement inside a routine, replace PL/pgSQL variable references with `$n` (exactly what PL/pgSQL itself does) and either (a) run `EXPLAIN (VERBOSE, GENERIC_PLAN, FORMAT JSON)`, or better (b) capture the analysed Query tree (debug_print_rewritten, or a `PREPARE` + parse-tree dump) — the Query tree is query-shaped and gives resolved Vars per target entry plus selected/inserted/updated column bitmaps per relation. This replaces hand-written resolution of `*`, USING, aliases, views, CTEs, and INSERT column mapping. The `debug_print_*` text format (`nodeToString`) needs a small reader; it is the PG outfuncs format, not JSON.
- **Dynamic route:** `auto_explain` nested logging is the only PG-native facility seen that yields post-substitution dynamic SQL; gaps: only executed paths, needs seed data that drives branches, simple-expression assignments (the record/`NEW.x :=`/`RETURN NEXT` forms) are invisible, and plan caching means PL/pgSQL variables show either as constants (custom plan) or `$n` (generic plan after ~5 executions; controllable with `plan_cache_mode`).
- RowDescription/`resorigtbl` is only usable for "pass-through column" lineage of SELECT projections; expressions, aggregates, UNION and function outputs are zero, and views resolve to the view.
- None of these PG facilities model PL/pgSQL *control/data flow* between statements (variables, records, OUT rows, RETURN NEXT); that layer still needs your own IR. PG gives exact per-statement transfer functions; your dataflow composes them.

### Gaps
- Did not test `debug_print_parse` inside a PL/pgSQL function call (expected to log each statement's tree as it is parsed by SPI; unverified).
- Did not test `plan_cache_mode=force_generic_plan` interaction with auto_explain output.
- Did not verify whether `EXPLAIN VERBOSE` of MERGE / INSERT ... ON CONFLICT labels target columns.

---

## Q5. Program-analysis frameworks / IR projects reusable for a generic dataflow in Python

### Takeaway
No mainstream static-analysis platform supports SQL/PL/pgSQL (CodeQL: no; Semgrep: not listed). The reusable pieces are (a) a **Datalog engine** to express the generic dataflow over your canonical facts — Soufflé (C++, UPL license, Python via generated SWIG wrapper or subprocess on CSV facts) or pure-Python pyDatalog — or simply a hand-rolled worklist over networkx; and (b) parsers (libpg_query/pglast, the new tree-sitter-postgres). The Grust group's PL/SQL→recursive-CTE work is a conceptual model for lowering PL/pgSQL to SQL, but no public compiler for it was found.

### Cited Findings
- CodeQL supported languages: C/C++, C#, GitHub Actions, Go, Java, Kotlin, JavaScript, Python, Ruby, Rust, Swift, TypeScript — no SQL/PL/SQL/PL/pgSQL. — [CodeQL supported languages](https://codeql.github.com/docs/codeql-overview/supported-languages-and-frameworks/)
- Semgrep's supported-languages page does not list SQL or PL/pgSQL at any maturity level (GA/Beta/Experimental). — [Semgrep supported languages](https://docs.semgrep.dev/supported-languages). Semgrep's dataflow engine (taint, constant and symbolic propagation) "can run on any language supported by Semgrep" with lower support level. — [Semgrep dataflow overview](https://semgrep.dev/docs/writing-rules/data-flow/data-flow-overview)
- Soufflé: Datalog-derived logic language with interpreter and compiler to parallel C++. — [Wikipedia: Soufflé](https://en.wikipedia.org/wiki/Souffl%C3%A9_(programming_language)). Python bindings are produced via SWIG: build with `cmake -DSOUFFLE_SWIG_PYTHON=ON`, generate with `souffle -s python <file.dl>`, then `import SwigInterface; p = SwigInterface.newInstance("<name>"); p.loadAll('.'); p.run(); p.printAll('.')`; no Windows guidance given; UPL license. — [Soufflé SWIG docs](https://souffle-lang.github.io/swig)
- [local experiment] `pip download pyDatalog` fetched `pyDatalog 0.22.4` with a `cp311-win_amd64` wheel, "A pure-python implementation of Datalog", author Pierre Carbonnelle, Requires-Python >=3.8. — [pyDatalog on PyPI](https://pypi.org/project/pyDatalog/)
- tree-sitter-postgres (Gavin M. Roy, announced 2026-04-03): Tree-sitter grammar for Postgres SQL + PL/pgSQL, **code-generated from Postgres' Bison sources** (PL/pgSQL from `pl_gram.y`, with an external scanner for dollar quoting and SQL fragments delegated to the SQL grammar via injection). — [PostgreSQL news](https://www.postgresql.org/about/news/tree-sitter-postgres-libpgfmt-pgfmt-and-libpgdump-3263/); [gmr/tree-sitter-postgres](https://github.com/gmr/tree-sitter-postgres)
- "Compiling PL/SQL Away" (Duta, Hirn, Grust, CIDR 2020) compiles PL/SQL functions into regular SQL subqueries, iteration into `WITH RECURSIVE`. — [CIDR 2020 paper](https://www.vldb.org/cidrdb/papers/2020/p1-duta-cidr20.pdf); follow-up "PL/SQL Without the PL" — [Tübingen DB group](https://db.inf.uni-tuebingen.de/publications/2020/pl/sql-without-the-pl/)
- The Tübingen DB group's public GitHub org lists `flummi` ("A to-SQL Compiler Prototype", Python, GPL-3.0) and `provenance-for-recursive-queries` (PLpgSQL), but no repository for the PL/SQL→CTE compiler itself. — [DBatUTuebingen repositories](https://github.com/orgs/DBatUTuebingen/repositories)

### Inferences
- The Grust lowering pipeline (from memory of the paper, not re-verified here: PL/SQL → SSA → ANF → mutually tail-recursive functions → `WITH RECURSIVE`) is the most principled published "canonical primitives" model for PL/pgSQL: every variable becomes a column of a loop-carried row; that is precisely a column-lineage-friendly IR (lineage of a CTE is then ordinary SQL lineage). Reimplementation would be needed (code not found).
- For your scale, the simplest generic engine is a Datalog-style fixpoint over facts like `assign(stmt, dst_var_or_field, src_col_or_var, kind)`, `reads(stmt, col)`, `writes(stmt, table.col, src)`, `flows(stmt1, stmt2)`; pyDatalog keeps it pure-Python (Windows-friendly), Soufflé gives speed but needs a C++ toolchain/SWIG (Windows support undocumented).
- tree-sitter-postgres is relevant only if you want an error-tolerant CST; libpg_query remains the semantic source of truth.

### Gaps
- Not researched within budget: Kythe, Python SSA libraries, Doop, ora2pg internals, pgsql-parser (JS wrapper of libpg_query), license/Python bindings of tree-sitter-postgres.
- pyDatalog maintenance status beyond the existence of 0.22.4 cp311 wheels not checked.

---

## Q6. Symbolic execution / constant folding of dynamic SQL (format, quote_ident, ||)

### Takeaway
The only existing tool found that statically evaluates dynamic SQL strings is plpgsql_check's constant tracing (verified: constant `format('... %I', 'audit')` is resolved; a parameter-dependent `format` is not). Since `format`, `concat`, `quote_literal(anyelement)` are STABLE and `quote_ident`, `quote_literal(text)`, `textcat` (`||`) are IMMUTABLE in PG17, the live server itself can serve as an exact constant folder for any argument list you can prove constant.

### Cited Findings
- [local experiment] `plpgsql_show_dependency_tb` resolved relations inside `RETURN QUERY EXECUTE format('SELECT id, note FROM %I', 'audit')` and `EXECUTE 'INSERT INTO tgt(id) SELECT id FROM src'`, but reported nothing for `EXECUTE format('DELETE FROM %I', t)` with `t` a parameter. — [plpgsql_check README (constant_tracing parameter)](https://github.com/okbob/plpgsql_check)
- [local experiment] PG17 `pg_proc.provolatile`: `format(text)`, `format(text, VARIADIC "any")`, `concat`, `concat_ws`, `quote_literal(anyelement)`, `quote_nullable(anyelement)` = `s` (stable); `quote_ident(text)`, `quote_literal(text)`, `quote_nullable(text)`, `textcat(text,text)` = `i` (immutable). — [PG17 pg_proc (provolatile)](https://www.postgresql.org/docs/17/catalog-pg-proc.html)
- [local experiment] auto_explain shows the post-substitution text of executed dynamic SQL (`"Query Text": "SELECT id, amt, upper(name) FROM src WHERE id > $1"` from a `format(...%I...)`) — the dynamic fallback. — [PG17 auto_explain](https://www.postgresql.org/docs/17/auto-explain.html)

### Inferences
- A principled design: constant-propagate PL/pgSQL scalars in your IR (lattice: Const | Unknown); when the `dynquery` expression's free variables are all Const, evaluate it by sending `SELECT <expr>` with those constants bound as parameters to PG17 (safe: stable/immutable string functions, no table access — check the expression's function OIDs are in an allow-list). Result text is then parsed like static SQL. This replaces the special-case rule for "RETURN QUERY EXECUTE of constant format() text".

### Gaps
- No standalone Python library for symbolic evaluation of PL/pgSQL `format()`/`quote_*` was found (searched implicitly via tool surveys; no dedicated search hit).

---

## Q7. psql script semantics (\gset, \set, :var / :'var', \if, COPY FROM STDIN, FETCH :n)

### Takeaway
No Python library re-implements psql's variable/meta-command semantics: pgspecial (used by pgcli) implements describe/list commands but **not** `\set`, `\gset`, `\if`, or variable interpolation. The faithful option is to run the real `psql` (present in the `postgres:17` image) with `-e/--echo-queries` (or server `log_statement=all`) and capture the **post-interpolation** SQL actually sent — verified for `\gset`, `:"var"`, `:'var'`, `:n`, `FETCH :n`, and inline `COPY ... FROM STDIN` data.

### Cited Findings
- [local experiment] pgspecial 2.2.1 (BSD, "Meta-commands handler for Postgres Database", Pgcli Core Team, Requires-Python >=3.9) registers `\!, \d, \dD, \dE, \dF, \dT, \db, \ddp, \df, \di, \dm, \dn, \do, \dp, \ds, \dt, \du, \dv, \dx, \e, \l, \n, \nd, \np, \ns, \sf, \z, describe` (+ io commands); grep found no `gset` or `\if` implementation. — [dbcli/pgspecial](https://github.com/dbcli/pgspecial)
- [local experiment] `psql -X -e -f script.sql` on PG17 echoed: `SELECT 'src' AS tname, 2 AS n` (then `\gset`), and for `\set col id` + `SELECT :"col" FROM :"tname" WHERE name = :'tname' LIMIT :n;` it echoed `SELECT "id" FROM "src" WHERE name = 'src' LIMIT 2;`; `FETCH :n FROM cur;` echoed as `FETCH 2 FROM cur;`; `COPY audit FROM STDIN;` with an inline row and `\.` → `COPY 1`. `\if :n` with n=2 errored `unrecognized value "2" for "\if expression": Boolean expected` (psql `\if` accepts only boolean-like values). — [PG17 psql docs (variables, interpolation, \gset, \if, -e)](https://www.postgresql.org/docs/17/app-psql.html)
- psycopg 3 provides `cursor.copy("COPY ... FROM STDIN")` with `write()/write_row()` for feeding inline data programmatically (not a psql interpreter). — [psycopg 3 COPY](https://www.psycopg.org/psycopg3/docs/basic/copy.html)

### Inferences
- For psql scripts, the cleanest canonicalisation is: execute the script against a scratch PG17 with real psql in echo mode (or server-side `log_statement=all` + `auto_explain` nested), and analyse the **echoed statement stream** as plain SQL. `\gset` variables become constants already folded into later statements; `COPY FROM STDIN` inline data becomes a "literal source" node. Requires the script to be executable (schema present, data enough for `\gset` queries to return one row).
- A static alternative needs your own psql-variable interpreter (psql's lexer rules: `:name`, `:'name'`, `:"name"`, `:{?name}`, no interpolation inside quoted literals or dollar quotes); no library does this.

### Gaps
- Did not find psql re-implementations in other languages (e.g. Rust/Go) with full `\if`/`\gset` semantics; `pg_regress` simply invokes psql (not verified in source here).
