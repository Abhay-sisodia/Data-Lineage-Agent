# Stress findings

## How this file is used

1. **Write a stress package and its key**, key first, from source, before running anything.
2. **Run it. Record every disagreement here** — as a finding with an ID, a category and a
   status. Do not fix anything yet.
3. **Fix them one at a time**, each with a regression test the day it is written (T3.4e),
   and **re-run the phase-0 measurement after each one**.

**One fix per measurement, always.** Every fix moves the grid; batch three and you cannot
say which one moved what, and attributable numbers are the only thing this project has.

**The category is the point, not the prose.** A single finding is a bug. The same category
appearing in three stress packages is a design fault, and that is what the register below
exists to make countable. Findings that turn out to be errors in the *key* are tagged
`key-error` and kept — a key is evidence, and a benchmark that quietly edits itself to agree
with the code has stopped measuring anything.

## Register

**Provenance is part of a finding's identity, so it is in the ID.** `S1-` to `S4-` are the
four stress packages. `P-` is a probe — the standing pair (`scripts/probe_parsers.py`,
`scripts/probe_analyser.py`) or a one-off written to answer a specific question — a
different instrument asking a different question, and one that has already found things no
stress package could: P-01 was a crash, P-02 a gap no package exercises, and a one-off probe
is what turned up the false-positive half of S4-10 that all four packages scored 0 FP around.
Keeping the series apart is what lets "every finding recurred across stress runs" stay a
true sentence about stress runs.

**Parser-capability facts are NOT in this register.** They live in
`docs/grammar_limitations.md` as the `GL-` and `SG-` series, because "Oracle allows this and
our parser rejects it" is a statement about a vendored grammar and a third-party library,
not about this analyser or its keys. Filing them twice is how a register starts lying about
its own scope. See the GL-002 note below for the one that most tempts an exception.

| ID | Category | Status | One line |
|---|---|---|---|
| P-01 | `crash` | **fixed** | `TABLE(f(...))` raised out of `analyse_source` — the whole file lost, uncounted |
| S3-01 | `flow-classification` | **fixed** | `ROLLUP`/`CUBE`/`GROUPING SETS` emitted no influence at all — SQLGlot files them outside `group.expressions` |
| S3-02 | `flow-classification` | **fixed** | a window's `PARTITION BY`/`ORDER BY` leaks as **value** through a CTE — D-4 reversed by one level of nesting |
| S3-03 | `construct-coverage` | **fixed** | a `MERGE` emitted no filter edge from any clause — neither its `USING` `WHERE` nor an arm-level one |
| S3-09 | `key-error` | **fixed** | a correlation read as a join condition — D-5 excludes an `ON` clause and `predicates.py` says the correlation STAYS |
| S3-10 | `silent-loss` | **fixed** | a correlation's OUTER operand fell back to the subquery's single source — bound to the wrong relation, silently |
| S3-04 | `flow-classification` | **fixed** | **misdiagnosed** — not a D-5 call site; the same `find_all` as S3-02, and closed by the same one-line change |
| S3-05 | `key-error` | **fixed** | four omissions in stress 3's own key, corrected 2026-09-12 and kept as evidence |
| S3-08 | `key-error` | **fixed** | three more, from applying D-2's exclusion as a NAME MATCH instead of by the reason it states |
| S3-06 | `flow-classification` | **fixed** | a `FOR` loop index emitted as a **value source** — the subscript chooses an element, it is not in the value |
| S3-07 | `construct-coverage` | **fixed** | `t.col` on the right of a `MERGE` `SET` did not resolve to the target's own column — declared, not silent |
| S4-01 | `silent-loss` | **fixed** | a top-level `UNION ALL` under `INSERT` with CTE arms yielded **zero edges** — the `WITH` hangs on the set operation and each arm was analysed detached from it |
| S4-02 | `silent-loss` | **fixed** | **misdiagnosed as CTE-specific.** A declared-cursor loop registered no row source at all, so `rec.field` bound to the TARGET; and `_field_of_row` was one-level. Both fixed; the assignment path remains |
| S4-07 | `silent-loss` | **fixed** | `v := rec.field` produced no edge in either loop form — the chain appeared to BEGIN at a variable, which the IR is entitled to say |
| S4-08 | `transform-classification` | **fixed** | a transform inside a cursor query did not reach the edge; fixed WITH S4-07 because they are one fact |
| S4-09 | `construct-coverage` | **fixed** | **filed against the wrong construct.** Nothing to do with cursors: a statement read INTO variables reported *none* of its own predicates or grouping — no `HAVING`, no `GROUP BY` influence, and not even a `WHERE` one level down — while the identical query writing a table produced all three |
| S4-11 | `key-error` | **fixed** | stress 4's key labelled the `HAVING` of one derived table and not the `WHERE` beside it — one clause of one statement, its sibling missed |
| S4-12 | `flow-classification` | **fixed** | a filter edge on a `SELECT … INTO` named the **wrong relation**: one `target_relation` for every column in the predicate, right only when the filtered column sat on the outermost `FROM`'s first table. Found by S4-09's own regression test, not by any package |
| S4-13 | `construct-coverage` | open | what a cursor FOR loop writes carries none of the cursor query's predicates — S4-09's remaining half, and the one the register's old S4-09 row described |
| S4-03 | `construct-coverage` | **fixed** | a `MERGE` emitted no **influence** edge — neither `GROUP BY` nor window; S3-03 added its filters and stopped there |
| S4-04 | `flow-classification` | **fixed** | a `MINUS`/`INTERSECT` second arm was read as **value** when the set operation sits in a CTE — convention (a) held for the top-level form only |
| S4-05 | `construct-coverage` | **fixed** | **misdiagnosed as two-collection.** A projection COMPUTED OR RENAMED in a derived table had no source at all; the second collection was just the one receiving it |
| S4-10 | `silent-loss` | **fixed** | **filed too narrowly, in the safer direction.** S3-06 removed the subscript at four call sites and put nothing in any of them — so the collection was lost in a `VALUES` list, an `UPDATE ... SET` and every `WHERE`, **and the index was still emitted as a source on the two paths S3-06 never reached**. That half is a false positive and was not filed at all |
| S3-11 | `key-error` | **fixed** | stress 3's key labelled two of the three reads of `l_batch` in one `INSERT` — the two that pass through a variable. Found when S4-10's fix made the analyser emit the third and the grid called a correct edge spurious |
| P-02 | `silent-loss` | **fixed** | a `DELETE`'s predicate never sees variables at all — `DELETE ... WHERE sid = v_cutoff` loses `V_CUTOFF` where the identical `UPDATE` keeps it. Def-use now claims the variable half, and S4-10's subscript rule came with it as the finding required |
| S4-06 | `key-error` | **fixed** | **four** in stress 4's own key: a trigger inheritance omitted, a view's internal `CASE` missed, a `GROUP BY` column omitted from a rank's influence, and an `ON` clause carrying a LITERAL labelled as filter |
| S1-01 | `identity` | **fixed** | band 0 deduplicated on `match_key()` and destroyed facts |
| S1-02 | `construct-coverage` | **fixed** | top-level set operators under `INSERT` refused, wrong reason |
| S1-03 | `flow-classification` | **fixed** | **misdiagnosed** — no `GROUP BY`/`HAVING` edge was emitted at all; D-2 adds both |
| S2-12 | `flow-classification` | **fixed** | a window's `PARTITION BY`/`ORDER BY` is neither value nor filter — it is a third flow |
| S2-13 | `key-error` | **fixed** | the `MINUS` arm's other two columns — a uniform rule applied to the first instance only |
| S2-14 | `flow-classification` | **fixed** | a join condition is structural in a `FROM` and a filter inside an `EXISTS` — the same clause, two answers |
| S1-04 | `refusal-taxonomy` | **fixed** | row-level DML refused or skipped; §3 makes variables first-class |
| S1-05 | `identity` | open | label format cannot express five facts in one file |
| S1-06 | `key-error` | **fixed** | two gaps in my own key, stated rather than quietly fixed — corrected 2026-09-09 |
| S2-01 | `construct-coverage` | open | `MERGE` with a `DELETE` arm fails to parse at all |
| S2-02 | `construct-coverage` | **fixed** | `INSERT ALL`/`INSERT FIRST` walked at last — the node was always there and the analyser declined it. Closed with **no new traversal**: both public delegations already existed |
| S2-15 | `key-error` | **fixed** | stress 2's key gave the source `WHERE` a filter edge on the FIRST target of a two-target `INSERT ALL` — the six VALUE edges were written for both |
| S2-03 | `refusal-taxonomy` | **fixed** | `PIVOT`/`UNPIVOT` refused even with a static column list |
| S2-04 | `silent-loss` | **fixed** | `BULK COLLECT` into a record collection yielded nothing, silently |
| S2-05 | `transform-classification` | **fixed** | `DECODE`/`NULLIF`/`GREATEST` read as derived, not conditional |
| S2-06 | `key-error` | **fixed** | ten units unlabelled, not seven; the header claim made true |
| S2-07 | `transform-classification` | **fixed** | `FIRST_VALUE`/`LAST_VALUE` — the key says `derived`, the analyser `aggregated` |
| S2-08 | `transform-classification` | **fixed** | two `_transform_of` copies, already drifted; and `LAG` reads like `FIRST_VALUE` but scored `aggregated` |
| S2-09 | `key-error` | **fixed** | `p_depth → DIM_CUSTOMER_HIER.DEPTH` never labelled — two of three bindings |
| S2-10 | `measurement-error` | **fixed** | trigger edges carry body-relative lines, so the refusal cross-checks could not match them |
| S2-11 | `key-error` | **fixed** | stress 1's key predated convention (c); a decided convention has to reach every key |
| PG-01 | `measurement-error` | open | **an `UPDATE` is analysed and never counted.** `update_statement` is not in `band0.SUPPORTED` — def-use owns it — and def-use does not touch `statements_seen`. So every `UPDATE` in the project is missing from the parse-coverage denominator, in BOTH dialects, and an `UPDATE` that also produced no edge would be fully SILENT |

| PG-02 | `key-error` | **fixed** | `stress_pg_01`'s key claimed `expected_boundaries: []` and called the empty list the point of the package; a real schema-context boundary was declared and the key was wrong |
| PG-03 | `construct-coverage` | **fixed** | every column PROJECTED through a `LATERAL` alias is unresolved — the correlation's filter edges are correct, the projection is lost. Declared, not silent. Cause was one node type: a LATERAL scope's `expression` is the `exp.Lateral`, whose `selects` is an empty list rather than an error, so `_projection_named` read "no such column" from "I don't know" |
| PG-04 | `construct-coverage` | **fixed** | `jsonb_build_object` is opaque to its own arguments — declared `source_unavailable`, so the refusal is honest and the classification is wrong Fixed 2026-10-01 with B1-04: a built-in is transparent to its arguments. |
| PG-05 | `silent-loss` | **fixed** | **`unnest(...)` in `FROM` drops the unnested element with NO boundary at all.** `generate_series` in the same position declares `dangling_reference`, so the machinery exists and `unnest` slips past it. The only known column-level silence in the PostgreSQL path. Not a boundary but an edge: the alias columns bind to the arrays by position, so every `FROM` spelling now matches `SELECT unnest(...)` |
| PG-06 | `measurement-error` | open | **the scope contract is stated per STATEMENT and the losses are per COLUMN.** `probe_postgres` asks "did this statement produce edges?"; PG-03/04/05 all answer yes while losing a column. Three of 34 in-scope constructs are partial and the probe cannot tell |
| PG-07 | `key-error` | **fixed** | `stress_pg_03`'s five refusal subjects predicted with the wrong line convention — the codes and the set were right, the numbering was the splitter's and not the SQL's |
| PG-08 | `refusal-taxonomy` | open | `CREATE_TRIGGER_PG`'s reason never reaches the reader: band 0 declines every non-CTAS `CREATE` first and the `(unit, line)` dedup discards the classifier's better message |
| PG-09 | `silent-loss` | **fixed (D-8)** | **a bare `SELECT` produces nothing at all and is not counted** — 0 edges, 0 refusals, 0 boundaries, `statements_seen = 0`. Read-only analytical SQL is a large share of what a lineage tool is pointed at, and it currently returns a clean run with no output |
| PG-10 | `flow-classification` | **fixed (D-6)** | the phase of a `WHERE` over an already-aggregated CTE — key says post-aggregation by what it does, analyser says pre-aggregation by the keyword. Both defensible; D-2's own argument favours the key |
| PG-11 | `transform-classification` | **fixed (D-7)** | the transform of a NON-aggregated operand in an aggregated expression — "hardest on the PATH" (key) against "hardest in the EXPRESSION" (analyser). They agree everywhere except when one operand is aggregated and another is not |
| PG-12 | `key-error` | **fixed** | `stress_pg_05` omitted a CTE predicate eleven levels from its target — the identical construct is labelled a filter in `stress_pg_02`'s key |
| PG-13 | `key-error` | **fixed** | `stress_pg_05` composed influence through a window `ORDER BY` for the depth-2 grouping keys and forgot it for the depth-5 one. **Same category as S2-13** — "a uniform rule applied to the first instance only", now third across two dialects, which makes it a fault in how keys are written rather than three slips |
| PG-14 | `silent-loss` | **fixed** | **a named `WINDOW` clause lost its PARTITION BY and ORDER BY, silently.** With two functions sharing the window, `RANK() OVER w` produces NO EDGE AT ALL and the target column vanishes from the lineage. No refusal, no boundary, 100% parse coverage. The construct a large reporting query actually uses |
| PG-15 | `convention` | **fixed (D-10)** | `SUM(x) FILTER (WHERE c)` and `SUM(CASE WHEN c THEN x END)` compute the same number and produce DIFFERENT lineage — a relation-targeted filter against a column-targeted value edge Implemented 2026-10-01; the conditional xfail is now a plain assertion. |
| PG-16 | `construct-coverage` | **fixed** | **one column-less relation in the dictionary refused EVERY statement in the run**, including statements that never mention it. SQLGlot's `qualify` raises per STATEMENT on an empty schema entry. Neither shipped dictionary has one, which is why nothing had ever reached it; a capture lacking column privileges on one unrelated table would have taken a whole run to zero edges |
| PG-17 | `dialect-leak` | **fixed** | **PostgreSQL dollar-quoting `$$word$$` is classified as Oracle's `$$PLSQL_UNIT` inquiry directive and the statement is refused.** Measured cost: 3 of 3 edges on a fully readable `INSERT ... SELECT`, 0% parse coverage. A false abstention (T3.1d) with no ambiguity to justify it — the refusal register was not dialect-aware, and **four** detection sites had to be scoped, not one |
| PG-21 | `silent-loss` | **fixed** | **`infer.py` keys aliases GLOBALLY, so one alias reused in two scopes mixes two tables' columns — and last write wins, so one real table is left EMPTY.** Four lines of SQL reproduce it: `sales.orders` gets `[]` and `sales.customers` gets all four columns, two of which it does not have. This is the inference INVENTING columns, the one thing its docstring forbids, and an empty relation then triggers PG-16's whole-run refusal |
| PG-20 | `refusal-granularity` | **fixed** | **one `JSON_TABLE` in one CTE out of 85 refuses a 2,948-line query in its entirety.** The refusal is correct per construct and catastrophic per file, because the refusal unit is the STATEMENT and a modern analytical query is one statement. Same shape as PG-16, and the general case of PG-06. Measured cost: **0 edges where 329 were available.** The construct's CTE is now refused and REMOVED, so it can neither be trusted nor contribute |
| PG-22 | `refusal-granularity` | open | **a provable column inside a refused CTE is refused with it.** PG-20's fix withdraws the CTE, so `s.id AS ship_id` beside a `JSON_TABLE` loses a true edge. Deliberate, because the classifier's refusal does not imply SQLGlot declines the body — but it is a false abstention (T3.1d) with a known shape, and the next narrowing |
| PG-23 | `classifier-correctness` | **fixed** | **`_strip_noise` desynchronises on a backslash-escaped quote in an `E'...'` string, and every later literal in the file is then mis-blanked.** `E'O\'Reilly'` at torture line 1790 over-spans a CTE by 150 lines and loses 12 of 81 CTEs from the scan. The stripper feeds `classify_statement`, so this can both invent and hide refusals anywhere after such a literal. Now 81 of 81, and the span ends where the CTE does |
| PG-19 | `measurement-error` | open | **a whole-file parse failure reports `statements_seen = 0` and `parse_coverage = None`, not `seen 1 / coverage 0.0`.** The boundary IS declared, so nothing is silent — but the file drops out of the coverage denominator entirely, so a batch in which a file failed to parse scores the same as one where that file did not exist. Found only because PG-17's spurious refusal had been padding the count |
| PG-18 | `construct-coverage` | open | **a recursive CTE's `SEARCH`/`CYCLE` decorations fail the parse outright, taking the whole file with them.** Narrowed by decomposition: the breaking token is `CYCLE ... TO <v>` — `CYCLE id SET c` and `CYCLE id SET c USING p` both parse, and adding `TO 'Y'` is what fails. `SEARCH` fails too, but only in the position the file uses it (followed by `, next_cte`, or with a multi-column `BY`). Cannot go in `rewrite.py`: these DEFINE columns (`org_is_cycle`, `org_dfs_order`) the query then filters on and projects, so stripping them would silently narrow lineage — they belong in the refusal register |
| B1-01 | `construct-coverage` | **fixed** | **a `WITH … INSERT` loses every edge through its CTEs** — the CTE is declared `dangling_reference` ("relation not in dictionary"); `INSERT … WITH` works. PostgreSQL's usual CTE-write spelling. 84 of B1's 115 misses. `stress/pg/b1_core_select/REPORT.md` Fixed 2026-10-01: `band0._with_statement_ctes` hangs the statement's WITH on the query. |
| B1-02 | `transform-classification` | **fixed** | the merged column of `USING` / `NATURAL` is read as `COALESCE(left, right)` for every join type; PostgreSQL uses the left input for INNER/LEFT, the right for RIGHT, COALESCE only for FULL Fixed 2026-10-01: `band0._merged_join_columns`, after every `qualify`. |
| B1-03 | `transform-classification` | **fixed** | `percentile_cont` / `percentile_disc … WITHIN GROUP (ORDER BY x)` classified `derived`, not `aggregated` Fixed 2026-10-01: `transforms.is_aggregate` reads a WITHIN GROUP / FILTER wrapper as its aggregate. |
| B1-04 | `construct-coverage` | **fixed** | `cardinality()` treated as an unknown routine (`source_unavailable`); a built-in-catalogue gap, the same shape as PG-04 Fixed 2026-10-01 with PG-04: `analysis/builtins.py`, a per-dialect built-in catalogue. |
| B1-05 | `identity` | **fixed** | **a quoted mixed-case identifier is matched case-insensitively** — `x.region` resolves to the `"Region"` projection, a silent wrong edge Fixed 2026-10-01: `band0._position_named` - exact first, case-blind only if unique. |
| B1-06 | `measurement-error` | **fixed** | every CTE-using statement gets a spurious `context_dependent_binding` boundary naming the CTE as an unqualified schema object Fixed 2026-10-01: `procedure._context_dependent_names` skips the statement's own CTE names. |
| B1-Q1 | `convention` | open, needs a decision | what `GROUPING(a, b)` produces — today `a, b` come out as an aggregated value of the column and as a filter when it is compared in WHERE |
| B1-Q2 | `convention` | open, needs a decision | is a NON-equality join condition written in WHERE (`a.d > b.d` between two inner-joined relations) structural, as it would be in ON (D-5)? D-11 decided equalities only; `stress_01`'s self-join labels `child.signup_date > parent.signup_date` a real filter |

**`B<n>-` is the PostgreSQL batch programme** (`stress/pg/`, from 2026-10-01): the batch number, then the finding. Each batch's REPORT.md carries the evidence; the register carries the row.

**`PG-` is the PostgreSQL stress series**, kept apart from `S1-`..`S4-` for the same reason
those are kept apart from `P-`: provenance is part of a finding's identity. A `PG-` finding
was produced by a package written against `docs/postgres_scope.md`, whose whole claim is that
the scope is known in advance — so "a PG finding appeared" and "a stress finding appeared"
are statements about two different promises.

## Decisions taken — 2026-09-10

Three findings were blocked on a decision rather than on code. Abhay settled all three on
2026-09-10. **They are recorded here before any of them is implemented**, so that what was
decided can be separated from what the code later turned out to do — the same reason the keys
are written before the analyser runs.

None of the three is implemented yet. Each is still `open` in the register until a regression
test fails without it.

> **Status, 2026-09-10, later the same day.** D-3 and D-1 are landed (S2-07, S1-04); D-2 is
> not. The text below is left exactly as it was written before any code, including the one
> claim implementation proved wrong — flagged in place rather than edited out.

### D-1 · S1-04 — row-level DML carries lineage; stop refusing it

**Decided: `INSERT … VALUES`, `UPDATE` and `DELETE` all carry lineage and must be stored, not
refused.** The scope is wider than the finding as written. S1-04 was raised about
`INSERT … VALUES` from variables; the decision covers row-level DML generally:

* `INSERT … VALUES (v_cust_id, …)` — the values set the target's contents, so the variable →
  column edges are real lineage. This is the shape the finding named.
* `UPDATE` — changes the data in place, and what it sets a column to is exactly a lineage
  fact.
* `DELETE` — changes what the table contains. The predicate columns decide *which* rows go,
  and therefore what the resulting table state is.

**Refusing these is the wrong default.** A refusal says knowledge stops; here the knowledge is
available in the statement and is simply not being read.

**This also settles proposed convention (c)**, which has been sitting unanswered in the
stress-2 key since it was written. That convention labelled a `DELETE`'s predicate columns as
filter edges against the deleted relation; the analyser emits none, and `s2_delete_with_subquery`
produces zero edges and no refusal. The decision above answers it: **convention (c) stands.**
The key was right and the analyser is wrong, which converts that half of S2-06's list from an
undecided convention question into an ordinary defect.

**The cost is still parse coverage and it must still be measured first.** Narrowing a refusal
buys edges and spends coverage, and coverage has 5.6 points of headroom against a kill
criterion. Measure before touching, as the finding said — the decision changes what to build,
not whether to check what it costs.

> **WRONG, AND MEASURED WRONG WITHIN THE HOUR — left standing because the point of writing
> decisions down before the code is that they can be checked against it.** There is no cost.
> Parse coverage is `analysed / seen` and a refused statement is *already* counted as seen, so
> un-refusing moves it into the numerator: phase-0 coverage went **76.1% → 80.4%** and the
> headroom against the kill criterion went from 5.6 points to 10.4. The sign was inherited
> from S2-04, where the change ran the other way — that fix ADDED refusals and did cost
> coverage. **The instruction to measure first was right for the wrong reason**, and it is the
> instruction rather than the reasoning that is worth keeping.

### D-2 · S1-03 — a filter edge must say WHICH phase it acts in

**Decided: `WHERE`, `HAVING` and `GROUP BY` are three different things and the edge must say
which.** They are not interchangeable and reporting them under one undifferentiated `filter`
flow tells a reader less than the source does:

| clause | when it acts | what it does |
|---|---|---|
| `WHERE` | **before** aggregation | removes rows, so they never reach the aggregate |
| `HAVING` | **after** aggregation | removes groups, after every row has been counted |
| `GROUP BY` | *at* aggregation | removes neither — decides which rows collapse together |

`WHERE` and `HAVING` produce **different results** from the same-looking predicate, and a
lineage report that cannot tell them apart cannot answer the question a reader actually has.
`GROUP BY` gets the third kind rather than being dropped: it is a real dependency of the
output, and discarding it would mean nothing in the report records that `period_month` was
grouped on.

**So `flow: filter` gains a phase — `pre-aggregation`, `post-aggregation`, `grouping`.** The
shape of that field in the IR is an implementation question; the semantic decision is that the
three are distinguishable and all three are kept.

**The double-count objection is answered by the distinction, not overruled by it.** S1-03
observed that two of the three false positives are columns *already* value sources for the
projection, so emitting them again as filter influence says one relationship twice. With the
phase on the edge they are two different true statements about the same pair — `ORDER_DATE`
supplies `period_month`'s value, **and** `ORDER_DATE` is what the rows were grouped by. Under
one flat `filter` flow that was a double-count; under three kinds it is not.

**This one moves the phase-0 keys.** The analyser and every key encoding the current answer
must change together, or they disagree silently — which is how a benchmark stops measuring
anything.

### D-3 · S2-07 — `FIRST_VALUE` / `LAST_VALUE` are `derived`

**Decided: `derived`. The key was right and the analyser is wrong.** They are window
(analytic) functions, not traditional aggregations: nothing is summed or counted, and the
value written appears verbatim in some row of the input. The window selects *which* row
supplies it.

The line this draws is **window-ness is not aggregation-ness** — `SUM() OVER ()` stays
`aggregated` because it computes a total over a set, and the `OVER` clause is not what makes
it so. Being an analytic function is not sufficient; computing a value over a set is.

Consistent with the ladder S2-05 already established — `aggregated > conditional > derived >
identity` — and with `MAX … KEEP (DENSE_RANK FIRST …)`, which the stress-2 key labels
`aggregated` because `MAX` genuinely aggregates. Worth re-checking that case when this lands.

### D-4 · S2-12 — a window's ordering is a THIRD kind of claim

**Decided 2026-09-10, and it required correcting the question first.** S2-12 was raised as
"is a window's `PARTITION BY` / `ORDER BY` filter influence?" — a yes/no. The answer is
neither.

* **Not `value`.** `ROW_NUMBER() OVER (ORDER BY total DESC)` does not take its value from
  `total`. The corpus has held this since `sq_03` and it was never in question.
* **Not `filter` either, and this is the half that was wrong.** **A window function removes
  no rows.** Every input row survives it. An edge saying `period_month` decided which rows
  landed in `fct_product_sales` is simply false, and it was being emitted.

What is true is that `rank_in_month`'s value depends on `period_month` and on `gross` —
change either and the rank changes — while neither is copied into it. `Flow.INFLUENCE`.

**The target is the COLUMN, not the relation.** A filter edge points at a relation because
it is a claim about rows. This one is about one output column, and the difference is the
difference between *"something about this table depends on order_date"* and *"rank_in_month
depends on order_date"*.

**Three arguments for it were already written in the keys, before the decision existed.**

1. `sq_03`'s ROW_NUMBER partition and LAG partition collided on one four-tuple under the
   relation-targeted form. One of the two was unstatable, and the key's own 2026-09-06
   completion note had already flagged the LAG half as "an omission, not a different rule".
2. Stress 1's `stress_cte_window` has the same collision — a ROW_NUMBER partition and a LAG
   ORDER BY, both tracing to `order_date`, both about different output columns.
3. `s2_keep_dense_rank`'s key says, in prose, *"line_amount is a filter influence **on this
   column** and not a source of its value"* — and then labels nothing, because "on this
   column" could not be said. D-4 gives that sentence a form.

**The two key sets held opposite conventions and nothing compared them.** `sq_03` labelled
these as `filter` edges; both stress keys labelled them not at all. The same analyser
behaviour therefore scored as correct in phase 0 and as a false positive in stress, for two
runs — the **S2-11 shape**, and the second instance of it in two days.

## Open work — what is left, and what each one needs

**One open. Nineteen fixed. Every STRESS finding has recurred across stress runs** — that
claim is about the `S1-`/`S2-` series only, and P-01 is the reason the qualifier is now
written down: a probe finding cannot recur in a stress package, because no stress package
contains the construct. **Both stress packages have ZERO false positives** — 100% precision on every band
and every flow in each — and so does the phase-0 corpus outside its one long-standing
band-1 value FP. **All five decisions D-1 to D-5 are landed, D-2 included** — S1-04's three
halves, S2-07, and the `HAVING`/`GROUP BY` split that touched every phase-0 key.

Each implementation turned up a new finding — **S2-08** from D-3, and **S2-09**, **S2-10**
and **S2-11** from D-1 — which is the pattern worth noticing: the defects were being hidden
by the refusals and omissions in front of them. Two of the four are about the *benchmark*
rather than the analyser, and neither would have been found by running the analyser harder.

| ID | Category | Blocked on | Cost if left |
|---|---|---|---|
| **S2-02** | `construct-coverage` | nothing external — **it parses**; the analyser declines a tree it already has | 7 edges; the honest refusal makes this a coverage gap, not a defect |
| **S2-01** | `construct-coverage` | a newer SQLGlot, untried — it cannot parse `MERGE … DELETE` at 30.18.0 | 7 edges, and it is a standard slowly-changing-dimension shape |
| **S1-05** | `identity` | **NOTHING — the evidence is in.** Stress 4 is production-shaped and 28.1% of its key was unstatable across 27 of 28 units, one fact claimed by seven statements | the benchmark cannot express a quarter of what it knows; it *hides* whether fixes worked |

### What each open finding is waiting for, in one line

Only the three in the table above are open. The rest of this list is kept because *what a
fixed finding turned out to be* is the part worth carrying forward.

- **S2-02** — needs multi-table-insert semantics. The refusal is *true*, so this is
  capability, not correctness — and **it parses**, so there is no parser dependency at all
  (corrected 2026-09-11). The cheapest real coverage left.
- **S2-01** — `MERGE … DELETE` never becomes a tree. Try a newer SQLGlot first; the rewrite
  route is ruled out on the admission test, so the fallback is a refusal code that names the
  real reason rather than "Invalid expression".
- **S1-05** — the match key. Gated on the production package, deliberately; see below.
- **S2-09** — done with S1-04; kept in the register because a key error is evidence.
- **S2-10** — `false_abstentions_recovered: 0` was not a measurement. Fixed by making the
  blind spot countable, because D-1 had already removed the live instance.
- **S1-03** — **D-2 landed.** `HAVING` is a `filter` with `phase: post-aggregation`; `GROUP BY`
  is `influence` on the aggregated column. 80 new labels across 12 key files.
- **S2-12** — **D-4: a third flow, `influence`, targeting the COLUMN.** Landed. A window
  removes no rows, so `filter` was false; the dependency is real, so silence was too.
- **S1-05** — the match key. **Do not start before the production package**: it is the decision
  that most wants real code in front of it, and the verdict's condition still stands. The
  number it has to beat is now 31%, not 21% — see S2-06 below.
- **S2-08** — **both halves landed.** One classifier in `analysis/transforms.py`; `LAG`/`LEAD`
  are `derived` by D-3's rule. Neither moved a number, and both reasons are recorded.

### Fix log

