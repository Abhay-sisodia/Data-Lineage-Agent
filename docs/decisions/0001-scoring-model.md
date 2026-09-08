# ADR-0001 · What we score, and how

**Date:** 2026-09-06 · **Status:** accepted · **Task:** T1.1

The same analyser can score 95% or 70% depending on these six answers. They are recorded
here because the precision figure is meaningless without them, and because anyone
reading a result later must be able to reconstruct what was counted.

Net effect: stricter scoring, a lower number, a more defensible one.

---

## 1 · The database is the referee, not us

**Decision.** Ground-truth labels are established by *executing* the corpus and observing
which columns actually change, wherever execution can settle it. Labels that execution
cannot settle are marked as read from source and carry lower authority.

**Why.** If one party writes both the answer key and the analyser, a systematic
misunderstanding appears in both, they agree, and the score certifies the
misunderstanding. `s5_positional_union` is the worked case: believe `UNION` binds by name
rather than position, and key and analyser are wrong together at 100%.

This is the architecture's own rule applied to ourselves — two models agreeing is often
the same wrong prior twice. Behaviour is the leg of the triangle that does not lie, and
it is independent of what anyone believes.

**Limits, stated.** Execution cannot settle: unexercised branches (dark by definition),
a guard's *meaning*, or anything in a `parse_only` file. Those stay source-read and are
flagged.

## 2 · Filter influence is lineage, scored separately

**Decision.** `WHERE last_login > v_cutoff` produces an edge, tagged `flow: filter`.
Value-carrying edges are `flow: value`. **Precision and recall are reported per flow
kind and never blended.**

**Why.** The spike's worked example counts it (edge e5) — a policy table silently
governing which rows load is exactly the finding no table-level tool produces. But a
filter edge is a different claim from a value copy, and mixing them into one number hides
which kind broke.

## 3 · Variables are first-class nodes

**Decision.** `ref_policy.window_days → v_days → v_cutoff` is two edges, not one collapsed
column-to-column hop.

**Why.** The IR has a `Variable` node type and the worked example scores e1 and e2
separately. Collapsing to endpoints hides *where* def-use broke — which is the entire
diagnostic value of band 1. It also inflates the score: an analyser that guesses the
endpoints without following the chain would look identical to one that reasoned correctly.

## 4 · Wrong transform class is a MISS

**Decision.** An edge with correct endpoints but the wrong transform class
(`identity` / `derived` / `aggregated` / `conditional`) counts as a **miss**, not a hit
and not partial credit. The class is part of the edge's identity for matching.

**Why.** Silent failure s8 exists precisely for this: `SUM(gross_amount)` and a row-level
copy of `gross_amount` share endpoints and mean entirely different things to whoever reads
the filing. An `is_active` derived from a `SUM(...)` is a correct edge carrying the wrong
meaning. Harsh, and deliberately so — precision is the number that gates the phase.

## 5 · Guards are excluded from precision, reported separately

**Decision.** Guard correctness is a separate published metric ("guard correct %"). It
does not enter the precision figure. Guards are compared after normalisation.

**Why.** `p_region = 'EU'` and `'EU' = p_region` are the same guard and different strings.
Letting string-comparison noise move the number that decides the company's direction would
be measuring our normaliser, not our analyser. Guards still matter — an edge that only
fires for EU customers is a different fact — so they are measured, just not in the gate.

## 6 · An edge's band is the hardest construct on its path

**Decision.** Where a path crosses bands, the edge is scored in the **highest** band it
traverses.

**Why.** Statements mix bands: `b1_02` has an `IF` (band 1) wrapping an `UPDATE` with a
subquery (band 0). Assigning by statement or by resolving mechanism would let band-1
difficulty hide inside a band-0 score — and the one thing worth learning from this phase
is whether band 1 works. Conservative by construction.

---

---

## Amendment 1 · Guard belongs in identity, not in matching

**Added 2026-09-06 during T2.0. Implemented in T2.1 (`lineage.ir.model`).**

