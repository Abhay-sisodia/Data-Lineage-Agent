# Final suite: the bounds (Step 0)

**Written 2026-10-07**, against the analyser at `f46f8e0`, unchanged.

```
.\.venv\Scripts\python.exe stress/pg/final/probe_limitations.py [-v] [name filter]
```

The probe sent **128 one-construct statements** through `analyse_source`, plus about 60
follow-ups (builtins, DDL options, script statements). It covered what B1–B11 and the
torture rounds exercised.

Most of what earlier rounds lost to SQLGlot is now restated by `pg_grammar.py`. That
includes SEARCH/CYCLE, SQL/JSON, JSON_TABLE with PASSING and NESTED, XMLTABLE, typed
literals, `0o`/`0b`/`_` numerals, OPERATOR(), AT LOCAL, IS NORMALIZED and USING sorts. psql
`:'v'` now parses as well, and PostgreSQL's `GROUP BY ALL a` (the set quantifier) keeps its
columns. **TS-3 does not recur in PostgreSQL.**

## 1. Excluded from the tests: parse limits

> **Update 2026-10-07: F0-01 fixed.** X-1..X-5 are now restated by `pg_grammar` and analysed
> (`tests/test_create_options.py`). Any `CREATE ... AS <query>` SQLGlot still keeps opaque is
> counted and refused PARSE_FAILED, never dropped. The table below is the state the suite
> was written against.

| # | Construct | What happens | Why it is excluded |
|---|---|---|---|
| X-1 | `CREATE MATERIALIZED VIEW … WITH [NO] DATA`, `… TABLESPACE t` | SQLGlot gives up and keeps the statement as a raw `Command` | parse limit (and silent: **F0-01**) |
| X-2 | `CREATE RECURSIVE VIEW` | `Command` | parse limit (F0-01) |
| X-3 | `CREATE VIEW … WITH [CASCADED\|LOCAL] CHECK OPTION` | `Command` | parse limit (F0-01) |
| X-4 | `CREATE TEMP TABLE … ON COMMIT DROP AS SELECT` | `Command` | parse limit (F0-01). `ON COMMIT PRESERVE ROWS` and plain TEMP parse |
| X-5 | `CREATE VIEW v WITH (security_barrier) AS …` | `PARSE_FAILED`, declared | parse limit |
| X-6 | PL/pgSQL, psql meta-commands (`\set`) | n/a | out of scope by decision |

The plain spellings of X-1 to X-4 parse and analyse: CREATE MATERIALIZED VIEW (also with a
column list or IF NOT EXISTS), CREATE VIEW, and CREATE TEMP TABLE AS. So the hard tier uses
those spellings.

## 2. Allowed in tests only with an `expect refusal` line: refused by design or by finding

| Construct | Refusal | Status |
|---|---|---|
| `TRUNCATE`, `COPY` | UNSUPPORTED_CONSTRUCT | by design (B3, in the refusal register) |
| `CREATE [UNIQUE] INDEX` | UNSUPPORTED_CONSTRUCT | **F0-04**: no lineage, so it should be counted without a refusal |
| `( VALUES … )` as a parenthesised set-operation arm | UNSUPPORTED_CONSTRUCT ("arm is Subquery") | **F0-07**. The unparenthesised `UNION ALL VALUES (…)` analyses |

## 3. Statements counted as nothing, correctly

`SET`, `BEGIN`/`COMMIT`, `ANALYZE`, `VACUUM`, `LOCK`, `COMMENT ON`, `DROP` give seen=0 and
no lineage. None of them moves data.

## 4. Conventions the keys follow

- **Closed world.** Every emitted edge (value, influence, filter) and every declared
  boundary must be stated in the key.
- **A refusal's line** is written as the line of the statement's first keyword. Today the
  analyser numbers it from the first comment above the statement (**F0-08**), so a refusal
  preceded by a comment shows as a line mismatch, which is the finding itself.
- `expect refusal <SCRIPT>:<line> | why` states an expected refusal. A refusal is a
  boundary of kind `refusal`, so the planned `refuse` directive was not needed.

## 5. Findings the probe already surfaced (none fixed: fixes come after all 15 tests)

| ID | Finding | Grade |
|---|---|---|
| **F0-01** | **Statements SQLGlot keeps as a raw `Command` are silent.** X-1 to X-4 each give statements seen = 0, no refusal and no boundary, so a whole view's or temp table's lineage disappears and coverage reads as complete. The same path correctly drops `SET`/`LOCK`/`VACUUM`. A `Command` whose text holds `AS SELECT`/`AS WITH` needs to be counted and refused (or restated). | **R1** silent loss |
| **F0-02** | **`t.*` used as a whole-row value** (`row_to_json(o.*)`, `(o.*)::text`) gives no edges and declares `unresolved_identifier STAGING.ORDERS.*`. D-20 says a whole-row use reads every column, and `to_jsonb(o)` without `.*` does exactly that. | R3 declared loss |
| **F0-03** | **Built-ins missing from the catalogue are declared as unknown calls** (`source_unavailable`, D-53): `xmlattributes`, `to_tsvector`, `to_tsquery`, `plainto_tsquery`, `setweight`, `ts_rank_cd`, `point`, `convert_to`, `pg_input_is_valid`, `pg_input_error_info`, `random_normal`, `txid_current_if_assigned`, `unistr`, `array_fill`, `trim_array`, `jsonb_array_length`, `atan2d`, `cosd`, `current_setting`, `inet_server_addr`, `pg_backend_pid`, `pg_typeof`, `statement_timestamp`, `transaction_timestamp`. The edges are right (arguments derived); the declaration is false, because it claims an unread body for a built-in. Under closed-world scoring, each one is an unexpected boundary. | R4 false declaration |
| **F0-04** | `CREATE [UNIQUE] INDEX` is refused (UNSUPPORTED_CONSTRUCT). It moves no data, so like a plain CREATE TABLE (D-33) it should be counted with no lineage and no refusal. | R2 unsound refusal |
| **F0-05** | Spurious `unresolved_identifier` for `SYSTEM_USER`, and for `DEFAULT` in `COLLATE pg_catalog."default"`. | R4 false declaration |
| **F0-06** | `UPDATE … SET (a, no_such_col) = (SELECT …)` emits edges into a column the target does not have, and declares nothing. PostgreSQL rejects the statement. | R4 (suspect SQL; low) |
| **F0-07** | A parenthesised `(VALUES …)` set-operation arm is refused, even without ORDER BY/LIMIT. It is valid PostgreSQL and the unparenthesised form works. | R3 declared loss |
| **F0-08** | A refusal is located at the first comment line above its statement, not at the statement (`-- c\nTRUNCATE …` is reported at line 1). Edge origins have the same offset, but they are not part of the match key. | R4 measurement |