| ID | Commit | Phase-0 impact |
|---|---|---|
| S1-01 `identity` | `a510049` | none — every cell identical |
| S2-04 `silent-loss` | `11314b3` | none |
| S1-02 `construct-coverage` | `46f4617` | none |
| S2-05 `transform-classification` | `1108223` | none |
| S2-03 `refusal-taxonomy` | `cda24a3` | none |
| S2-06 + S1-06 `key-error` | `d1b591f` | none — `cells` byte-identical to `phase0_final.json` |
| S2-07 `transform-classification` (D-3) | `3e9187e` | none — `cells`, `gate`, `parse_coverage` and `honest_abstention` all identical |
| S1-04 + S2-09 `refusal-taxonomy` (D-1, `INSERT … VALUES`) | `7b25db2` | **`cells` identical — and the first fix to move a phase-0 number.** Parse coverage 76.1% → 80.4%; false-abstention rate 9.1% → 0% |
| S1-04 + S2-11 `refusal-taxonomy` (D-1, `DELETE` and `UPDATE`) | *this change* | `cells` identical. Parse coverage 80.4% → **81.6%** |

**Eleven fixes, and the phase-0 GRID has not moved once.** That is a statement about the corpus,
not about the analyser: none of these constructs appears in it in the form that breaks. It
sharpens the verdict's existing caveat considerably — the corpus is not just synthetic, its
*construct mix and package shape* are unrepresentative in ways that hide real defects.

**S1-04 is the first to move a phase-0 number of any kind**, and it moved the two that are
not on the grid: parse coverage 76.1% → 80.4%, and the false-abstention rate to zero. Worth
noticing what that says about which axes the corpus can actually exercise. Nine fixes'
worth of recovered lineage was invisible to precision and recall, and the tenth showed up
only on the honesty axes — the ones ADR-0001 added because the grid alone cannot tell a
correct answer from a lucky one.

### Where the stress packages stand now

Both re-scored after the ten fixes. The per-run tables further down are the **first-run**
figures and are left as measured. The analyser did not change in the S2-06 pass — **only the
keys did** — so every movement in this table is the benchmark getting more honest, in both
directions.

| | stress 1 | | stress 2 | |
|---|---|---|---|---|
| | before S2-06 | now | before S2-06 | now |
| 0 value | 100% / 100% | 100% / 100% | 93.9% / 75.6% | **100% / 69.4%** |
| 0 filter | 61.5% / 88.9% | **75.0% / 90.0%** | 76.0% / 76.0% | **78.6% / 84.6%** |
| **1 value — the gate** | **95.5% / 87.5%** | **100% / 96.2%** | **81.8% / 56.2%** | **100% / 76.2%** |
| 1 filter | 100% / 84.6% | 100% / **86.7%** | 100% / 75.0% | 100% / 75.0% |
| 2 value | 100% / 100% | 100% / 100% | 100% / 50.0% | 100% / 50.0% |
| 2 filter | 100% / 100% | 100% / 100% | 100% / 100% | 100% / **50.0%** |
| parse coverage | 76.9% | **92.9%** | 65.5% | **80.0%** |

**Precision is now 100% on every band and flow of both packages except band-0 filter**, and
what remains there is S1-03's `GROUP BY` columns — three false positives in each file, the
thing D-2 exists to settle.

**The `now` column includes D-3 (S2-07), which moved four cells and only in stress 2.**
Band-0 value went 97.0% / 65.3% → **100% / 69.4%** — two false positives removed and the two
matching false negatives recovered, exactly as predicted before running. Stress 1 has no
`FIRST_VALUE`/`LAST_VALUE` and did not move. **Band-0 value precision is now 100% on both
packages, alongside band-1 value.**

**Precision went up everywhere it moved, and recall went down.** Both are the same fact: the
analyser's correct edges were being counted as false positives against units I had not
labelled, and the facts it does *not* produce were not being counted at all. Band-1 value
precision reaching 100% on both packages is the clearest single result — **there is now no
band-1 value edge in either stress package that the analyser produces and the key denies.**

**AND STRESS 1 NO LONGER CLEARS THE GATE.** 84.6% against an 85% recall floor, missing by
0.4 points, on the strength of one label: `V_IDS -> TMP_RECENT.CUST_ID`, the `FORALL` write
that S2-04 taught the analyser to refuse by name. It was always missing; it was never
counted. The gate margin reported for stress 1 — "clears its floor by 0.5 points" — was
measured against a key with a hole in it, and the hole was on the gate's own axis.

Stress 2 still fails both floors. Most of what remains there is S1-04, S2-01 and S2-02 —
lost statements rather than wrong answers.

**Band-2 filter fell from 100% to 50% and that is the honest number.** The key now states
the four edges `s2_dynamic_in_loop` would produce if it were analysable, and the refusal of
that unit is *correct*. A correct abstention costs recall; a benchmark that only counts the
cost of wrong refusals cannot tell you what abstaining buys.

---

### Recurrence — what stress 2 settled

**`identity` reached three, and the rule said what to do about it.** The register's own
condition was *"if a third turns up in stress 2, the match key stops being a scoring decision
and becomes an IR decision."* It turned up, and it scaled badly:

| | labels | colliding keys | facts unstatable | share |
|---|---|---|---|---|
| stress 1 (362 lines, 13 units) | 68 | 5 | 5 | **7%** |
| stress 2 (753 lines, 28 units) | 130 | 15 | 27 | **21%** |
| stress 2, **key completed** (S2-06) | 169 | 32 | 52 | **31%** |

The file doubled and the loss tripled, because collisions grow with the number of *pairs* of
writers to a table, not with the number of writers.

**The 21% was measured on an incomplete key and it understated the problem.** Labelling the
ten missing units added 39 facts, of which **25 could not be stated** — the validator
rejected the file until they were removed. Nearly two thirds of the new work was unstatable,
against 21% of the old, because every one of those units writes a table that something else
in the file already writes. Three units — `s2_three_way_resolution`, `s2_row_limiting`,
`s2_truncate_reload` — contribute **zero** labels between them. The growth curve is 7% at 13
units, 31% at 28, and it is steeper than the first measurement suggested. One key in stress 2 has **five** members —
`STG_CUSTOMER.CUST_ID → TMP_RECENT.CUST_ID`, written by five different units. Bands differ
within collisions (0, 1 and 2 all appear), so the collision also forces a choice about which
band is charged for the fact.

**S1-02 is not about `UNION`.** Stress 2 was built to ask that, and `INTERSECT` and `MINUS`
fail identically — both refused as *"INSERT … VALUES carries no column lineage from a
relation"*. The defect is **any top-level set operator under an INSERT**, which is a much
larger surface than the original finding suggested.

**S1-03 and S1-04 both recurred** unchanged: `GROUP BY` columns as filter edges (3 more false
positives), and `INSERT … VALUES` from variables (`s2_recursive_walk`).

**Nothing in the register has yet failed to recur.** Every open finding from stress 1
reappeared in stress 2, which is the strongest argument available for fixing them before
writing stress 3.

**Categories.** `identity` · `construct-coverage` · `flow-classification` ·
`refusal-taxonomy` · `key-error` · **`silent-loss`** (new) · **`transform-classification`**
(new). `silent-loss` earns its own name because the distinguishing feature is not the missing
construct but the **absence of a refusal** — the failure mode the product exists to prevent,
and the only one a coverage statement cannot report. `transform-classification` is a
different axis from `flow-classification`: one is value-versus-filter, the other is which
transform class, and ADR-0001 §4 makes a wrong transform a MISS rather than partial credit.

---

## Stress test 1

362 lines, 13 program units, 68 hand-labelled edges written from source before the analyser
was run over the file. Six forbidden-edge rules, three expected boundaries. *(The key now
holds 81 edges: S1-06's two corrections were applied 2026-09-09. The figures in this section
are the first run and are left as measured.)*

**Zero forbidden edges produced.** None of the six named wrong answers fired — including the
UDF argument read as a value source, the window `PARTITION BY` read as a value source, and
path fusion through `tmp_recent`. Those are the failures that matter most and the corpus
defences held at four times the package size they were built against.

## Score

| band / flow | precision | recall |
|---|---|---|
| 0 value | 100% | 87.5% |
| 0 filter | **58.3%** | 77.8% |
| 1 value — the gate | **95.5%** | 87.5% |
| 1 filter | 100% | 84.6% |
| 2 value | 100% | 100% |
| 2 filter | 100% | 100% |

Guard accuracy 100% of 10. Parse coverage 75% (12 statements seen, 9 analysed).

**The gate clears its floor by 0.5 points**, against 1.2 on the phase-0 corpus. On a file
four times the size, written to be awkward.

---

## S1-01 · `identity` · FIXED — band 0 deduplicated on `match_key()` and destroyed facts

`match_key()` is `(source, target, flow, transform)` — no guard, no origin. Band 0 collapsed
its edge set on that key before returning, so **two procedures writing the same columns
produced one edge**. The survivor kept one origin, one band and one guard; the other fact was
gone before it ever reached the IR — not refused, not declared, not counted.

This is worse than the scoring-layer collision ADR-0001 amendment 1b measured, because there
the ledger still holds both. Here the second fact never exists. It also made `procedure.py`'s
comment — *"merge on ledger identity so two facts differing only by guard or origin both
survive"* — a statement about something that had already happened upstream.

**Same root cause as S1-05**, one layer earlier: both key on `match_key()`, which carries
neither guard nor origin.

**How it showed up:** `stress_dynamic_mixed`'s constant `EXECUTE IMMEDIATE` is recovered
correctly and yields three band-2 edges. `stress_transaction_control` writes the same three
columns of `FCT_REVENUE_STAGE` statically. The static edges sorted first, and **the entire
recovered dynamic statement disappeared** — while the dynamic-SQL machinery reported success.
Band-2 value recall read 40%, and the cause was nothing to do with dynamic SQL.

**Fix:** deduplicate on `identity()`. **Free on the phase-0 corpus — every cell
byte-identical**, because those packages are small enough that two statements rarely write
the same pair. Band-2 value recall here went **40% → 100%**. Pinned by
`tests/test_band0.py::test_two_units_writing_the_same_columns_both_survive`.

## S1-02 · `construct-coverage` · FIXED — top-level set operators under `INSERT` were refused

**As first measured:** `stress_set_ops` was refused as **"INSERT ... VALUES carries no
column lineage from a relation"**. It is not an `INSERT ... VALUES`; it is an `INSERT ... SELECT` whose expression
SQLGlot parses as `exp.Union` rather than `exp.Select`, and the band-0 analyser treats
anything that is not a `Select` as `VALUES`.

The whole statement is dropped — **five labelled edges, including both `UNION` arms.**

**Why the corpus missed it:** `s5_positional_union` wraps its `UNION` in a subquery
(`SELECT col_a, col_b FROM ( … UNION ALL … )`), so the INSERT's expression is a `Select` and
the set-operation path is reached. The form real ETL actually writes — `UNION` directly under
the INSERT — was never covered.

It is at least declared rather than silent, so it is a loud failure. But the reason given is
false, and anyone reading the boundary would look for a `VALUES` clause that is not there.

**Confirmed general by stress 2:** `INTERSECT` and `MINUS` fail identically. The defect is
every top-level set operator, not `UNION`.

**Fix.** Each arm is analysed as its own `SELECT` against the same target — the treatment
`MERGE`'s two arms already get — and identical facts deduplicate downstream. Arms are
flattened recursively, because sqlglot nests them left-associatively.

**Arms are not treated uniformly, and that is the semantic half.** A `UNION` arm **adds
rows**, so it genuinely supplies the values of the rows it contributes. `INTERSECT` and
`MINUS` only **remove** rows from the first arm: the value written always comes from arm 1,
and the later arms decide which of those survive. Treating every arm as a feed would have
claimed `gtt_stage.cust_id → gtt_stage.cust_id` for a `MINUS` against the target itself — a
value edge for rows that were specifically **excluded**. Later arms of `INTERSECT`/`MINUS`
therefore become filter influence on the written relation.

That rule was written into the stress-2 key *before* this code existed (convention (a)), so
implementing it is not the key being tuned to the analyser.

**Effect.**

| | before | after |
|---|---|---|
| phase-0 measurement | — | **every key identical**, verified by stash-and-compare |
| stress 1 band-0 value recall | 87.5% | **100%** |
| stress 1 parse coverage | 69.2% | **76.9%** |
| stress 2 parse coverage | 51.7% | **58.6%** |

**Phase 0 could not have caught this and still cannot show it.** Both `s5_positional_union`
and `sq_05_set_operations` wrap their set operations in a subquery, so the corpus has no
top-level form at all — which is why the measurement is byte-identical before and after.

**And stress 2 barely moved, which is itself a finding.** `s2_intersect_minus` now emits
exactly the nine edges its key predicted, and band-0 value TP did not change — because those
match keys were **already satisfied by other units** writing the same columns. A fix that
recovered nine real edges is invisible to the score. That is S1-05's cost, demonstrated
rather than argued: the collision does not just lose facts, it hides whether they came back.

Four regression tests in `tests/test_complex_sql.py`, including the `MINUS`-arm semantics
and a three-armed `UNION` for the recursive flattening.

## S1-03 · `flow-classification` · OPEN — **the finding itself was misdiagnosed**

> **READ THIS FIRST, 2026-09-10.** The section below is the finding as originally written
> and it is **wrong about what the analyser does**. It is left standing because a register
> that quietly corrects itself is no more trustworthy than a key that does. The correction
> follows it.

### The correction

**The analyser emits no `GROUP BY` filter edges and no `HAVING` filter edges. It never has.**
Established two ways before touching anything:

* **By code.** Nothing in `src/` reads either clause. `grep` for `args.get("group")`,
  `args.get("having")`, `exp.Group` and `exp.Having` across the whole package returns
  nothing. `band0._filter_edges` reads `args.get("where")` and only that.
* **By running it.** An `INSERT … SELECT` with `WHERE o.currency = 'GBP'`, `GROUP BY
  o.cust_id, TRUNC(o.order_date,'MM')` and `HAVING SUM(o.gross_amount) > 0` produces exactly
  one filter edge — `STG_ORDERS.CURRENCY`. The GROUP BY and the HAVING produce nothing.

**So what are the three false positives?** `STG_ORDERS.ORDER_DATE`,
`STG_ORDER_LINES.PRODUCT_ID` and `STG_ORDER_LINES.LINE_AMOUNT → relation:FCT_PRODUCT_SALES`
are **window `PARTITION BY` and `ORDER BY` columns**, emitted by `_influence_columns` and
traced *through the CTE* to their base columns:

| the edge | what the finding said | what it is |
|---|---|---|
| `STG_ORDERS.ORDER_DATE` | `GROUP BY` | `ROW_NUMBER() OVER (PARTITION BY m.period_month …)` |
| `STG_ORDER_LINES.PRODUCT_ID` | `GROUP BY` | `LAG(m.gross) OVER (PARTITION BY m.product_id …)` |
| `STG_ORDER_LINES.LINE_AMOUNT` | `HAVING` | `… ORDER BY m.gross DESC`, and `m.gross` is `SUM(l.line_amount)` |

The mistake was a coincidence that held up under casual checking: `stress_cte_window` groups
by `TRUNC(o.order_date,'MM')` and `l.product_id` **and** partitions its windows by the same
two columns, one CTE layer up. The columns matched the GROUP BY, so the GROUP BY was blamed.

**Verified by instrumenting the emitting call site**, not by reading the SQL again — the
edges come from `band0.py:810`, which is the `_influence_columns` branch, not from
`_filter_edges`.

### What this does to D-2

**D-2 is not a reclassification. It is a feature.** The decision reads naturally as
"stop lumping three things under one word", but there is nothing to un-lump: two of the three
kinds are not emitted at all. Implementing it means **adding** `HAVING` and `GROUP BY` edges
that have never existed, then tagging all three phases.

That may well still be the right thing — the semantic argument is untouched by this, and
`WHERE` versus `HAVING` genuinely produce different results from the same-looking predicate.
But the cost profile is the opposite of what the fix order assumed:

* it **fixes none of the current false positives**, because none of them is a GROUP BY or a
  HAVING edge;
* it **adds** edges to every package that aggregates, so every key gains labels rather than
  having existing ones re-tagged;
* precision can only go **down** until the keys are updated, and recall is unaffected either
  way.

### D-2 as built, 2026-09-11

**Three clauses, sorted by what they actually do**, rather than one field covering all
three. D-4 had just created `Flow.INFLUENCE` for "decided which value, not which rows", and
`GROUP BY` turned out to be exactly that shape - so the mechanism was already there.

| clause | flow | target | phase |
|---|---|---|---|
| `WHERE` | `filter` | the written relation | `pre-aggregation` |
| `HAVING` | `filter` | the written relation | **`post-aggregation`** |
| `GROUP BY` | **`influence`** | **the aggregated column** | n/a |

`WHERE` and `HAVING` both **remove rows** - a group is a row once it has formed - so both
are `filter`, and the phase is what separates them. `GROUP BY` removes nothing, so calling
it a filter would repeat the error D-4 was raised to fix.

**`phase` defaults to `pre-aggregation` and only a `HAVING` writes it.** There are 170
filter labels across 38 key files and every one predates D-2; adding `phase:
pre-aggregation` to all of them would restate the default 170 times and make the churn
indistinguishable from a real change in any future diff. The default *is* the assertion, and
it is correct for every filter edge that is not a `HAVING`. Phase is fully in the match key -
there is no soft matching. `ForbiddenEdge` and `OriginAssertion` are deliberately
phase-indifferent: a forbidden edge names a wrong endpoint BINDING, and no predicate phase
changes that.

**Only aggregated columns are influenced.** The grouping keys are usually projected as well,
and their values are copied through untouched; emitting influence onto one would say a column
decides its own value.

### Two defects found by building it

**The grouping scope has to be resolved, and the first cut resolved it wrongly.** A statement
with two aggregating CTEs has two independent groupings, and attaching the union to every
aggregated column would claim `refund_total` is governed by the revenue CTE's `GROUP BY`.
That much was designed in. What was not: the first implementation **returned at the first
aggregating scope it found**, so a grouping two levels down was silently dropped.
`sq_02_cte_chain` is the case - `net_sales` is `gross - refunded` where `refunded` is a
`MAX(...)` over a `SUM(...)` from a different CTE, each with its own `GROUP BY` - and
`STG_RETURNS.PRODUCT_ID` was missing. It looked right in `stress_cte_window` only because
there the two aggregations are **siblings rather than nested**, so both are found at depth 1.
A rule that depends on how the CTEs are stacked is not a rule. Fixed to traverse every
aggregating scope on the path, which is what this analyser does everywhere else.

**The report started lying, and that is worse than a report that omits.** `EdgeKey` grew from
`(source, target, flow, transform, guard)` to `(..., phase, guard)`, and four renderers read
the guard at index 4. Every miss and false positive printed `when pre-aggregation` where the
guard belonged - so two edges differing only by guard looked identical, and a guard that was
there looked absent. Fixed in one `describe_key`, which also shows the phase **only for a
`filter`**: on a value edge it would be noise that reads like a claim.

### Effect

| | signed | now |
|---|---|---|
| phase-0 gate | 96.2% / 96.2% | **identical** |
| phase-0 0/filter | 38 TP, 0 FP, 5 FN | 35 TP, 0 FP, 5 FN |
| phase-0 0/influence | - | **50 TP, 0 FP, 0 FN** |
| phase-0 1/influence | - | **4 TP, 0 FP, 0 FN** |
| stress 1 0/influence | - | **25 TP, 0 FP, 0 FN** |
| stress 1 2/influence | - | **4 TP, 0 FP, 0 FN** |
| stress 2 0/influence | 11 TP (D-4 only) | **16 TP, 0 FP, 0 FN** |
| false positives, both stress packages | 0 | **0** |

**Every influence cell is 100% / 100%, in all three packages.** 80 new labels across 12 key
files, and not one of them disagrees with the analyser - which is the expected result for a
mechanical rule and is *not* evidence the rule is right. The evidence for that is the
clause-by-clause check against source in `sq_01` (flat `GROUP BY`, three grouping keys, four
aggregates), `sq_02` (nested CTEs), `stress_cte_window` (sibling CTEs) and `sq_03` (a window
and a grouping on the same column).

**One label needed correcting by hand and a test caught it.** `s7_unexercised_branch`'s
generated labels missed `unexercised: true` and sat at band 0, because the analyser reports
the INSERT's line and the key reports the SELECT's, so the lookup that copies band and guard
from the same statement's existing labels missed by one line.
`test_the_harness_separates_disagreement_from_absence` failed on
`unexercised_accuracy == 1.0`. **The unexercised axis is the one thing in this key set that
cannot be backfilled**, and a generated label silently defaulting it to false is exactly the
error that axis exists to prevent.

**The one `HAVING` in the entire corpus is in stress 1.** That is why the construct went
unread for three stress runs, and it is the sharpest available statement about the corpus:
a clause present in almost every real aggregate query appears once across 38 packages.
Stress 1's key had already flagged `HAVING` as one of three constructs with no precedent in
phase 0 - it was right, and the gap was in the analyser rather than the labelling.

### The question the false positives actually raise — undecided

**Is a window's `PARTITION BY` / `ORDER BY` filter influence on the written relation?** The
analyser says yes and has a reasoned docstring for it. Every key says no, by omission. That
disagreement is the whole of the remaining band-0 filter precision gap in both stress
packages, and it is a *different* convention question from D-2.

The keys' stated convention — *"a window function's ARGUMENT is a value source; its
PARTITION BY and ORDER BY are not"* — settles that they are not **value** sources and is
silent on whether they are **filter** edges. Both readings are consistent with it, which is
how the two sides have disagreed for two stress runs without either noticing.

### The other three false positives, for completeness

Stress 2 has three more, and none is a GROUP BY either:

* `GTT_STAGE.AMOUNT`, `GTT_STAGE.PERIOD_MONTH → relation:GTT_STAGE` (`s2_intersect_minus`) —
  later arms of `INTERSECT`/`MINUS` as filter influence, convention (a) from S1-02. The
  analyser applies it more widely than the key labelled it.
* `STG_ORDER_LINES.ORDER_ID → relation:DIM_CUSTOMER` (`s2_update_correlated`) — the *other*
  operand of the `EXISTS` join predicate. **This is S1-06 for the third time**: the key
  labels one operand and the convention says both.

---

### The finding as originally written (2026-09-08), left as measured

Three of the five band-0 filter false positives are `GROUP BY` columns:
`STG_ORDERS.ORDER_DATE`, `STG_ORDER_LINES.PRODUCT_ID` → `relation:FCT_PRODUCT_SALES`, plus
`STG_ORDER_LINES.LINE_AMOUNT` from the `HAVING`.

`GROUP BY` does not select rows; it decides which rows aggregate together. Two of those
columns are **already value sources** for the projected `period_month` and `product_id`, so
emitting them again as filter influence double-counts one relationship under two flows. The
`HAVING` is arguably a genuine filter — it selects groups — and that half may be correct.

Needs a decision, not a patch. Recorded rather than fixed.

**DECIDED 2026-09-10 — see D-1/D-2/D-3 above; this one is D-2.** The framing in the paragraph
above turned out to be the wrong question. It asked whether a `GROUP BY` column is filter
influence, yes or no, and the answer is that **`filter` was never one thing.** `WHERE` removes
rows before aggregation, `HAVING` removes groups after it, and `GROUP BY` removes neither —
three behaviours reported under one word. All three are kept and the edge says which.

The double-count objection survives as an observation and stops being a reason to drop the
edge: with the phase on the edge, `ORDER_DATE → period_month [value]` and
`ORDER_DATE → FCT_PRODUCT_SALES [filter/grouping]` are two different true statements rather
than one relationship counted twice.

Not yet implemented. Both the analyser and every phase-0 key encoding the flat `filter` flow
have to move in the same change.

## S1-04 · `refusal-taxonomy` · FIXED — `INSERT … VALUES` from variables was refused

Three misses, all of the same shape: `INSERT INTO tmp_recent VALUES (v_cust_id,
v_last_login)` and the audit write inside the exception handler. The refusal reason —
*"carries no column lineage from a relation"* — is true of relations and false of variables,
which ADR-0001 §3 makes first-class nodes.

Already the one false abstention in the phase-0 measurement (`s6`). This test adds three more
instances and shows the shape is common: writing a temp table row-by-row from cursor
variables is ordinary PL/SQL.

**DECIDED 2026-09-10 — D-1, and the scope is wider than this finding.** Row-level DML carries
lineage and must be stored rather than refused: `INSERT … VALUES` as described here, and
`UPDATE` and `DELETE` on the same reasoning. An `UPDATE` sets a column to something, which is
a lineage fact by definition; a `DELETE` changes what the table contains, and its predicate
columns decide which rows go.

**That also answers proposed convention (c)** — a `DELETE`'s predicate columns as filter edges
against the deleted relation. The stress-2 key asserted it, the analyser emits nothing, and
S2-06 logged it as an open convention question. It is now decided in the key's favour, which
makes `s2_delete_with_subquery`'s zero edges and zero refusals an ordinary defect rather than
a disagreement. **Note the shape of that failure: not a refusal, but silence** — the
`silent-loss` category, in a unit nobody had classified that way.

**THE PARSE-COVERAGE COST DOES NOT EXIST, AND THE FINDING HAD THE SIGN BACKWARDS.** Both
this section and D-1 said to measure the cost first because "narrowing a refusal costs parse
coverage, which has 5.6 points of headroom against a kill criterion". It was measured first,
and coverage went **up**. Parse coverage is `analysed / seen`, and a refused statement is
already counted as *seen*; un-refusing it moves it into the numerator. The claim appears to
have been inherited from S2-04, where the change ran the other way — that fix ADDED refusals
and did cost coverage. **Phase-0 parse coverage 76.1% → 80.4%**, and the headroom against the
kill criterion went from 5.6 points to 10.4.

**Implemented in `defuse._analyse_insert_values`, not in band 0, and that placement is the
whole design.** An unqualified name in a VALUES list is a PL/SQL variable far more often than
a column, and band 0 has no unit scope to tell the two apart — binding `v_cust_id` to
whichever relation is in scope is exactly the silent failure `defuse` exists to prevent. So
band 0 stops refusing and **claims nothing**, which is not the same as claiming there is
nothing: the statement is counted as *analysed*, and def-use owns the VALUES list
(`triggers._insert_values_edges` already owned the `:NEW.` case).

Binding is **positional** against the target column list — the rule a `UNION` arm and a
`FETCH INTO` already follow. `INSERT INTO t VALUES (…)` with **no column list** is declared
as unresolved rather than bound against the dictionary's column order: that order would
usually be right and would sometimes write a value into the wrong column, which is a false
edge that type-checks and reads perfectly.

**Effect.**

| | before | after |
|---|---|---|
| phase-0 `cells` | — | **identical**, every one |
| phase-0 parse coverage | 76.1% | **80.4%** |
| phase-0 false-abstention rate | 9.1% (1 of 11) | **0%** |
| phase-0 refusals | 11 | 9 |
| stress 1 parse coverage | 76.9% | **92.3%** |
| stress 1 band-1 value — the gate | 100% / 84.6% | **100% / 96.2%** |
| stress 2 parse coverage | 65.5% | **79.3%** |
| stress 2 band-1 value — the gate | 100% / 55.0% | **100% / 71.4%** |

**The phase-0 grid did not move and the phase-0 REPORT did.** Ninth fix in a row with
byte-identical cells — and the first one to move a phase-0 number at all. `s6_updatable_view`
was the signed measurement's only false abstention; it is gone, and the axis reads zero.

**`s6`'s cells could not move, for a reason worth stating.** The edge that refusal was
costing — `DIM_CUSTOMER.CUST_ID → DW_AUDIT_LOG.CUST_ID` — **was already being produced**, by
the trigger path, from the very statement band 0 was refusing. The package was simultaneously
declaring "I could not read this" and reporting an edge from it. See **S2-10**: the check
that exists to catch precisely that contradiction cannot see it.

**Stress 1 is back over the gate** — 96.2% against the 85% floor, from 84.6%. Three guarded
writes recovered with their guards intact (`when 'A' = V_STATUS`, `when EXCEPTION
NO_DATA_FOUND`), which matters because the guard is part of the match key: recovering the
edge without it would have scored as a miss *and* a false positive.

**`INSERT … VALUES` landed first, on its own measurement.** The `UPDATE` and `DELETE` halves
followed and are recorded below, each measured separately.

### S1-04, the `DELETE` half — convention (c), implemented

**`DELETE` was not refused. It was skipped, uncounted, in silence.** `delete_statement` was
not in band 0's `SUPPORTED` set, so the loop hit `continue` *before* `statements_seen += 1`:
no edges, no refusal, and not even a statement seen. Def-use did not claim it either. **That
is the S2-04 root cause exactly** — two passes each correctly deciding the statement was not
theirs, and nobody owning the result — and it went unnoticed because S2-06 had logged
`s2_delete_with_subquery` as an open *convention* question. The convention was worth
debating; the silence never was, whichever way it went.

Implemented in `band0._analyse_delete`. A DELETE is set-based and has no variables, so unlike
`INSERT … VALUES` it belongs in band 0 — the opposite placement, for the same reason.

**Every column in the predicate, at any depth.** `stg_customer.status_code` two levels down
inside an `IN` subquery still decides which revenue rows are destroyed, and a policy table
silently governing that is the finding no table-level tool produces (ADR-0001 §2). A DELETE
has no projection, so `build_scope` is not usable the way it is for a SELECT and the
predicate is walked directly; `_relation_for` binds each column, and **an unqualified name
that more than one table could supply is declared rather than guessed** — the `sq_07` failure
in a new place.

**`DELETE FROM t` with no predicate produces nothing, and that is a complete answer.** All
three DELETEs in the phase-0 corpus are that form, which is why convention (c) landing moved
no phase-0 cell at all. The statements are now *counted*, which is the part that changed.

### S1-04, the `UPDATE` half — which turned out not to be an UPDATE problem

**`UPDATE` was never broken.** `defuse._analyse_update` has always read
`UPDATE … SET c = v WHERE …`, which is why `s2_update_correlated` scores. The single
outstanding miss — `V_VAL → DIM_CUSTOMER.LIFETIME_VALUE` in `s2_locking` — was a **SQLGlot
parse failure on `WHERE CURRENT OF c_lock`**: *"Invalid expression / Unexpected token"*, and
the whole statement was lost. Same family as S2-01, not a refusal-taxonomy problem.

Fixed by a pre-parse rewrite in the new `lineage.parsing.rewrite` — the technique S2-01's own
entry proposes. **The module is written to stay small and says so**: a clause may only be
stripped if SQLGlot cannot parse the statement *at all* **and** the clause names no column and
no variable, so removing it cannot change any edge. `WHERE CURRENT OF` is a rowid the open
cursor is holding; the rows were already chosen by the cursor's own `WHERE`, which is analysed
where it is written. Anything failing that second test belongs in the refusal register, because
a rewrite that drops a real predicate would silently *narrow* lineage — worse than the parse
failure it fixes.