Labelling band 1 surfaced a case §5 did not anticipate. In `b1_09`, the edge
`P_REGION → DIM_CUSTOMER (filter)` occurs twice: once on the happy path, once inside the
`NO_DATA_FOUND` handler. Those are **two different facts** — one fires when the policy
lookup succeeds, the other only when it fails. `b1_03` has the same shape: the
`v_total → v_total` self-edge appears as both the accumulation and the decay.

Excluding guard from the edge key collapses them into one, and the exception-path
occurrence cannot be represented at all.

**What §5 got right:** guard must stay out of *precision*, or the gate moves on
string-comparison noise rather than on analysis quality.

**What it got wrong:** it applied that exclusion to *identity* as well. Two facts that
differ only by the condition under which they fire are still two facts, and a ledger that
cannot hold both is lossy.

**Resolution, for T2.1.** Separate the two notions:

- **Ledger identity** — `(source, target, flow, transform, guard, origin)`. What makes a
  fact distinct.
- **Match key** — `(source, target, flow, transform)`. What decides a hit when scoring.

Implemented as `IREdge.identity()` and `IREdge.match_key()`.

The label format still keys on the match key, so `b1_03` and `b1_09` each under-count by
one edge until their label sets are re-expressed against IR identity. Both say so in the
file. Recorded rather than quietly absorbed, because a benchmark that hides its own
limitations is the thing this project exists not to be.

### Amendment 1a · How the scoring layer implements it

**Added 2026-09-06 during week 3, after T3.0 found four more cases.** The gap had grown
from "two files under-count by one" to "three silent failures cannot be tested at all":
`s2`, `b2_05` and `b2_02` each invite a wrong answer whose match key is identical to a
correct one, and `s7`'s APAC filter could not be written down beside its EU twin.

**The obvious implementation is wrong.** Adding guard to the match key makes a mis-stated
guard produce a miss *and* a false positive, dragging guard-comparison noise onto the gate
— exactly what §5 forbids, and T2.4 measured that noise at 0/5 on phrasing alone.

**So matching is two-stage.** Group both sides by match key; where a group holds more than
one edge, pair them off by normalised guard. A lone edge with a wrong guard still matches
and is counted wrong only in the separate guard figure. Guards discriminate solely where
discrimination is needed.

**Origin is not used, and that was measured rather than assumed.** Across the corpus, label
and analyser agree on the origin *unit* for 160 of 170 matched edges but on the *line* for
only 12. Line numbers are hand-read while labelling; requiring them would collapse recall
for a reason unrelated to lineage being right. Unit agrees far better, but its ten
disagreements are an open modelling question — when a view's predicate or a callee's
expression is inlined, does the edge belong to the caller or to the unit that wrote it? —
and putting an unsettled convention inside the gate would move the headline number on a
decision nobody has taken.

**Residual limitation, stated.** The three origin-only silent failures remain inexpressible
in the key. They need direct assertions in T3.3 and T3.5, not a scoring change.

**Predictions are deduplicated by scoring key before counting.** The analyser merges on
`identity()`, which includes origin, so one fact reached by two routes survives as two
edges; counting both would punish precision for saying the same true thing twice.

**Effect on the numbers, recorded because it is an edit to a benchmark:** four edges
restored to the keys (`b1_02`, `b1_03`, `b1_09`, `s7`). Band-1 filter recall rose
88.9% → 90.9% and band-1 value recall fell 100% → 96.2%, because `b1_03`'s decay is now a
target the analyser misses. `b1_02`'s restored edge *raises* precision by removing a false
positive, and is flagged as such in that file.

---

### Amendment 1b · Origin was tried in the scoring layer, measured, and rejected

*2026-09-09. Measured at commit `8ff5c03`, corpus `0c1cf179…`, full 36-package run.*

Amendment 1 settled that **guard** separates facts without entering precision, and left an
obvious follow-up: should **origin** do the same? Six packages had said the v0 match key was
under-specified, and `docs/ir-v0.md` named it "the most likely v1 change". It was built
behind a config flag and measured rather than argued. **It does not hold.**

