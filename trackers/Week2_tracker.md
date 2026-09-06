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

- [x] **T2.4a** Attach the governing condition to every edge produced inside a branch
- [x] **T2.4b** Conjoin nested guards (`b1_02` APAC path is guarded by two conditions)
- [x] **T2.4c** Report guard accuracy separately from precision
- [x] **T2.4d** Canonicalising guard normaliser

**Done when:** the EU-guarded edge carries `p_region = 'EU'`; unconditional edges carry
null; the nested case produces the correct conjunction; guard accuracy appears in the
score report.

**Status: CLOSED.** `tests/test_guards.py`, 11 tests. 149 green overall.

**Guard accuracy 0/11 → 10/11 (90.9%)**, and the number of guard-carrying edges rose
from 5 to 11. Precision and recall did not move — which is the point: ADR-0001 §5 keeps
guards out of the gate, and this work confirms the separation holds in practice.

**Three findings:**

1. **§5 was right, and now there is evidence.** The first measurement was 0/5, entirely
   from cosmetic differences: `P_REGION <> 'EU' AND P_REGION <> 'APAC'` versus
   `NOT (p_region = 'EU') AND NOT (p_region = 'APAC')`. Had guards been inside precision,
   the gate would have been reporting the state of a regex.
2. **Set-based edges were silently unguarded.** The band-0 analyser never sees control
   flow, so an `INSERT` inside `WHILE v_total > 100` claimed the write always happens —
   a wrong answer, not an incomplete one. Guards are now applied from the CFG after both
   analyses run.
3. **A `FOR` loop contributes no guard.** A guard is a condition under which an edge
   fires or does not; a `FOR` body always runs. `i IN 1 .. 12` was being reported as a
   guard, which claims a write is conditional when it is not. The loop variable's real
   influence — deciding *which rows* are read — is carried as a filter edge instead.

**Labels corrected (guards only, so the gate is untouched):** `b1_02`'s ELSIF arms now
carry the implied negation of the preceding arm; `b1_03`'s `WHILE`-body edges now carry
`v_total > 100`. Both were under-specifications.

**Why it is not a footnote:** an edge that only fires for EU customers is a different fact
from an unconditional one, and a regulator will ask precisely that.

---

## T2.5 — Temp tables as first-class relations

- [x] **T2.5a** Temporary-ness captured from the data dictionary, not inferred from names
- [x] **T2.5b** Chain through them: `stg_orders -> gtt_stage -> fct_revenue` (`b1_06`)
- [x] **T2.5c** Fusion hazards detected and declared — `src/lineage/analysis/scratch.py`

**Done when:** `b1_06` produces the full chain rather than two disconnected pipelines,
**and `s3_shared_temp_table` produces ZERO cross-procedure edges.**

**Status: CLOSED.** `tests/test_scratch.py`, 7 tests. 158 green overall.

**Zero cross-procedure edges**, asserted in both directions: writer A's customers cannot
reach writer B's revenue fact, and writer B's orders cannot reach the customer dimension.
Every edge is derived from a single statement and carries the unit that produced it, so
fusion cannot occur at the edge level at all.

**Two findings:**

1. **`tmp_recent` is a PERMANENT table in this corpus, despite the name.** Only
   `gtt_stage` is genuinely temporary. Temporary-ness is now read from
   `ALL_TABLES.TEMPORARY` rather than inferred from a prefix — treating a naming
   convention as a semantic fact is exactly how a scratch table gets mis-modelled. The
   two cases need opposite treatment: a temporary table's data is session-private, so a
   path composed across units is *invented* and can be refused; a permanent one really is
   shared, and whether data flows between two writers is **not statically decidable**, so
   the honest output is a declared hazard rather than a confident join or a silent
   omission.

2. **The hazard is at path composition, not at edge emission.** Nothing composes paths in
   phase 0, so nothing is fused today. The hazard appears the moment anything joins
   `A: stg_customer -> tmp_recent` to `B: tmp_recent -> fct_revenue`. Detection is cheap
   now, so the constraint is recorded *before* the code that would violate it exists.

