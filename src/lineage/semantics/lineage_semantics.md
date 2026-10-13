# Lineage semantics: how to read the parser output and the lineage graph

Semantics version: 1 · result format `lineage-result/1` · graph schema `lineage-graph/1` · dialect: PostgreSQL

## 1. What you are reading

- The lineage comes from a **deterministic SQL parser**. Every edge is a fact it proved from the SQL text (Tier A).
- The graph holds **exactly** what the parser said: nothing inferred, nothing guessed.
- Where the parser could not see, it says so with a **boundary** or a **refusal**. These are facts too; report them, never fill them in.
- An edge is **one hop**: one statement moving data from a source to a target. A multi-step path is a chain of hops, each with its own origin.

## 2. Node ids

- Column: `schema.table.column`, e.g. `staging.orders.amount`. Every PostgreSQL name is schema-qualified (D-25).
- Table (any relation, including views and temp tables): `schema.table`, e.g. `mart.sales`; temp tables are under `pg_temp`.
- `<result:FILE>`: the output of a bare SELECT (no target table) in that file; its columns are `<result:FILE>.column`.
- Variable: a PL/pgSQL variable, e.g. `v_total`.
- Statement: `FILE#UNIT:LINE` - `UNIT` is `<script>` for plain SQL, or the routine's name.
- Boundary: `KIND:SUBJECT`, upper-cased, e.g. `SOURCE_UNAVAILABLE:CALC_MARGIN`.
- Refusal: `FILE#UNIT:LINE:SCOPE`.

## 3. Graph schema

Node labels: `Table`, `Column`, `Variable`, `File`, `Statement`, `Boundary`, `Refusal`, and `Procedure` / `Literal` (reserved, not emitted today).

| Relationship | From → to | Meaning |
|---|---|---|
| `FEEDS` | Column/Variable → Column/Variable | value flow |
| `FILTERS` | Column/Variable → Table | filter flow: decides which rows of the table land |
| `INFLUENCES` | Column/Variable → Column/Variable | influence flow |
| `KEYS` | Column/Variable → Column/Variable | join-key flow: the key that chose a lookup's row (section 4) |
| `HAS_COLUMN` | Table → Column | the column belongs to the table |
| `CONTAINS` | File → Statement | the statement is in the file |
| `READS` / `WRITES` | Statement → Table | tables a statement reads / writes |
| `DECLARES` | File → Boundary | the file declared this boundary (carries `detail`, `line`) |
| `BLOCKED_BY` | Statement → Boundary | the boundary sits on that statement |
| `BOUNDS` | Boundary → Column/Table | the node whose lineage stops there |
| `REFUSED` | Statement → Refusal | the statement was refused |
| `MENTIONS` | Refusal → Table | a table the refused statement's SQL names (what the refusal may affect) |

Lineage relationship properties: `id`, `transform`, `phase`, `band`, `tier`, `mechanism`, `guard`, `file`, `unit`, `line`, `statement`.

## 4. Flows

- `value`: the source's data is **in** the target's value - copied, computed or aggregated. Relationship `FEEDS`.
- `filter`: the source decides **which rows** reach the target relation; none of its data is in any column. Relationship `FILTERS`, ending at a **table**, so it bounds every column of that table.
- `influence`: the source decides the target's value **without being part of it** - a GROUP BY key, a window's PARTITION BY / ORDER BY, an ordered aggregate's ORDER BY. Relationship `INFLUENCES`.
- One source can be both a value and an influence of the same target (D-47): two facts, both true.
- `join_key`: the source is a **lookup join's key** - it chose which row of a joined table the target's value was read from (`LEFT JOIN ref.status_map m ON m.code = o.status` and `m.label` projected: `o.status` is a join key of the label). Relationship `KEYS` (D-57).
  - Not one of the scored flows: D-5 still gives a key no value, filter or influence edge. `upstream` / `downstream` walk `KEYS` only with `join_keys=true`; impact reports include them.
  - Say "the lookup is keyed on X", never "X feeds Y".
  - The same holds however the lookup is written (D-58): a LATERAL or scalar subquery's correlation (`WHERE c.id = o.customer_id`), or a range in an outer join's ON (`o.ship_date BETWEEN fx.valid_from AND fx.valid_to`), is a `join_key` too. When the lookup **aggregates** (`(SELECT SUM(p.amount) FROM payments p WHERE p.order_id = o.id)`), its key decides which rows are summed and is an **influence**, as a GROUP BY is.

## 5. Transform (value edges)

A ladder, weakest to strongest: `identity` < `derived` < `conditional` < `aggregated`.

- `identity`: copied unchanged.
- `derived`: computed from it (arithmetic, a cast, a function).
- `conditional`: chosen by a condition between two or more columns (CASE, COALESCE, GREATEST, ...; D-19). Also a predicate that **cannot remove a result row** (section 10): a WHERE inside an outer-joined CTE or subquery, or inside a scalar subquery, decides whether the value is there or NULL, so its columns are `conditional` value sources of the columns that come through it, not filters.
- `aggregated`: summarised over many rows (SUM, COUNT, MAX, ...).
- An edge carries the **hardest** transform on its path (D-7), and one value fact per source and target per statement (D-51).
- Filter and influence edges always carry `identity`.

## 6. Filter phase