**The case for it.** `s2_schema_context` produces `STG_CUSTOMER.CUST_ID →
TMP_RECENT.CUST_ID` legitimately at line 19 and *fabricates the identical edge* at line 26
from a schema that was never granted. Same source, target, flow, transform **and** guard, so
two-stage pairing cannot separate them and the label validator cannot express the second as
a forbidden edge — it contradicts a required one. Prediction dedup would then collapse the
pair into one claim, so a fabricated edge could vanish into a legitimate one and still score
100%. A hole in **precision**, invisible by construction. `b2_05` is the same shape.

**What the measurement said.** Origin unit in prediction dedup:

| | baseline | origin in dedup |
|---|---|---|
| band-1 value (the gate) | 96.2% / 96.2% | **96.2% / 96.2%** |
| band-0 filter precision | 100% | **97.4%** |
| band-1 filter precision | 100% | **90.9%** |
| band-2 filter precision | 100% | **93.5%** |
| false positives, corpus-wide | 1 | **6** |
| true positives / recall | — | **unchanged in every cell** |

**It caught no fabrication and manufactured five false positives.** Every one is correct
analysis being punished:

- `b1_08` — `STG_ORDERS.ORDER_ID → STG_ORDERS` reached from `FN_DISCOUNT_RATE`,
  `FN_NET_AMOUNT` and `B1_NESTED_CALLS`: one callee summarised at three call sites.
- `b1_09`, `s3` — `DIM_CUSTOMER.CUST_ID → DIM_CUSTOMER` reached from the procedure and again
  from `TRG_RECENT_AUDIT`: a trigger edge inherited by the table's writer, which is the
  whole T3.3 design.

**Multi-unit origin is the normal case, not the exceptional one.** Interprocedural
summarisation and trigger inheritance both reach one true fact by several routes, so origin
cannot distinguish *a second route* from *a second fact*. The dedup comment written a week
earlier had already said so; the measurement is what made it a decision.

**And the fabrication it was built for is unreachable here.** `s2` and `b2_05` refuse the
offending statement before it emits anything, so there was never a second claim to separate.
**The hole is real but latent** — the flag pays its full cost and none of its benefit.

**A same-unit *pairing* pass was also built, and removed as inert rather than harmless.** It
cannot change the counts, but it also never fires: `labels._reject_duplicates` makes
(match key, guard) unique among labels and dedup makes it unique among claims, so a group
never holds two candidates to choose between. Every cell, the guard figure and the execution
axis measured byte-identical with it enabled. Config that cannot change an output is
decoration, not a control.

**Standing conclusion.** Origin has no useful role in the scoring layer. The `s2` / `b2_05`
hole needs what those label files have said all along — **a direct origin assertion in the
label format** ("this edge must come from *this* unit"), scored beside the forbidden-edge
rules rather than inside the match key. That is a label-schema change, not a scoring change.

`scoring.origin_in_dedup` stays in the config, defaulted **off** and declared in every
report, so this finding is reproducible rather than a story to be taken on trust. Nine tests
in `tests/test_origin_in_key.py` pin it, including the corpus fact that makes the hole
latent — because the argument for turning it on is persuasive on paper and only the
measurement refutes it.

**Effect on the numbers: none.** Every measured key is identical to the signed
`measurements/phase0_final.json`. The **config fingerprint changes** — `eed291d0…` →
`4e253979…` — because a new declared limit exists, which is the pinning mechanism working
as designed rather than a drifted result.

---

## Consequences

- Edge identity for matching is `(source, target, flow, transform)`, with guard used to
  pair edges *within* a match-key group (amendment 1a). **Origin is not used at all
  (amendment 1b), and that is now measured rather than assumed.** Band, origin and evidence are
  carried but compared separately; guard is also reported separately and stays out of
  precision.
- Reported per band: precision and recall, split by flow kind, plus guard-correct % and
  tier distribution. **No blended headline number exists** — by design.
- The 95% gate applies to **band-1 value-flow precision**. That must be stated wherever
  the figure appears, or the figure is unreadable.
