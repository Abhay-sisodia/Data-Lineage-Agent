# Week 2 Tracker — The Procedural Core

**Companion to** [Phase0_tracker.md](Phase0_tracker.md), which holds the phase-level
gates. This file breaks week 2 into small, individually verifiable tasks.

**Status:** not started · **Created:** 2026-09-06 · **Baseline commit:** `ec8cef1`

---

## Objective

**Measure band-1 precision.** This is the week the spike exists for.

Band 0 is a solved problem and always was. Local variables, branches, loops, cursors,
temp tables and package state are where every lineage tool quietly stops working and
starts guessing — and a guessed edge inside a regulatory filing is worse than no product
at all.

**The number that comes out of T2.8 decides the company.** Below 95% band-1 precision the
regulatory positioning is dead and this becomes a migration-triage business with a
different buyer.

---

## Method: compiler dataflow, not SQL parsing

The instinct to reach for a SQL parser is right for one statement and wrong for a
procedure. A procedure is a program: control flow, mutable state, calls.

```
parse -> control flow graph -> def-use chains -> resolve calls -> emit IR edges
                                     |
                            unanalysable -> declare, never guess
```

---

## Sequencing rule, carried from week 1

**T2.0 comes first.** Label the ground truth before the analyser exists to be attached to.

Week 1 proved why: `observe_corpus.py` found a trigger writing `DIM_CUSTOMER.LIFETIME_VALUE`
that reading the source would never have revealed, and it caught a design error in the
`s5` positional-UNION case. Had the analyser existed first, both would have been easy to
rationalise away. Label first, measure second.

---

## T2.0 — Ground truth for band 1 *(do this first)*

The answer key for the nine band-1 packages. Observation-anchored per ADR-0001 §1.

- [ ] **T2.0a** Observe all nine band-1 procedures; record what each actually writes
- [ ] **T2.0b** Label `b1_01` local variables, `b1_02` if/case, `b1_03` loops
- [ ] **T2.0c** Label `b1_04` cursor FOR, `b1_05` explicit cursor, `b1_06` temp tables
- [ ] **T2.0d** Label `b1_08` nested calls, `b1_09` exception handlers
      *(`b1_07` already labelled in week 1)*
- [ ] **T2.0e** Record which labels observation could NOT settle, and why

**Done when:** every band-1 package has a committed label set; each edge records its own
provenance; `pytest` hash-checks all of them; and the count of `source_read` labels is
stated explicitly rather than buried.

**Watch for:** exception handlers and rare branches will not execute on the seed data.
Those labels are `source_read` and `unexercised: true` — do not quietly upgrade them to
`observed` because the analyser found them.

---

## T2.1 — IR v0

The representation everything downstream reads. Designed against what the corpus actually
contains, not against a wishlist.

- [x] **T2.1a** Node types: Column, Relation, Variable, Procedure, Statement, Boundary, Literal
- [x] **T2.1b** Edge attributes: transform, guard, origin, mechanism, tier, valid/tx time
- [x] **T2.1c** Supersession — closes the old interval and appends; never mutates
- [x] **T2.1d** Validator rejecting any edge missing origin or mechanism
- [x] **T2.1e** ADR-0001 amendment 1 implemented — `identity()` vs `match_key()`

**Done when:** a schema validator rejects an incomplete edge; `guard` is the only nullable
attribute; serialisation round-trips losslessly; an update attempt appends rather than
mutating; `PredictedEdge` in the harness is replaced by the real IR edge.

**Status: CLOSED.** `src/lineage/ir/model.py`, 14 tests in `tests/test_ir.py`.
119 tests green overall.

- The IR is now the shared vocabulary: `labels` and `scoring` import `Node`, `Flow`,
  `Transform` and `Origin` from it rather than maintaining parallel definitions that
  could drift. `PredictedEdge` is an alias for `IREdge`.
- **Origin is required on every edge.** A fact whose origin cannot be stated is not
  evidence, and "which line produced this" is the first question anyone asks.
- **`identity()` = match key + guard + origin; `match_key()` = source, target, flow,
  transform.** Two edges differing only by the condition under which they fire are two
  facts in the ledger but one target when scoring — which is what amendment 1 required.
- **`tx_from` defaults to a fixed sentinel, not `now()`.** Stamping wall-clock time on
  every edge would break byte-identical reproducibility for no benefit in a phase with
  no ledger. Verified: two analyser runs produce identical output.