**The stress-2 key predicted the exact answer before this code existed**: one value edge from
the `SET` clause, and no filter edge, because the predicate names nothing. That is what it
produces, and the test asserts both halves — the edge appearing *and* no filter edge being
invented.

**It has ONE home, not two.** `band0` and `defuse` both import it. S2-08 is on the register
because `_transform_of` was duplicated and drifted; adding a third duplicated concept while
that finding is open would have been indefensible.

**`LOCK TABLE` and `EXIT WHEN` still fail to parse and are still declared as boundaries.**
Both carry no lineage — a lock and a loop exit — so the declarations are honest and cost
nothing but noise in `boundaries_declared`. Checked rather than assumed: an earlier reading of
this run mistook the `EXIT WHEN` boundary for the `FETCH`, which does parse and whose edges
are recovered.

### Effect of the two halves

| | before | after |
|---|---|---|
| phase-0 `cells` | — | **identical**, every one |
| phase-0 parse coverage | 80.4% | **81.6%** |
| stress 1 parse coverage | 92.3% | **92.9%** |
| stress 1 band-1 filter | 100% / 85.7% | 100% / **86.7%** |
| stress 2 parse coverage | 79.3% | **80.0%** |
| stress 2 band-0 filter | 76.0% / 73.1% | **78.6% / 84.6%** |
| stress 2 band-1 value — the gate | 100% / 71.4% | 100% / **76.2%** |

**Tenth fix, and the phase-0 grid has still never moved.** Cumulatively S1-04 has taken
phase-0 parse coverage from **76.1% to 81.6%** and the false-abstention rate from 9.1% to
zero, without changing a single scored cell.

**It also completed a key.** The DELETE in `stress_transaction_control`'s `ELSE` branch
started producing a correct guarded edge that stress 1's key did not have — because that key
was written *before* convention (c) was proposed in stress 2. Logged as **S2-11**: a
convention decided in one key has to reach every key, or the two encode different answers and
the benchmark stops meaning one thing.

Four regression tests in `tests/test_refusals.py`: the subquery DELETE asserted edge-by-edge,
the unconditional DELETE asserted on the *count* rather than the edges (the silence is what
regressed before), the `WHERE CURRENT OF` case asserted on both sides, and a guard that an
ordinary `WHERE` survives the rewrite untouched.

Five regression tests in `tests/test_refusals.py` (the positional binding asserted
column-by-column, because an off-by-one produces the right *number* of edges into the wrong
columns; the literal case; the missing-column-list case; and the guard). One existing test in
`tests/test_band0.py` asserted the old behaviour and was **rewritten rather than deleted** —
`test_unsupported_statement_is_refused_not_guessed` still tests the abstention path, now with
a construct the register genuinely refuses, and
`test_a_literal_insert_values_is_analysed_not_refused` records beside it that the change is
deliberate.

## S2-08 · `transform-classification` · FIXED — two homes for one rule, and LAG

Two problems, found by implementing D-3 rather than by a stress run. Both fixed 2026-09-11.

### The classifier had two homes, and they had drifted

`band0` and `defuse` each carried their own `_transform_of` with their own constant tuples.
**`defuse.CONDITIONAL_EXPRESSIONS` was still the pre-S2-05 `(exp.Case, exp.If)` pair**, and
the `COALESCE`-versus-`NVL` rule — the half of S2-05 that needed a rule rather than a list —
did not exist in band 1 at all. S2-05 landed in band 0 and never reached band 1, so a finding
recorded as fixed was half-fixed.

**Measured against the old `defuse.py` rather than argued.** A `DECODE` and a two-column
`COALESCE` read into variables produced three edges, all `derived`, all now `conditional`.
Under ADR-0001 §4 a wrong transform is a MISS on both sides, so each was costing a false
positive *and* a false negative in any package that reached it.

**NO NUMBER MOVED, AND THAT IS THE FINDING.** Phase-0 cells, the gate and both stress
packages are unchanged, because nothing in the corpus or either stress file reaches a
`DECODE`, `NVL2`, `GREATEST`, `NULLIF` or two-column `COALESCE` through def-use. The defect
was real, cost nothing measurable, and would have stayed invisible until someone wrote a
package that hit it — the same shape as S2-06 surviving four fixes.

`analysis/transforms.py` now owns `TRANSFORM_RANK`, the three constant tuples,
`is_aggregate`, `is_conditional`, `transform_of` and `combine`. Both modules import it under
their existing private names, so call sites and the tests that import
`band0._transform_of` are untouched.

**`tests/test_transforms.py` asserts IDENTITY, not behaviour** — `band0._transform_of` **is**
`defuse._transform_of` **is** `transforms.transform_of`. Two separately-defined functions can
agree on every case a test happens to list and still drift on the next one added, which is
exactly the history here. Two of its tests fail against the old `defuse.py`.

### LAG and LEAD — D-3's rule applied, and unmeasurable

`LAG(total)` reads another **row** of the same column and computes nothing over a set, which
is the same argument that moved `FIRST_VALUE` under D-3. `sq_03`'s key says so in prose —
*"LAG(total) reads another ROW of the same column"* — and then labelled it `aggregated`.
**No key ever argued for `aggregated` on the merits;** the label recorded what the analyser
did. So this is D-3's rule applied rather than a new decision, and `LAG`/`LEAD` join the
value-selecting set.

**It cost nothing, and the reason is worth more than the change.** Not one cell moved
anywhere. **Both `LAG` instances in the entire corpus are `LAG(SUM(...))`** — `sq_03`'s and
stress 1's are each a `LAG` over a CTE's `SUM` — so the aggregate is on the path and the
ladder keeps `aggregated` whatever `LAG` itself is called. The question was unmeasurable
here, and the register had it filed as needing a phase-0 measurement it could never have had.

**It is not unmeasurable in general.** A bare-column `LAG(line_amount)` — the form real
reporting SQL actually writes — is `derived` under this rule and was `aggregated` before.
It appears nowhere in 38 packages. Pinned by test, because nothing else can pin it.

**A test written for this moment fired.** D-3 left
`test_lag_is_still_aggregated_and_that_is_recorded_as_inconsistent`, whose docstring said:
*"pins the CURRENT state rather than endorsing it ... if that finding is ever decided the
other way, this test is the thing that should fail."* It failed. That is the whole value of
writing a test against a state you expect to change, and it is the one mechanism in this
register that has ever announced a convention change instead of waiting to be noticed.

**The first rewrite of it asserted against the wrong layer** and is worth recording.
`transform_of` looking at `LAG(total)` in isolation correctly says `derived` — the `SUM` is
in a *subquery*, and it is `_trace` plus `combine` that walk the path. The replacement is
end-to-end through `analyse_source`, which is where the claim actually lives.

## S2-09 · `key-error` · FIXED — two of three bindings labelled

`s2_recursive_walk` writes `INSERT INTO dim_customer_hier (cust_id, parent_cust_id, depth)
VALUES (p_cust_id, v_parent, p_depth)`. The key labelled the first two and not the third.

`p_depth` is a parameter, which ADR-0001 §3 makes a first-class node, and it is written into
`depth` by exactly the rule that makes the other two edges true. There is no reading of the
statement under which two of its three bindings are lineage and the third is not.

**Third instance of the same shape** — after S1-06 and S2-06 — and the same cause each time:
**the unit was expected to be refused, so its key was written to size the refusal rather
than to be complete.** It surfaced the moment the statement started producing edges, exactly
as `s2_pivot_static` did under S2-03.

`tests/test_stress_keys.py` cannot catch this one. It counts units, and this unit was
labelled — just not fully. **A completeness check at unit granularity does not catch an
incomplete unit**, and no cheap check does: knowing a VALUES list has three bindings and the
key has two means parsing the source, which is the analyser's job. Recorded rather than
solved.

## S2-11 · `key-error` · FIXED — a convention that never reached the older key

`stress_transaction_control`'s `ELSE` branch is
`DELETE FROM fct_revenue_stage WHERE period_month < TRUNC(SYSDATE, 'YYYY')`. The moment the
DELETE half of S1-04 landed, it produced a correct, correctly-guarded filter edge that
**stress 1's key did not contain** — and so scored as a false positive.

The key is not wrong for its own time. It was written before stress 2 existed, and
**proposed convention (c) is a stress-2 convention**: a DELETE writes no value, but its
predicate columns are a real dependency of what the table ends up containing. D-1 settled it.
Stress 1 had simply never been asked the question.

**This is the failure mode D-2 warns about, arriving from the other direction.** That warning
is about the analyser and the keys drifting apart within one change. This is two *keys*
drifting apart across time: a convention decided against one package silently leaves the
older package encoding the opposite answer, and the register cannot tell which is which
because both look like ordinary disagreements in the grid.

**A decided convention has to be applied to every key in the same change.** Added with its
guard (`v_count <> 0 AND p_strict <> 1`, the `ELSE` arm) and a note recording where the
convention came from and why it is arriving late.

Fourth `key-error` in this register, after S1-06, S2-06 and S2-09. The first three were
omissions; this one is different in kind — nothing was forgotten, the key was complete
against the conventions that existed when it was written. **No test can catch this class**,
and `tests/test_stress_keys.py` never could: it counts units, and the unit was labelled.

## S2-10 · `measurement-error` · FIXED — the refusal cross-checks could not see trigger edges

Found while measuring S1-04. `s6_updatable_view` produced an edge from the same statement it
refused, and **both** guards against that reported clean:

* `edges_from_refused_statements` — `[]`
* `false_abstentions_recovered` — `0`

Neither was a measurement. Both compare a refusal's line against an edge's `origin.line`, and
**the two are numbered in different spaces**: a trigger body is analysed out of the
DICTIONARY, wrapped in a synthetic `CREATE OR REPLACE PROCEDURE`, so its edges carry lines
relative to that wrapper (3, 4, 7), while a refusal on the same statement comes from the FILE
pass and carries the file line (39). `refusal.covers()` can only ever answer "no".

**The consequence was never a wrong score** — origin is not in the match key (amendment 1b) —
**it was a kill-criterion row reporting PASS without looking.** `edges from refused` is
rendered in the verdict, and the verdict leans on it.

### The live instance was already gone, which changed the fix

**D-1 removed it.** `s6`'s refusal was the `INSERT … VALUES` one, and un-refusing that
construct took the last trigger-unit refusal out of the corpus. Measured before touching
anything: **no refusal in any package now lands in a trigger unit**, so there was no live
defect left to repair — only a blind guard waiting for one.

That makes the honest fix "make the blind spot countable", not "renumber something and hope".
`refusal.not_cross_checkable()` returns the refusals whose contradiction check **could not
run**, and the measurement reports them beside the violations:

```
  edges from refused     0   <- must be 0; checked mechanically, not by inspection
  of which NOT checkable 0   <- line spaces differ, so the check above could not run (S2-10)
```

The kill-criterion row gains `(n NOT CHECKABLE)` whenever that count is non-zero, so a `0 of
9 refusals  PASS` can never again mean "nine checked" when some of them were not.

### What was NOT done, and why it is recorded rather than hidden

**The architectural fix is bigger and belongs to its own measurement.** Band 0 analyses
trigger bodies out of the FILE while `triggers.py` analyses them out of the DICTIONARY, so
**the same statement is read twice in two coordinate spaces**. Only the dictionary pass's
edges survive — measured: band 0 contributes no trigger edges at all, in any package — so
the file pass's reading of a trigger body exists only to produce refusals and statement
counts. Collapsing that is the right change, and it moves parse coverage, which makes it a
separate fix rather than a tidy-up inside this one.

**The keys disagree with each other about trigger line numbers too.** `b0_04` records 18/19,
stress 2 records 1, the analyser emits 3. None of it affects scoring, and all of it would
have to be settled by that same architectural change.

### Pinned by reconstruction, because the corpus cannot exercise it

`tests/test_refusal_crosscheck.py` rebuilds the S2-10 condition from scratch — a file that
writes `tmp_recent`, so the trigger's edges are inherited at body lines, **and** redefines
that trigger with a `CONNECT BY` body the file pass refuses at a file line. It asserts both
halves: the refusal's line and the edge's line are disjoint, the old check still finds
nothing, and the new one reports exactly one un-checkable refusal.

Two guards beside it: an ordinary unit's refusal must **not** be reported as un-checkable, or
the new count becomes noise and the row stops meaning anything; and no package in the corpus
has an un-checkable refusal today, so if a future change puts one back inside a trigger body
the suite fails and names S2-10 instead of a kill-criterion row quietly reporting PASS.

## S2-11 · `key-error` · FIXED — a convention that never reached the older key

`stress_transaction_control`'s `ELSE` branch is
`DELETE FROM fct_revenue_stage WHERE period_month < TRUNC(SYSDATE, 'YYYY')`. The moment the
DELETE half of S1-04 landed, it produced a correct, correctly-guarded filter edge that
**stress 1's key did not contain** — and so scored as a false positive.

The key is not wrong for its own time. It was written before stress 2 existed, and
**proposed convention (c) is a stress-2 convention**: a DELETE writes no value, but its
predicate columns are a real dependency of what the table ends up containing. D-1 settled it.
Stress 1 had simply never been asked the question.

**This is the failure mode D-2 warns about, arriving from the other direction.** That warning
is about the analyser and the keys drifting apart within one change. This is two *keys*
drifting apart across time: a convention decided against one package silently leaves the
older package encoding the opposite answer, and the register cannot tell which is which
because both look like ordinary disagreements in the grid.

**A decided convention has to be applied to every key in the same change.** Added with its
guard (`v_count <> 0 AND p_strict <> 1`, the `ELSE` arm) and a note recording where the
convention came from and why it is arriving late.

Fourth `key-error` in this register, after S1-06, S2-06 and S2-09. The first three were
omissions; this one is different in kind — nothing was forgotten, the key was complete
against the conventions that existed when it was written. **No test can catch this class**,
and `tests/test_stress_keys.py` never could: it counts units, and the unit was labelled.

## S2-10 · `measurement-error` · OPEN — the refusal cross-checks cannot see trigger edges

Found while measuring S1-04. `s6_updatable_view` produced an edge from the same statement it
refused, and **both** guards against that reported clean:

* `edges_from_refused_statements` — `[]`
* `false_abstentions_recovered` — `0`

Neither is a measurement. Both compare a refusal's line against an edge's `origin.line`, and
**the two are numbered in different spaces**: a trigger body comes from the dictionary, so its
edges carry lines relative to the BODY (3, 7), while the refusal carries the line in the FILE
(39). `refusal.covers()` can never match, so every trigger-sourced edge is invisible to both
checks.

**The consequence is not a wrong score — it is a guard that reports PASS without looking.**
`edges from refused` is rendered in the measurement as a kill-criterion row, and the verdict
leans on it. Scoring itself is unaffected: origin is not in the match key (amendment 1b).

The fix is to give trigger edges a file-relative origin, or to teach `covers()` about the two
spaces. The first is better and larger — several keys record trigger origins in body
coordinates already (`b0_04` uses lines 18/19, stress 2 uses line 1), so they disagree with
each other as well.

## S2-12 · `flow-classification` · FIXED — window ordering is neither value nor filter

Raised by the S1-03 correction: the three band-0 filter false positives blamed on `GROUP BY`
are window `PARTITION BY` / `ORDER BY` columns. Decided as **D-4** above — a third flow,
`Flow.INFLUENCE`, targeting the output column.

**Implemented** by splitting `band0._influence_columns` in two. `_correlated_filter_columns`
keeps the nested-`WHERE` half, which genuinely selects the row a correlated subquery reads
and stays `filter` against the relation. `_window_influence_columns` takes the window half
and emits `Flow.INFLUENCE` against the target column. `_value_columns` subtracts both, so
the original defect — partition and order columns read as VALUE sources — stays fixed.

**Effect.**

| | before | after |
|---|---|---|
| phase-0 gate | — | **identical** |
| phase-0 0/filter | 38 TP, 0 FP, 5 FN | 35 TP, 0 FP, 5 FN |
| phase-0 0/influence | — | **4 TP, 0 FP, 0 FN — 100% / 100%** |
| stress 1 band-0 filter | 75.0% / 90.0% | **100% / 90.0%** |
| stress 1 band-0 influence | — | **100% / 100%** on 4 |
| stress 2 band-0 filter | 78.6% / 84.6% | **88.0% / 84.6%** |
| stress 2 band-0 influence | — | **100% / 100%** on 11 |
| forbidden edges produced | 0 | 0 |

**THE PHASE-0 GRID MOVED, FOR THE FIRST TIME IN TWELVE FIXES.** Three `sq_03` labels left
band-0 filter for band-0 influence, and a fourth was added — `ORDER_DATE → PRIOR_MONTH`,
LAG's own ORDER BY, which the old form could not state because it collapsed onto the
ROW_NUMBER partition's four-tuple. **Precision stayed 1.0 in every cell it was 1.0 in, and
the gate is byte-identical.** The movement is three labels reclassified and one recovered,
which is what a convention change is supposed to look like.

**Stress 1 now has zero false positives in the entire package** — 100% precision on every
band and every flow.

**The forbidden lists still hold.** `LINE_AMOUNT → RANK_IN_MONTH` and
`ORDER_DATE → PRIOR_MONTH` are both named in stress 2's forbidden list as **value** edges,
and both are now labelled as **influence** edges. Zero forbidden edges produced, as before:
the two are different claims about the same pair of columns, which is precisely what giving
influence its own flow buys.

**Three false positives remain in stress 2**, and neither is a window:

* `GTT_STAGE.AMOUNT`, `GTT_STAGE.PERIOD_MONTH → relation:GTT_STAGE` — `INTERSECT`/`MINUS`
  later arms as filter influence, convention (a). The analyser applies it more widely than
  the key labelled it.
* `STG_ORDER_LINES.ORDER_ID → relation:DIM_CUSTOMER` — the other operand of the `EXISTS`
  predicate. **S1-06 for the third time**, in a third package.

Four regression tests: the decisive one asserts a statement with no `WHERE` produces **no
filter edges at all**; one pins that two windows over the same partition column feed two
separately-named output columns; one guards that an ordinary `WHERE` still targets the
relation. `tests/test_complex_sql.py`'s window test asserted the old behaviour and was
**rewritten rather than deleted**, keeping the original defect's assertion and recording the
new form beside it in negative.

## S2-13 · `key-error` · FIXED — one column of a three-column MINUS

`s2_intersect_minus`'s second statement is
`SELECT o.cust_id, TRUNC(o.order_date,'MM'), o.gross_amount FROM stg_orders o MINUS SELECT
g.cust_id, g.period_month, g.amount FROM gtt_stage g`.

Convention (a) — proposed in this key, implemented under S1-02 — makes every column of the
later arm a constraint on which of arm 1's rows survive. **The key labelled `cust_id` and
stopped.** There is no reading under which one column of a three-column `MINUS` constrains
and the other two do not: a row is removed when **all three** match.

Fifth instance of the same shape, after S1-06, S2-09, S2-11 and the `EXISTS` operand below.
Each was found by a different mechanism and **none of the mechanisms would have caught this
one** — `tests/test_stress_keys.py` counts units and this unit was labelled; the S2-09 shape
was a VALUES list; S2-11 was a convention arriving from a later package. This one surfaced
only because D-4 cleared enough noise from the band-0 filter cell to leave it visible.

Stress 2 band-0 filter **88.0% → 96.0%** precision, recall 84.6% → 85.7%.

## S2-14 · `flow-classification` · FIXED — a join condition means two different things

**The same join condition produces different lineage depending on where it is written.**
Measured directly, not inferred:

| where the join sits | filter edges produced |
|---|---|
| `FROM stg_orders o JOIN stg_order_lines l ON l.order_id = o.order_id` | **none** |
| the identical `ON` clause, inside an `EXISTS` in a `WHERE` | **two**, one per operand |

The second is `s2_update_correlated`, and it is the last false positive in either stress
package.

**The cause looks incidental rather than decided.** `_filter_edges_for` walks the WHERE
subtree with `find_all(exp.Column)`, which sweeps up everything nested inside it — including
the `ON` clause of a join inside an `EXISTS`. Nothing chose that; it falls out of walking a
subtree wholesale.

**The key is in the wrong position too, and in a way nothing supports.** It labels
`STG_ORDERS.ORDER_ID` and not `STG_ORDER_LINES.ORDER_ID` — one operand of a structural join.
Two of the key's own conventions are in play and the key satisfies neither:

* *"join conditions are structural, not filter lineage"* → **neither** operand is an edge;
* *"every operand of a predicate is a filter edge"* (s7, applied corpus-wide in `0b9a9e8`)
  → **both** are.

Exactly one is the only answer with no argument behind it.

**Recommendation, for whoever decides.** Structural, wherever it is written. The join does
not decide which `dim_customer` rows are updated — the correlation `o2.cust_id = d.cust_id`
does that, and it is labelled separately. The join only connects two tables so the `EXISTS`
can be evaluated at all, which is the same job it does in a `FROM`. That reading costs one
labelled edge (recall 85.7% → 85.2% in stress 2) and takes band-0 filter precision to 100%
in both packages.

### D-5, and the fix

**Decided 2026-09-11: structural wherever written.** The recommendation above, taken.

**It had to reach three separate call sites**, which is the finding underneath the finding.
`band0._filter_edges`, `band0._analyse_delete` and `defuse._filter_edges_for` all walk a
predicate subtree, and so does `defuse._predicate_columns` for a correlated subquery in a
`SET` clause. Measured before fixing: the same `ON` clause leaked filter edges from an
`EXISTS` in an `INSERT ... SELECT`, from an `IN` subquery in a `DELETE`, and from the
`UPDATE` that raised the finding. **Four places, one rule.** It lives in
`analysis.predicates.predicate_columns` and both modules import it - S2-08 is already on the
register for a rule that was duplicated across those two files and drifted, and adding a
fifth copy of a predicate walk would have been the same mistake with a different name.

**The correlation is kept and that is the whole distinction.** `o2.cust_id = d.cust_id`
decides which `dim_customer` rows are updated and both of its operands survive; the join
that made the `EXISTS` evaluable contributes nothing. Excluding one and keeping the other is
the difference between reporting the dependency and reporting the plumbing.

**The key's label was withdrawn**, not corrected — stated in place, because it lowers recall.

**Effect.**

| | before | after |
|---|---|---|
| phase-0 everything | — | **identical** — no corpus package nests a join inside a predicate |
| stress 2 band-0 filter | 96.0% / 85.7% | **100% / 85.2%** |
| stress 1 | 100% precision | unchanged |
| **false positives, both packages** | 1 | **0** |

**BOTH STRESS PACKAGES NOW HAVE ZERO FALSE POSITIVES** — 100% precision on every band and
every flow in each. Whatever else is wrong, the analyser is not currently inventing anything
either package can see.

Three regression tests, one per call site, each asserting the ON clause contributes nothing
**and** that the correlation still contributes both operands. The third pins the half that
was always right, so D-5 cannot regress it.

## S1-05 · `identity` · OPEN — the label format could not express five facts in one file

> **Escalated 2026-09-13 by S4-09, and the argument has changed kind.** Everything below,
> and stress 4's 28.1%, is about labels that *cannot be stated*. S4-09 showed the collision
> **hiding a live defect**: `STG_ORDERS.CURRENCY -> FCT_REVENUE_PART [filter]` scored as a
> true positive while the unit it was labelled against emitted nothing, because the same key
> was collapsed out of a different unit that emits it correctly. A cell read 100% over a
> silent miss. That is no longer a cost in expressiveness — **it is the measurement reporting
> a pass it did not earn**, which is the one failure mode this whole register exists to
> prevent.

The **first draft of the key was rejected by the validator.** Five genuine facts, each
written by two different procedures in this one file, collide on the match key — and unlike
`b1_02`/`b1_03`/`b1_09`/`s7`, **guard does not separate them.** All ten edges are
unconditional. Only origin does, and origin was measured out of the key.

```
STG_ORDERS.CUST_ID    -> FCT_REVENUE.CUST_ID       set_ops : udf_caller
STG_ORDERS.ORDER_DATE -> FCT_REVENUE.PERIOD_MONTH  set_ops : udf_caller
STG_ORDERS.CURRENCY   -> FCT_REVENUE (filter)      set_ops : udf_caller
STG_CUSTOMER.EMAIL    -> DIM_CUSTOMER.EMAIL        merge_upsert : cursor_for_loop
DIM_CUSTOMER.CUST_ID  -> DIM_CUSTOMER (filter)     trg_recent_audit : cursor_for_loop
```

Two differ in **band** as well (0 vs 1, 1 vs 2), so the collision forces a choice about which
band the fact is scored in, and the losing band's recall is charged for a fact that is
genuinely there.

**Same root cause as S1-01**, one layer later: the analyser deduplicated on `match_key()`
and so does the key. Fixing the analyser half did not help here, because the key still cannot
hold both facts to score them against.

**This is the sharpest result in the run.** Amendment 1b concluded origin has no useful role
in the scoring layer, and it was right *about the phase-0 corpus*, where packages hold one to
three units. This file holds thirteen — ordinary for real code — and the collision appears
immediately. **The conclusion does not survive contact with package size**, and the
production package, when it arrives, will be larger than this one.

It also masked S1-02: `set_ops` contributes **zero** edges, yet two of its labels still
matched, because `udf_caller` happens to write the same columns. A whole statement was lost
and the score barely moved.

## S1-06 · `key-error` · FIXED — two gaps in my own key

Stated because a key is evidence and its errors are part of the result.

- **The `EXISTS` correlation's second operand.** I labelled `STG_ORDERS.CUST_ID` as the filter
  source and omitted `STG_CUSTOMER.CUST_ID`. `s7`'s key labels *both* operands, so the
  analyser is right and the key was incomplete.
- **`BULK COLLECT` works.** I predicted a refusal and labelled none of it; the analyser
  correctly emits `STG_CUSTOMER.CUST_ID → variable:V_IDS` and the statement's `WHERE` filter.
  Two false positives that are the key's fault, not the analyser's.

**Both corrected 2026-09-09, in the S2-06 pass**, and the second one was found rather than
remembered: `stress_bulk_operations` had *no labels at all*, and the completeness check
written for S2-06 failed on stress 1 the first time it ran. The finding was recorded as
`closed` when it was only *stated*, which is how it survived four intervening fixes.

**The prediction was written down before the re-run and it was wrong in the right
direction.** S1-06 estimated band-0 filter precision would go from 58.3% to "roughly 80%".
Measured: **61.5% → 75.0%**, recall 88.9% → 90.0%. The three remaining false positives are
all S1-03's `GROUP BY` columns, which is the whole of what is left there.

**And correcting it took stress 1 below the gate** — band-1 value recall 87.5% → 84.6%, under
the 85% floor, because `V_IDS → TMP_RECENT.CUST_ID` is a real band-1 value fact that the
analyser refuses. That is the cost of a key error being paid a run late, and it is the reason
this file's own rule says a finding is not closed until a test fails without the fix.

---

## What this says about the phase-0 numbers

Nothing directly — the phase-0 measurement is unchanged and re-verified byte-identical after
the fix. But three of the six findings are **artefacts of package size**, not of construct
difficulty, and the corpus cannot see them because every package in it is small. That is a
sharper version of the caveat the verdict already carries: the corpus is synthetic, and its
*shape* is as unrepresentative as its content.

---

## Stress test 2

753 lines, 28 program units, 130 hand-labelled edges written from source before the analyser
was run over the file. Six forbidden-edge rules, four expected boundaries. Twice the size of
stress 1 and aimed at what stress 1 did not reach.

*(The "130" was always the count of facts written, not of labels the file could hold — 103
survived the validator. After S2-06 it is 169 facts and 117 labels, with five expected
boundaries. The figures in this section are the first run and are left as measured.)*

| band / flow | precision | recall |
|---|---|---|
| 0 value | 82.8% | 58.5% |
| 0 filter | 81.8% | 72.0% |
| **1 value — the gate** | **81.8%** | **56.2%** |
| 1 filter | 100% | 75.0% |
| 2 value | 100% | 50.0% |
| 2 filter | 100% | 100% |

**The gate fails both floors** — 81.8% against 95%, 56.2% against 85%. **Parse coverage is
55.6%** (27 statements seen, 15 analysed), which is below the 70% kill-criterion floor.

Read that carefully before drawing a conclusion from it. This file was written to be hostile
and roughly a third of it is constructs that *should* be refused; the kill criterion is
phrased about **real code**, and this is not real code. What it does say is that the
criterion's 5.6 points of headroom on the phase-0 corpus is a property of that corpus's
construct mix, not of the analyser.

**Zero forbidden edges produced**, again — including the window `ORDER BY` traps, the
positional-binding trap on `INTERSECT`, and the flattened nested `CASE`. Six named wrong
answers, none fired. That result has now held twice at increasing size, and it is the part
of the phase-0 verdict that is standing up best.

**Twelve refusals as first measured, and only five of them correct.** `CONNECT BY`, `MODEL`,
and three `INSERT … VALUES` of literals only (which carry no lineage anyway) are right. The
other seven were S1-02, S1-04, S2-01, S2-02 and S2-03 below — four of those five are now
fixed, and the refusals that remain are S2-01 and S2-02 — S1-04 landed in full under D-1.

## S2-01 · `construct-coverage` · OPEN — `MERGE` with a `DELETE` arm does not parse

`s2_merge_with_delete` is refused `PARSE_FAILED`: *"SQLGlot could not parse: Invalid
expression / Unexpected token."* The statement is a `MERGE` whose matched arm carries both an
`UPDATE … WHERE` and a `DELETE WHERE`, which is standard Oracle and standard in slowly-
changing-dimension loads.

**Seven labelled edges lost**, including the whole `NOT MATCHED` insert arm and the trigger
edge it inherits.

Unlike S1-02 this is a genuine parser limitation rather than a mis-branch — the text never
becomes a tree — so the fix is either a SQLGlot version bump, a pre-parse rewrite that strips
the `DELETE` clause before analysing the rest, or an explicit refusal code that says what
actually happened. The current message is at least honest about being a parse failure.

**Re-confirmed 2026-09-11 against SQLGlot 30.18.0** by the parser probe (see *The parser
probe* below): the failure is at `Line 2, Col: 45`, the `DELETE` keyword inside the `WHEN
MATCHED` arm. Logged as **SG-001** in `docs/grammar_limitations.md`. **Note what that does
NOT establish** — the probe pinned the current version's behaviour, not whether a newer
SQLGlot fixes it. The version bump is still an untested option, and it should be tested
before it is costed.

## S2-02 · `construct-coverage` · OPEN — `INSERT ALL` is unsupported

`s2_multi_table_insert` is refused as *"unsupported statement type MultitableInserts"*. That
is an honest, correctly-coded refusal and it costs **seven labelled edges** — one `SELECT`
feeding two targets, each behind its own `WHEN`.

