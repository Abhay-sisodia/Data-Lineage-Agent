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

---

## Consequences

- Edge identity for matching is `(source, target, flow, transform)`. Guard, band, origin
  and evidence are carried but compared separately.
- Reported per band: precision and recall, split by flow kind, plus guard-correct % and
  tier distribution. **No blended headline number exists** — by design.
- The 95% gate applies to **band-1 value-flow precision**. That must be stated wherever
  the figure appears, or the figure is unreadable.