- `EdgeLedger.as_of()` already answers "what was the lineage on the filing date"
  in miniature. Phase 2 replaces the storage, not the semantics.

**Note:** `mechanism` and `tier` stay separate. Mechanism is *how it was found*; tier is
*how well it was corroborated*. An AST-derived edge is still only Tier A if nothing
contradicts it.

---

## T2.2 — Control flow graph

- [x] **T2.2a** Basic blocks and sequential flow within a procedure body
- [x] **T2.2b** Branches — `IF` / `ELSIF` / `ELSE`, with reconstructed `ELSE` condition
- [x] **T2.2c** Loops — `FOR`, `WHILE`, with back edges
- [x] **T2.2d** Exception handlers as additional CFG edges, tagged exceptional
- [x] **T2.2e** Cursor constructs build without error
- [x] **T2.2f** `guards_reaching()` — the guard path T2.4 needs

**Done when:** CFGs for the band-1 adversarial packages match hand-drawn node and edge
counts; exception handlers appear as edges; construction terminates on the deepest
procedure in the corpus.

**Status: CLOSED.** `src/lineage/analysis/cfg.py`, 21 tests. 140 green overall.

Hand-counted and asserted: `b1_01` 6 nodes / 5 edges · `b1_02` 8 / 10 ·
`b1_03` 9 / 10 with 2 back edges · `b1_09` 12 / 17 with 6 exception edges.

**Three defects found and fixed while building, each visible only by reading the output:**

1. **Merge points inherited a branch guard.** The `COMMIT` after an `IF/ELSIF/ELSE` was
   reported as guarded by `p_region = 'EU'` — it runs whichever arm was taken. Fixed by
   computing guards as the **common prefix over every incoming path** rather than
   following the first predecessor. Nested branches still conjoin correctly.
2. **`FOR` loop exits carried a meaningless negation.** `NOT (i IN 1 .. 12)` was being
   attached to every statement after the loop. A `FOR` loop always completes, so its
   iteration spec is not a boolean guard; only `WHILE` exits are conditional.
3. **Handler bodies looked unconditional.** Excluding exception edges from the guard walk
   left an error-path write with no guard at all. Handler bodies now fall back to their
   exception edge and carry `EXCEPTION NO_DATA_FOUND` — which independently matches the
   guard written into `b1_09`'s ground truth before this code existed.

**Why exception handlers matter:** `b1_09` writes `is_active` from a completely different
source on the `NO_DATA_FOUND` path. On a failure day that *is* the lineage, and it exists
nowhere in the happy path.

---

## T2.3 — Def-use chains

The core of the week. Values moving between statements through variables.

- [ ] **T2.3a** Variable declarations and scope (local, parameter, package-level)
- [ ] **T2.3b** `SELECT ... INTO v` as a definition
- [ ] **T2.3c** `v := expr` assignment as a definition
- [ ] **T2.3d** Variable *use* inside SQL — the SQLGlot handoff point
- [ ] **T2.3e** Reaching definitions over the CFG
- [ ] **T2.3f** Cursor `FETCH INTO` bound **positionally** to the cursor's select list

**Done when:** `b1_01` produces the documented chain
`ref_policy.window_days -> v_days -> v_cutoff -> row filter on tmp_recent`;
variable-carried edges are scored as their own category; `b1_05` binds fetch targets by
position, not by name.

**The trap in T2.3f:** `v_email` and `v_region` are fetched in the cursor's column order.
Matching on name wires them to the wrong sources — and it compiles, runs, and looks fine.

**Handoff reminder:** `tests/test_smoke_toolchain.py` pins the behaviour this depends on —
SQLGlot surfaces `v_cutoff` as an unbound column reference. That marker is where def-use
takes over.

---

## T2.4 — Branch guards

- [ ] **T2.4a** Attach the governing condition to every edge produced inside a branch
- [ ] **T2.4b** Conjoin nested guards (`b1_02` APAC path is guarded by two conditions)
- [ ] **T2.4c** Report guard accuracy separately from precision (harness already supports it)

**Done when:** the EU-guarded edge carries `p_region = 'EU'`; unconditional edges carry
null; the nested case produces the correct conjunction; guard accuracy appears in the
score report.

**Why it is not a footnote:** an edge that only fires for EU customers is a different fact
from an unconditional one, and a regulator will ask precisely that.

---

## T2.5 — Temp tables as first-class relations

- [ ] **T2.5a** Model temp and global temp tables as relations scoped by (procedure, session)
- [ ] **T2.5b** Chain through them: `stg_orders -> gtt_stage -> fct_revenue` (`b1_06`)