Worth separating from S1-02: this refusal is **true**. The analyser genuinely does not handle
multi-table insert, says so, and the boundary is counted. The finding is a coverage gap, not a
correctness defect, and it is the shape ETL uses to fan one source into staging and reject
tables.

### Corrected 2026-09-11 — this is not a parse failure, and it is smaller than it reads

The parser probe handed SQLGlot both `INSERT ALL` and `INSERT FIRST`. **Both parse cleanly.**
This entry sits beside S2-01 in every summary list above, and the two were being carried as
the same kind of problem. They are not:

* **S2-01** — there is no tree. Nothing can be written against it until the parser changes.
* **S2-02** — **the tree already exists.** `MultitableInserts` is a node SQLGlot hands us and
  the analyser declines to walk. The work is lineage semantics on a parsed structure, which
  is ordinary analyser work of the kind already done for `MERGE` and `UNPIVOT`.

That materially re-orders the queue: S2-02 has no external dependency, no version bump and no
rewrite rule — and its shape (one `SELECT` fanning into several targets, each behind its own
`WHEN`) is a conditional edge per target, which the transform ladder already expresses.

**Worth recording as a method failure, not just a correction.** "Unsupported" was read as
"unparseable" for the whole life of this entry, by me, in the summary tables, without anyone
checking which parser was refusing. The refusal message was accurate the entire time; nobody
asked it the follow-up question.

### Fixed 2026-09-14 — and it cost no new traversal at all

The prediction in the paragraph above held: ordinary analyser work on a parsed structure. What
was not predicted is how little of it there was, because **both delegations this needed were
already public for other findings.**

* `resolve_projection` (public for S4-02) binds each `VALUES` item. An arm's values name the
  SOURCE QUERY'S OUTPUT COLUMNS — `VALUES (cust_id, period_month, gross_amount)` are select
  list aliases, not base columns — so every one of them is precisely the question S4-02 made
  that function public to answer. It also carries the transform: `TRUNC(order_date,'MM')` is
  derived in the source and the arm's item is a bare name, so reading only the arm would score
  `identity` on all six value edges.
* `resolve_query_influence` (public for S4-09, three days ago) gives the source's `WHERE`,
  `HAVING` and grouping, because the source is an ordinary query and nothing about it is
  special to this statement type.

**The `WHEN` becomes a guard, not a filter edge.** `gross_amount` deciding which of two tables
a row lands in is a condition on the edge, and `guard` is the field for exactly that; emitting
it as filter influence as well would report one fact twice in two mechanisms. Drop the guards
instead and the IR says `gross_amount` always reaches both tables — a false statement rather
than a missing one.

**The guard had to be rendered unquoted, and the measurement is what said so.** By the time a
statement reaches band 0, `qualify` has quoted every identifier, so `.sql()` gives
`"GROSS_AMOUNT" > 100` while every other guard in the project comes from CFG source text and
reads `gross_amount > 100`. `normalise_guard` folds case, operand order, conjunct order and
negated equality — and **does not touch quotes**. The edges would have matched on their keys
and then scored guard-INCORRECT. Stress 2 reported `guard correct n/a of 0` before this fix
because all six guarded claims were missing; it now reports **100% of 6**, which is a number
that only exists because the quoting was checked before the run rather than after.

**`INSERT FIRST` is not `INSERT ALL` with different spelling**, and no key covers it. Under
`FIRST` a row goes to the first matching arm only, so arm two fires on `NOT (c1) AND c2`; an
`ELSE` arm fires on the negation of every condition. The edges are identical under either
kind and **only the guard differs — which is the one kind of error that can never surface as a
false positive.** Handled here, with the regression test using overlapping conditions so the
difference is observable, rather than left for a package that happens to contain one.

**Result.** Stress 2 band-0 value 33/0/16 → **39/0/10**, band-0 filter 23/0/4 → **25/0/3**,
guard correct **100% of 6**, parse coverage 80.0% → **83.3%** and statements analysed 24 → 25.
Zero false positives in every cell of all four packages. Phase 0 unmoved — the corpus contains
no multi-table insert, which is itself worth knowing: **this construct was measured only by
the stress packages, and phase 0 could never have found it.**

## S2-15 · `key-error` · FIXED — the filter edge for one of two targets

`s2_multi_table_insert` writes two tables from one `SELECT`. The key wrote the six VALUE
edges for both targets and the one FILTER edge for the first:

```sql
WHERE o.currency = 'GBP'    -- evaluated ONCE, before any WHEN is tested
```

Every row reaching either arm passed that predicate, so which rows land in `gtt_stage` depend
on `currency` exactly as much as for `fct_revenue_stage`. S2-13 is already on the register for
*"a uniform rule applied to the first instance only"* — this is that, in the key written to
measure the very statement S2-02 is about, and **it stood for five days because the whole
statement was refused, so nothing ever contradicted it.** A refusal does not just cost edges;
it also suspends the check on the key that describes them.

## S2-03 · `refusal-taxonomy` · FIXED — `PIVOT`/`UNPIVOT` refused even when decidable

**As first measured:** both `s2_pivot_static` and the `UNPIVOT` in `s2_unpivot_listagg` were
refused under the rule u1 established for `PIVOT` with a **subquery** column list — where the output shape is genuinely
not static. These two have **literal** column lists (`IN ('GBP' AS gbp, 'USD' AS usd)` and
`IN (net_amount, order_count)`), so the output columns are knowable at parse time.

This is over-refusal — a **false abstention**, the axis T3.1d exists to measure. It costs
three labelled edges and, unlike a wrong edge, it costs parse coverage too. The refusal
register needs to distinguish the decidable form from the undecidable one, exactly as the
dynamic-SQL classifier already distinguishes a constant string from an assembled one.

**Un-refusing alone would have been worse than the refusal, and that was measured before
writing any code.** With the register entry disabled, the pass-through columns
(`period_month`, `product_id`, `cust_id`) trace correctly and the **transposed** columns —
`gbp`, `usd`, `amount` — silently produce nothing, because their sources live in the pivot
clause and not in the select list. A reader would see two columns traced and reasonably
conclude the statement was understood. That is the S2-04 shape, so the fix had to be an
implementation rather than a deletion.

**Implemented** in `band0._pivot_columns`:

* **PIVOT** — one output column per IN-list alias, each fed by the aggregate's argument at
  `aggregated`. The `FOR` column decides *which* output column a row lands in, so it is
  filter influence and never a value source.
* **UNPIVOT** — the reverse of every other construct here: one output column fed by
  **several** inputs at once, at `identity`. This is convention (b) from the stress-2 key,
  written before the code existed. The `FOR` column is a generated label naming which input
  a row came from, and has no upstream at all.

**The subquery form stays refused.** There the output columns *are* the data, so no
positional binding is possible. `u1_pivot_subquery` still refuses and the phase-0 refusal
count is unchanged at 11 — the over-refusal guard, tested.

**Effect.**

| | before | after |
|---|---|---|
| phase-0 measurement | — | **every key identical** |
| stress 2 band-0 value recall | 68.3% | **75.6%** |
| stress 2 parse coverage | 58.6% | **65.5%** |
| stress 2 band-0 value precision | 96.6% | 93.9% |

**The precision dip is mine, not the analyser's.** `s2_pivot_static` was never labelled —
the same omission as `s2_locking` — so its correct edges score as false positives. Logged
under S2-06 rather than corrected here, because the run stands as measured.

One existing test asserted the old behaviour and was **moved rather than deleted**:
`test_a_literal_list_pivot_is_no_longer_refused` now records that the change of behaviour is
deliberate, and keeps the subquery case refused beside it.

## S2-04 · `silent-loss` · FIXED — `BULK COLLECT` into a record collection yielded nothing

`s2_bulk_limit` produces **zero edges and zero refusals**. Not one of its 27 statements is
flagged, and the unit does not appear in the output at all.

Stress 1's `BULK COLLECT INTO v_ids` — a `TABLE OF NUMBER` — worked correctly. The difference
here is `TABLE OF <record>` with `LIMIT`, fetched inside a loop and then written out by
`FORALL`. The collection element is a record, and the def-use analysis models neither the
record nor the collection.

**This is the category that matters most.** A refusal is a deliberate statement that
knowledge stops; silence is indistinguishable from having found nothing. Five labelled edges
disappear with no symptom anywhere in the report — which is the exact failure mode `s2`,
`s3`, `s6` and the whole silent-failure suite exist to catch, arriving through a construct
the suite does not contain.

**Root cause.** `forall_statement` is not in band 0's `SUPPORTED` set, so band 0 skips it —
and skipping is `continue` *before* `statements_seen += 1`, so it is not even counted as
seen. Def-use does not claim it either, because the values arrive through a collection
subscript it does not model. `FETCH … BULK COLLECT INTO` falls through both the same way.
Two passes, each correctly deciding the statement is not theirs, and nothing owning the
result.

**Fix: refuse both, by name, in the register.** Two constructs added — `FETCH_BULK_COLLECT`
and `FORALL` — with reasons that say these are *implementable and not implemented* rather
than implying the language beat us. Refusing is the honest floor here, not the ambition.

**`SELECT … BULK COLLECT INTO` is deliberately left alone.** It works today — stress 1
traces `stg_customer.cust_id → v_ids` through it — and a pattern matching `BULK COLLECT`
generally would have refused a statement the analyser already gets right. That is the
over-refusal the register's own DB-link note warns about, so the FETCH pattern is
FETCH-specific and a test pins the distinction.

**Effect.**

| | before | after |
|---|---|---|
| phase-0 measurement | — | **every key identical**, verified by stash-and-compare |
| stress 1 parse coverage | 75.0% | 69.2% |
| stress 2 parse coverage | 55.6% | 51.7% |
| stress 1 / stress 2 cells | — | unchanged |

**The coverage drop is the fix working.** Every refusal costs exactly one statement of
coverage, and these statements were previously free because nobody counted them. The score
cells do not move because the edges were already missing — what changed is that the report
now says so. Pinned by three tests in `tests/test_refusals.py`, including the over-refusal
guard.

## S2-05 · `transform-classification` · FIXED — `DECODE`, `NULLIF`, `GREATEST` were read as derived

Four of the eleven false positives are the same disagreement:

| expression | key says | analyser says |
|---|---|---|
| `DECODE(status_code, 'A', 1, 0)` | conditional | derived |
| `NVL(NULLIF(region, 'XX'), 'UNKNOWN')` | conditional | derived |
| `COALESCE(GREATEST(gross_amount, discount_amt), 0)` | conditional | derived |

`_transform_of` treats only `exp.Case` and `exp.If` as conditional. **`DECODE` is `CASE`
written differently** — Oracle's own documentation defines it that way — and `NULLIF`,
`COALESCE` and `GREATEST` all select one of several inputs on a condition.

Under ADR-0001 §4 a wrong transform class is a **MISS, not partial credit**, so each of these
costs a false positive *and* a false negative — eight cells of damage from four expressions.
It is also the kind of error that reads as correct in a report: the edge is there, the
endpoints are right, and only the word describing what happened to the value is wrong.

**Fix.** `DECODE`, `NULLIF`, `GREATEST`, `LEAST` and `NVL2` join `CASE` and `IF` as
conditional. Each selects the output from alternatives on a test rather than computing it
from the input.

**`COALESCE` needed a rule rather than a list**, because sqlglot gives `NVL` the same node
and the two cannot be told apart by type:

* `COALESCE(a, b)` over two **columns** is a genuine choice of source — the value comes from
  `a` or from `b` on a test. Conditional.
* `NVL(x, 0)` has one column and a **constant floor**. The column's value flows through
  unchanged whenever it exists; the literal is null-safety, not business logic. Calling that
  conditional would tell a reader there is a branch in the rule when the only branch is a
  null guard. Derived.

So: conditional when more than one argument can actually supply a column.

**That line was chosen on the merits and then checked against the cost, in that order.**
Classifying *every* `COALESCE` as conditional was measured first and costs one phase-0 label
— `sq_06`'s `NVL(parent.depth, 0) + 1`, band-0 value precision 100% → 98.9%. The rule above
leaves it alone, but the reason it is right is the semantic one, not the free one.

**Effect.**

| | before | after |
|---|---|---|
| phase-0 measurement | — | **every key identical** |
| stress 2 band-0 value precision | 82.8% | **96.6%** |
| stress 2 band-0 value recall | 58.5% | **68.3%** |

Exactly the eight cells predicted, recovered. Eleven regression tests, including the
`COALESCE`-versus-`NVL` distinction and the ladder check that aggregation still outranks.

## S2-06 · `key-error` · FIXED — my key again, and worse than it was recorded

- **`s2_locking` was never labelled.** I wrote the unit and skipped it in the key; its two
  correct edges (`DIM_CUSTOMER.CUST_ID → V_ID`, `LIFETIME_VALUE → V_VAL`) score as false
  positives. My omission.
- **`s2_pivot_static` was never labelled either**, which only became visible once S2-03 was
  fixed and the unit started producing edges.
- **The count was wrong: TEN units, not seven.** The finding named 14, 15, 16, 17, 18, 21 and
  27. It missed **22 (`s2_three_way_resolution`), 25 (`s2_row_limiting`) and 26
  (`s2_truncate_reload`)** — and 22 was missed *because the key contained a section header
  for it*, mis-numbered, sitting above unit 23's edges. A header with no edges under it read
  as a labelled unit. Section numbering for units 9, 13, 19, 21 and 28 was missing entirely.
- **The header's false claim is now true rather than deleted.** All three refused units (17,
  21, 27) are labelled in full, so a refusal's cost can be read off the key. Unit 21's
  refusal is *correct* and it still costs four labels; that case is now stated explicitly,
  because a key that sizes only the wrong refusals cannot say what abstaining buys.
- **Proposed convention (c) was rejected by the code.** I labelled a `DELETE`'s predicate
  columns as filter edges against the deleted relation; the analyser emits none.
  `s2_delete_with_subquery` produces zero edges and no refusal. That is a convention question
  I raised and the analyser answered differently — not a defect until someone decides.
- **`FIRST_VALUE`/`LAST_VALUE` was never "recorded as undecided" in any way that mattered.**
  Promoted to **S2-07** below. The key still said `derived`, so it was scored against the
  analyser on every run since — the finding claimed an outcome nobody implemented.

**One correction could not be made at all.** The trigger stanza was missing its third edge —
`DIM_CUSTOMER.CUST_ID → relation:DIM_CUSTOMER`, the other operand of `WHERE cust_id =
:NEW.cust_id`, which `b0_04` names as a two-edge block copied between keys. Adding it was
rejected by the validator: unit 9 already states that four-tuple at band 0. Two facts, one
band apart, one match key. It is recorded in the key as unstatable and **S1-05 now blocks a
key correction, not only a measurement.**

**Pinned by `tests/test_stress_keys.py`**, which asserts every program unit in a stress
package is either labelled or carries an explicit `# NO LABELS: <UNIT>` declaration saying
why. It failed on stress 1 the first time it ran — see S1-06.

## S2-07 · `transform-classification` · FIXED — `FIRST_VALUE` and `LAST_VALUE`

| expression | key says | analyser says |
|---|---|---|
| `FIRST_VALUE(p.product_name) OVER (…)` | derived | aggregated |
| `LAST_VALUE(l.unit_price) OVER (…)` | derived | aggregated |

Both readings are defensible and that is the problem — under ADR-0001 §4 a wrong transform is
a **MISS, not partial credit**, so each instance costs a false positive *and* a false
negative. Four cells in stress 2, on a construct that is ordinary in reporting SQL.

The argument for `aggregated`: they are window functions and take a partition. The argument
for `derived`: they *select* an existing value rather than computing one over a set — nothing
is summed, and the value written appears verbatim in some row of the input. The same question
does not arise for `SUM() OVER ()`, which both sides call `aggregated`.

Carved out of S2-06, which claimed it was "recorded as undecided rather than scored against
the analyser". It was scored on every run.

**DECIDED 2026-09-10 — D-3: `derived`. The key was right and `_transform_of` changes.** They
are window (analytic) functions, not aggregations. The rule the decision fixes is that
**window-ness is not aggregation-ness**: `SUM() OVER ()` stays `aggregated` because it
computes a total over a set, and the `OVER` clause is not what makes it one. Being analytic is
not sufficient; computing a value over a set is.

**Root cause.** sqlglot derives `FirstValue`, `LastValue` and `NthValue` from `exp.AggFunc`,
and `AGGREGATE_FUNCTIONS` ends in that catch-all — so the classifier never had a chance to
disagree. The fix is a `VALUE_SELECTING_WINDOW_FUNCTIONS` exclusion checked *before* the
catch-all, in `band0._is_aggregate` and mirrored in `defuse._transform_of`.

**`NTH_VALUE` is included, by the rule rather than by a new decision.** It is `FIRST_VALUE`
generalised — pick the nth row's value instead of the first. It appears nowhere in the corpus
or either stress package, so it costs nothing; leaving it out would only mean the next stress
package finds the same disagreement again.

**Effect.**

| | before | after |
|---|---|---|
| phase-0 measurement | — | **identical** — `cells`, `gate`, `parse_coverage`, `honest_abstention` |
| stress 2 band-0 value precision | 97.0% | **100%** |
| stress 2 band-0 value recall | 65.3% | **69.4%** |
| stress 1 | — | unchanged |

**Phase 0 could not move and that was predicted before running.** `FIRST_VALUE`/`LAST_VALUE`
appear nowhere in the corpus — only in `s2_analytics_suite`. Four cells, exactly the two
expressions × two sides that ADR-0001 §4 charges for a wrong transform.

`MAX … KEEP (DENSE_RANK FIRST …)` was re-checked beside it and stays `aggregated`: `MAX`
genuinely aggregates, and `KEEP` only says which row breaks the tie.

**Pinned by three tests in `tests/test_band0.py`** — the eight-way classification table, the
ladder guard (`SUM(FIRST_VALUE(…) OVER (…))` is still `aggregated`, because the exclusion is
per-node and would otherwise swallow an enclosing aggregate), and a test that pins `LAG` at
its *current* answer while pointing at S2-08.

## S2-08 · `transform-classification` · OPEN — the rule has a ragged edge and two homes

Found by implementing D-3, not by a stress run. Two separate problems, both about the same
function.

**1. `LAG` and `LEAD` read exactly like `FIRST_VALUE` and are classified the opposite way.**
D-3's rule is that window-ness is not aggregation-ness: computing over a set is what makes an
aggregation. `LAG(total)` computes nothing over a set — it reads another *row* of the same
column, which is the argument that made `FIRST_VALUE` derived. `sq_03`'s own key says so in
prose: *"LAG(total) reads another ROW of the same column."* And then labels it `aggregated`.

They were left alone deliberately. **Every key in the corpus labels `LAG` `aggregated`** —
`sq_03_window_functions` and stress 1 both — so moving it changes phase-0 keys and is a
separate measurement, not a free extension of D-3. But the rule as it now stands cannot be
stated to a reader without an exception list, and that is worth fixing one way or the other:
either `LAG`/`LEAD` join the value-selecting set and the keys move, or D-3's rule needs a
sharper statement than "computes over a set" that genuinely separates them.

**2. `_transform_of` exists twice and has already drifted.** `band0.py` and `defuse.py` each
carry their own copy with their own constants. **S2-05's fix landed only in band 0** —
`defuse.CONDITIONAL_EXPRESSIONS` is still `(exp.Case, exp.If)`, the pre-S2-05 pair — so a
`DECODE` reached through def-use is still classified `derived`, and the finding recorded as
fixed is half-fixed. D-3 was applied to both copies by hand, which is the same trap set again.

The two halves are related: a rule that lives in two places will keep diverging, and a rule
with an unexplainable exception list is the kind that gets copied wrong. Merging the
classifiers is the durable fix, and it moves band-1 numbers, so it wants its own measurement.

**No stress package has exercised the S2-05 half.** That is not evidence it is harmless —
it is the same gap that hid S2-06 for four fixes.

## The parser probe — 2026-09-11

Not a stress package. **121 documented Oracle constructs handed straight to both parsers**,
each recorded as accept or reject, with no analyser, no keys and no scoring. Logged in full
as `docs/grammar_limitations.md` (renamed from *Grammar limitations*, because fourteen of the
seventeen findings turned out to be SQLGlot's rather than the grammar's).

**Both halves are now scripts, not descriptions** — `scripts/probe_parsers.py` and
`scripts/probe_analyser.py`, re-runnable and committed. Three register changes came out of
instruments that existed only as throwaway code I ran and narrated; a measurement nobody else
can re-run is an anecdote, and this project does not accept those anywhere else. The analyser
probe exits non-zero on any unexplained silence, so it can be a check rather than a report.

**It exists because every other instrument in this project measures the analyser against the
corpus, and the corpus cannot tell you what it does not contain.** S2-06 is the standing
proof: ten unlabelled units hid four fixes' worth of defects behind a clean-looking score.
The probe attacks the same blind spot from the other side — it asks what Oracle allows that
we reject, rather than what our own examples happen to cover.

**Seventeen constructs rejected, three of them new information for this register:**

| What it changed | Finding |
|---|---|
| **S2-02 is not a parse failure** | `INSERT ALL` and `INSERT FIRST` both parse. Corrected in its entry, and it moves up the queue. |
| **S2-01 re-confirmed, and bounded** | Fails on SQLGlot 30.18.0 at the `DELETE` keyword. The probe did **not** test a newer SQLGlot — that remains the untried first step. |
| **A new grammar defect, not yet given an S-number** | `FILE_EXT` (GL-002) makes `FNC`, `PKB`, `PKS`, `PRC`, `TRG` and `VW` unusable as identifiers anywhere. |

### GL-002 stays out of this register — decided 2026-09-11

`grammars/plsql/PlSqlLexer.g4:1756` is a SQL\*Plus file-extension rule applied everywhere, so
a table called `VW` or a variable called `PRC` fails to *lex*. It is the entry in that
document most likely to be hit by code someone has already written, and a lexer error does
not stay local — it can take a whole package down.

**It is still not a finding about this analyser.** The defect is in a vendored third-party
grammar; the analyser's behaviour given that grammar is correct, and a fix is a grammar
patch, not an analyser change. It carries `GL-002` in the document that owns that subject.
Duplicating it here would make this register's own claims unreadable — "every open finding
recurred across stress packages" cannot be checked if the register mixes in items no stress
package could ever produce.

**What it gets instead is a pointer and a severity note**, which is the part that actually
matters: it is the highest-impact entry in `docs/grammar_limitations.md`, and if a production
package fails to lex, this is the first thing to check.

### The analyser triage, and P-01

The probe pair's second half (`scripts/probe_analyser.py`) pushes every construct the parser
probe ACCEPTED through `analyse_source` and buckets the result: **48 EDGES, 8 REFUSED, 4
DECLARED, 3 SILENT, 0 CRASH** at the time of writing.

**That produced P-01, a category this register had never had: `crash`.** `TABLE(f(...))` has
no relation name, so `_trace` built `Boundary(subject="")` and the `ValidationError` escaped
`analyse_source`.

**A crash is not a worse silent loss, it is a different axis.** Every other failure mode here
costs one statement and leaves the rest of the file intact and counted — a refusal is
declared, a boundary is declared, and even a silent miss leaves the statement counted as
analysed. An exception out of `analyse_source` costs **the whole file**, and *parse coverage
cannot see it*, because nothing was measured at all. The coverage statement, the refusal
register and the kill criteria are all blind to it by construction.

Fixed with a guard that declares the unreadable row source and emits nothing, keeping any
locally provable column beside it — the argument `REMOTE_OBJECT` already makes for a db-link
reference. Nine tests, all failing without the fix at `band0.py:307`. Phase-0 grid, gate and
parse coverage unchanged: no corpus file contains a table function, which is the point.

**The instrument had to be corrected before its output could be trusted.** Six constructs
first came back SILENT and only one was real: three were trigger files, whose edges arrive
through the dictionary pass via the procedure that writes the table (S2-10 again), and one
was a recursive CTE selecting a literal. Two more were declaring a boundary while being
counted as silent, which is why the probe now has a `DECLARED` bucket — **an instrument that
cannot tell a declared boundary from silence is measuring the wrong thing in a project whose
whole thesis is that difference.**

### The method mattered more than any single result

The first probe run named every trigger `trg` and reported six trigger forms as rejected —
compound triggers, `FOLLOWS`, `INSTEAD OF`, `WHEN`, `DISABLE`, `AFTER LOGON`. **That reading
was wrong**, and it was wrong in the most expensive direction: it would have gone into the
documentation as "the grammar cannot parse compound triggers", which is a much larger claim
than the truth. Renaming to `t_aud` parses all seven forms clean. The name was the defect.

Two rules came out of that, and both are the same rule this register already runs on:

1. **Vary one thing.** A probe that changes the construct *and* the identifier cannot
   attribute its own failure.
2. **A rejection is a hypothesis until the minimal case confirms it** — the parser equivalent
   of "a finding is not closed until a regression test fails without the fix".

**The probe's accept-list is recorded too**, which is the half most likely to be discarded as
uninteresting. It is not: it stops the same ground being re-probed, and it is what caught the
S2-02 correction — `INSERT ALL` was on the accept side of a list nobody expected to read.

### What the probe is not

It ran against **no analyser**. A construct that parses may still yield no edges, a wrong
transform, or a silent loss — `INSERT ALL` is exactly that case. **Parsing is a floor, not a
score**, and nothing in `docs/grammar_limitations.md` should be read as coverage.

## Stress 3 — modern complexity, 2026-09-12

**Combinations, not constructs.** Stress 1 went wide and stress 2 went deep on individual
constructs; this package targets the 48 constructs `scripts/probe_analyser.py` found
emitting edges **no key had ever checked** — the largest unverified surface in the
project — and it combines them, because every combination-shaped bug here passed its
minimal case first. `_grouping_influence` handled a flat `GROUP BY` and dropped nested
ones; the window-influence FPs were only wrong once traced through a CTE.

**It worked.** Six units, 69 labels, and the first run produced six analyser findings and
four errors in my own key.

```
  band  flow       TP  FP  FN   precision    recall
  0     filter      7   0   2      100.0%     77.8%
  0     influence   5   0  16      100.0%     23.8%
  0     value      27   8   1       77.1%     96.4%
  1     filter      0   0   1         n/a      0.0%
  1     value       4   3   3       57.1%     57.1%
  2     filter      2   0   0      100.0%    100.0%
  2     value       1   0   0      100.0%    100.0%
```

**Every FP and FN is attributable to exactly one finding.** 8 band-0 value FPs = 6 from
S3-02 plus 2 from S3-04. 3 band-1 value FPs = S3-06. 16 influence FNs = 12 from S3-01 plus
4 from S3-02. That is what a package aimed at combinations buys: no residue.

### What the package CONFIRMED, which is half of its value

* **S2-08's prediction was right, and this is the first evidence for it.** `LAG(o.gross_amount)`
  over a bare column scores `derived`. Unit 3 was built so that column's only path is that
  LAG — no aggregate, no CASE — because the corpus cannot test this: every `LAG` in it is
  `LAG(SUM(...))` and the ladder keeps `aggregated` whichever way D-3 is read.
* **A plain `GROUP BY` two CTE scopes below the `INSERT` works.** Unit 1 scored **11 of 11**,
  including a HAVING at `post-aggregation` and a cross-column CASE condition where
  `COUNT(DISTINCT order_id)` decides which arm of another column's value runs.
* **Three proposed conventions were independently agreed by the analyser** — (d) `KEEP
  (DENSE_RANK FIRST ORDER BY x)` is influence, (e) `GROUPING_ID`'s arguments are value and
  aggregated, (f) an `IN (SELECT …)` semi-join's outer column is filter. Written in the key
  before the run, matched after it. That is the only kind of agreement worth anything.

### S3-02 · FIXED 2026-09-12 — and it closed S3-04 too

**D-4 was reversed by one level of nesting.** In `sq_03` the window sits in the statement
that writes, and its `PARTITION BY`/`ORDER BY` are correctly `influence`. Move the same
window into a CTE and the partition and order columns came out as **value** edges instead —
6 false positives and 4 missing influence edges, the largest single contributor to this
package's score.

The most dangerous kind of wrong available here: **a false value edge says a column
contributed to a number when it only decided the row ordering**, which is exactly the claim
S2-12 and D-4 exist to prevent.

**The cause was one `find_all`.** `_value_columns` encodes two exclusions the IR rests on —
a window's ordering columns supply no value (D-4), a correlated predicate's columns supply
no value (D-5) — and both were applied to the projection the *caller* could see. `_trace`'s
recursion into a subquery then walked `projection.find_all(exp.Column)`: every column,
exclusions gone. **A rule that depends on how the CTEs are stacked is not a rule** — a
sentence already written in `_grouping_influence`, which learned it from `sq_02_cte_chain`.
This was the same lesson in the function next door.

**Fixed in two measurements, because it was one defect with two faces.**

* **The leak** (`a6f7bc2`): `_value_columns` in the recursion. Stress-3 band-0 value precision
  **77.1% → 100%**. I predicted 6 of the 8 false positives would go; **all 8 went**.
* **The loss** (`…`): a new `_window_influence` that descends through nested scopes,
  deliberately mirroring `_grouping_influence` — same walk, same depth cap, same question.
  Influence **5 → 9 TP, 16 → 12 FN**, no new false positives. **Silence was the worse half
  to leave, and a precision-only test would have called the job done after the first
  commit.**

Phase-0 grid, gate and parse coverage byte-identical across both. No corpus package nests a
window inside a CTE — which is the whole reason stress 3 had to exist.

### S3-01 · FIXED 2026-09-12 — every form of GROUP BY is a GROUP BY

`_grouping_influence` read `group.expressions`, and SQLGlot files the extended forms under
their own args:

```
GROUP BY a, b                 -> expressions=[a, b]
GROUP BY ROLLUP (a, b)        -> rollup=[...],        expressions=[]
GROUP BY CUBE (a, b)          -> cube=[...],          expressions=[]
GROUP BY GROUPING SETS (...)  -> grouping_sets=[...], expressions=[]
```

So D-2 held for a plain `GROUP BY` — including one two CTE scopes down, which stress 3
confirmed at 11/11 — and produced **nothing at all** for the three forms a reporting
warehouse actually uses. `ROLLUP` is not an exotic construct; it is what a subtotal is
written with.

**The fix walks the whole `GROUP BY` node rather than adding `rollup` to the loop, and the
mixed form is why.** `GROUP BY a, ROLLUP (b, c)` fills *both* args, so an arg-by-arg version
that missed one would emit a **partial grouping** — an answer that claims `SUM(amt)` is
governed by less than it is, and reads as complete. **S2-13 is on this register for exactly
that shape**: a uniform rule applied to the first instance only. Walking the node makes
"every column under a GROUP BY is a grouping column" true by construction instead of by a
list of arg names that has to be kept in step with a third-party parser.

