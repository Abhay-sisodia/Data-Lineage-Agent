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

| ID | Category | Status | One line |
|---|---|---|---|
| S1-01 | `identity` | **fixed** | band 0 deduplicated on `match_key()` and destroyed facts |
| S1-02 | `construct-coverage` | **fixed** | top-level set operators under `INSERT` refused, wrong reason |
| S1-03 | `flow-classification` | open | `GROUP BY` columns emitted as filter edges |
| S1-04 | `refusal-taxonomy` | open | `INSERT … VALUES` from variables refused; §3 makes variables first-class |
| S1-05 | `identity` | open | label format cannot express five facts in one file |
| S1-06 | `key-error` | **fixed** | two gaps in my own key, stated rather than quietly fixed — corrected 2026-09-09 |
| S2-01 | `construct-coverage` | open | `MERGE` with a `DELETE` arm fails to parse at all |
| S2-02 | `construct-coverage` | open | `INSERT ALL` unsupported — declared, but 7 edges lost |
| S2-03 | `refusal-taxonomy` | **fixed** | `PIVOT`/`UNPIVOT` refused even with a static column list |
| S2-04 | `silent-loss` | **fixed** | `BULK COLLECT` into a record collection yielded nothing, silently |
| S2-05 | `transform-classification` | **fixed** | `DECODE`/`NULLIF`/`GREATEST` read as derived, not conditional |
| S2-06 | `key-error` | **fixed** | ten units unlabelled, not seven; the header claim made true |
| S2-07 | `transform-classification` | **fixed** | `FIRST_VALUE`/`LAST_VALUE` — the key says `derived`, the analyser `aggregated` |
| S2-08 | `transform-classification` | open | two `_transform_of` copies, already drifted; and `LAG` reads like `FIRST_VALUE` but scores `aggregated` |

## Decisions taken — 2026-09-10

Three findings were blocked on a decision rather than on code. Abhay settled all three on
2026-09-10. **They are recorded here before any of them is implemented**, so that what was
decided can be separated from what the code later turned out to do — the same reason the keys
are written before the analyser runs.

None of the three is implemented yet. Each is still `open` in the register until a regression
test fails without it.

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

## Open work — what is left, and what each one needs

**Five open. Eight fixed. Nothing in the register has yet failed to recur across stress runs.**
**D-3 is landed** (S2-07). D-1 and D-2 are decided and not yet implemented — see above.
Implementing D-3 turned up **S2-08**, which is new and undecided.

| ID | Category | Blocked on | Cost if left |
|---|---|---|---|
| **S1-04** | `refusal-taxonomy` | **decided (D-1)** — implementation. Measure the parse-coverage cost first; it has 5.6 points against a kill criterion | 3 edges in stress 1, 2 in stress 2; the one false abstention in the signed phase-0 measurement |
| **S1-03** | `flow-classification` | **decided (D-2)** — implementation, and it must land in the analyser **and** the phase-0 keys in one change | 3 false positives per stress run, and a report that cannot tell a pre- from a post-aggregation filter |
| **S2-08** | `transform-classification` | a decision on `LAG`/`LEAD`, and a refactor for the duplicated classifier | S2-05 never reached band 1 at all; and a rule that reads as arbitrary from outside |
| **S2-01** | `construct-coverage` | real work — SQLGlot cannot parse `MERGE … DELETE` at all | 7 edges, and it is a standard slowly-changing-dimension shape |
| **S2-02** | `construct-coverage` | real work — `INSERT ALL` is genuinely unimplemented | 7 edges; the honest refusal makes this a coverage gap, not a defect |
| **S1-05** | `identity` | **the production package.** Moves every number in the phase | **31%** of the stress-2 key unstatable once the key is complete; also *hides* whether fixes worked |

### What each open finding is waiting for, in one line

- **S1-04** — **D-1: row-level DML carries lineage.** `INSERT … VALUES`, `UPDATE` and `DELETE`
  all get read rather than refused. Measure the parse-coverage cost before touching.
- **S1-03** — **D-2: the filter flow gains a phase.** `pre-aggregation` / `post-aggregation` /
  `grouping`, all three kept and distinguishable. Analyser and phase-0 keys in one change.
- **S2-01** — needs a SQLGlot bump, a pre-parse rewrite that strips the `DELETE` clause, or a
  refusal code that names the real reason. Currently honest but uninformative.
- **S2-02** — needs multi-table-insert support. The refusal is *true*, so this is capability,
  not correctness.
- **S1-05** — the match key. **Do not start before the production package**: it is the decision
  that most wants real code in front of it, and the verdict's condition still stands. The
  number it has to beat is now 31%, not 21% — see S2-06 below.
- **S2-08** — two questions from implementing D-3. Is `LAG`/`LEAD` `aggregated` or `derived`?
  And the classifier exists **twice**, in `band0.py` and `defuse.py`, already drifted.

### Fix log

