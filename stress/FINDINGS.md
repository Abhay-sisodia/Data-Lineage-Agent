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

**Provenance is part of a finding's identity, so it is in the ID.** `S1-`/`S2-` are the two
stress packages. `P-` is the probe pair (`scripts/probe_parsers.py`,
`scripts/probe_analyser.py`) — a different instrument asking a different question, and one
that has already found things no stress package could. Keeping the series apart is what
lets "every finding recurred across stress runs" stay a true sentence about stress runs.

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
| S4-09 | `construct-coverage` | open | a declared cursor's own `WHERE` produces no filter edge against what the loop writes |
| S4-03 | `construct-coverage` | **fixed** | a `MERGE` emitted no **influence** edge — neither `GROUP BY` nor window; S3-03 added its filters and stopped there |
| S4-04 | `flow-classification` | **fixed** | a `MINUS`/`INTERSECT` second arm was read as **value** when the set operation sits in a CTE — convention (a) held for the top-level form only |
| S4-05 | `construct-coverage` | open | `BULK COLLECT` into **two** collections resolves only the first — the second target loses its column source |
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
| S2-02 | `construct-coverage` | open | `INSERT ALL` unsupported — declared, but 7 edges lost; **parses fine**, so this is semantics, not parsing |
| S2-03 | `refusal-taxonomy` | **fixed** | `PIVOT`/`UNPIVOT` refused even with a static column list |
| S2-04 | `silent-loss` | **fixed** | `BULK COLLECT` into a record collection yielded nothing, silently |
| S2-05 | `transform-classification` | **fixed** | `DECODE`/`NULLIF`/`GREATEST` read as derived, not conditional |
| S2-06 | `key-error` | **fixed** | ten units unlabelled, not seven; the header claim made true |
| S2-07 | `transform-classification` | **fixed** | `FIRST_VALUE`/`LAST_VALUE` — the key says `derived`, the analyser `aggregated` |
| S2-08 | `transform-classification` | **fixed** | two `_transform_of` copies, already drifted; and `LAG` reads like `FIRST_VALUE` but scored `aggregated` |
| S2-09 | `key-error` | **fixed** | `p_depth → DIM_CUSTOMER_HIER.DEPTH` never labelled — two of three bindings |
| S2-10 | `measurement-error` | **fixed** | trigger edges carry body-relative lines, so the refusal cross-checks could not match them |
| S2-11 | `key-error` | **fixed** | stress 1's key predated convention (c); a decided convention has to reach every key |

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
4. **S2-02** (`construct-coverage`). **Promoted above S2-01 on 2026-09-11**, because the probe
   showed the two are not the same kind of problem. `INSERT ALL` **parses** — the tree is
   already there and the analyser declines to walk it. No version bump, no rewrite rule, no
   external dependency: ordinary lineage semantics on a parsed node, and the cheapest real
   coverage left on the board.
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