Stress-3 band-0 influence: **5 TP / 16 FN → 24 TP / 0 FP / 0 FN**, precision and recall both
100%. Phase 0 byte-identical — no corpus package uses an extended grouping form.

Seven regression tests. The plain `GROUP BY` is parametrised **alongside** the four broken
forms rather than left implicit, so a future failure says whether the form or D-2 itself
broke; and one test guards the opposite direction — a grouping key copied through must
*still* take no influence, checked under `ROLLUP` specifically, because that is the path this
fix opened.

### S3-08 — three more key errors, and the same root cause as (f)

The fix turned up three influence edges my key had omitted, all in unit 2. **I had applied
D-2's exclusion as a name match.** D-2 says a grouping key projected through takes no
influence onto itself, *and gives the reason*: "their values are copied through untouched".
I read that as "skip the grouping-key columns" and skipped them wherever the name appeared
on both sides.

Ask D-2's actual test — is the value copied? — and the exclusion never applied to any of
the three:

* **`rank_in_month` is `GROUPING_ID(product_id, category_name)`.** Change the grouping and
  the bitmap changes. Both arguments are grouping keys *and* value sources under convention
  (e); value and influence are different flows with different match keys, and both facts
  hold at once.
* **`category_name` is `LISTAGG(c.category_name)`.** The output shares the source's NAME and
  is not the source's VALUE — a different grouping collapses a different set of rows into a
  different string.

**This is the same species of error as (f) in S3-05**, three findings apart: a convention
applied by pattern rather than by the reason it was written for. Worth naming as a pattern
rather than logging twice — when a rule carries its justification, the justification is the
rule, and the summary is a lossy copy of it.

### S3-06 · FIXED 2026-09-12 — a subscript is not a value

`l_batch(i).refund_amount` parses as `Dot(Anonymous(l_batch, [Column(i)]), refund_amount)`,
so **the subscript `i` is the only `exp.Column` in the whole expression** - the collection
name is the function name and the field is a bare identifier. Every path looking for value
sources found `i`, and nothing else, and emitted `i -> l_adjusted`.

**A subscript chooses WHICH element, exactly as a join key chooses which row.** None of the
loop counter is in the number that comes out; incrementing it moves you to a different
element rather than changing any value. The same argument D-5 made for join conditions and
D-4 made for a window's ordering, reaching the same answer - and it is the third time this
register has had to make it.

**The scope lookup IS the rule.** `pkg.f(amt)` is a genuine call whose argument genuinely
contributes; only a call on a name that is a DECLARED VARIABLE is an index, because PL/SQL
has no way to call a variable. So the test is "is this name in scope", not "does this look
like a subscript" - and a control test asserts a real function's argument still produces its
edge, because "call arguments are never values" is the obvious wrong fix.

**Two call sites, one home.** The assignment path scans identifiers out of TEXT and needed
names; the `VALUES` path walks an AST and needed node ids. Both derive from one
`_subscript_columns` rather than carrying the rule twice - S2-08 is on this register for a
rule duplicated across these two modules that then drifted, and a second copy would have
drifted the same way. Ids rather than names, so `l_batch(i).amt + i` keeps the value edge it
is owed.

> **Amended 2026-09-13 by S4-10, and the two sentences above are why.** There were FOUR
> call sites, not two, and this entry says "two" because those were the two that emitted the
> false positive I was looking at. The other two — `UPDATE ... SET` and every predicate walk
> — kept emitting `i` for another nine days. **And none of the four said what IS read**: the
> index was removed and the collection was never put in its place, so the rule was applied
> in the subtractive direction only. `_subscript_columns` is now `_subscripted` and returns
> both halves together, for exactly that reason. Left in place rather than rewritten,
> because "it is the third time this register has had to make it" reads differently once you
> know the count was wrong when it was written.

**Stress 3 now has ZERO FALSE POSITIVES on every band and every flow.** Band-1 value
precision 57.1% -> 100%. Phase 0 byte-identical.

### S3-03 · FIXED 2026-09-12 — a MERGE filters in two places and reported neither

`_analyse_merge` built value edges only, so

```sql
USING (SELECT ... FROM stg_customer c WHERE c.signup_date < SYSDATE) s
```

produced **nothing at all**. A predicate deciding which customers the load touches, absent
from the IR, in the statement type a warehouse does its upserts with. **That is the finding
filter lineage exists for** — ADR-0001 2 argues the case on a policy table silently
governing which rows load, and a `MERGE`'s `USING` clause is where that gets written.

The second site is the arm: `WHEN MATCHED THEN UPDATE SET … WHERE s.revenue_total > 0`,
which sits on the `Update` rather than the `WHEN`. Tested separately, because a fix that
only walked the `USING` scope would pass the first test and leave this one failing.

**The `ON` clause is deliberately not a third site.** It is a join condition, structural
wherever written (D-5), and reading it as a filter is exactly what S2-14 was — so there is a
test asserting a `MERGE`'s `ON` clause produces no filter edge, and another asserting a
predicate-free `MERGE` produces none at all. The cheap way to pass a recall test is to sweep
every column into a filter edge.

Stress-3 band-0 filter: **7 TP / 2 FN → 10 TP / 0 FP / 0 FN.** Phase 0 byte-identical.

### S3-09 — the fourth instance of one mistake, and the last one I will log separately

The fix surfaced a correct filter edge my key had omitted: the inner operand of the scalar
subquery's correlation, `WHERE r.cust_id = c.cust_id`.

My key's convention list says *"a JOIN CONDITION is structural wherever it is written
(D-5)"*, and I applied it to the correlation. `analysis/predicates.py` says the opposite in
as many words: **"THE CORRELATION IS NOT EXCLUDED and that is the distinction that
matters … excluding the join and keeping the correlation is the difference between reporting
the dependency and reporting the plumbing that made it reachable."** D-5 excludes a join's
`ON` clause, and nothing else.

**That is four times in one package** — (f) in S3-05, twice in S3-08, and here — always the
same error: **a convention applied by the pattern I remembered instead of by the reason it
states.** Logging a fifth separately would be noise. The rule worth carrying forward is that
**when a convention carries its justification, the justification IS the convention**, and
any summary of it — including the summary at the top of a key I wrote myself — is a lossy
copy that will eventually be applied to a case it does not cover.

The tension is recorded rather than hidden: a reading exists on which this correlation
governs the *value* of `revenue_total` rather than which rows reach the target. That reading
is not this project's — the s7 convention was applied corpus-wide in `0b9a9e8` — and a
stress key is not where a settled convention gets quietly reopened.

### S3-10 · FIXED 2026-09-12 — a correlation's outer operand bound to the wrong relation

Found while confirming S3-09 rather than by the package, because **stress 3 could not see
it**: both operands of `r.cust_id = c.cust_id` are named `cust_id`, so the outer one bound to
`FCT_REVENUE`, produced the same edge as the inner one, and deduplicated into a correct-
looking answer.

Rebuilt with distinguishable names, the mechanism is visible:

```sql
USING (SELECT s.sid,
              (SELECT SUM(r.net) FROM rev r WHERE r.rid = s.sid) AS total
         FROM src s) x
```

`r.rid` is emitted correctly. **`s.sid` is not in the scalar subquery's scope**, so `_trace`
falls back to that scope's single source and looks for `SID` on `REV` — declaring
`unresolved_identifier REV.SID` when it is absent, and **emitting an edge from the wrong
relation when a column of that name happens to exist.** `predicates.py` says both operands
stay; one of them cannot get there.

**This is the s2 shape** — a name bound to a relation the statement never meant — and the
only reason it is not already a false positive in the corpus is that the fallback usually
lands on a name that does not exist. Categorised `silent-loss` rather than
`flow-classification` because the failure mode that matters is the wrong-relation edge, not
the missing one.

#### How it was fixed, and the two things measuring corrected

`_trace` searched this scope and the scopes BELOW it, never above. A correlated reference
names a relation in an ENCLOSING query, so the fix walks `scope.parent` outward before
giving up - and **the single-source fallback is now for UNQUALIFIED names only**. With one
relation in scope `amount` can only mean that relation's column; `s.sid` is a different
claim, because the statement said which relation it meant.

**Two things I had written down were wrong, and measuring said so.**

1. **It is not general to `_trace`, though it lives there.** An `INSERT ... SELECT` reaches
   the same correlation by TWO routes - `_correlated_filter_columns` from the outer scope,
   where the alias IS in scope and resolves correctly, and `_filter_edges` from the inner
   scope, which mis-binds. So the correct edge existed all along, with a **spurious
   `unresolved_identifier` boundary** beside it: a boundary promising that knowledge stops
   at a name another code path had resolved without difficulty. A MERGE's synthetic wrapper
   projects `*`, so only the broken route ran. **The defect was in shared code and exactly
   one caller exposed it.**
2. **The first regression test proved nothing.** It asserted `SRC.SID` was among the edge
   SOURCES - and `s.sid` is also the first projected column, so it arrives by a value edge
   whatever the predicate does. It passed without the fix. Now it asserts `SRC.SID` as a
   FILTER source, in MERGE form, which the wrong binding cannot produce.

**The confirmed before-and-after is the cleanest silent-loss evidence in this register.**
With the two operands sharing a column name - `WHERE r.sid = s.sid` over a relation that has
a `sid` - the analyser emitted **one** filter edge and **no boundary**: the outer operand's
edge was replaced by a duplicate of the inner one and deduplicated away. An edge missing,
nothing declared, and the remaining edge looking exactly right.

### S3-07 · FIXED 2026-09-12 — in a MERGE the target is in scope

`_analyse_merge` builds its scope over the `USING` clause alone, because that is the thing
whose projections need tracing. So a reference qualified with the TARGET's alias resolved
against the wrong side entirely:

```sql
WHEN MATCHED THEN UPDATE SET lifetime_value = NVL(t.lifetime_value, 0) + s.amount
```

`t.lifetime_value` came out as `unresolved_identifier`, and the self-edge was absent. **That
accumulator is how a MERGE adds to a running total**, and the self-edge is a real one —
`trg_recent_audit` has the identical shape and `b2_05` has labelled it since it was written.

**The USING scope is consulted FIRST, and that ordering is the whole safety of the fix.** An
alias present in the `USING` clause belongs to that source whatever it is called; preferring
the target would silently redirect a real source reference at the table being written —
**a worse defect than the one being fixed**, because it would fabricate a self-edge rather
than omit one. There is a test on that ordering, one on an unaliased target (`MERGE INTO tgt
… SET tgt.total = …` is legal, and a fix keyed only on the alias would work on every example
written while testing), and one asserting a column the target does not have is still
declared rather than invented.

Stress-3 band-0 value: **27 TP / 1 FN → 28 TP / 0 FP / 0 FN.** Phase 0 byte-identical.

### What stress 3 has left, and what it is

```
  band  flow       TP  FP  FN   precision    recall
  0     filter     11   0   0      100.0%    100.0%
  0     influence  24   0   0      100.0%    100.0%
  0     value      28   0   0      100.0%    100.0%
  1     filter      0   0   1         n/a      0.0%
  1     value       4   0   3      100.0%     57.1%
  2     filter      2   0   0      100.0%    100.0%
  2     value       1   0   0      100.0%    100.0%
```

**Zero false positives everywhere. Every band-0 and band-2 cell is 100% on both axes.** The
only misses left are band 1, and all five are ONE DECLARED REFUSAL: `FETCH ... BULK COLLECT`
into a cursor `%ROWTYPE` collection. S2-04 fixed `SELECT ... BULK COLLECT INTO`; the cursor
form with a `LIMIT` is a different path and is still refused, loudly and counted.

**Nothing the analyser claims about this package is wrong.** What it does not know, it
says.

**The package is now measuring recall against declared gaps rather than hunting false
claims** - which is the state the phase-0 corpus reached after eighteen findings, and this
one reached in a day.

### S3-04 was misdiagnosed, and the register says so

Filed as *"a correlated scalar subquery in `MERGE … USING` leaks both correlation columns —
D-5's fifth call site"*. **It is not a fifth call site and it is not in `defuse`.** It was
the same recursion as S3-02 and the same one-line change removed it, which is how the
prediction of "6 of 8" turned into 8 of 8.

Kept rather than deleted. A register that quietly drops its own wrong diagnoses loses the
only record of how the analyser is actually reasoned about — and this is the second
misdiagnosis on it, after S1-03. Both were "I know which module this is in" before
measuring.

### S3-05 — my own key, four errors, corrected and kept

**The S2-06 shape, in a key written four days after S2-06 closed.** Each omission made
CORRECT analyser edges score as false positives:

1. Unit 5's surviving variable-level chain was unlabelled. The `FETCH … BULK COLLECT` is
   refused, so the link to `stg_returns` breaks — but everything downstream of `l_batch`
   is still real def-use and still emitted. Stress 2 labels 28 variable nodes for exactly
   this reason.
2. `DIM_CUSTOMER.CUST_ID → DIM_CUSTOMER` (band 2) omitted. **`b2_05` has carried both
   halves of that trigger's `WHERE` since it was written**, and this key copied one.
3. `REF_POLICY.REGION → TMP_RECENT` omitted — convention (f) was stated for the semi-join
   and then applied to one of its two halves. That is S2-13's mistake, which is already on
   this register.
4. `expected_boundaries: []`, claiming the package stops nowhere. It stops in one place,
   loudly, and **a key that does not say so cannot tell a declared gap from a silent one**
   — the only distinction this project ultimately sells.

Correcting them moved band-0 filter to 100% and both band-2 flows to 100%. The errors are
kept rather than quietly amended: a benchmark that edits itself to agree with the code has
stopped measuring anything.

### The S1-05 collision that happened while writing this

Unit 3 first wrote `fct_revenue`, like unit 1. **Six of its edges then collided with unit
1's on the match key** — same source column, same target column, same flow and transform,
from a different statement doing a different thing — and the key would not load at all.
Origin is not in the match key (amendment 1b).

Retargeting unit 3 at `fct_revenue_stage` is realistic and was the right call, but it is a
workaround. **S1-05 is now blocking a package written today, not just the stress-2 key
written three days ago**, and it did so within an hour of starting. Recorded here because
the gate on S1-05 says to decide it against production code, and this is the closest thing
to production shape the project has produced.

## Stress 4 — depth at scale, 2026-09-12

**1504 lines, 28 units, 48 statements, and NO NEW CONSTRUCT.** Every keyword appears in
stress 1, 2 or 3. The question was not "what else breaks" but "do the seven fixes hold when
the same constructs are stacked to the depth real ETL is written at" — so anything that
breaks here breaks because of **depth, scale or interaction**.

```
  band  flow       TP  FP  FN   precision    recall
  0     filter     54   0   5      100.0%     91.5%
  0     influence  59   1   7       98.3%     89.4%
  0     value      73   2   5       97.3%     93.6%
  1     filter      2   0   3      100.0%     40.0%
  1     influence   0   0   1         n/a      0.0%
  1     value      20   0   9      100.0%     69.0%
  2     filter      2   0   0      100.0%    100.0%
  2     value       0   1   0        0.0%       n/a

  parse coverage 100.0%   (31 statements seen, 31 analysed, 0 refusals)
```

### The fixes hold. That is the first result and it is not a small one.

**Four false positives in 1504 lines**, and three of the four are errors in my own key.
Band-1 value precision is **100%**. The seven stress-3 fixes were each written against a
small, deliberate case; here they ran against five-level CTE chains, four and five table
joins, aggregation at two grains in one statement, windows stacked three scopes apart and
two MERGEs of different shape, and none of them regressed.

Specifically verified at depth: `GROUP BY` influence through a `ROLLUP` sitting **above**
another `GROUP BY` (S3-01); window influence traced through **three** nested scopes with
three different partitions, and not leaking as value (S3-02); a `MERGE`'s `USING` and
arm-level filters at the bottom of a four-level chain (S3-03); a subscript excluded for
**two** collections indexed by one loop variable (S3-06); a `MERGE` accumulator self-edge
(S3-07); a correlation resolving outward from two scopes down (S3-10).

> **One of those six is weaker than it reads, per S4-10 on 2026-09-13.** The S3-06 check
> confirmed the index was *excluded* at depth and said nothing about whether the collection
> was *reported* — it was not, in any of the three statements it appears in. Verifying an
> exclusion is half a verification when the rule has two halves, which is the same shape as
> the test error S4-10 records.

### S1-05 IS NOW A HARD BLOCKER, AND THIS IS THE EVIDENCE ITS GATE ASKED FOR

**334 edges were written from source. Only 240 could be STATED.**

| | |
|---|---|
| edges written from source | 334 |
| statable | 240 |
| **lost to match-key collision** | **94 — 28.1%** |
| colliding keys | 56 |
| units affected | **27 of 28** |
| worst single collision | **7 statements, one statable fact** |

The match key is `(source, target, flow, transform, phase)` and origin is not in it
(amendment 1b). `STG_ORDERS.ORDER_DATE -> FCT_PRODUCT_SALES.PERIOD_MONTH [value/derived]`
is produced by **seven different statements doing seven different things**, and the key can
say it once. **The key would not load at all** until the duplicates were collapsed; every
dropped claim is listed in a comment block at the top of the key file.

**The gate on S1-05 said to decide it against production code rather than more synthetic
evidence.** This is the closest thing to production shape the project has produced, and the
answer is unambiguous: at this shape the benchmark cannot express a quarter of what it
knows. Stress 2 measured 31% on a much smaller key and that was arguable; 28% across 27 of
28 units, where the collisions are *between real statements rather than within one*, is not.

### What scale exposed: five findings, and two of them are silent

**ZERO REFUSALS IN 1504 LINES, AND PARSE COVERAGE 100%.** Every loss below is either
silent or declared by nothing more than `context_dependent_binding`, which appears on every
unit and explains nothing.

**S4-01 · `silent-loss` · a top-level `UNION ALL` under `INSERT` yields NOTHING.**
`s4_customer_lifecycle` produces **zero edges**. Both arms are aggregating CTEs, both bind
by position, and twelve labelled edges vanish — with no refusal, no explanatory boundary,
and the statement counted as analysed. **S1-02 is on this register as "top-level set
operators under `INSERT` refused, wrong reason", fixed.** This is the same family
surviving where the arms are CTEs rather than base tables, and it is now worse than it was:
a refusal at least declared itself.

**S4-02 · `silent-loss` · a cursor `%ROWTYPE` field does not reach the base column.**
`s4_cursor_ladder` emits eight edges and every one of them starts at a **variable**:
`V_CUST_ID -> FCT_REVENUE_PART.CUST_ID` is there, `STG_ORDERS.CUST_ID -> V_CUST_ID` is not.
The cursor's query is a three-level CTE chain with its own `GROUP BY` and `HAVING`, and
`rec.cust_id` never resolves through it.

**This is the most dangerous shape in the package.** The def-use chain is intact, so the
output looks like working lineage — it simply begins nowhere. A missing edge is visible; a
chain that starts at a variable reads as a chain whose source is a variable, which is a
sentence the IR is entitled to say.

**S4-03 · `construct-coverage` · a MERGE emits no influence edge.** S3-03 gave a `MERGE`
its filter edges and stopped there: `_analyse_merge` never calls `_grouping_influence` or
`_window_influence`. Six edges across the two MERGEs — every `GROUP BY` in a `USING` clause
and every window in one.

**S4-04 · `flow-classification` · a set operation's second arm is read as VALUE.** Stress
2 proposed convention (a) — the second arm of a `MINUS`/`INTERSECT` decides which rows
survive and supplies no value, so it contributes filter. **It was never implemented.**
`STG_RETURNS.CUST_ID -> GTT_STAGE.CUST_ID [value/identity]` is the one analyser false
positive in the package, and three matching filter edges are missing.

**S4-05 · `construct-coverage` · `BULK COLLECT` into two collections resolves one.**
`SELECT a, b BULK COLLECT INTO c1, c2` loses the column source of the second target. Both
band-1 units use the two-collection form because that is how a real batch loader is
written.

### S4-06 — three key errors of mine, and one is worth more than the other two

* **A trigger inheritance omitted.** Unit 5 writes `dim_customer`, which carries
  `trg_customer_default`, and I labelled the `trg_recent_audit` inheritance in unit 11 while
  forgetting this one. The analyser got it right.
* **A view's internal `CASE` missed.** `v_cust_l2` derives `status_flag` as
  `CASE WHEN status = 1 THEN 1 ELSE 0 END`, so `LOWER(status_flag)` is `conditional`, not
  `derived`. I read the outer expression and not the view.
* **An `ON` clause labelled as a filter.** `JOIN ref_policy p ON p.region = 'EU'` compares a
  column to a LITERAL, which *looks* like a filter and is structurally an `ON` clause. D-5
  is explicit that an `ON` clause is structural wherever written. **Fifth instance of the
  one mistake S3-09 named** — a convention applied by the pattern I remembered rather than
  by the reason it states.

### What this package establishes about the method

**A large key is a different instrument from a small one, and its failure mode is its own
size.** 334 hand-written labels produced five real findings and three key errors — a much
better ratio than stress 3's seven and four — but it also hit a structural limit that no
smaller package could reach. The 28% collision rate is not a labelling mistake; it is what
happens when 28 statements write to eight tables from eight tables, which is what an estate
looks like.

**Recall, not precision, is now the whole story.** Precision is 100% on five of eight cells
and the single false positive is a convention that was never implemented. Every other gap
is something the analyser does not know and does not say. Two of them it does not say *at
all*.

## S4-10 · `silent-loss` · FIXED — one rule, half-applied at four call sites, 2026-09-13

Filed from stress 4 as *"a subscripted collection in a `VALUES` list contributes no
source"*. **Measuring it found one defect with two directions across five statement paths,
and the direction that was not filed is the worse one.**

S3-06 settled the rule on 2026-09-12: `l_batch(i)` reads the collection, `i` only chooses
which element, so the index is not a source — the collection form of D-5's argument about
join keys. **It removed `i` from the paths it knew about and put nothing in any of them.**

The assignment path kept working by accident. It scans identifiers out of TEXT, so
`l_batch` arrives as a name whatever the AST looks like. Every path that walks the AST saw
the collection only as `Anonymous.this` — never an `exp.Column`, so never a candidate:

| written | lost | AND emitted |
|---|---|---|
| `VALUES (l_ids(i))` | `L_IDS` | — |
| `VALUES (l_batch(i).sid)` | `L_BATCH` | — |
| `SET amt = l_amts(i)` | `L_AMTS` | **`I -> TGT.AMT` value** |
| `WHERE sid = l_ids(i)` | `L_IDS` | **`I -> TGT` filter** |
| `INSERT … SELECT … WHERE sid = l_ids(i)` | `L_IDS` | **`I -> TGT` filter** |

**The last three rows are false positives, and they are S3-06's own defect still live on
paths S3-06 never reached** — a loop counter reported as the value source of a monetary
column and as a filter source of a read table. All four stress packages scored zero false
positives with these standing, because no package subscripts a collection inside an
`UPDATE` or a `WHERE`. A probe found them in one run.

**Why the exclusion and the replacement are now one object.** `_subscripted` returns
`index_ids` and `collections` together, so a call site that drops the index from its walk
is holding the collection at the same moment. The previous design let a call site take half
the rule and look finished, which is exactly what four of them did.

### A third defect underneath: the transform

`l_amts(i)` parses as a function call, so the ladder scored every value written from a
collection as `derived` when nothing had been done to it. ADR-0001 §4 makes a wrong
transform **a miss on both sides**, so this was a false positive *and* a miss per edge, not
a cosmetic slip. Each declared-variable call is now rewritten to a plain name and the
ladder is asked the ordinary question: `l_amts(i)` → identity, `l_amts(i) * 1.05` →
derived, `CASE WHEN l_amts(i) …` → conditional, `SUM(l_amts(i))` → aggregated,
`pkg.f(l_amts(i))` → derived. Writing a second ladder here is how S2-08 happened.

Worth recording that the first version of this guard **matched nothing**: `transform` is
top-down, so the `Dot` of `l_batch(i).sid` is visited while its child is still the call, and
a guard written against the rewritten shape never fires. It looked right, and the
measurement said `derived`. Diagnosis before treatment, for the fifth time in this file.

### Why a regression suite did not catch it — a test error

`test_collection_subscript.py` asserts BOTH halves for the assignment form:

```python
assert "I" not in sources, "the loop index leaked as a value source"
assert "L_BATCH" in sources, "the collection itself is the real source and was lost"
```

and, six lines below, **only the first** for the `VALUES` form. So that test passed against
a statement that emitted no edge whatever, and the silent loss survived the suite for nine
days. The convention was stated in that file and applied to one of its two cases — the same
*applied by the pattern remembered rather than by the reason it states* shape the register
already carries five times, this time in the instrument rather than in the key. The missing
assertion is now there, with the date and the reason on it.

**Result.** Stress 4 band-1 value 26/0/3 → **29/0/0**, stress 3 band-1 value 4/1/3 →
**5/0/3**, all four packages back to zero false positives in every cell, phase 0 unmoved:
gate 96.2% / 96.2%, guard 100% of 27, parse coverage 81.6%. Eleven regression tests fail
without the fix; the four that pass without it are the controls.

## S3-11 · `key-error` · FIXED — two of the three reads of one collection

The `INSERT` at stress 3 line 192 writes three columns:

```sql
VALUES (l_batch(i).product_id, l_adjusted, l_flag)
```

S3-05's correction block labelled the two that arrive **through a variable** and missed the
third, whose value comes straight out of the collection. The block exists to state that
*"everything downstream of `l_batch` is still real def-use and still emitted"* — and then
listed two of the three places it is emitted.

Found on 2026-09-13 when S4-10's fix made the analyser emit the third edge and the grid
reported a correct edge as spurious, dropping band-1 value precision to 80%. **The edge was
judged on its own terms before being added** — it is right by exactly the argument that
justifies the two already there — because a benchmark that edits itself to agree with the
code has stopped measuring anything.

Same family as S3-09, S3-08 and S4-06#4, for which the register already carries the rule
rather than a further entry. Logged anyway, because **the grid moved**, and a measurement
that changes with no register entry behind it is the thing the register is for.

## P-02 · `silent-loss` · OPEN — a `DELETE`'s predicate never sees variables

Found by probe on 2026-09-13 while measuring S4-10, and **deliberately not fixed with it**:

```sql
DELETE FROM tgt WHERE sid = v_cutoff;   -- V_CUTOFF lost
UPDATE tgt SET flag = 1 WHERE sid = v_cutoff;   -- V_CUTOFF reported
```

`_analyse_delete` lives in **band 0**, which has no `UnitScope` and therefore cannot tell a
variable from a column at all. The def-use pass never claims a `DELETE`, so nothing looks
at its predicate for variables — the S2-04 shape again: two passes each correctly deciding
the statement is not theirs, and nobody owning the result.

This is wider than subscripts and is not what S4-10 was about, so it is filed rather than
folded in — one fix per measurement. It also explains why the `DELETE` form of the S4-10
probe showed no `I -> TGT` false positive: not a rule, just band 0 being unable to resolve
`i` to anything. **A correct output for the wrong reason, which stops being correct the
moment the gap is closed** — so whoever takes P-02 must add the subscript rule in the same
change, and the S4-10 regression tests are what will say so.

## S4-09 · `construct-coverage` · FIXED — a statement read into a variable has predicates too

Filed as *"a declared cursor's own `WHERE` produces no filter edge against what the loop
writes"*. **The construct was wrong.** Cursors are one place this shows; the defect is that
a `SELECT … INTO` — bulk or scalar — reported nothing about the clauses that decide which
rows it reads:

| written | band 0, writing a table | band 1, reading into a variable |
|---|---|---|
| `WHERE region = 'EU'` one level down | filter, pre-aggregation | **nothing** |
| `HAVING SUM(amt) > 0` | filter, post-aggregation | **nothing** |
| `GROUP BY sid` | influence onto the aggregate | **nothing** |

`_analyse_select_into` had its own one-level walk: one top-level `WHERE`, with a target from
`_read_relation` — **which returns `None` the moment the `FROM` is a subquery.** So a
derived table or a CTE silenced the clause that WAS handled, as well as the two that never
were. The S2-04 shape a fourth time: a construct each pass correctly decides is not quite
its own.

Closed by `band0.resolve_query_influence`, **the third use of one handoff**: S4-02 gave
band 1 `resolve_projection` for a cursor record's field, S4-05 reused it for a projection
computed in a derived table, and this is the same delegation for the non-value half. The
traversal and both influence resolvers are reused unchanged; S2-08 is on the register for a
rule that lived in two modules and drifted, and the one-level walk this replaces is what
that drift looks like before anyone notices.

### The target convention, which I got wrong twice

*A filter edge targets the relation the statement WRITES; when it writes a variable, the
relation read.* **Those are two clauses, and I read them as one.**

My first cut targeted the filtered column's own relation. I then "corrected" it to the
relations the statement's VALUES come from, on the strength of the band-0 claims — where
`DIM_CUSTOMER.IS_ACTIVE` targets `FCT_REVENUE`, `GTT_STAGE` and `TMP_RECENT`, never
`DIM_CUSTOMER`. But **those statements write a relation**, so they are the first clause and
say nothing about this one.

`fn_stress_net` settled it in a single run. It reads values from two relations through a
join and its key claims exactly ONE filter edge, `STG_ORDERS.ORDER_ID -> STG_ORDERS`; the
value-relations reading emitted two, scoring a true positive and a false one off one
predicate. Ten band-1 claims across three packages agree with the first reading, and the two
that appear not to — `STG_ORDERS.CURRENCY` and `DIM_CUSTOMER.IS_ACTIVE` onto
`FCT_REVENUE_PART` — are the cursor case, where the loop does write a relation. First clause
again. **Six examples agreed with the wrong answer; one disagreed, and it was the only one
that mattered.**

### S1-05 was concealing this, not merely counting it

`STG_ORDERS.CURRENCY -> FCT_REVENUE_PART [filter]` scores as a **true positive** in stress
4. The cursor ladder emits nothing of the sort — the claim was collapsed out of
`S4_FILTER_CASCADE`, which writes a table and emits it correctly, and origin is not in the
match key. So a correct-looking cell was carrying a silent miss from a different unit.

Until now S1-05's cost has been recorded as *claims that cannot be stated*. This is the
first demonstration that the collision **hides a defect** rather than losing a label, and it
argues the finding up rather than merely confirming it.

**Result.** Stress 4 band-1 filter 2/0/3 → **5/0/1**, band-1 influence 0/0/1 → **1/0/0**,
zero false positives in every cell of all four packages, stress 1/2/3 identical to the digit.
Phase 0 unmoved: gate 96.2% / 96.2%, guard 100% of 27, parse coverage 81.6%. Five of the
nine new tests fail without the fix.