| ID | Commit | Phase-0 impact |
|---|---|---|
| S1-01 `identity` | `a510049` | none — every cell identical |
| S2-04 `silent-loss` | `11314b3` | none |
| S1-02 `construct-coverage` | `46f4617` | none |
| S2-05 `transform-classification` | `1108223` | none |
| S2-03 `refusal-taxonomy` | `cda24a3` | none |
| S2-06 + S1-06 `key-error` | `d1b591f` | none — `cells` byte-identical to `phase0_final.json` |
| S2-07 `transform-classification` (D-3) | *this change* | none — `cells`, `gate`, `parse_coverage` and `honest_abstention` all identical |

**Eight fixes, and the phase-0 measurement has not moved once.** That is a statement about the
corpus, not about the analyser: none of these five constructs appears in it in the form that
breaks. It sharpens the verdict's existing caveat considerably — the corpus is not just
synthetic, its *construct mix and package shape* are unrepresentative in ways that hide real
defects.

### Where the stress packages stand now

Both re-scored after the seven fixes. The per-run tables further down are the **first-run**
figures and are left as measured. The analyser did not change in the S2-06 pass — **only the
keys did** — so every movement in this table is the benchmark getting more honest, in both
directions.

| | stress 1 | | stress 2 | |
|---|---|---|---|---|
| | before S2-06 | now | before S2-06 | now |
| 0 value | 100% / 100% | 100% / 100% | 93.9% / 75.6% | **100% / 69.4%** |
| 0 filter | 61.5% / 88.9% | **75.0% / 90.0%** | 76.0% / 76.0% | 76.0% / **73.1%** |
| **1 value — the gate** | **95.5% / 87.5%** | **100% / 84.6%** | **81.8% / 56.2%** | **100% / 55.0%** |
| 1 filter | 100% / 84.6% | 100% / 85.7% | 100% / 75.0% | 100% / 75.0% |
| 2 value | 100% / 100% | 100% / 100% | 100% / 50.0% | 100% / 50.0% |
| 2 filter | 100% / 100% | 100% / 100% | 100% / 100% | 100% / **50.0%** |
| parse coverage | 76.9% | 76.9% | 65.5% | 65.5% |

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

## S1-03 · `flow-classification` · OPEN — `GROUP BY` columns are emitted as filter edges

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

## S1-04 · `refusal-taxonomy` · OPEN — `INSERT … VALUES` from variables is still refused

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

Not yet implemented. The parse-coverage cost is the thing to measure first.

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
fixed, and the refusals that remain are S1-04, S2-01 and S2-02.

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

## S2-02 · `construct-coverage` · OPEN — `INSERT ALL` is unsupported

`s2_multi_table_insert` is refused as *"unsupported statement type MultitableInserts"*. That
is an honest, correctly-coded refusal and it costs **seven labelled edges** — one `SELECT`
feeding two targets, each behind its own `WHEN`.

Worth separating from S1-02: this refusal is **true**. The analyser genuinely does not handle
multi-table insert, says so, and the boundary is counted. The finding is a coverage gap, not a
correctness defect, and it is the shape ETL uses to fan one source into staging and reject
tables.

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

## Fix order for what remains

Ordered by what each one would teach, not by how annoying it is. Seven are done; this is the
queue from here. **S2-06 and S1-06 are struck from it** — both were key corrections, both are
now pinned by `tests/test_stress_keys.py`, and neither moved the phase-0 measurement.

1. ~~**S2-07** (`transform-classification`). **D-3.**~~ **Done.** Four cells, phase 0
   unmoved, and it did what a first item should: it turned up S2-08.
2. **S1-04** (`refusal-taxonomy`). **D-1.** Row-level DML stops being refused. Bigger than
   S2-07 and independent of it. Narrowing a refusal costs parse coverage, which has **5.6
   points of headroom against a kill criterion**, so measure the cost before touching.
3. **S1-03** (`flow-classification`). **D-2.** Last of the three decided items and by far the
   largest: it changes the IR's filter flow, the analyser, and **every phase-0 key**. Do it
   after the other two are landed and measured, so its movement in the grid is attributable to
   it alone.
4. **S2-01** (`construct-coverage`). `MERGE … DELETE` never becomes a tree, so this is a
   SQLGlot bump, a pre-parse rewrite, or at minimum a refusal code that names the real reason.
5. **S2-02** (`construct-coverage`). `INSERT ALL` is capability work. The refusal is true, so
   nothing is *wrong* today — it is a gap, and the shape ETL uses to fan one source into
   staging and reject tables.
6. **S2-08** (`transform-classification`). Needs a decision on `LAG`/`LEAD` and a refactor
   that merges the two `_transform_of` copies. Do the merge whenever S2-05's band-1 half is
   worth repairing — it is a recorded fix that only half landed.
7. **S1-05** (`identity`). Last, because it is the largest and the only one that moves every
   number in the phase. **Do not start it until the production package has been measured** —
   the verdict's condition still stands, and this is precisely the decision that wants real
   code in front of it rather than more synthetic evidence. It is now blocking key
   *corrections* as well as measurements: see S2-06's trigger edge.

**A finding is not closed until a regression test fails without the fix.** Reproducing it
once in a stress run is a symptom; the test is the fix's only durable statement.

## Before stress 3

Every open finding from stress 1 recurred in stress 2, so a third package will mostly restate
what is already here. **Clear S1-04, S1-03 and S2-07 first** — then a stress 3 measures
something new rather than re-reporting known gaps.

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