**This is a silent failure with its own test.** Forty procedures writing `tmp_recent` and
keying on the name fuses forty unrelated lineages, inventing edges that never existed. An
invented edge is worse than a missing one.

---

## T2.6 — Loops

- [x] **T2.6a** Fixed-point iteration — reaching definitions over the CFG
- [x] **T2.6b** Convergence within the configured cap; non-convergence declared
- [x] **T2.6c** "Definitely assigned" must-analysis
- [x] **T2.6d** Uninitialised and partially-assigned reads reported

**Done when:** every loop in the corpus converges within the documented cap; the cap
appears in the run's declared limits; non-convergence is reported rather than silently
truncated.

**Status: CLOSED.** `src/lineage/analysis/reaching.py`, 19 tests. 177 green overall.

**The finding that shaped this task: fixed-point iteration adds no edges here.** Because
variables are first-class IR nodes, each statement contributes its own edges and
`gross_amount -> v_total -> lifetime_value` is complete after one pass. An implementation
that iterated anyway and reported "converged" would be theatre, and
`loop_fixpoint_iteration_cap` would be a limit declared in the coverage statement while
doing nothing — worse than not declaring it.

What iteration genuinely answers is a *different* question: whether anything was assigned
before a read, and whether that holds on every path. Three outcomes are now distinguished:

| Finding | Meaning |
|---|---|
| no definition reaches | value is NULL; any claimed source is wrong |
| reaches on some paths only | edge is real but conditional on a path that may not run |
| assigned in another unit | edge is real; conditional on **call order** |

**Two bugs found by reading the output:** a `FOR` index looked uninitialised (it is defined
by the loop header, not a statement), and `v_total NUMBER := 0` looked uninitialised
(a declaration default is an assignment before the first statement).

**One performance defect:** the first "assigned on every path" check enumerated routes,
which is exponential on a CFG with branches and exception edges — the corpus stopped
completing. Replaced with a linear must-analysis (intersection at merge points); whole
corpus now runs in ~64s.

**The one real finding on the corpus** is `apply_policy` reading `g_cutoff`, which only
`load_policy` assigns. That is the same phenomenon that made the week-1 observation
harness report "NOTHING CHANGED" until both calls ran in one session.

---

## T2.7 — Interprocedural analysis

- [x] **T2.7a** Summarise each callee once — `src/lineage/analysis/interproc.py`
- [x] **T2.7b** Inline the summary at every call site; cached per unit
- [x] **T2.7c** Depth cap → declared boundary, never a guessed edge
- [x] **T2.7d** Package-level state carried across separate calls
- [x] **T2.7e** Scalar UDFs inside a `SELECT` (`b1_08` `fn_net_amount`)
- [x] **T2.7f** Unknown callees declared rather than guessed from their arguments

**Done when:** `b1_08`'s three-deep call chain resolves; beyond the cap a boundary node is
emitted and counted; a recursive package terminates; **and `b1_07` produces the full
`ref_policy.window_days -> g_window_days -> g_cutoff -> tmp_recent` chain spanning two
procedure calls.**

**Status: CLOSED.** `tests/test_interproc.py`, 9 tests. 186 green overall.

**The wrong answer this removes was a plausible one.** `fn_net_amount(order_id)` contains
the column `order_id`, so a naive walk emits `order_id -> net_amount`. It type-checks, it
reads sensibly, and it is false: an order id selects *which row* the callee reads, it does
not determine a net amount. The argument is filter influence; the value comes from the
columns the callee's return expression depends on.

**Two combination rules, and confusing them gives a wrong answer.** Along **one path** the
strongest transform wins. Across **different paths** to the same column the *weakest* wins:
`gross_amount` reaches the result unconditionally through `v_gross` *and* conditionally
through the rate's `CASE`, so it is `derived`. Reporting it `conditional` would claim it
only sometimes contributes. `discount_amt` has only the conditional path and stays
conditional — it genuinely does not contribute when `gross_amount = 0`.