## S4-12 · `flow-classification` · FIXED — a filter edge naming the wrong relation

Found by S4-09's own regression test, having survived four stress packages.

```sql
SELECT SUM(s.amt) INTO v FROM src s JOIN dim d ON d.sid = s.sid WHERE d.is_active = 1;
```

emitted `DIM.IS_ACTIVE -> SRC [filter]`. `_filter_edges_for` takes ONE `target_relation` and
points every column in the predicate at it, and `_read_relation` answers with the outermost
`FROM`'s first table. **A predicate's columns do not all belong to that table.** The edge was
right only by coincidence, and in every stress package the coincidence holds — the predicate
is always on the driving table, so nothing measured it.

The column side now belongs to the delegated walk, which knows each column's own relation.
`variables_only=True` keeps the local walk for the variable side, which is the half only this
module can classify: to band 0, `v_cutoff` in a `WHERE` is a column, and binding it to
whichever table is in scope is this module's opening paragraph.

**Worth noting which instrument found it.** Not a stress package and not a probe — a
regression test written to pin a convention, whose first run disagreed with the code for a
reason I had not predicted. A test written to state *why* an answer is right will sometimes
find that a different answer was wrong.

## S4-13 · `construct-coverage` · OPEN — a cursor loop writes without its cursor's predicates

S4-09's remaining half, and what the old S4-09 row actually described:

```sql
CURSOR c_customer IS SELECT ... WHERE o.currency = 'GBP' ... HAVING SUM(...) > 0;
BEGIN
  FOR rec IN c_customer LOOP
    INSERT INTO fct_revenue_part (...) VALUES (v_cust_id, ...);
```

Which rows reach `fct_revenue_part` is decided entirely by the cursor's query, and none of
it is reported. Separate from S4-09 because it is a different problem: **the predicates are
in one statement and the write is in another**, so this needs the loop's written relation
connected to the cursor's query, not a resolver reached from the statement being analysed.

`STG_ORDERS.GROSS_AMOUNT -> FCT_REVENUE_PART [filter/post-aggregation]` is the one remaining
miss in stress 4. Two of its siblings score as true positives only because of the collision
described under S4-09 — so closing this will move the grid less than it should, and that is
S1-05's doing rather than this fix's.

## Fix order for what remains

Ordered by what each one would teach, not by how annoying it is. Eleven are done; this is the
queue from here. **S2-06, S1-06, S2-09 and S2-11 are struck from it** — all four were key
corrections, none moved the phase-0 measurement.

1. ~~**S2-07** (`transform-classification`). **D-3.**~~ **Done.** Four cells, phase 0
   unmoved, and it did what a first item should: it turned up S2-08.
2. ~~**S1-04** (`refusal-taxonomy`). **D-1**, all three halves.~~ **Done.** `INSERT … VALUES`,
   then `DELETE`, then `UPDATE`, each on its own measurement. Phase-0 parse coverage 76.1% →
   81.6% and the false-abstention rate to zero, with the grid unmoved throughout. **The
   `UPDATE` half was not an UPDATE problem** — it was a parse failure on `WHERE CURRENT OF`,
   diagnosed before being treated, which is why it cost a rewrite rule rather than an
   analyser change.
3. ~~**S1-03** (`flow-classification`). **D-2.**~~ **Done.** `HAVING` became a `filter` with
   `phase: post-aggregation` and `GROUP BY` an `influence`; 80 new labels across 12 key files.
   It moved the grid once, deliberately, and nothing else.
4. ~~**S2-02** (`construct-coverage`). **Promoted above S2-01 on 2026-09-11**, because the probe
   showed the two are not the same kind of problem. `INSERT ALL` **parses** — the tree is
   already there and the analyser declines to walk it. No version bump, no rewrite rule, no
   external dependency: ordinary lineage semantics on a parsed node, and the cheapest real
   coverage left on the board.~~ **Done 2026-09-14**, and the promotion was right: it needed no
   new traversal, because `resolve_projection` and `resolve_query_influence` were both already
   public. Six value edges, two filter edges, six correct guards, +3.3pp parse coverage. The
   probe that re-read the refusal message in September paid for itself here.
5. **S2-01** (`construct-coverage`). `MERGE … DELETE` never becomes a tree. Confirmed against
   SQLGlot 30.18.0; **a newer SQLGlot has not been tried, and trying it is the first step**,
   because it is the only option that costs nothing if it works. Failing that, a refusal code
   that names the real reason — the rewrite route is ruled out below.
6. ~~**S2-08** (`transform-classification`).~~ **Done.** Both halves: the classifiers were
   merged into `analysis/transforms.py`, and `LAG`/`LEAD` were settled by D-3's existing rule
   rather than a new decision. Neither moved a number, and the reason each did not is recorded
   in its entry — that is the finding, not an absence of one.
7. **S1-05** (`identity`). Last, because it is the largest and the only one that moves every
   number in the phase. **Do not start it until the production package has been measured** —
   the verdict's condition still stands, and this is precisely the decision that wants real
   code in front of it rather than more synthetic evidence. It is now blocking key
   *corrections* as well as measurements: see S2-06's trigger edge.

   > **Its condition is now met, and the case is stronger than the entry says.** Stress 4 is
   > the production-shaped package the verdict asked for: 334 labels, 28 units, **28.1% of
   > the key unstatable across 27 of them**. And S4-09 found the collision *concealing a
   > silent miss behind a 100% cell*, which is a different and worse charge than the one
   > this queue was waiting on evidence for. Promoting it is a decision for Abhay, not a
   > detail to be settled by whoever picks up the next finding.

**`lineage.parsing.rewrite` is now on the table for S2-01.** That entry has proposed a
pre-parse rewrite since it was written, and S1-04's `UPDATE` half built the module and the
admission test for it: strip only what SQLGlot cannot parse at all AND that names no column
or variable. `MERGE … DELETE` fails the second half of that test — the `DELETE` arm carries
a `WHERE` with real columns — so it is **not** a rewrite candidate on those terms, and the
honest options remain a SQLGlot bump or a refusal code that names the real reason. Worth
knowing before someone reaches for the new hammer.

**A finding is not closed until a regression test fails without the fix.** Reproducing it
once in a stress run is a symptom; the test is the fix's only durable statement.

## Before stress 3

Every open finding from stress 1 recurred in stress 2, so a third package will mostly restate
what is already here. **All five decisions are now landed and every register item except
S1-05, S2-01 and S2-02 is closed**, so the condition this section set has been met: a stress 3
would now measure something new rather than re-report known gaps.

**But the parser probe suggests a stress 3 is no longer the best next instrument.** Two of the
three findings it produced came from asking what Oracle allows that we reject — a question no
stress package asks, because a stress package can only contain constructs someone thought to
write. A third hand-written corpus would inherit exactly that limit. **S1-05's gate points the
same way**: the thing this project most needs in front of it is real production code, not a
third synthetic package.

**Do not write stress 3 to put a number on S1-05's growth curve. That number is in.** 7% at
13 units, 31% at 28 with the key complete, and three of the twenty-eight units unable to
state a single fact. A third synthetic package would produce a fourth point on a curve whose
shape is no longer in question, and the verdict's condition already says what the next
evidence has to be: **real code**.

What stress 3 should still reach is **control flow that is genuinely deep** rather than wide.
Both stress files are broad and shallow, and nothing yet has tested a call chain against the
interprocedural depth cap.

**And write the key with `tests/test_stress_keys.py` in front of you.** Both key-error
findings in this register were the same mistake — a unit written into the SQL and never
labelled — and both were invisible until something counted the units.

---

# PostgreSQL stress — the `PG-` series

Written against `docs/postgres_scope.md`, which makes a stronger promise than the Oracle
packages do: **inside the scope nothing is refused and nothing fails; outside it everything
is refused by name.** A `PG-` finding is therefore always one of two things — a defect, or a
place where the scope claims more than it can do.

## Stress PG 1 · `stress_pg_01_wide_coverage.sql` · first run, 2026-09-23

Seven statements. Breadth inside the measured scope: a CTE chain into windows into an
aggregate INSERT, a positional `UNION ALL`, `UPDATE … FROM`, `DELETE … USING`,
`ON CONFLICT DO UPDATE`, `CREATE TABLE AS SELECT`, and one all-literal INSERT.

### Score

```
  band  flow       TP  FP  FN   precision    recall
  0     filter     16   0   0      100.0%    100.0%
  0     influence  19   0   0      100.0%    100.0%
  0     value      23   0   0      100.0%    100.0%
```

**58 of 58, zero false positives, zero misses, zero refusals.** Which is the contract the
package was written to test, and it holds — *for the edges*. Two things in the same output
contradict the key, and neither is visible in that grid.

Worth stating why 100% is credible here rather than suspicious: statements 1 and 2 are close
ports of Oracle's `stress_cte_window` and `stress_set_ops`, whose originals already score
100% against keys written months earlier under the same conventions. What is being measured
for those two is **dialect parity**, and parity is the outcome the dialect seam was built
for. Statements 3 to 6 have no Oracle equivalent and are the genuinely new evidence.

## PG-01 · `measurement-error` · OPEN — an `UPDATE` is analysed and never counted

The package prints `statements seen 6` and the file contains **seven** statements.

The SQL comment above statement 7 predicted "stress_pg_01 has SEVEN statements". That
prediction was written before the run and it failed, which is the only reason this was
caught: in a PL/SQL package with thirty statements nobody counts by hand.

### What it is

`band0.SUPPORTED` holds four kinds — `insert_statement`, `merge_statement`,
`delete_statement`, `create_statement`. **`update_statement` is not one of them**, by design:
an `UPDATE` is analysed by def-use, which runs per unit. But `statements_seen` is incremented
only on the band-0 path, and def-use never touches it. So:

```
line   89 seen=0 edges=5    UPDATE dim_customer d ... FROM stg_orders s
```

Five correct edges, counted as zero statements.

### It is not a PostgreSQL defect

Confirmed on Oracle against the captured corpus dictionary — one `UPDATE` and one `INSERT`
in a procedure report `seen 1`, with the UPDATE's filter edge present in the output:

```
seen 1  analysed 1  edges 6
   column:DIM_CUSTOMER.CUST_ID -> relation:DIM_CUSTOMER filter     <- from the UPDATE
```

**Every `UPDATE` in the project is missing from the parse-coverage denominator, and has been
since coverage existed.** PostgreSQL did not cause it; a seven-statement script made it
countable where a package body hid it.

### Why it is filed as `measurement-error` and not as cosmetic

Two consequences, and the second is the serious one.

1. **Parse coverage is overstated.** The phase-0 figure is 81.6%. Analysed UPDATEs would
   join both numerator and denominator, which *raises* a ratio below 1 — so the signed
   number is not merely imprecise, it is imprecise in the flattering direction.

2. **An `UPDATE` that def-use also fails on is fully SILENT.** No edge, no refusal, no
   count, nothing in coverage. That is the exact outcome `docs/postgres_scope.md` says is
   never acceptable, and it is reachable today: **P-02 in this register is precisely such a
   case for `DELETE`** — a predicate that never sees variables. The two findings are the
   same shape, and one of them has no instrument that can see it.

### Why the probe missed it

`probe_postgres.bucket()` returns `EDGES` as soon as `result.edges` is non-empty and only
consults `statements_seen` when there are none. Both UPDATE constructs in the probe produce
edges, so both passed. **The instrument checks that a statement was counted only in the case
where it produced nothing** — which is exactly the case where being uncounted is easiest to
notice by other means, and not the case where it hides.

That is the same class of instrument flaw as the NO-SOURCE bucket this probe already gained,
and it wants the same treatment: a bucket cannot certify "nothing falls through unowned"
while its own happy path never asks.

### Not fixed here, deliberately

One fix per measurement. This one moves `parse_coverage` in the **signed phase-0
measurement**, so it is a deliberate re-measurement with a recorded before and after, not a
side effect of writing a stress package. What is needed:

* count an `UPDATE` where it is analysed, without double-counting one that band 0 also sees;
* widen the probe so an uncounted statement is a finding even when it produced edges;
* re-run phase 0 and record the movement.

## PG-02 · `key-error` · FIXED — the key claimed no boundaries and one was declared

`stress_pg_01_wide_coverage.yaml` shipped with `expected_boundaries: []` and a note calling
the empty list "the point of this package". The run declared one:

```
context_dependent_binding | <SCRIPT>:1 | unqualified names from line 1 resolve through
whichever schema executes this unit ...
```

**The analyser is right and the key was wrong.** Every name in the package is unqualified,
and in PostgreSQL what they resolve to depends on `search_path` — the s2 schema-context
failure that `Dictionary.default_schema` exists to make visible, and which
`docs/postgres_scope.md` already lists under what the scope does not cover.

Corrected in the key rather than argued with: a declared boundary is a *deliverable*, and a
key that expects none is asserting the analyser should have been silent about a real
known-unknown. Tagged `key-error` and kept, per the rule at the top of this file.

## Stress PG 2 · `stress_pg_02_deep_nesting.sql` · first run, 2026-09-23

Five statements, each nested deeply enough that a value must survive several rewrites.

### Score

```
  band  flow       TP  FP  FN   precision    recall
  0     filter     14   0   1      100.0%     93.3%
  0     influence  21   0   1      100.0%     95.5%
  0     value      17   0   5      100.0%     77.3%
```

**Zero false positives in every flow.** Nothing was guessed — which is the property this
project is organised around, and it held under five-level CTE chains, a recursive CTE, a
ROLLUP, three subquery predicate shapes and two window functions with an explicit frame.

Statement A — the five-level chain — scored **complete**. `gross_amount` passed through two
SUMs and a window SUM and arrived as `aggregated`; the asymmetry the statement was built to
test (a grouping key influences the columns its aggregate actually feeds, not every
aggregate in the query) came out exactly as labelled. So did the recursive CTE, including
the deliberate absence of any source for `depth`.

**Seven misses, all in statements C and D, and every one DECLARED.** No silent statement, no
crash. They resolve into three findings, each isolated against a control below.

## PG-03 · `construct-coverage` · OPEN — a `LATERAL` subquery's columns do not resolve

Everything projected from inside a `LATERAL` is lost. Four of the seven misses.

Isolated, with the control that makes it specifically LATERAL and not derived tables:

```
CONTROL  plain derived table       3 edges
         stg_orders.gross_amount -> gtt_stage.amount  value/aggregated     <- present

LATERAL  same query, correlated    4 edges
         BOUNDARY unresolved_identifier GROSS_AMOUNT                        <- lost
```

The correlation's own filter edges are produced correctly (`stg_orders.cust_id` and
`stg_customer.cust_id` both reach the relation). It is the **projection** that fails: the
LATERAL alias is not registered as a row source, so every column selected through it is an
unresolved identifier.

Declared, not silent — the analyser says `unresolved_identifier` by name. That is the
difference between a gap and a defect in the taxonomy, and it is why this is
`construct-coverage` rather than `silent-loss`.

## PG-04 · `construct-coverage` · OPEN — `jsonb_build_object` is opaque to its own arguments

```
CONTROL  COALESCE(o.currency, 'x')            stg_orders.currency -> ... value/derived
         jsonb_build_object('c', o.currency)  BOUNDARY source_unavailable JSONB_BUILD_OBJECT
```

A function that builds a document out of its arguments is treated as a source of unknown
data rather than as an expression over the columns passed to it. Both of statement D's
`detail` edges are lost this way.

`source_unavailable` is a reasonable default for an unrecognised function — **the refusal is
honest, and the classification is what is wrong.** Its arguments are right there in the
tree, and two columns that demonstrably reach `detail` are reported as unreachable.

## PG-05 · `silent-loss` · OPEN — a set-returning function in `FROM` drops its column with NO boundary

The one finding here whose failure mode is silence, and it is the worst-shaped of the three.

```
unnest in FROM           1 edge, NO boundary declared       <- t.tag lost, nothing says so
generate_series in FROM  1 edge, BOUNDARY dangling_reference GENERATE_SERIES(1, 5) AS ...
```

`unnest(c.tags) AS t(tag)` loses `stg_customer.tags -> audit_log.changed_by` and declares
**nothing at all**. `generate_series` in the same position declares `dangling_reference`, so
the machinery exists and `unnest` is slipping past it.

## PG-17 · `dialect-leak` · OPEN — PostgreSQL dollar-quoting refused as Oracle conditional compilation

Found by `pg_parser_torture_fact_shipment_margin.sql` (2,963 lines, 70 CTEs, synthetic,
no DDL). The file was refused for an unrelated parse failure (PG-18), and the refusal
arrived carrying **the wrong reason**:

```
REFUSAL [CONTEXT_DEPENDENT] conditional compilation - which branch exists in the
compiled unit depends on PLSQL_CCFLAGS at compile time ...
```

There is no conditional compilation in the file. `PLSQL_CCFLAGS` is an Oracle compiler
setting and the file is PostgreSQL. `refusal.py`'s `CONDITIONAL_COMPILATION` pattern is
`\$IF\b|\$\$[A-Za-z_]\w*`, whose second alternative is Oracle's `$$PLSQL_UNIT` inquiry
directive — and which also matches PostgreSQL's ordinary dollar-quoted string literal
`$$it's a 'quoted' $ value$$` at line 29.

**The collision is narrower than it first looks, and the narrowness is the tell.** Only the
untagged form whose body opens with a letter or underscore matches:

```
$$it's a 'quoted' $ value$$   -> CONDITIONAL_COMPILATION    <- false positive
$tag$ nested $$ dollars $tag$ -> -
$q$O'Reilly$q$                -> -
$$$$                          -> -
$$PLSQL_UNIT                  -> CONDITIONAL_COMPILATION    <- the intended match
```

So the bug does not fire on dollar-quoting as a category; it fires on the subset that
happens to look like an Oracle identifier. That makes it exactly the kind of defect that
survives casual testing.

**The cost, measured rather than argued** — two spellings of one statement that PostgreSQL
treats as identical, which is the metamorphic shape `tests/test_postgres_equivalence.py`
already uses:

```
SELECT o.cust_id, 'manual review'  , o.amount  ->  3 edges, 0 refusals, coverage 1.0
SELECT o.cust_id, $$manual review$$, o.amount  ->  0 edges, 1 refusal,  coverage 0.0
```

All three edges lost, including the `filter` edge from a `WHERE` clause that has nothing to
do with the literal. A **false abstention** (T3.1d): the statement is completely readable
and nothing about it is context-dependent.

The shape of the defect matters more than this instance. The refusal register is a flat
list of constructs with no notion of which dialect each belongs to, so every Oracle-only
pattern is live against PostgreSQL input and vice versa. `CONDITIONAL_COMPILATION` is the
one a stress file happened to reach; it is not evidence that it is the only one. The fix is
a `dialects` field on `Construct` — not a narrower regex, which would leave the seam intact
and the next collision undiscovered.

## PG-17 · FIXED, 2026-09-24 — scope the register by dialect, at all four sites

`Construct` already had a `dialects` field and `applies_to()`; `CONDITIONAL_COMPILATION`
had simply never been scoped. The repair looked like one line and was not.

**Four sites detect Oracle conditional compilation, and scoping the `Construct` fixed
one of them.** The first attempt added `dialects=frozenset({"oracle"})`, and the
regression test still failed with the identical refusal — because `band0.analyse_source`
calls `classify_statement(statement.text)` **without a dialect**, and the parameter
defaults to `"oracle"`. Every PostgreSQL statement was being classified against the Oracle
register regardless of what the config said, so the scoping could not take effect.

| site | fix |
|---|---|
| the `Construct` itself | `dialects=frozenset({"oracle"})` |
| `classify_program` pass 1 | already passed `program.dialect` |
| `classify_program` pass 2 — the `$IF` span | gate moved INSIDE `conditional_compilation_spans` |
| `band0.analyse_source` — the ccflag span, and the per-statement classify | both now pass `program.dialect` |

The span gate went inside the function rather than at its two call sites, because a gate
written twice is a gate the third caller forgets — which is precisely how this defect
existed. `$IF\b` matches `$IF$`, a legal PostgreSQL dollar-quote tag, so an unscoped span
would refuse every statement inside `DO $IF$ ... $IF$` for a reason that cannot be true of
PostgreSQL.

**`classify_statement`'s `dialect="oracle"` default is left in place and is now documented
as a hazard.** Six Oracle call sites in `tests/test_refusals.py` predate the parameter, and
removing the default to fix a PostgreSQL defect would be a wider change than the
measurement justifies. It is the thing that hid this finding, so the docstring says so.

**Both halves were verified to be load-bearing** rather than assumed — each was reverted
in-process and the metamorphic pair re-run:

```
with the fix as committed              PASSES   single=3 edges/0 refusals  dollar=3 edges/0 refusals
half 1 reverted (Construct unscoped)   FAILS    single=3 edges/0 refusals  dollar=0 edges/1 refusal
half 2 reverted (band0 omits dialect)  FAILS    single=3 edges/0 refusals  dollar=0 edges/1 refusal
```

A fix where one half does nothing is a fix half of which is untested.

`tests/test_dialect_scoped_refusals.py` — 10 tests. The load-bearing one is metamorphic and
needs no key. The last is a STRUCTURAL guard rather than a case: it asserts that **no**
construct with `dialects=None` matches an ordinary PostgreSQL dollar-quoted literal, so the
next Oracle-only entry added without a scope fails there rather than in someone's run.
Narrowing the regex was considered and rejected for exactly this reason — it would have
closed the instance and left the seam open.

Oracle detection is unchanged and was checked directly, not inferred: `probe_analyser.py`
still reports `conditional-compilation-flow ... CONTEXT_DEPENDENT`, the Oracle probe holds
at 63 / DECLARED 4 / EDGES 50 / REFUSED 6 / SILENT 3, and the PostgreSQL probe at 44/44 with
EDGES 35 / REFUSED 6. Suite 836 passed, 1 xfailed (PG-15).

## PG-19 · `measurement-error` · OPEN — a whole-file parse failure leaves no trace in coverage

Uncovered by fixing PG-17, and worth separating from it: the spurious refusal had been
**padding the statement count**, so removing it exposed what the numbers do when a file
does not parse.

```
before PG-17's fix   statements seen 1   analysed 0   coverage 0.0    refusals 1 (wrong reason)
after                statements seen 0   analysed 0   coverage None   refusals 0
```

**This is not a regression, and that was checked rather than argued.** A control with no
dollar-quoting anywhere — so the PG-17 pattern could never have fired on it, before or
after — behaves identically:

```
unparseable, NO dollar quoting    seen=0 analysed=0 coverage=None boundaries=['parse_failure']
unparseable, WITH dollar quoting  seen=0 analysed=0 coverage=None boundaries=['parse_failure']
parseable (control)               seen=1 analysed=1 coverage=1.0   boundaries=[]
```

The behaviour predates both findings. PG-17 was masking it.

**The `parse_failure` boundary IS declared, so this is not silence** — the failure taxonomy
is satisfied and a reader of one file's report can see that nothing was claimed. The problem
is the aggregate. A file that fails to parse contributes nothing to the coverage
*denominator*, so a batch of ten files in which one is unreadable reports the same coverage
as a batch of nine that never contained it. Parse coverage is supposed to be the number that
falls when the analyser cannot read something; here it is the number that looks away.

`coverage = None` rather than `0.0` is the honest answer to "what fraction of 0 statements
were analysed", so the repair is not simply to divide differently — it is to decide whether
an unparseable file counts as one unanalysed statement or needs a separate declared figure.
That is a decision about what the measurement MEANS, so it is recorded here and not taken
in passing. It also touches the signed phase-0 numbers, which makes it its own measurement.

## PG-21 · `silent-loss` · OPEN — a reused alias mixes two tables' columns, and empties one

Found while building a parseable variant of the torture file, and it is the worst-shaped
finding in this series: the inference **invents columns**, which `infer.py`'s own docstring
names as the one thing it must never do.

`infer_dictionary` builds ONE alias map for the whole file:

```python
aliases[fold(table.alias, dialect)] = key    # global, last write wins
```

Aliases are scoped to a SELECT in SQL and are not unique across a file. Four lines
reproduce it:

```sql
WITH a AS (SELECT b.amount, b.order_no FROM sales.orders    b),
     c AS (SELECT b.region, b.tier     FROM sales.customers b)
SELECT a.amount, c.region FROM a CROSS JOIN c
```

```
sales.customers  ['amount', 'order_no', 'region', 'tier']   <- two it does not have
sales.orders     []                                          <- lost every column it has
```

**Both halves are dangerous, in different ways.** The invented columns let `qualify` bind a
name to the wrong table, and an edge built on that is wrong while looking ordinary - the
silent-wrong-answer category. The emptied relation is a column-less entry in the dictionary,
which is exactly PG-16's trigger, so a single reused alias can take a whole run to zero
edges by a route that mentions neither alias nor table.

Measured at scale on the torture variant, where `b` names four different things
(`bill_of_material`, a `generate_series` ordinality alias, `cust_yoy`, `bom_explode`) and
`x` names five: `mfg_eng.bill_of_material` acquired `yr`, `revenue`, `row_hash`, `segment`,
`_dup_cnt`, `integration_id` and `bucket_start`, none of which are its columns.

**The fix is scope-aware resolution**, not a bigger map: resolve a qualified column's
alias against the aliases visible in ITS enclosing select, walking outward only for
correlated references. That is a real change to `infer.py` and needs its own measurement,
so it is recorded rather than taken in passing.

A related extension was drafted and **deliberately dropped**: lending columns from a
`SELECT *` DERIVED TABLE the way the existing rule does for a `SELECT *` CTE, so that
`CROSS JOIN LATERAL (SELECT * FROM mfg_eng.bill_of_material x) AS b2` followed by
`b2.scrap_pct` resolves. The rule is sound in isolation and was the obvious way to clear the
`Unknown column: scrap_pct` refusal, but subquery aliases (`b2`, `q`, `z`, `t`) are reused
far more often than CTE names, so adding it before the scoping fix would spread this defect
rather than contain it. Scope first, then extend.

## PG-03 · FIXED, 2026-09-25 — unwrap the LATERAL, and the D-4 rule it unmasked

Two changes, and the second one is the interesting one.

**1. `_projection_named` unwraps the wrapper.** SQLGlot sets a LATERAL source's
`scope.expression` to the `exp.Lateral`, not to the query inside it, and `Lateral.selects`
is an **empty list rather than an error**. So the lookup read a definite "no such column"
out of "I don't know", and declared `unresolved_identifier` while the projection sat two
nodes away under `Lateral -> Subquery -> Select`. `_query_of_scope` now unwraps both.

