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
| S1-06 | `key-error` | closed | two gaps in my own key, stated rather than quietly fixed |
| S2-01 | `construct-coverage` | open | `MERGE` with a `DELETE` arm fails to parse at all |
| S2-02 | `construct-coverage` | open | `INSERT ALL` unsupported — declared, but 7 edges lost |
| S2-03 | `refusal-taxonomy` | open | `PIVOT`/`UNPIVOT` refused even with a static column list |
| S2-04 | `silent-loss` | **fixed** | `BULK COLLECT` into a record collection yielded nothing, silently |
| S2-05 | `transform-classification` | **fixed** | `DECODE`/`NULLIF`/`GREATEST` read as derived, not conditional |
| S2-06 | `key-error` | closed | `s2_locking` unlabelled; two window-transform disagreements |

### Recurrence — what stress 2 settled

**`identity` reached three, and the rule said what to do about it.** The register's own
condition was *"if a third turns up in stress 2, the match key stops being a scoring decision
and becomes an IR decision."* It turned up, and it scaled badly:

| | labels | colliding keys | facts unstatable | share |
|---|---|---|---|---|
| stress 1 (362 lines, 13 units) | 68 | 5 | 5 | **7%** |
| stress 2 (753 lines, 28 units) | 130 | 15 | 27 | **21%** |

The file doubled and the loss tripled, because collisions grow with the number of *pairs* of
writers to a table, not with the number of writers. One key in stress 2 has **five** members —
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
was run over the file. Six forbidden-edge rules, three expected boundaries.

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

`stress_set_ops` is refused as **"INSERT ... VALUES carries no column lineage from a
relation"**. It is not an `INSERT ... VALUES`; it is an `INSERT ... SELECT` whose expression
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

## S1-04 · `refusal-taxonomy` · OPEN — `INSERT … VALUES` from variables is still refused

Three misses, all of the same shape: `INSERT INTO tmp_recent VALUES (v_cust_id,
v_last_login)` and the audit write inside the exception handler. The refusal reason —
*"carries no column lineage from a relation"* — is true of relations and false of variables,
which ADR-0001 §3 makes first-class nodes.

Already the one false abstention in the phase-0 measurement (`s6`). This test adds three more
instances and shows the shape is common: writing a temp table row-by-row from cursor
variables is ordinary PL/SQL.

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

## S1-06 · `key-error` · CLOSED — two gaps in my own key

Stated because a key is evidence and its errors are part of the result.

- **The `EXISTS` correlation's second operand.** I labelled `STG_ORDERS.CUST_ID` as the filter
  source and omitted `STG_CUSTOMER.CUST_ID`. `s7`'s key labels *both* operands, so the
  analyser is right and the key was incomplete.
- **`BULK COLLECT` works.** I predicted a refusal and labelled none of it; the analyser
  correctly emits `STG_CUSTOMER.CUST_ID → variable:V_IDS` and the statement's `WHERE` filter.
  Two false positives that are the key's fault, not the analyser's.

Corrected in a later pass they would raise band-0 filter precision from 58.3% to roughly 80%.
Left uncorrected here so the first run stands as it was measured.

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

**Twelve refusals, and only five of them are correct.** `CONNECT BY`, `MODEL`, and three
`INSERT … VALUES` of literals only (which carry no lineage anyway) are right. The other seven
are S1-02, S1-04, S2-01, S2-02 and S2-03 below.

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

## S2-03 · `refusal-taxonomy` · OPEN — `PIVOT`/`UNPIVOT` refused even when decidable

Both `s2_pivot_static` and the `UNPIVOT` in `s2_unpivot_listagg` are refused under the rule
u1 established for `PIVOT` with a **subquery** column list — where the output shape is genuinely
not static. These two have **literal** column lists (`IN ('GBP' AS gbp, 'USD' AS usd)` and
`IN (net_amount, order_count)`), so the output columns are knowable at parse time.

This is over-refusal — a **false abstention**, the axis T3.1d exists to measure. It costs
three labelled edges and, unlike a wrong edge, it costs parse coverage too. The refusal
register needs to distinguish the decidable form from the undecidable one, exactly as the
dynamic-SQL classifier already distinguishes a constant string from an assembled one.

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

## S2-06 · `key-error` · CLOSED — my key again

- **`s2_locking` was never labelled.** I wrote the unit and skipped it in the key; its two
  correct edges (`DIM_CUSTOMER.CUST_ID → V_ID`, `LIFETIME_VALUE → V_VAL`) score as false
  positives. My omission.
- **`FIRST_VALUE` and `LAST_VALUE` transform.** I labelled them `derived`; the analyser says
  `aggregated`. Both defensible — they are window functions that select a value rather than
  compute one. Recorded as undecided rather than scored against the analyser.
- **Proposed convention (c) was rejected by the code.** I labelled a `DELETE`'s predicate
  columns as filter edges against the deleted relation; the analyser emits none.
  `s2_delete_with_subquery` produces zero edges and no refusal. That is a convention question
  I raised and the analyser answered differently — not a defect until someone decides.

## Fix order, and why not simply oldest first

Ordered by what each one would teach, not by how annoying it is.

0. ~~**S2-04**~~ (`silent-loss`) — **done.** Was promoted to first because everything else
   here is declared: a refusal, a boundary, a wrong-but-visible edge. This one lost five
   facts with no symptom anywhere, and a coverage statement that cannot report that is the
   one thing the product must never ship.
1. **S1-02** (`construct-coverage`). Small, certain, and now known to cover **every** top-level
   set operator rather than just `UNION` — `INTERSECT` and `MINUS` fail identically. Ordinary
   ETL, and the machinery already exists: `s5` proves it works one level down.
1b. **S2-05** (`transform-classification`). Cheap and mechanical — extend `_transform_of` to
   treat `DECODE`, `NULLIF`, `COALESCE` and `GREATEST` as conditional. Costs two cells per
   expression today because a wrong transform is a MISS on both sides.
2. **S1-04** (`refusal-taxonomy`). Needs a decision before code: the refusal is not wrong so
   much as *coarse*, and narrowing it costs parse coverage, which has **5.6 points of
   headroom against a kill criterion**. Measure before touching.
3. **S1-03** (`flow-classification`). A convention question, not a defect. Whatever is decided
   must be applied to the analyser *and* the phase-0 keys in the same change, or the two
   disagree silently.
4. **S1-05** (`identity`). Last, because it is the largest and the only one that moves every
   number in the phase. Do not start it until the production package has been measured — the
   verdict's condition still stands, and this is precisely the decision that wants real code
   in front of it rather than more synthetic evidence.

**A finding is not closed until a regression test fails without the fix.** Reproducing it
once in a stress run is a symptom; the test is the fix's only durable statement.
