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

### Amendment 1c · Origin assertions — where an edge is allowed to come *from*

*2026-09-09. The mechanism amendment 1b said was needed instead of a match-key change.*

**The gap.** A forbidden edge names a wrong **answer**. Two packages invite a wrong
**derivation of the right answer**, which no forbidden rule can express — the rule would ban
the required edge too, and `_reject_self_contradiction` correctly refuses it.

- `s2_schema_context` fabricates `STG_CUSTOMER.CUST_ID → TMP_RECENT.CUST_ID` by dropping an
  ungranted schema qualifier and binding to the local table. Character for character its own
  legitimate edge from the statement above; **only the line differs**.
- `b2_05_triggers` attaches a trigger's edges to the caller rather than the table — crediting
  a procedure with a write it never issued, and losing that trigger for the six other writers
  of `tmp_recent`.

**The mechanism.** `origin_assertions` in the label format:

```yaml
origin_assertions:
  - source: {kind: column, name: STG_CUSTOMER.CUST_ID}
    target: {kind: column, name: TMP_RECENT.CUST_ID}
    flow: value
    must_come_from: [{unit: S2_SCHEMA_CONTEXT, lines: [17, 22]}]
    reason: >
      Statement 2 selects cust_id from a schema that was never granted...
```

Four decisions in it, each load-bearing:

1. **Universally quantified.** *Every* emitted edge with this key must have an allowed
   origin. Asking whether *some* edge does would pass with a fabricated one beside it —
   precisely the failure being hunted.
2. **`must_come_from` is a list.** One true fact legitimately arrives by several routes: a
   callee summarised at three call sites, a trigger edge inherited by every writer of its
   table. **This is the whole reason the mechanism works where the match-key change failed**
   — amendment 1b measured that collapse at five false positives.
3. **Checked before dedup.** Dedup keeps one claim per key and picks by line order, so a
   fabricated edge could be the one discarded. Checking after it would let the defect through
   on an implementation detail.
4. **Reported on its own axis, never folded into precision.** The wrongly-derived edge still
   matches its label, because it *is* the right edge. Counting it as a false positive would
   claim the analyser invented something — a different and weaker claim. Same treatment as
   forbidden edges.

**Line ranges are safe here and not in the match key**, which looks inconsistent and is not.
Lines are unusable for *matching* — label and analyser agree on the line for 12 of 170
matched edges, because labellers hand-read them. An assertion is written against **one pinned
source**: `verify_against` refuses to score a key whose `source_sha256` has moved, so a range
cannot silently come to mean a different statement. `s2` needs a range because both
statements share a unit; `b2_05` uses unit-only because a trigger owns its edges wherever
inside it they arose.

**Effect on the numbers: none.** Six assertions across two packages, all satisfied, and no
cell moves — the axis is orthogonal by construction.

**The honest reading: this is a guard, not a catch.** Both holes are **latent**. `b2_05`
already attributes correctly, and `s2` emits nothing at all for statement 2. Tests feed the
fabrication in directly, because a guard nobody has seen fire is a guard nobody has tested.

> **And measuring this found a live defect that is worse than the one being guarded against.**
> `s2` reports **2 statements seen, 2 analysed, 0 refusals, 0 boundaries** — and emits no edge
> for statement 2. The ungranted schema is dropped with no symptom anywhere, and the statement
> counts as *analysed*, so it inflates parse coverage. That is the silent absorption `s2`
> exists to catch, and nothing in the harness reports it, because `run_measurement` counts
> boundaries declared and never compares them against `boundaries_expected`. Corpus-wide,
> **18 of 18 expected boundaries are "not declared"** while 35 boundaries are declared — the
> two vocabularies simply do not match as strings (`b2_06` declares
> `insert_statement@20: REMOTE_CUSTOMER@CRM_LINK.CUST_ID (relation not in dictionary)` where
> the key expects `B2_DB_LINKS: remote_customer@crm_link - database link target out of
> coverage`). **The boundary axis is unscored across the entire corpus.** This is
> `docs/ir-v0.md`'s #1 open item — boundaries are strings, not nodes — with a measurement
> behind it for the first time. Recorded here, not fixed in passing: it touches parse
> coverage and the verdict's evidence claims, and deserves its own decision.

---

### Amendment 1d · Boundaries are scored, on kind and subject rather than prose

*2026-09-09. Found while building amendment 1c, and larger than what it was found under.*

**Two independent failures, either of which alone made the axis useless.**

1. **Nothing checked boundaries at all.** `run_measurement` counted declarations and never
   compared them against `boundaries_expected`. A silently omitted boundary — the specific
   failure the label format calls "the failure mode the whole product exists to avoid" —
   produced no symptom anywhere.
2. **Had it compared them, it would have been noise.** All **18** expected boundaries read
   as undeclared while **35** were declared, because the two sides describe the same fact in
   different prose. The key says `B2_DB_LINKS: remote_customer@crm_link - database link
   target out of coverage`; the analyser says `insert_statement@20:
   REMOTE_CUSTOMER@CRM_LINK.CUST_ID (relation not in dictionary)`. Same fact, zero shared
   words, exact string equality between them.

**The fix.** A boundary carries a **kind** (closed enum, like the refusal taxonomy) and a
**subject** — the object or statement it is about. Expectations are stated the same way, and
comparison is on `(kind, subject)`. The prose survives untouched, for humans; it is simply no
longer the identity.

`Declared` is a `str` subclass, which is a transitional choice and is documented as one.
Boundaries are produced at a dozen sites and consumed at fifty-odd; carrying the structure on
the string lets the axis be scored now without a refactor of every producer, consumer and
test. **The real fix — a `Boundary` node that can be an edge endpoint — is still
`docs/ir-v0.md` item 1**, and this does not close it.

**Result: 37 boundaries declared, all classified; 18 expected; 14 satisfied; 4 not declared.**
Those four are genuine gaps, and every one was invisible before:

| package | kind | subject |
|---|---|---|
| `b2_03_dbms_sql` | `dynamic_sql` | `B2_DBMS_SQL` |
| `b2_04_metadata_driven_etl` | `suppressed_error` | `B2_METADATA_DRIVEN_ETL:54` |
| `s2_schema_context` | `context_dependent_binding` | `S2_SCHEMA_CONTEXT:20` |
| `u1_loud_constructs` | `source_unavailable` | `U1_WRAPPED` |

`b2_03` is the sharpest: a package whose entire subject is an opaque `DBMS_SQL` handle
declares **no boundary at all**, and scored clean for it.

**`coverage.py` stopped guessing too.** Dangling references were recovered by string-matching
a marker constant and then splitting the sentence on punctuation to guess which words were
the object name. It now reads kind and subject — which is why `CUSTOMER_ACTIVITY` became
`FINANCE_DW.CUSTOMER_ACTIVITY`: the schema was always part of the name and the prose parser
had been dropping it.

**Effect on the gate: none.** Every cell identical, band-1 value precision 96.2%, parse
coverage 76.1%. `boundaries_declared` 71 → 73 and the dangling list gains
`REPORTING.STG_CUSTOMER` — both from the `s2` analyser fix that this work uncovered, not from
the scoring change.

**The undeclared four are left undeclared, deliberately.** Making the analyser emit them is
analyser work with its own measurement; recording them as a known, counted gap is what this
amendment is for. A check that is switched on and immediately silenced by fixing the label to
match the code would have proved nothing.

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