An empty list is the dangerous spelling of unknown, and this is the third time that shape
has appeared: `_sole_relation` reading the wrong `from_` key returned None rather than
raising (PG-21's neighbour), and `infer.py`'s passthrough test read a star from anywhere in
the subtree. All three looked correct and were silently doing nothing.

**2. D-4 now covers ORDERED AGGREGATES, not just windows** - and `stress_pg_02`'s key is
what forced it. With laterals resolving, `string_agg(o.status_code, ',' ORDER BY
o.order_date)` emitted `order_date -> note` as a **value** edge, which the key's forbidden
list rejects by name: the ORDER BY decides the order of the pieces and no part of
`order_date` is in the string.

**The key had been right about this since it was written**, and said so explicitly - "that
is the definition of `influence` (D-4), arrived at for window functions and applying
unchanged to an ordered aggregate". The column simply sat behind a LATERAL alias and never
resolved, so the gap could not show. Fixing PG-03 did not create this defect; it revealed
one that the key had already priced.

`_window_influence_columns` now returns an `exp.Order` whose parent is an `exp.AggFunc`,
taking the ORDER node's `expressions` and NOT its `this` - the `this` is the aggregated
value itself. Both halves live in one function on purpose: S2-08 is on the register for a
rule that lived in two places and drifted, and this is one rule.

**The grid moved, deliberately, and every flow improved with NO false positives:**

```
                   before      after
value            17 / 0 / 5   19 / 0 / 3
filter           14 / 0 / 1   15 / 0 / 0
influence        21 / 0 / 1   22 / 0 / 0
```

Misses fall from seven to three, and the three left are PG-04 (`jsonb_build_object` x2) and
PG-05 (`unnest` x1) - both still open, neither a LATERAL. Zero false positives throughout
is the number that matters: the fix resolved names it had been declaring and invented none.

**Measured on `another_stress_test.sql`** (3,737 lines, 134 CTEs, which builds this exact
shape 111 times):

```
before   29 edges, 18 boundaries (16 unresolved_identifier), 0 value edges
after    32 edges,  3 boundaries ( 1 unresolved_identifier), 2 value edges
```

The fourteen `PAYLOAD_SCORE_n` boundaries - stages 3, 11, 19 … 107, exactly 3 + 8k - all
resolve. What remains is PG-04 and a function row source.

`tests/test_lateral_projection.py`, 5 tests, 4 of which fail without the fix. The
load-bearing one is metamorphic and needs no key: a LATERAL that only names an expression
must produce the same lineage as the expression written inline. The fifth is a guard that
an unresolvable name inside a lateral is still declared rather than confidently bound -
resolving more must never mean resolving names that do not exist.

Suite 845 passed, 1 xfailed. Oracle probe unchanged at 63 / DECLARED 4 / EDGES 50 /
REFUSED 6 / SILENT 3 (its `LISTAGG ... WITHIN GROUP` is a different node type and is
untouched); PostgreSQL probe 44/44.

## PG-21 · FIXED, 2026-09-24 — resolve a qualifier in the scope that contains it

Three changes, because the finding had two mechanisms and the fix unblocked a third.

**1. Scope-aware resolution.** The file-wide `aliases` map is gone. `_scope_map(select)`
returns only the aliases introduced by THAT select's own `FROM` and `JOIN`s, and `_resolve`
walks outward through enclosing selects so a correlated reference still finds its outer
relation. `_owning_select` draws the boundary: a table inside a subquery in the `FROM`
belongs to the subquery's scope, and what the outer scope sees is the subquery's ALIAS.

`_sole_relation` carried the same defect on the unqualified path - it looked its answer up
in the same global map - and now computes the key from the table in its own `FROM`.

**2. The star must be in the CTE's OWN projection.** `find_all(exp.Star)` searches the whole
subtree, so a CTE whose select list RENAMES satisfied the passthrough test on a star buried
in a `LATERAL`, and lent its source table the CTE's output alias:

```
mfg.bom = ['component_item_id', 'parent_item_id', 'qty_per', 'top_item_id']
                                                              ^^^^^^^^^^^ not its column
```

`_projects_star` now looks at the projection list, counting both `*` and `alias.*`, and
descending into set-operation arms.

**3. The deferred extension, now safe.** A derived table that projects a star over exactly
one stored relation lends its columns, as a `SELECT *` CTE does - so
`CROSS JOIN LATERAL (SELECT * FROM mfg_eng.bill_of_material x) AS b2` followed by
`b2.scrap_pct` resolves. This was drafted BEFORE the scoping fix and deliberately held back,
because `b2`, `q` and `z` are reused far more often than CTE names and a global map would
have spread the defect. Note that SQLGlot puts the alias on the `Lateral` and leaves the
`Subquery` beneath it unnamed, so `_query_of` unwraps both.

**Effect on the torture variant** (2,920 lines, 85 CTEs, 30 base tables):

```
before   245 columns   2 relations EMPTY   REFUSED: Unknown column: scrap_pct   0 edges
after    169 columns   0 relations empty   coverage 1.0                       301 edges
```

The 76 columns that disappeared were invented ones. `mfg_eng.bill_of_material` had held
`yr`, `revenue`, `row_hash`, `segment`, `_dup_cnt` and `bucket_start`; it now holds nine
plausible columns and nothing else.

Four tests in `tests/test_infer_dictionary.py`; three of them fail without the fix. The
fourth - the correlated reference - passes either way, and is kept as a guard that the scope
walk is not too NARROW, which is the obvious way to break this while fixing it. Suite 840
passed, 1 xfailed; postgres probe 44/44, Oracle probe unchanged.

## PG-20 · `refusal-granularity` · FIXED, 2026-09-30 — one CTE's construct refuses 2,948 lines

Once the torture file was made parseable, the run produced this:

```
statements seen 1   analysed 0   coverage 0.0   EDGES 0   refusals 3
   REFUSAL [SHAPE_NOT_DECIDABLE] XMLTABLE/JSON_TABLE - columns are projected out of a
   document by path expression, and the document's structure is not in the dictionary
```

The refusal is **correct about the construct**: `JSON_TABLE`'s columns genuinely come out of
a document by path, and refusing it is the honest answer. It is **catastrophic about the
file**, because two CTEs out of 85 carried it and the refusal unit is the STATEMENT — and a
modern analytical query is one statement with eighty-five CTEs in it.

The other 83 CTEs are ordinary joins and aggregates the analyser reads perfectly well. All
of their lineage is discarded to abstain from two.

This is the general case of PG-06 ("the scope contract is stated per STATEMENT and the
losses are per COLUMN") and the same shape as PG-16, where one column-less relation refused
every statement in a run. Three findings now point at one cause: **the refusal unit is too
coarse for the SQL people actually write.** A construct that poisons one CTE should refuse
that CTE's columns and declare a boundary there, leaving the rest of the statement analysed.

Worked around for the variant by rewriting both CTE bodies into ordinary `jsonb` and `xpath`
projections over the same source columns — which, note, yields BETTER lineage than the
original: `shp_hdr.header_attrs -> hdr_packages.pkg_id` is a real edge where `JSON_TABLE`
gives only a refusal.

### The fix, and the measurement that priced it

The variant's rewrite removed the finding's own trigger, so it was put back: one
`JSON_TABLE` into `hdr_packages`, the CTE that carried it originally, leaving the other 84
untouched. One statement, 2,932 lines, an inferred dictionary of 29 relations.

```
                      seen  analysed  partial  EDGES  refusals        coverage  violations
as shipped               1         0        0      0  1 statement          0.0           0
with CTE scoping         1         1        1    329  1 cte-scoped         1.0           0
unpoisoned ceiling       1         -        -    334  0                      -           -
```

**One poisoned CTE of 85 now costs 5 edges instead of 334.** The five are the ones that ran
through `hdr_packages` itself, and they are declared rather than dropped.

Three parts, and the third is the one that makes the first two safe.

**1. Locating the confinement** (`refusal.cte_bodies`, `refusal.cte_scoped_construct`). Text
scanning, not SQLGlot: this module classifies constructs the SQL parser may not read at all
— that is `classify_program`'s third pass — so asking SQLGlot to find the CTEs would make
the refusal depend on the very parse whose failure is the thing being refused, and would put
the answer in a different coordinate space from the line numbers a `Refusal` is addressed
by. The confinement check is exact rather than inferential: **blank the poisoned bodies out
of the text and re-run the classifier.** If anything is still refused — a second construct
in the main query, or in a different CTE — the statement is refused whole. Everything the
scan cannot prove returns None, and None is the old behaviour.

**2. Addressing the refusal.** One refusal PER CTE, each spanning its own body. A single
refusal covering the first and last poisoned CTE of 85 would swallow every clean CTE between
them, and `violations` — which asks whether any edge originates inside a refusal's span —
would then report all 329 recovered edges as contradictions. The narrow spans are what make
that cross-check mean something here rather than merely pass.

That broke the coverage arithmetic, which was `seen - len(refusals)`, and would have gone
NEGATIVE for two poisoned CTEs in one statement. `Refusal` therefore gains a `scope`, and a
CTE-scoped refusal **does not subtract**: a statement that produced 329 edges was analysed,
and reporting `analysed 0` beside them is not conservatism but an arithmetic that
contradicts its own output. The withheld part is counted separately as `partial_statements`,
so the new category is visible rather than folded into a number that already existed.

**3. Removing the body, not merely distrusting it.** *A construct the CLASSIFIER refuses is
not thereby a construct SQLGlot declines to interpret.* `CONNECT BY` is the recorded case —
band 0's own comment: it parses cleanly and emits three `x -> x` self-edges, which look
entirely ordinary beside a real projection. Leaving a poisoned body in the tree would
re-create exactly the guessed edge the classifier exists to prevent, and that edge would
carry the STATEMENT's origin, outside the CTE-scoped refusal's span, so `violations` would
not have caught it either. What remains after removal is a reference to a relation nobody
defines, which is what it is; the columns taken from it are declared per column
(`doc_rows.weight (relation not in dictionary)`) beside the refusal's own reason.

### Two guards, both failing closed

The spans come from TEXT and the tree is the authority, so the narrowing is confirmed twice
before it is trusted, and both failures land back on refusing the statement whole — the
behaviour this finding improves on, which is the right place to land when the improvement
cannot be proved.

* **Every requested name must actually be dropped.** This one is a scar. The first version
  of `_drop_ctes` read `parsed.args.get("with")`, which is `None` twice over: an INSERT's
  `WITH` hangs off the inner `Select`, and the key there is spelled `with_`. So it found
  nothing, dropped nothing, returned no boundary — **and the reduction still produced the
  right four edges, because `JSON_TABLE` happens to dangle on its own.** A silent no-op
  wearing a passing result. That is `from_` vs `from` in `infer.py` and `with` vs `with_` in
  `bisect_chain.py` for the third time: an argument key answering *empty* where "not found"
  was meant. The clause is now located by traversal, and the caller compares what was
  dropped against what it asked for.
* **The construct must be gone from the re-read tree.** Catches a span that was too LONG: if
  blanking it hid a construct that actually lives elsewhere, check 1 in part 2 would have
  passed while the construct stayed in the tree. Not hypothetical — PG-23 over-spans a CTE
  by 150 lines in this very file. Honest limitation: this re-reads GENERATED SQL, so a
  construct SQLGlot normalises beyond recognition would slip past it.

### A duplicate refusal this exposed, and fixed

`procedure.analyse_source` ran `classify_program` as a second pass and deduped against band
0's refusals by exact `(unit, line)`. But the third pass addresses a construct at the line
the CONSTRUCT sits on while band 0 addresses the line the STATEMENT starts on — 795 lines
apart in the torture file. So one `JSON_TABLE` was refused twice at two addresses, costing a
second `statements_seen` and reporting coverage 0.5 for a statement that was analysed and
produced 329 edges. The dedup now tests SPANS, and appends candidates as it accepts them so
the test also holds within the pass: two constructs in one statement previously produced two
refusals and two `statements_seen` for one statement, against `_deduplicate`'s own stated
policy of one refusal per statement.

### Verification

`tests/test_cte_scoped_refusal.py`, 19 tests. **7 fail with the behaviour disabled**
(`cte_scoped_construct` forced to None, which is exactly the pre-fix state); the 12 that
pass either way are the scanner's unit tests and the fallback assertions, which are correct
before and after by design. Suite 862 passed, 2 skipped (Oracle container down), 1 xfailed.
Oracle probe 63 unchanged, PostgreSQL probe 44/44.

**Phase 0 was NOT re-measured**, and that is a gap rather than a result:
`scripts/exercise_corpus.py` needs the Oracle container, which is not running here. The
proxies that do run are unchanged — `test_refusals.py` in full, including
`test_all_seven_constructs_in_the_corpus_are_flagged`, which is the direct guard on the
dedup widening, plus both construct probes.

### What it did not fix

`PG-22`: the refusal withdraws the CTE, so a provable column inside a poisoned CTE goes with
it. Column-level narrowing is the next step and is registered rather than taken.

## PG-22 · `refusal-granularity` · OPEN — a provable column inside a refused CTE is refused with it

PG-20's fix narrows the refusal from the statement to the CTE. The unit is still coarser
than the loss:

```sql
doc_rows AS (
    SELECT s.id        AS ship_id,        -- owes nothing to the document
           jt.weight   AS weight         -- out of the document by path
    FROM   silver.shipments s,
           JSON_TABLE(s.header_attrs, '$' COLUMNS (weight numeric PATH '$.kg')) AS jt
)
```

`shipments.id -> tgt.ship_id` is a TRUE edge and PG-20's fix declines to make it, because it
removes the whole CTE body.

**Deliberate, and the reason is the same one that made PG-20's fix safe.** A construct the
classifier refuses may still be one SQLGlot interprets confidently enough to emit an edge
from — `CONNECT BY`'s self-edges. Trusting *part* of a body whose shape is not decidable
means deciding, per column, which part the construct reaches, and that is a judgement the
current machinery does not make. Refusing the body is the safe direction.

It is still a false abstention (T3.1d), now with a known shape and a measurable cost, and it
is the fourth finding in the refusal-granularity family with PG-06, PG-16 and PG-20. The
narrowing that would close it — refuse the COLUMNS of a poisoned CTE that the construct
actually feeds, keep the rest — is also what PG-06 asks for on the measurement side, so the
two should probably be taken together.

**Risk if left open:** lost edges, never wrong ones. The loss is declared as a refusal with
the CTE named, so a reader can see the abstention; what they cannot see is that some of the
withheld columns were provable.

## PG-23 · `classifier-correctness` · OPEN — the noise-stripper desynchronises on `E'...\'...'`

Found while measuring PG-20, in the classifier's own foundation rather than in the fix.

`_strip_noise` blanks comments and string literals before any construct pattern is applied,
so that `-- CONNECT BY is not used here` does not trip the classifier. Its string scan
treats `''` as an escaped quote, which is right for a standard-conforming literal. It does
not know that an **`E'...'` string also honours a BACKSLASH escape**:

```sql
E'O\'Reilly'          -- torture file, line 1790
```

The scan takes the `\'` as the closing quote, and from that point every subsequent quote in
the file has the wrong polarity: real code is blanked, real literals are left visible.

**Measured consequence**, on the torture variant:

```
lexer_traps body, actual      lines 1779-1807
lexer_traps body, as scanned  lines 1779-1958      <- over-spans by 150 lines
top-level CTEs, actual        81
top-level CTEs, as scanned    69                   <- 12 lost after the desync
```

**Why this is a finding about the classifier and not about PG-20's scanner.** The same
stripped text feeds `classify_statement`, which is what every refusal decision in both
dialects is made from. After such a literal the classifier is reading a corrupted view of
the file, so it can both **invent** a refusal (a construct name inside a string it failed to
blank) and **miss** one (a real construct it blanked by mistake). Neither is loud.

Nothing in the shipped corpora contains an `E'...\'...'` literal, which is why this had
never surfaced; the torture file contains one because it was written to contain one.

**Risk if left open:** a refusal decision anywhere after an `E`-string with an escaped quote
is unsound in both directions, and the unsoundness is silent. PG-20's second guard is what
keeps *that* fix safe against it — it re-reads the tree rather than trusting the span — but
nothing protects `classify_statement` itself.

**The repair is small and its blast radius is not.** Honouring `\` inside an `E'...'`
literal is a few lines, and it changes what the classifier sees in every file, so it needs
its own before/after measurement rather than riding along with something else.

## PG-23 · FIXED, 2026-10-01 — `refusal._is_escape_string_prefix`

The string scan now honours `\` inside a literal whose opening quote follows an `E` or `e`,
so `\'` stays inside the string and `\\'` closes it. The prefix counts only as a whole
token: `LIKE'C:\'` ends in an `E` too, and reading it as an escape string would swallow the
rest of the statement — the same desync, moved. Oracle has no `E'...'` form, so no Oracle
reading can change.

**Its own before/after, as this entry asked:**

```
                                         before        after
parseable torture, top-level CTEs        69 / 81       81 / 81
parseable torture, lexer_traps span      +150 lines    ends where the CTE ends
parseable torture, end to end            334 edges     334 edges, 0 refusals either way
original and no-dollar variants          unchanged     unchanged
```

The end-to-end number not moving is the expected result, not a null one: the torture file
contains nothing the classifier refuses after line 1790, so the corrupted view had nothing
to invent or hide *in that file*. The tests construct both directions directly — a
`JSON_TABLE(` inside a later literal invented a refusal, and a real `CONNECT BY` after the
literal was missed — and six of nine fail with the fix reverted.

PG-20's second guard stays. It was justified by this finding, but what it guards against is
any span the stripper gets wrong, and the next lexer gap will look identical from there.

**Not covered, recorded so it is not assumed:** `dynamic._split_concatenation` and
`_split_arguments` have their own `''`-only quote scans. Both serve PL/SQL constant folding,
and an `E'...'` operand already makes `_literal_value` return None, so folding refuses — the
safe direction. Oracle's `q'[...]'` alternative quoting is a different lexer gap of the same
shape and is not handled by this change.

## P-02 · FIXED, 2026-10-01 — `defuse._analyse_delete_filter`

Def-use now claims a `DELETE`, for exactly what band 0 cannot bind: declared variables and
cursor-record fields. The column side stays with band 0, which resolves it through
subqueries this walk does not — the split `_analyse_insert_filter` already makes, so a
DELETE and an INSERT ... SELECT now divide their predicates the same way.

```
DELETE FROM tgt WHERE sid = v_cutoff        TGT.SID -> TGT (AST), V_CUTOFF -> TGT (DEF-USE)
UPDATE tgt SET flag = 1 WHERE sid = v_cutoff  identical
```

**The condition this entry set is met in the same change.** The predicate goes through
`_subscripted`, so `WHERE sid = l_ids(i)` emits `L_IDS -> TGT` and not `I -> TGT`. The
test for the index passes with the fix reverted too — which is this entry's point exactly:
it used to be right only because nothing could see `i`. It is now right because the rule
says so, and it is pinned beside the test that proves the variable became visible.

A cursor-record field (`WHERE sid = rec.sid`) resolves through the cursor's query to
`SRC.SID`. That half was not in the finding as filed, and was lost the same way.

## PG-05 · FIXED, 2026-10-01 — `band0._trace_through_unnest`

**The cause was a projection that was not one.** SQLGlot gives `UNNEST(...) AS t(tag)` a
scope whose `selects` are the alias IDENTIFIERS. `_projection_named` returned the bare
`tag` identifier as if it were an expression, found no column in it, and `t.tag` traced to
nothing — no edge and no boundary. The LATERAL spellings unwrap to an empty `selects`, which
is why they at least declared `unresolved_identifier`.

The fix is an edge, not a boundary, because nothing here is unknowable: PostgreSQL binds
the alias columns to the arrays BY POSITION. The transform is read off the `UNNEST` node,
so every spelling now produces what `SELECT unnest(c.tags)` always did:

```
FROM c, unnest(c.tags) AS t(tag)                         was: nothing     now: c.tags -> x derived
FROM c CROSS JOIN LATERAL unnest(c.tags) AS t(tag)       was: declared    now: the same edge
FROM c, unnest(c.tags) WITH ORDINALITY AS t(tag, n)      was: nothing     now: the same edge
FROM c, unnest(c.a, c.b) AS t(x, y)                      t.y binds to c.b, not c.a
```

Two shapes are declared rather than guessed, and the tests pin both. An alias list that
does not map one-to-one onto the arrays — an array of records expanded into its fields —
would bind a field to the wrong array. A bare `AS t` with no column list is declared too,
though PostgreSQL names that column `t`; resolving it is a small follow-up, not a
correctness gap. The `ORDINALITY` column is a counter, like `ROW_NUMBER()`, and gets no
edge and no boundary: no column's value is in it.

**Measured:** `stress_pg_02` value moved 19/0/3 -> 20/0/2. False positives stay at zero, and
the two misses left are PG-04's `jsonb_build_object`. The pin in `test_postgres_stress.py`
was edited deliberately, with the reason beside it.

## PG-18 · `construct-coverage` · OPEN — `CYCLE ... SET ... USING` fails the parse outright

The first blocker in the torture file, at line 59:

```sql
) SEARCH DEPTH FIRST BY org_id SET org_dfs_order
  CYCLE org_id SET org_is_cycle TO 'Y' DEFAULT 'N' USING org_cycle_path,
```

```
ParseError: alias does not support CTE. Line 59, Col: 18.
```

One statement, so one parse failure takes **the entire 2,963-line file**: 0 edges.

**Decomposed rather than described, because the first write-up of this finding was wrong.**
It said "`CYCLE ... SET ... USING` fails, unlike `SEARCH`, which parses" - and both halves
of that are false. Holding the recursive CTE fixed and varying only the decoration:

```
CYCLE id SET c                              OK
CYCLE id SET c USING p                      OK
CYCLE id SET c TO 'Y'                       FAIL   column does not support CTE
CYCLE id SET c TO 'Y' DEFAULT 'N'           FAIL
CYCLE id SET c TO 'Y' DEFAULT 'N' USING p   FAIL

SEARCH DEPTH FIRST BY id SET ord  then SELECT        OK
SEARCH DEPTH FIRST BY id SET ord  then , next_cte    FAIL
SEARCH BREADTH FIRST BY id, pid SET ord              FAIL
```

So the breaking token is **`TO <value>`**, the cycle-mark clause - not `CYCLE`, and not
`USING`, which was named in the first draft purely because it was the last thing on the
line. And `SEARCH` is not innocent: it parses when it is the LAST thing before the final
`SELECT`, and fails both in the position this file uses it (followed by `, next_cte`) and
with a multi-column `BY`. An earlier probe recorded `SEARCH DEPTH FIRST` as OK, which was
true of the probe's own input and not of the file's.

**The file's problem is not one blocker.** Peeling them off in order moves the error rather
than clearing it:

```
line   59   CYCLE ... TO 'Y' DEFAULT 'N'
line   59   SEARCH ... followed by a comma
line  168   ORDER BY h.version_no USING >
line  764   JSON_VALUE(... RETURNING timestamp with time zone ...)
            ... and more behind those
```

which agrees with the isolated probe: **31 of 138 constructs drawn from this file do not
parse.** Supporting `CYCLE` would move the failure to line 168, not produce lineage.

**The failure is loud, which is the part that is working.** A `parse_failure` boundary is
declared and `statements_analysed` is 0 against `statements_seen` 1, so nothing is claimed
and a reader can see that nothing was claimed. Refusal, not silent loss.

**`CYCLE` cannot go in `rewrite.py`, and the reason is worth recording.** That module's
admission test has two parts, and `CYCLE` passes the first (SQLGlot cannot parse the
statement at all) while failing the second decisively: the clause **defines** the column
`org_is_cycle`, and the query then reads it —

```sql
WHERE  org_is_cycle = 'Y'          -- orphan_checks, line 1314
       ot.org_is_cycle             -- wh_org projection, line 294
```

Stripping the clause would make the file parse and would quietly delete a column that two
places select. That is precisely the "rewrite that drops a real predicate silently narrows
lineage" case the module's docstring rules out, so `CYCLE` belongs in the refusal register
with a reason of its own — an honest refusal over a readable-looking wrong answer.

### What the torture file measured beyond the two findings

A single blocker means the file-level result (0 edges) says nothing about how many
constructs are actually unsupported, so 138 constructs drawn from the file were probed in
isolation, and the 40 highest-risk of those that parse were then checked for whether they
KEEP the identifiers the SQL names.

```
SQLGlot postgres parse       107 / 138  (78%)
identifier fidelity           40 /  40  (100%) - zero silent loss
```

The 31 parse failures cluster, and the clustering is informative: **SQL/JSON (PG 16/17)**
— `JSON_VALUE`/`JSON_QUERY` with `RETURNING`/`PASSING`/`ON EMPTY`/`ON ERROR`, `JSON()`,
`JSON_SCALAR`, `JSON_SERIALIZE`, `JSON_ARRAYAGG ... ORDER BY`; **XML** — `XMLPI`,
`XMLROOT`, `XMLSERIALIZE ... INDENT`, `XMLEXISTS`, `XMLTABLE` with `XMLNAMESPACES`;
**`USING` in three unrelated roles** — `ORDER BY x USING <`, `array_agg(... ORDER BY x USING >)`,
`JOIN ... USING (c) AS alias`; **prefix operators** — `|/`, `||/`, `@`, `?-`, `?|`, `@-@`, `#`;
and **type syntax** — `int4 '5'`, `CAST(... AS text ARRAY[4])`, `national character`.

These are upstream in SQLGlot, not in this codebase, and every one of them fails loudly.
The result that matters for the analyser is the second line: of the constructs that do
parse — `JSON_TABLE` with `NESTED PATH`, `XMLTABLE`, `ROWS FROM`, `TABLESAMPLE`,
`GROUPING SETS`, `GROUP BY DISTINCT`, inheriting named windows, `DISTINCT ON`, whole-row
references, 2-D array subscripts, `WITH RECURSIVE` inside a scalar subquery — **not one
lost an identifier.** No construct in this file parses and is then modelled wrong.

### Why this one matters more than PG-03 and PG-04

They are visible and it is not. A reader of the output sees `unresolved_identifier
GROSS_AMOUNT` and knows something was not traced; for `unnest` there is nothing to see.
`audit_log.changed_by` reads as a column with no upstream — which is a FINDING about the
data, not a gap in the analysis, and is exactly the confusion this product exists to
prevent.

## PG-06 · `measurement-error` · OPEN — the scope contract is stated per STATEMENT and the losses are per COLUMN

The finding underneath the other three, and the one that changes what
`docs/postgres_scope.md` is entitled to claim.

`probe_postgres.bucket()` asks *"did this statement produce any edges?"* All three of
`LATERAL`, `unnest` and `jsonb_build_object` answer yes — they are in the probe's EDGES
bucket, and the scope document lists all three as in scope. **Every one of them loses a
column.** A statement can be counted, analysed, produce edges, and still drop half of what
it depends on.

The probe's own docstring already says EDGES is "NOT a claim the edges are CORRECT - only a
ground-truth key proves that." It was right, and this is the first measurement that shows
what the gap is worth: **three of the 34 in-scope constructs are partial, and the probe
cannot tell.**

So the contract needs two statements where it has one:

* **statement level** — measured by the probe. Nothing falls through unowned. *Holds:
  43/43, and both PG packages counted every statement they should have (modulo PG-01).*
* **column level** — measurable only by a key. Every column that has an upstream gets an
  edge. *Does not hold for three constructs, now named in `docs/postgres_scope.md`.*

Filed as `measurement-error` rather than `construct-coverage` because nothing in the
analyser is wrong here. The INSTRUMENT claimed more than it measured, and the scope document
repeated the claim.

## Stress PG 3 · `stress_pg_03_boundary.sql` · first run, 2026-09-23

Nine statements, alternating: five out of scope, four in scope. The question is not whether
the refusals happen — the probe already measured that — but whether a refusal stays **local**.

### Score

```
  band  flow       TP  FP  FN   precision    recall
  0     filter      5   0   0      100.0%    100.0%
  0     influence   6   0   0      100.0%    100.0%
  0     value       9   0   0      100.0%    100.0%

  statements seen 8   analysed 3   parse coverage 37.5%
  refusals 5: 4x UNSUPPORTED_CONSTRUCT, 1x SOURCE_UNAVAILABLE
```

**Containment holds completely.** All four in-scope statements produced exactly their
labelled edges while surrounded by out-of-scope ones — including statement 2, which sits
directly after a dollar-quoted routine body, and statement 9, which follows four consecutive
refusals. None of the four forbidden edges appeared: nothing was recovered from inside a
refused body, and no Oracle-style trigger inheritance was invented for the refused
`CREATE TRIGGER`.

The negative controls held too: a literal `'truncate_log'` next to a real `TRUNCATE` ten
lines below did not trip either pattern.

37.5% parse coverage is the correct reading of this file and not a failure — five of nine
statements are deliberately out of scope, and **`analysed 3` rather than 4 is PG-01 again**,
predicted in this key before the run: statement 9 is an `UPDATE`, analysed and uncounted.

## PG-07 · `key-error` · FIXED — refusal subjects predicted with the wrong line convention

The key predicted five refusal subjects by hand-reading the line of each SQL keyword: 36,
59, 84, 104, 116. The splitter anchors a statement at the start of its **block**, comment
header included: 1, 55, 79, 97, 109. All five read `NOT DECLARED`.

Corrected, and the correction is narrow. **Predicted correctly: which five statements are
refused and the code each carries** — four `UNSUPPORTED_CONSTRUCT`, one
`SOURCE_UNAVAILABLE`, matched one for one. That is the package's substantive claim.
**Wrong: a numbering convention the key had no way to know**, which is a fact about the
splitter rather than about the SQL.

Kept as a finding because "it was only a convention" is precisely what someone laundering a
real disagreement would also say. The rule the key's own header set — a mismatch is a
key-error, recorded — is the rule that was followed.

## PG-08 · `refusal-taxonomy` · OPEN — `CREATE_TRIGGER_PG`'s reason never reaches the reader

The refusal fires and the code is right. The **reason** is the wrong one.

```
what the reader gets   "CREATE of TRIGGER, which does not write rows from a query"
what the classifier has "CREATE TRIGGER - PostgreSQL triggers name a FUNCTION that holds
                         the body, so the lineage is in a routine this project cannot yet
                         parse ..."
```

Confirmed by calling the classifier directly: `classify_statement(sql, "postgres")` returns
`CREATE_TRIGGER_PG` with the full reason, and `"oracle"` returns `None`, so the dialect
scoping works. But `band0` declines every non-CTAS `CREATE` first, and
`procedure.analyse_source` dedupes refusals on `(unit, line)` — so the band-0 message wins
and the classifier's is discarded.

### Why it is worth a finding rather than a shrug

A refusal's whole value is its reason. "Does not write rows from a query" is true of a
`CREATE INDEX` and tells a reader nothing to act on; the classifier's reason names the
parked grammar decision and points at ADR-0002 §5, which is what someone deciding whether
to fund a PL/pgSQL grammar actually needs. Two refusal messages for one statement, and the
less useful one wins by ordering.

Low severity — the statement is refused, counted, and carries the right code, so no number
moves and nothing is silent. It also raises the question of whether `CREATE_TRIGGER_PG` is
reachable at all through the normal entry point, which the register should settle before
anyone trusts the construct list as a description of behaviour.

## Stress PG 4 · `stress_pg_04_query_complexity.sql` · first run, 2026-09-23

Three statements, 79 labels, **no routines and no DDL** — all of the difficulty inside the
SELECT. Q1 joins four relations in a CTE, re-aggregates, and joins that to a second CTE at a
different grain, with two window functions, a HAVING and an outer filter over an aggregate.
Q2 puts an `EXCEPT` between two CTEs that read the same relation, then `GROUPING SETS` and a
window function over an aggregate. Q3 puts two correlated scalar subqueries in the select
list of a `FULL OUTER JOIN`, under `DISTINCT ON`.

### Score

```
  band  flow       TP  FP  FN   precision    recall
  0     filter     21   1   1       95.5%     95.5%
  0     influence  34   0   0      100.0%    100.0%
  0     value      21   2   2       91.3%     91.3%
```

**The three disagreements are the three the key flagged in advance, and there are no
others.** That is the result worth recording. A key that flags its uncertainty and then
disagrees somewhere else has learned nothing; this one predicted exactly where it was on
new ground.

Each disagreement costs a false positive AND a false negative, because transform and phase
are in the match key — ADR-0001 §4, a wrong transform is a miss on both sides.

What came out clean, first time: `GROUPING SETS` influence (34/34 — S3-01's Oracle defect
has no PostgreSQL counterpart), convention (a) across an `EXCEPT` between two CTEs reading
one relation, a window function over an aggregate (`SUM(SUM(...)) OVER`), `RANK`'s
`ORDER BY` given an aggregate EXPRESSION rather than a column, both correlated scalar
subqueries, a predicate wrapped in `COALESCE`, and every forbidden edge absent — including
the unreferenced CTE projection (`line_facts.region`) that an over-eager tracer would emit.

`DISTINCT ON` produced no edge, so the convention question it raises is not forced yet.

## PG-09 · `silent-loss` · OPEN — a bare `SELECT` produces nothing at all, and is not counted

```
SELECT o.cust_id, SUM(o.gross_amount) FROM stg_orders o
  JOIN stg_customer c ON c.cust_id = o.cust_id
 WHERE o.currency = 'GBP' GROUP BY o.cust_id;

-> 0 edges, 0 refusals, 0 boundaries, statements_seen = 0
```

Not refused, not declared, **not counted**. Fully SILENT, and the reason is structural:
`band0.SUPPORTED` holds INSERT, MERGE, DELETE and CREATE, and an edge needs a TARGET to
name at its far end.

### Why this is more than a modelling nicety

Read-only analytical SQL is a large share of what a lineage tool gets pointed at — view
bodies, BI queries, report definitions, the contents of `dbt` models. A user who hands the
analyser a file of them gets a clean run, 100% parse coverage, and **no output**, with
nothing anywhere recording that the file was skipped.

It is the same shape as PG-01 and worse in degree: PG-01 loses an `UPDATE` from the
denominator while still emitting its edges; this loses the entire statement.

### What a fix has to decide

Not merely "support SELECT" — the model question is what the far end of the edge is. A bare
SELECT has projections, not columns of a relation, so the target is either a synthetic node
(`<query>:3.cust_id`) or the projection's name. That is a decision, and it interacts with
S1-05's identity work. **The minimum honest interim behaviour is a refusal**, which costs
one line and converts a silence into a statement.

Worked around in `stress_pg_04` by wrapping each query in the smallest possible
`INSERT INTO ... SELECT`. Recorded in that file's header so the wrapper is not mistaken for
a design preference.

## PG-10 · `convention` · OPEN, needs a decision — the phase of a `WHERE` over an aggregated CTE

```
key       stg_orders.cust_id -> fct_product_sales  filter  POST-aggregation
analyser  stg_orders.cust_id -> fct_product_sales  filter  PRE-aggregation
```

`WHERE a.buyers >= 3` in Q1's outer query, where `buyers` is `COUNT(DISTINCT f.cust_id)`
computed inside the `agg` CTE.

**Both readings are defensible and neither is a bug.** Syntactically it is a `WHERE`, and
`FilterPhase` defaults a WHERE to pre-aggregation. Semantically it removes rows after the
aggregate that decides them has been computed — which is precisely what D-2 created
post-aggregation to express, and D-2's own argument is about WHEN a filter acts rather than
which keyword introduced it.

The distinction is not cosmetic: `WHERE quantity > 0` discards order lines before they are
counted; this discards products whose distinct-buyer count came to two. Same relation, same
flow, different results — which is the test D-2 states for when the phase is on the edge.

**A decision for Abhay, as D-2 itself was.** Whichever way it goes, it must reach every key
at once: two keys holding opposite conventions for one construct is exactly how S2-12
happened, and neither side noticed for two runs.

## PG-11 · `transform-classification` · OPEN, needs a decision — the transform of a
## non-aggregated operand in an aggregated expression

```
net_amount = gross_amount - COALESCE(discount_amt, 0) - COALESCE((SELECT SUM(...)), 0)

key       stg_orders.gross_amount -> fct_revenue.net_amount  value  DERIVED
analyser  stg_orders.gross_amount -> fct_revenue.net_amount  value  AGGREGATED
```

Only the THIRD operand passes through an aggregate. `gross_amount` and `discount_amt`
contribute per-row values and nothing else.

**The two readings are "hardest transform on the PATH" and "hardest transform in the
EXPRESSION",** and they agree everywhere except here — which is why no package before this
one has asked. Every earlier case had all operands inside the aggregate
(`SUM(gross - NVL(discount,0))`) or all of them outside it.

* The key's reading is per-path: the ladder ranks how THIS SOURCE reached THIS TARGET, and
  gross_amount reached it by subtraction.
* The analyser's reading is per-expression: the value written is an aggregate, so everything
  feeding it is marked as such.

Per-path carries more information — it distinguishes the column contributing a row value
from the column contributing a sum — and per-expression is easier to defend as a statement
about the target. ADR-0001 §4's wording ("the hardest construct on the path") favours the
key; the implementation favours itself, which is not an argument.

**Filed as a decision, not as a defect, and the key is NOT being edited to agree.** A
benchmark that adopts the implementation's reading whenever they differ has stopped
measuring anything. Both readings are written down above so whoever settles it is choosing
between two stated positions rather than ratifying whichever one shipped.

## Stress PG 5 · `stress_pg_05_single_query.sql` · first run, 2026-09-23

**One statement. 461 lines. Sixteen CTEs, seven base relations, five changes of grain, four
window functions, a recursive CTE, an `EXCEPT` and a `UNION ALL`. 85 labels.**

The other four packages ask whether a CONSTRUCT is handled. This one asks whether a
PIPELINE is, which is the question production warehouse SQL actually poses: a value enters
at `stg_order_lines.quantity` and arrives sixteen derivations later in a column nobody can
trace by hand.

### Score, after two key corrections

```
  band  flow       TP  FP  FN   precision    recall
  0     filter     14   3   2       82.4%     87.5%
  0     influence  44   0   0      100.0%    100.0%
  0     value      25   0   0      100.0%    100.0%
```

**Value: 25 of 25, no false positives, no misses.** Every value edge survived the full
depth — including the four-level path
`unit_price/quantity → line_value → line_total → … → net_amount → net_revenue → a window SUM`,
`MIN(order_date)` reaching a different target than `date_trunc(order_date)` from the same
column, `COUNT(DISTINCT category_id)` as stg_products' only contribution to the whole query,
and `category_name` carried out of a recursive CTE's anchor arm.

**Influence: 44 of 44.** Six GROUP BYs and four window clauses, including two windows whose
`PARTITION BY` comes from a different CTE than their argument.

**Every forbidden edge stayed absent** — most importantly the unreferenced-projection trap:
`order_facts.line_total` is computed and never used, so `stg_order_lines.unit_price` reaches
no target at all despite its relation being genuinely read, joined, and a real value source
through a *different* column. Nothing was invented from it.

**All three remaining discrepancies are PG-10**, the open phase decision — and the key
priced them in advance: *"this edge and the two around it are the price."* Settle PG-10 the
key's way and this package is 100% on all three flows.

### What actually failed here was the key, twice

Both corrections were verified against **my own earlier keys**, not against the analyser's
output — which is the only thing that makes correcting them legitimate rather than
laundering.

## PG-12 · `key-error` · FIXED — a CTE predicate eleven levels from the target, omitted

`WHERE c.parent_category_id IS NULL` in `cat_tree`'s anchor arm selects the roots of the
hierarchy, and `cat_tree` decides which `category_name` can reach `top_category_name`. A
real filter, and this key did not label it.

Confirmed wrong by `stress_pg_02_deep_nesting.yaml`, which labels the **identical**
construct as a filter. My own convention, applied inconsistently — not a convention learned
from the output.

## PG-13 · `key-error` · FIXED — influence composition applied at depth 2 and forgotten at depth 5

`returns_by_order` groups by `order_id`; that influences `refund_total` → `net_amount` →
`net_revenue`, which is the `ORDER BY` of **both** windows in `enriched`. So it influences
`region_rank` and `cohort_rank` too. The key stopped at six targets and should have had
eight.

The rule is not new, and that is the whole problem: **this key already composes it** for
`monthly_customer`'s grouping keys — `stg_orders.cust_id` and `order_date` both list
`region_rank` and `cohort_rank` among their twelve targets — and for `gross_amount` reaching
`region_rank` through the same window `ORDER BY`. It was applied at the shallower grouping
and dropped at the deeper one.

**S2-13 is the same mistake in the Oracle keys**: *"a uniform rule applied to the first
instance only."* Third occurrence of that category across two dialects, which by this
register's own standard makes it a design fault in how keys get written rather than three
separate slips.

### The finding under the finding

A 461-line package was built to find where the ANALYSER breaks down at depth. What it found
is that **the key breaks down at depth first** — twice, both times by forgetting at level
eleven a rule it applied correctly at level two, while the analyser applied it uniformly at
every level.

That is worth more than the score. It says the hand-labelling method this project rests on
has a length limit of its own, and that at this size a key needs a mechanical cross-check —
derive influence closure from the stated rule and diff it against the hand list — rather
than a second careful reading. The next package this size should have that check written
before the key, not after.

## Decisions taken — 2026-09-23

Two findings were blocked on a decision rather than on code. Abhay settled both on
2026-09-23. **Recorded here before either is implemented**, so that what was decided is
separable from what was built — the same discipline D-1 to D-5 were recorded under.

Both went to the KEY's reading, which means both are now **analyser defects with a decided
correct answer**, not open questions. Their register rows change category accordingly.

### D-6 · PG-10 — a filter's phase is decided by what it DOES, not by its keyword

`WHERE a.buyers >= 3`, where `buyers` is `COUNT(DISTINCT ...)` computed in an inner CTE, is
a **post-aggregation** filter.

The rule, stated generally: **a predicate acts post-aggregation when every column it
constrains reaches it through an aggregate.** A `WHERE` over an already-aggregated CTE
value qualifies; a `WHERE` on a raw column does not; a `HAVING` always does.

Why this way. D-2 created `FilterPhase` to express WHEN a filter acts, and its own argument
is about consequences: `WHERE quantity > 0` discards line items before they are summed,
`HAVING SUM(quantity) > 0` discards products whose total came to zero. Same column, same
flow, different results. A `WHERE` over an aggregated CTE has the second behaviour and the
first keyword — so classifying by keyword would put two filters with identical consequences
on opposite sides of the distinction D-2 exists to draw.

Affects 5 labels across `stress_pg_04` and `stress_pg_05`, which already hold this reading
and currently score them as 3 false positives and 2 misses. **The keys do not move; the
analyser does.**

### D-7 · PG-11 — the transform ladder ranks the PATH, not the expression

In `gross_amount - COALESCE(discount_amt, 0) - COALESCE((SELECT SUM(...)), 0)`, the
transform for `gross_amount` is **`derived`**, and for the subquery's column
**`aggregated`**. Three sources reach one target and they do not all carry the same class.

The rule, stated generally: **the transform is the hardest construct on the path from THIS
SOURCE to THIS TARGET** — not the hardest construct anywhere in the expression that
produced the target.

Why this way. It is what ADR-0001 §4 already says, and it carries strictly more
information: it distinguishes the column contributing a per-row value from the column
contributing a sum. Under the expression reading those two facts are indistinguishable, and
a reader asking "is this number an aggregate of that column?" gets yes for a column that was
only ever subtracted once.

The two readings agree everywhere except when one operand of an expression is aggregated and
another is not — which is why no package before `stress_pg_04` asked the question.

Affects 2 labels in `stress_pg_04`. `stress_pg_05` reaches the same construct and is
unaffected, because there the mixed expression is itself wrapped in a `SUM` one CTE up, so
every operand is aggregated by the time it reaches a target under either reading. That
asymmetry is worth keeping in mind when implementing: **the fix must not change
`stress_pg_05`.**

## Metamorphic testing — the `PG-14`/`PG-15` instrument, 2026-09-23

**A new instrument, and the cheapest one this project has.** Write the same logic several
ways and assert the lineage is identical. No ground-truth key, no hand-labelling, no author
judgement — a difference between two spellings of one query is a defect BY CONSTRUCTION.

It was built in response to PG-13, where a 461-line package found that the KEY degrades
before the analyser does. Metamorphic pairs have no key to degrade. They cannot say whether
an answer is RIGHT — only a key does that — but they say, with no room for argument, that
two answers which must agree do not.

It found a silent defect in the first eighteen pairs.

### Sixteen invariants that hold

alias renaming · join reordering · schema qualification · `SELECT *` expansion ·
`BETWEEN` against two comparisons · `NOT BETWEEN` against a negated pair · `IN` list against
an `OR` chain · CTE chain against a flattened query · CTE against an inline derived table ·
`GROUP BY` ordinal against the expression · `GROUP BY` on an output alias · unqualified
column references · quoted identifiers · `CAST` against `::` · `UNION` against `UNION ALL` ·
redundant subquery nesting · `HAVING` on an output alias.

Several of those were expected to fail. `GROUP BY 1` and `GROUP BY <alias>` both resolve
correctly, and so does `SELECT *` expanded from the dictionary.

## PG-14 · `silent-loss` · OPEN — a named `WINDOW` clause loses its PARTITION BY and ORDER BY

```
RANK() OVER (PARTITION BY c.region ORDER BY c.signup_date)   -> 2 influence edges
RANK() OVER w ... WINDOW w AS (PARTITION BY ... ORDER BY ...) -> 0 influence edges
```

No refusal. No boundary. The statement is counted and `parse_coverage` is 100%.

Worse in the form people actually write it. A named window exists to be **shared**, and with
two functions on one window:

```
SELECT c.region, RANK() OVER w, SUM(c.cust_id) OVER w
  FROM stg_customer c WINDOW w AS (PARTITION BY c.region ORDER BY c.signup_date);

-> 2 edges. `rank_in_region` HAS NO EDGE AT ALL - no value (RANK takes no argument,
   correctly) and no influence (wrong). The target column vanishes from the lineage
   entirely, and nothing anywhere records it.
```

The window's ARGUMENT still resolves, so `SUM(c.cust_id) OVER w` keeps its value edge. That
is what makes it dangerous: the output looks populated and plausible, and only the
dependency that explains WHY a row ranks where it does is missing.

### Why this one is worth prioritising

It is exactly the construct a large reporting query uses. `stress_pg_05` has four window
functions with repeated `PARTITION BY` clauses — **a production author would have written
one `WINDOW` clause and reused it**, and this analyser would have returned a clean 100%
parse coverage with the window influence silently absent.

Third entry in the `silent-loss` category for PostgreSQL, after PG-05 and PG-09.

## PG-15 · `convention` · OPEN — `FILTER (WHERE ...)` and `SUM(CASE WHEN ...)` disagree

Two spellings of the same number, two different lineage claims:

```
SUM(x) FILTER (WHERE o.currency = 'GBP')
  -> currency -> RELATION fct_revenue          [filter]

SUM(CASE WHEN o.currency = 'GBP' THEN x END)
  -> currency -> COLUMN fct_revenue.net_amount [value/aggregated]
```

**Neither is obviously wrong, and that is the finding.** Each is defensible on its own: the
FILTER clause removes rows, so `filter`; the CASE sits inside the expression, so the
predicate is part of how the value is computed.

But they compute the identical result, and a model that answers differently depending on
which spelling the author chose is answering a question about syntax rather than about data.

**The CASE form's answer is the more useful one**, and `stress_pg_02`'s key said so before
this pair was written — its note on the FILTER edge records that a relation-targeted filter
cannot express that the restriction applies to `amount` and not to `note` in the same select
list. The column-targeted edge can.

Needs a decision, like D-6 and D-7. The candidate resolution is that a predicate confined to
ONE aggregate is a dependency of that aggregate's COLUMN rather than of the relation —
whichever way it is written.

## PG-14 · FIXED, 2026-09-23 — `band0._resolve_named_windows`

Closed with a rewrite: each `WINDOW w AS (...)` definition is copied into the `OVER w` sites
that name it, immediately after view inlining and before `qualify`.

**A rewrite rather than a lookup**, for two reasons. It is what the SQL already means — a
named window is defined as textual substitution — and every downstream consumer (value
columns, the transform ladder, the influence walk) then works unchanged instead of each
needing its own upward lookup. **S2-08 is on this register for one rule that acquired two
homes and drifted**, and adding a second window-resolution path would have set that up
again.

Resolution walks outwards from the use site's own select and stops at the first definition
of the name, so an inner `WINDOW w` shadows an outer one exactly as a column alias does.

### Closing condition met

Three tests, each verified to FAIL with the fix disabled and pass with it:

* `test_a_named_window_keeps_its_partition_and_order` — the metamorphic pair itself
* `test_a_shared_named_window_reaches_every_function_that_uses_it` — the form the construct
  exists for, asserted **per target column** so a regression names which function lost its
  window, plus the check that `RANK()` still gets influence and **no** value, which an
  over-eager fix would collapse
* `test_a_named_window_is_resolved_through_a_derived_table` — the definition in an inner
  select. This one exists because a fix that simply scanned the whole statement for any
  `WINDOW` clause would pass the other two **vacuously**

### Measurement after the fix

**Nothing else moved.** The rewrite is a no-op for any statement without a `WINDOW` clause,
which is every Oracle corpus package and all four Oracle stress files.

```
phase 0      gate 96.2% / 96.2%   guard 100% of 27   parse coverage 81.6%
             all seven kill criteria PASS, 0 edges from refused statements
stress       all nine grids byte-identical (4 Oracle, 5 PostgreSQL), run_stress exit 0
suite        794 passed, 1 xfailed (PG-15), 0 skipped
```

### What this says about the instrument

PG-14 was found by a metamorphic pair in the first eighteen written, and **no ground-truth
key would have found it** — not one of the five PostgreSQL packages uses a named window,
because I did not think to write one. A key can only contain constructs its author thought
of. A rewrite pair only needs the author to know that two spellings mean the same thing,
which is a much lower bar and a much wider net.

Worth acting on: the same technique should be pointed at Oracle, where the corpus is larger
and the keys are older.

## PG-10 · FIXED, 2026-09-23 — `band0._filter_phase`, implementing D-6

One helper, applied at both filter-emitting sites: `_filter_edges`'s `WHERE` branch and
`resolve_query_influence`'s clause loop. Both already had the answer and were throwing it
away — `_trace` returns the transform by which a column reached the predicate, and both
sites discarded it into `_`. A filter is post-aggregation when that transform is
`AGGREGATED`.

One home, not two. **S2-08 is on this register for a rule that acquired two copies and
drifted**, and this rule now has exactly one.

Decided per COLUMN, which is finer than D-6's wording and agrees with it everywhere the
question has been asked. The two readings differ only for a conjunct mixing an aggregated
operand with a non-aggregated one (`WHERE buyers > cust_id`), which no package contains;
that case is left deliberately undecided rather than settled by implementation accident.

### Effect

```
stress_pg_04   filter  21/1/1  ->  22/0/0     100% / 100%
stress_pg_05   filter  14/3/2  ->  16/0/0     100% ON ALL THREE FLOWS
```

`stress_pg_05` — one 461-line statement, sixteen CTEs, seven relations, 85 labels — is now
perfect. Its key priced this in advance: *"this edge and the two around it are the price."*

### It moved three Oracle edges, and that was the interesting part

**Phase 0 did not move** (gate 96.2%/96.2%, guard 100% of 27, parse coverage 81.6%, all
seven kill criteria PASS) — no corpus package has the construct. `stress_04_depth_at_scale`
has three, and its band-0 filter dropped to 98.2% / 96.6% until its key was brought forward.

Each was verified against the source before anything was edited:

| unit | predicate | aggregated by |
|---|---|---|
| `S4_RETURN_RATE:1007` | `WHERE NULLIF(k.sold_total, 0) IS NOT NULL` | `SUM(l.line_amount)` in a CTE |
| `S4_VIEW_RESOLUTION:699` | `WHERE j.revenue_total > 100` | `SUM(r.net_amount)` in a CTE |
| `S4_BULK_PIPELINE:624` | `WHERE g.revenue_total > 0` | `SUM(r.net_amount)` in a derived table |

All three are D-6 cases. The analyser is right and stress 4's key predated the decision —
**S2-11 exactly: a decided convention has to reach every key.**

### D-6 HAS A COST, AND IT IS S1-05

Only one of the three was a phase change. **The other two were deletions**, because under
D-6 they collapse onto post-aggregation labels the key already carried:

* `FCT_REVENUE.NET_AMOUNT -> DW_DIM_CUSTOMER_V2` — the `HAVING` and the outer `WHERE` of
  **one statement** now share a match key. The deleted label's own note read: *"Distinct
  from the HAVING above by phase alone, which is what putting phase in the match key
  buys."* D-6 spends exactly that.
* `FCT_REVENUE.NET_AMOUNT -> FCT_REVENUE` — worse: `S4_BATCH_WRITER:1115` and
  `S4_BULK_PIPELINE:624` are **two different procedures**, and their two facts are now one
  unstatable label. Only ORIGIN separates them, and origin was measured out of the match
  key (ADR-0001 amendment 1b).

**This is the first time a DECISION rather than package size has produced an S1-05
collision**, which is new evidence for that entry and is recorded on both surviving labels
rather than left to be rediscovered.

### Closing condition met

`tests/test_filter_phase.py` — five tests, three verified to fail with D-6 reverted. It
asserts both directions, because a fix that marked every filter post-aggregation would pass
the first assertion and destroy the distinction D-2 built:

* a `WHERE` over an aggregated CTE is post-aggregation
* a `WHERE` on a raw column is **still** pre-aggregation
* both phases appear in one statement, keyed per column
* `HAVING` is unchanged — D-6 widens D-2's rule, it does not replace it
* the same construct in **Oracle**, because a phase rule firing in one dialect only would
  make the two benchmarks answer differently about the same SQL

## PG-11 · FIXED, 2026-09-24 — `transforms.transform_of_path`, implementing D-7

`transform_of` answers a question about an EXPRESSION - is there an aggregate anywhere in
it? That is the right question when every column enters the same way and the wrong one as
soon as they do not. `transform_of_path(expression, column)` walks from the COLUMN upwards
to the projection root, so only the nodes actually between them count.

Added beside `transform_of` in the one module both bands import, for the reason that module
exists at all: **S2-08, a rule with two homes has no home.** All seven call sites in
`band0` moved from one transform per projection to one per column.

### Effect

```
stress_pg_04   value  21/2/2  ->  23/0/0     100% ON ALL THREE FLOWS
stress_pg_05   unchanged                      as D-7 required
phase 0        unchanged, every cell          gate 96.2%/96.2%, coverage 81.6%
all four Oracle stress grids unchanged
```

`stress_pg_04` was the package that asked the question, and it is now clean. **Both of its
disagreements were decisions its key flagged before the run** — PG-10 and PG-11 — and both
went to the key.

### Why so little moved

D-7 narrows the answer ONLY where the two readings differ: when one operand of an
expression is aggregated and another is not. Everywhere else they agree, and in particular
they agree whenever the mixed expression is itself wrapped in an aggregate one level up —
`SUM(gross - discount - refund)` — which is the ordinary shape in both corpora. That is why
`stress_pg_05` reaches the identical construct and does not move, exactly as the decision
predicted when it was recorded.

**This is a case where a fix moving almost nothing is the evidence, not a disappointment.**
A change to the transform of every edge in both dialects that moved two labels is a change
that did what it claimed and no more.

### Closing condition met

Four tests appended to `tests/test_transforms.py`, two verified to fail with D-7 reverted.
The two that pass either way are there on purpose — they pin the behaviour that must NOT
change, and without them a fix reporting `derived` for everything would satisfy the headline
assertion and be just as wrong as what it replaced:

* a mixed expression gives each operand its own transform — all three asserted together
* an aggregate one level up still makes every operand `aggregated`
* a `CASE` reaches only the columns inside it, not the ones beside it (the middle rung, which
  no package exercises)
* a bare column is still `identity`

### Both decisions are now implemented

`tests/test_postgres_stress.py` no longer exempts any package from
`test_nothing_is_ever_guessed`. All five PostgreSQL packages emit nothing their key does not
claim, and four of the five are 100% on every flow.

## PG-09 · FIXED, 2026-09-24 — a standalone SELECT is counted and refused

`band0._is_standalone_query`. A query block at the top level of a script is counted and
refused with `UNSUPPORTED_CONSTRUCT`; the silence becomes a statement.

### The test is POSITION, not statement kind, and that is the whole difficulty

**Both frontends call a query `query_block`.** Oracle's ANTLR frontend never emits
`select_statement` — its corpus kinds are `insert_statement`, `update_statement`,
`query_block`, and so on — and `query_block` is exactly what a `SELECT … INTO` is. Refusing
by kind would have refused a construct def-use handles and that the signed band-1 gate is
computed from.

What actually separates them is where the statement sits: a query block inside a routine has
an `INTO` or is a cursor's body; one at the top level of a SCRIPT has neither. Only the
script frontend produces the synthetic `<script>` unit, so **an Oracle program cannot satisfy
the test by construction** rather than by a name coincidence.

### Refused, not analysed — and that is the honest answer, not a shortcut

The missing piece is a MODEL DECISION, not parser work. An edge needs a target, and a bare
`SELECT` has projections rather than columns of a relation. Naming that target — a synthetic
node, the projection's own name — interacts with S1-05's identity work and is not a decision
to make as a side effect of closing a silence.

A refusal is never free: this statement now costs one statement of parse coverage, and a
file of nothing but `SELECT`s reports **0%** where it used to report 100%. **0% is a true
statement about a file the analyser cannot do anything with; 100% was a false one.**

### Measurement

```
phase 0   unchanged, every cell   gate 96.2%/96.2%, coverage 81.6%, 9 refusals, same codes
stress    all nine grids identical, statements seen identical
scope     probe 43 -> 44 constructs, REFUSED 6 -> 7, still 44/44 and 0 silent
```

`docs/postgres_scope.md` now carries the construct with its reason, so the boundary is
stated in advance rather than discovered.

### Closing condition met

`tests/test_standalone_select.py` — five tests, three verified to fail with the fix
reverted. The two that pass either way are the over-reach guards and are the point of the
file:

* **`test_oracle_select_into_is_untouched`** — the load-bearing one. Oracle's
  `SELECT … INTO` shares the kind, carries band-1 edges the gate depends on, and must not be
  refused. The scoping claim is only worth something because this checks it.
* `test_a_select_inside_an_insert_is_not_refused` — `INSERT … SELECT` contains a query block
  and is the commonest statement in both corpora. A rule matching on the query rather than
  its position would refuse every load in the project.

## D-8 · PG-09 completed, 2026-09-24 — a query's own output is a place lineage can land

Taken by Abhay after pasting a **real 500-line Databricks model** into the inspection UI
and getting a refusal. That is the best evidence this decision could have had, and it
arrived the same day the interim shipped.

### What the evidence showed

The query parsed cleanly in all four dialects — 52 column references, six CTEs, `EXISTS`
subqueries, nested `CASE`/`CAST`/`NULLIF`/`TRIM`, a `GROUP BY` with `MAX(CASE …)`, a
`UNION ALL` and a `ROW_NUMBER()` window. **Changing one thing — wrapping it in an `INSERT`
— produced 19 edges at 100% parse coverage with zero refusals.** The parser was never the
problem; the missing target was.

### The decision

**A targetless query's own projections are the target**, with two ways to name the relation:

* **nothing supplied** → `<result>.<column>`. Any pasted query yields lineage with no
  configuration. Enough to read ONE query; not enough to tell two apart, since every file
  would write into the same relation.
* **`result_target` supplied** → that relation instead, exactly as an `INSERT` into it
  would. This is what makes a directory of dbt/Databricks models **chain**: model A's
  output columns are named the way model B's `FROM` clause will name them.

`result_target` is a PARAMETER, not an `AnalysisConfig` field — config is fingerprinted into
every signed measurement, and this is a property of one invocation rather than a declared
limit of the run.

Implemented as `band0._analyse_standalone_query`, which builds the equivalent `INSERT` and
delegates — the same move `_analyse_create_table_as` makes, so CTEs, set operations,
windows, the transform ladder and the filter phase all come for free rather than being
written a second time (S2-08).

**One place it deliberately differs from CTAS**: an unnamed projection is named by POSITION
and declared, where CTAS refuses. A created table's column name is decided by the server, so
guessing it invents schema; a result set's third column IS the third column, so position is
its identity rather than a guess about one.

### Scope contract

The standalone `SELECT` moves from REFUSED to **in scope**: `EDGES 34 → 35`,
`REFUSED 7 → 6`, still 44/44 with zero silent.

### Measurement

```
phase 0   unchanged, every cell   gate 96.2%/96.2%, coverage 81.6%, 9 refusals
stress    all nine grids identical
suite     812 passed, 1 xfailed
```

## New: `resolution/infer.py` — a provisional dictionary from SQL text

Real schemas are rarely in a captured dictionary, and without one every table is a dangling
reference — so the fix above is only half of what a user needs. `infer_dictionary` reads
the relations and columns out of the SQL itself.

**It is guessed, not captured, and the code refuses to let that be forgotten.**
`captured_at` is set to the literal `inferred-from-sql`, a value no real capture can
produce, and `is_inferred()` lets any caller ask. The UI shows a warning banner whenever it
is in use.

What it cannot do, each stated in the module docstring because the failures are quiet: it
holds only the columns the text mentions, so it under-reports and **cannot expand
`SELECT *`**; unqualified column references are dropped rather than attributed, because
guessing which relation owns `cust_id` would invent a fact; and it has no views, synonyms,
triggers or temporariness, none of which are in the text.

Measured on the pasted model: 5 relations, 19 columns, **the same 19 edges** as the
hand-built dictionary, plus two honest warnings (13 unattributable column references, and a
`SELECT *` it cannot expand).

## PG-16 · `construct-coverage` · FIXED, 2026-09-24 — a column-less relation refused everything

**Found by a user pasting a production Databricks model into the inspection UI**, which is
a provenance no instrument in this project has: not a stress package, not a probe, not a
metamorphic pair. A real file, from a real warehouse, pasted by someone who wanted an
answer rather than a measurement.

```
dictionary: {"s.orders": ["cust_id", "amount"], "s.lonely": []}
query:      SELECT o.cust_id, o.amount FROM s.orders o;     <- never mentions s.lonely

before:  0 edges, refused NAME_NOT_RESOLVED - "Table  must have at least one column"
after:   2 edges, no refusal
```

SQLGlot's `qualify` raises on an empty schema entry, and it raises **for the statement**.
So one column-less relation anywhere in the dictionary refused every statement in the run,
with a reason naming a table the reader never asked about — and the empty table name in
that message is why it reads as gibberish rather than as a diagnosis.

### Why nothing had ever reached it

Neither shipped dictionary has an empty column list: `corpus/dictionary.json` has 23 entries
and 0 empty, `stress/dictionary_postgres.json` 16 and 0. Every test, probe and stress package
in the project binds against one of those two, so the condition was unreachable from inside.

**A capture can produce one easily.** `columns()` reads what the capturing user is permitted
to see; a table with column privileges withheld comes back empty, and a permission gap on
one unrelated table would then take a whole production run to zero edges. That is the
failure this would have caused in the field, and it is worse than what the user actually
hit, because on a large estate nobody would connect the two.

### The fix, and why omitting is the honest answer

`_schema_for` skips relations with no columns. An entry with no columns tells SQLGlot
nothing it can use; leaving it out means references to that table resolve as **dangling** —
visible, counted, and local to the statement that touches it, which is exactly what the
taxonomy says an unknown object should be. The alternative is a catastrophic refusal whose
blast radius has nothing to do with its cause.

### Closing condition met

`tests/test_columnless_relation.py` — three tests, all three verified to fail with the fix
reverted. The third is the control, and is the point: without it a fix that returned an
EMPTY schema for everything would satisfy the first two assertions and destroy all name
resolution.

Phase 0 unchanged, every cell. 824 passed, 1 xfailed.

## Decisions taken — 2026-10-01 (D-9, D-10, D-11)

Taken by Abhay at the start of the batch programme (`stress/pg/`), **before any key was
written**, for the same reason as every earlier decision: a key written first would have
decided each of these quietly in its own favour. The full statements, including what each
one deliberately does NOT cover, are in `stress/pg/b1_core_select/OBJECTIVES.md`. **All three
were implemented the same day**, after the B1 baseline was measured against them - see
`stress/pg/b1_core_select/REPORT.md` section 8. Each one also reached an older key or test:
D-9 added one label to `stress_02` (its key had asked for exactly that), D-10 turned
PG-15's xfail into an assertion, and D-11's first implementation was wider than decided
and was narrowed (B1-Q2).

* **D-9 — a column that decides which row survives is a filter.** It covers `DISTINCT ON`
  keys and tie-breakers, the partition and order of a ranking window that is then filtered
  (`= 1`, `<= n`), and the `ORDER BY` of a `LIMIT`/`FETCH`/`OFFSET`. An `ORDER BY` with no
  row limit gives nothing.
* **D-10 — `FILTER (WHERE c)` is read as `CASE WHEN c`.** This decides **PG-15**: `c` is a
  value edge into that one aggregated column, never a filter on the relation.
* **D-11 — a join condition is structural wherever it is written.** An EQUALITY in WHERE (first recorded as "a comparison", wider than decided; corrected the same day - see B1-Q2)
  between columns of two inner-joined relations of the same FROM gives no edge, as in `ON`.
  Comparisons that touch the null-supplying side of an outer join, and correlations to an
  outer query, stay filters.