- `pre-aggregation`: removes raw rows before any grouping (a WHERE on base rows).
- `post-aggregation`: removes groups or aggregated rows (HAVING; a WHERE over an aggregated CTE's value, D-6; a LIMIT or DISTINCT ON over an aggregated query, D-56).

## 7. Band, tier, mechanism

- Band, the hardest construct on the edge's path:
  - `0`: set-based SQL;
  - `1`: procedural (variables, loops, cursors);
  - `2`: dynamic SQL and triggers;
  - `3`: out of scope.
- Tier, the evidence type:
  - `A`: parser-proven;
  - `B`: code, runtime and profile agree;
  - `C`: model inference confirmed by a second, different path;
  - `D`: model reasoning alone, which never enters a signed packet.
  - Today every edge is Tier `A`.
- Mechanism, how the parser found the edge: `AST`, `DEF-USE`, `CFG`, `LOG`, `INFERRED`.
- There is **no confidence score**, by design.

## 8. Boundaries: where knowledge stops inside an analysed statement

The statement was analysed; one part of it could not be proven. Edges may still exist beside a boundary.

| Kind | Meaning | Say to the user |
|---|---|---|
| `dangling_reference` | a named object we were never given (an ungranted schema, a remote target) | "X is outside what was analysed" |
| `context_dependent_binding` | the name resolves through the executing schema (search_path) | "depends on which schema runs it" |
| `dynamic_sql` | statement text not knowable statically | "built at run time; not traceable from the code" |
| `refusal` | marks a refused statement (see section 9) | "this statement was not analysed" |
| `parse_failure` | the parser could not read the statement | "this SQL could not be read" |
| `unresolved_identifier` | a name that is not a column of the relation it binds to | "X could not be resolved" |
| `cross_unit_state` | state written in one routine and read in another | "passes through shared state" |
| `fusion_hazard` | a shared scratch table where composing paths could invent a flow | "paths through X may not connect" |
| `ddl_semantics` | lineage carried by DDL that DML-only reading misses | "moved by DDL, not by a query" |
| `row_correspondence` | columns resolve but row-to-row mapping does not | "which rows match is unknown" |
| `suppressed_error` | an exception handler hides failure | "a missing edge proves nothing here" |
| `depth_cap` | analysis stopped at a declared limit | "stopped at the analysis depth limit" |
| `source_unavailable` | the body exists but could not be read (e.g. an unknown function: its arguments still flow in, D-53) | "rests on code we could not read" |

## 9. Refusals: statements not analysed at all

A refused statement has **zero edges**; never guess what it does. It is tied to its file and line, and through `MENTIONS` to the tables its SQL names - report it for a question only when it names a table on that lineage.

| Code | Meaning |
|---|---|
| `PARSE_FAILED` | the text did not parse as SQL |
| `UNSUPPORTED_CONSTRUCT` | it parses, but has no lineage semantics here (e.g. TRUNCATE, COPY, a DO block) |
| `SHAPE_NOT_DECIDABLE` | output columns are not fixed by the text |
| `NAME_NOT_RESOLVED` | an object or column is missing from the schema |
| `DYNAMIC_NOT_CONSTANT` | executed text is not constant |
| `REMOTE_OBJECT` | the object lives in another database |
| `SOURCE_UNAVAILABLE` | there is no source to read |
| `CONTEXT_DEPENDENT` | the meaning depends on run-time state not in the text |
| `BUDGET_EXCEEDED` | a declared analysis limit stopped it |

## 10. Conventions that change how a fact reads

- A join's **key equality** gives **no value, filter or influence edge**: it is plumbing, in ON or WHERE (D-5, D-11). Where the joined table is a lookup that supplies the target's value, the key is reported separately as `join_key` (D-57).
- A non-key condition in an **inner** join's ON is a **filter**, as in WHERE (D-48). A non-equality condition between two tables (a range join) is a filter with both columns as sources (D-49).
- `IN`, `EXISTS`, `NOT IN`, `NOT EXISTS` match keys are **filters**, both columns (D-52).
- A row-choosing cut is a **filter** on what orders it: `rn = 1` over ROW_NUMBER, DISTINCT ON keys, ORDER BY under LIMIT (D-9). An ORDER BY without a limit gives nothing.
- Where a predicate **cannot remove a result row**, its columns are **values** (or influence), not filters (D-12, D-14, D-45) - except a comparison that matches the lookup's rows to a row outside it, which is a join key, or an influence under an aggregate (D-58):
  - a scalar subquery in the select list;
  - the nullable side of an outer join;
  - a one-row LATERAL.
- `agg(x) FILTER (WHERE c)`: `c` is a value of that aggregate, not a filter (D-10).
- A GROUP BY **influences** every aggregate over its groups, including COUNT(*) one level up (D-2, D-55).
- CYCLE's implicit guard is a filter on the cycle columns (D-54).
- A whole-row use (`row_to_json(t)`, `t.*` in an expression) reads every column of `t` (D-20).
- An unknown function passes its arguments through as `derived`, and is declared `source_unavailable` (D-53).
- `grouping(a, b)` carries no lineage (D-50).
- A literal or a parameter has no source: a column fed only by literals has no upstream.

## 11. Answering rules

1. Answer **only** from tool results. Every lineage claim cites the edge id, and file:line where useful.
2. Never add an edge, a source or a transform the graph does not hold.
3. Report every boundary and refusal on the path as an explicit unknown.
4. Use the vocabulary above: value / filter / influence, the transform names, pre / post aggregation.
5. Say "no lineage found" when the graph has none; never infer it from names.
6. When a name matches more than one node, ask which one.