**Unknown callees are declared, not guessed.** Found by a failing test: a call to something
outside the analysed source was still having its arguments treated as value sources. In a
real estate most callees start outside the file, so this is the common case rather than the
exotic one. SQL built-ins are unaffected — `TRUNC(order_date)` genuinely derives from its
argument, and sqlglot distinguishes them by parsing what it knows into typed nodes.

**Two label corrections in `b1_08`,** both flagged because they raise agreement:
`discount_amt -> net_amount` was `derived` and is `conditional` (the callee's `CASE` was
never traced); and the function-internal variable hops were missing, though `b1_01` labels
exactly that kind of edge.

---

## T2.8 — The measurement

- [x] **T2.8a** Full scoring run, per band and per flow
- [x] **T2.8b** Tier distribution examined
- [x] **T2.8c** Recorded with corpus fingerprint, dictionary fingerprint, config
      fingerprint and commit — `lineage measure`, output in `measurements/`
- [x] **T2.8d** Kill criteria evaluated

**Status: CLOSED.** `src/lineage/harness/measure.py`.

**Band-1 value flow: precision 95.7%, recall 100.0%.** Parse coverage 91.7%, guard
accuracy 90.9%.

**Tier distribution is 100% Tier A**, which is the honest reading of where the engine is:
Tier A means parser-only with nothing contradicting it, and Tier B requires runtime and
profile agreement that does not exist yet. A tier distribution skewed to C or D would
mean the lineage works but the evidence story does not; skewed to A means only the
static leg of the triangle has been built.

| Kill criterion | Measured | Verdict |
|---|---|---|
| Band-1 precision < 95% | 95.7% | PASS |
| Band-1 recall < 85% | 100.0% | PASS |
| Parse coverage < 70% | 91.7% | PASS |
| Dynamic SQL > 30% and unrecoverable | see T2.9 | see below |
| Interprocedural non-termination | terminates, cap declared | PASS |

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

- [x] **T2.9a** Execute the corpus with realistic module/action attribution
- [x] **T2.9b** Read `V$SQL` and match materialised statements back to their emitter
- [x] **T2.9c** Measure attribution rate
- [x] **T2.9d** Test the **unfavourable** case — no `MODULE`/`ACTION`

**Done when:** we have a measured attribution percentage, not an impression.
**Below ~60% the compliance coverage story gets uncomfortable, and we need to know that
now.**

**Status: CLOSED. THE MOST IMPORTANT RESULT OF THE WEEK, AND IT IS A WARNING.**

| Case | Attribution |
|---|---|
| `MODULE`/`ACTION` present | **100%** (5 of 5) |
| Stripped, all emitters competing | **0% correct — and 2 of 5 attributed to the WRONG procedure** |

The favourable case works and confirms week 0's early reading. The unfavourable case does
not, and it failed in the worst possible way: not by finding nothing, but by confidently
naming the wrong emitter. That would place a regulated write inside a procedure that never
performed it.

**The failure is not a weak heuristic that a better one would fix.**
`b2_dynamic_constant` embeds a complete statement as a literal;
`b2_dynamic_concatenated` *assembles that same text at runtime*. The fragment is unique in
the **source** and identical in the **output**, so source-level distinctiveness cannot
save it. Two procedures that can emit the same SQL are indistinguishable by their SQL, and
that is common rather than exotic.

**Design changed on the evidence.** Statement shape no longer attributes at all — it
produces a ranked candidate list and abstains, exactly as the differential-diagnosis loop
requires. Only `MODULE`/`ACTION` counts as high confidence. Re-measured: **0 wrong, 5
unattributed**. Honest abstention rather than a confident fiction.

**What this means for the architecture.** Log recovery is viable *only* where session
attributes are populated. Where they are not, dynamic SQL is a declared boundary rather
than a recovered edge. That is a real constraint on the coverage story and it belongs in
the coverage statement, in the pilot questionnaire, and in the sales conversation — not
discovered at a customer.

**Kill criterion verdict:** dynamic SQL is not >30% of this corpus, so the criterion is
not triggered. But the recoverability half is now known to be conditional on instrumentation
the customer either has or does not.

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