**Done when:** `b1_06` produces the full chain rather than two disconnected pipelines,
**and `s3_shared_temp_table` produces ZERO cross-procedure edges.**

**This is a silent failure with its own test.** Forty procedures writing `tmp_recent` and
keying on the name fuses forty unrelated lineages, inventing edges that never existed. An
invented edge is worse than a missing one.

---

## T2.6 — Loops

- [ ] **T2.6a** Fixed-point iteration to a stable edge set
- [ ] **T2.6b** Termination proven, not observed once; cap from config, reported when hit

**Done when:** every loop in the corpus converges within the documented cap; the cap
appears in the run's declared limits; non-convergence is reported rather than silently
truncated.

**Hard case:** `b1_03`'s second loop has a write target that depends on the loop counter,
so the edge set is not fixed by reading the statement.

---

## T2.7 — Interprocedural analysis

- [ ] **T2.7a** Summarise each callee once (in / out / writes / reads)
- [ ] **T2.7b** Inline the summary at every call site; cache it
- [ ] **T2.7c** Depth cap → boundary node, counted, never a guessed edge
- [ ] **T2.7d** Package-level state carried across separate calls
- [ ] **T2.7e** Scalar UDFs inside a `SELECT` (`b1_08` `fn_net_amount`)

**Done when:** `b1_08`'s three-deep call chain resolves; beyond the cap a boundary node is
emitted and counted; a recursive package terminates; **and `b1_07` produces the full
`ref_policy.window_days -> g_window_days -> g_cutoff -> tmp_recent` chain spanning two
procedure calls.**

**T2.7d is the nastiest legitimate case in the band.** State survives across calls with no
parameter passing and nothing in either statement connecting them.

---

## T2.8 — The measurement

- [ ] **T2.8a** Full band-1 scoring run, per band and per flow
- [ ] **T2.8b** Tier distribution examined — if most edges land in Tier C/D the lineage
      works but the evidence story does not
- [ ] **T2.8c** Record the number against corpus hash, dictionary fingerprint and commit
- [ ] **T2.8d** Mid-point checkpoint against kill criteria

**Done when:** band-1 value-flow precision and recall are measured and recorded, with the
label-provenance split printed alongside.

### Thresholds

| Metric | Target | Floor |
|---|---|---|
| Band-1 value precision | ≥ 98% | **≥ 95%** |
| Band-1 value recall | ≥ 85% | ≥ 85% |
| Parse coverage | ≥ 90% | ≥ 70% |
| Honest abstention | 100% | pass/fail |

**If band-1 precision cannot clear 95%,** the regulatory positioning is dead. The engine
still sells migration triage and dead-code detection — different buyer, different pitch.
That is a legitimate outcome of the plan working, discovered in week 2 rather than month
eighteen.

---

## T2.9 — Log recovery sub-spike *(runs in parallel, start now)*

The plan is explicit that this starts in week 2, not week 3: it is the highest-leverage
idea in the architecture and **the one most likely to fail quietly**.

- [ ] **T2.9a** Execute the corpus with realistic module/action attribution
- [ ] **T2.9b** Read `V$SQL` and match materialised statements back to their emitter
- [ ] **T2.9c** Measure attribution rate on `b2_02`, `b2_03`, `b2_04`
- [ ] **T2.9d** Test the **unfavourable** case — scheduler-driven, no `MODULE`/`ACTION`

**Done when:** we have a measured attribution percentage, not an impression.
**Below ~60% the compliance coverage story gets uncomfortable, and we need to know that
now.**

**What week 0 already established:** `MODULE`/`ACTION` *do* survive into `V$SQL` when set
explicitly. That is the favourable case only. The register warns attribution commonly
degrades when a scheduler drives the call — T2.9d is the honest test.

---

## Standing rules

- **Never emit a guessed edge.** Refuse, declare, count.
- **Score per band and per flow.** Never blend.
- **Label before you analyse.** Not after.
- **Read any result as a ceiling.** The corpus was written knowing what the analyser must
  handle; real legacy code is consistently worse.

## Carried-forward risks

- **30 labelled edges is a small key.** T2.0 roughly triples it, still small.
- **No real production package.** Highest-value outstanding item; human latency.
- **12 of 30 current labels are `source_read`** — the weakest provenance.
- **Third-party corpora undecided** (utPLSQL / Alexandria / db-sample-schemas / none).
