# Stress test 1 — findings

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

## 1 · FIXED — band 0 deduplicated on `match_key()` and destroyed facts

`match_key()` is `(source, target, flow, transform)` — no guard, no origin. Band 0 collapsed
its edge set on that key before returning, so **two procedures writing the same columns
produced one edge**. The survivor kept one origin, one band and one guard; the other fact was
gone before it ever reached the IR — not refused, not declared, not counted.

This is worse than the scoring-layer collision ADR-0001 amendment 1b measured, because there
the ledger still holds both. Here the second fact never exists. It also made `procedure.py`'s
comment — *"merge on ledger identity so two facts differing only by guard or origin both
survive"* — a statement about something that had already happened upstream.

**How it showed up:** `stress_dynamic_mixed`'s constant `EXECUTE IMMEDIATE` is recovered
correctly and yields three band-2 edges. `stress_transaction_control` writes the same three
columns of `FCT_REVENUE_STAGE` statically. The static edges sorted first, and **the entire
recovered dynamic statement disappeared** — while the dynamic-SQL machinery reported success.
Band-2 value recall read 40%, and the cause was nothing to do with dynamic SQL.

**Fix:** deduplicate on `identity()`. **Free on the phase-0 corpus — every cell
byte-identical**, because those packages are small enough that two statements rarely write
the same pair. Band-2 value recall here went **40% → 100%**. Pinned by
`tests/test_band0.py::test_two_units_writing_the_same_columns_both_survive`.

## 2 · OPEN — top-level `INSERT … SELECT … UNION ALL` is refused, with the wrong reason

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

## 3 · OPEN — `GROUP BY` columns are emitted as filter edges

Three of the five band-0 filter false positives are `GROUP BY` columns:
`STG_ORDERS.ORDER_DATE`, `STG_ORDER_LINES.PRODUCT_ID` → `relation:FCT_PRODUCT_SALES`, plus
`STG_ORDER_LINES.LINE_AMOUNT` from the `HAVING`.

`GROUP BY` does not select rows; it decides which rows aggregate together. Two of those
columns are **already value sources** for the projected `period_month` and `product_id`, so
emitting them again as filter influence double-counts one relationship under two flows. The
`HAVING` is arguably a genuine filter — it selects groups — and that half may be correct.

Needs a decision, not a patch. Recorded rather than fixed.

## 4 · KNOWN — `INSERT … VALUES` from variables is still refused

Three misses, all of the same shape: `INSERT INTO tmp_recent VALUES (v_cust_id,
v_last_login)` and the audit write inside the exception handler. The refusal reason —
*"carries no column lineage from a relation"* — is true of relations and false of variables,
which ADR-0001 §3 makes first-class nodes.

Already the one false abstention in the phase-0 measurement (`s6`). This test adds three more
instances and shows the shape is common: writing a temp table row-by-row from cursor
variables is ordinary PL/SQL.

## 5 · The label format could not express five facts in one file

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

**This is the sharpest result in the run.** Amendment 1b concluded origin has no useful role
in the scoring layer, and it was right *about the phase-0 corpus*, where packages hold one to
three units. This file holds thirteen — ordinary for real code — and the collision appears
immediately. **The conclusion does not survive contact with package size**, and the
production package, when it arrives, will be larger than this one.

It also masked defect 2: `set_ops` contributes **zero** edges, yet two of its labels still
matched, because `udf_caller` happens to write the same columns. A whole statement was lost
and the score barely moved.

## 6 · Two gaps in my own key

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
