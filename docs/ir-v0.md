# IR v0 · The lineage fact, written down

**Date:** 2026-09-08 · **Status:** straw-man, argue with it · **Task:** T3.7
**Measured against:** `measurements/t3_6_full_run.json` — 36 packages, 258 labelled edges
**Reconciled:** 2026-09-09 — deviation 2 and the "most likely wrong" ranking, against
ADR-0001 amendment 1b. The document was argued with, as intended, and lost.

The IR is the compounding asset. A new source dialect becomes a new parser rather than a
rewrite; when migration becomes a product, transpilation is IR out to a target dialect.
Everything else in this system is replaceable — this is the part that has to be right, and
it is the part that is most expensive to change later.

So this document exists to be **attacked**. Every field below earns its place by naming the
corpus package that forced it. A field justified by "it seemed useful" is a field that will
be wrong in a year and load-bearing by then.

---

## The schema

```python
class Node:
    kind: COLUMN | VARIABLE | RELATION | PROCEDURE | STATEMENT | BOUNDARY | LITERAL
    name: str                       # upper-cased on construction

class Origin:
    unit: str                       # program unit, e.g. PKG_POLICY.LOAD
    line: int

class IREdge:
    source:      Node
    target:      Node
    flow:        VALUE | FILTER                                   # default VALUE
    transform:   IDENTITY | DERIVED | AGGREGATED | CONDITIONAL     # default IDENTITY
    band:        int  (0..3)
    mechanism:   AST | DEF-USE | CFG | LOG | INFERRED
    tier:        A | B | C | D                                     # default A
    guard:       str | None                                        # default None
    origin:      Origin                                            # REQUIRED
    unexercised: bool | None                                       # default None
    valid_from:  datetime                                          # sentinel in phase 0
    valid_to:    datetime | None                                   # None = open interval
    tx_from:     datetime                                          # sentinel in phase 0
```

Two derived keys, and the distinction between them is the single most contested decision
in the model:

```python
match_key() = (source, target, flow, transform)                     # what decides a hit
identity()  = (*match_key(), guard, origin)                         # what makes a fact distinct
```

---

## Why `mechanism` and `tier` are separate (T3.7b)

They are asked in the same breath and they answer different questions.

- **`mechanism` is *how we found it*.** A provenance field. `AST` means a resolved parse
  path; `DEF-USE` means a def-use chain across statements; `LOG` means the database told
  us. It is a fact about our method.
- **`tier` is *how well it is corroborated*.** An evidence-strength field, and — critically
  — assigned **by evidence type, never by model confidence**. Tier A is a resolved AST
  path. Tier B is code, runtime and profile agreeing. Tier C is model inference reproduced
  by a second, different path. Tier D is model reasoning alone, and never enters a signed
  packet.

**Collapsing them is the obvious simplification and it destroys the product.** The whole
regulatory pitch is *evidence*, and evidence has a type. If mechanism decided tier, then
`AST` would imply Tier A permanently — and an AST-derived edge that the runtime
*contradicts* would still read as the strongest thing we have. That is the exact failure
the tiering exists to prevent. Two fields let an edge say "found by parsing, and nothing
has corroborated it", which is the truth about 238 of 238 edges in this corpus.

The current run makes the point concretely: mechanism is `{AST: 215, DEF-USE: 23}` and tier
is `{A: 238}`. Two mechanisms, one tier. A single field could not have said that.

**What tier is not.** It is not a confidence score, it is not a probability, and it must
never be computed from one. The register's rule, kept: *evidence tiers by evidence TYPE,
never by model confidence.*

---

## Every deviation from the spike's draft schema (T3.7c)

Each row names the package that forced it. Nothing here is preference.

### 1 · The literal rule — and three node kinds that have not earned their place

**Forced by:** `b0_05_alias_chains`, `s1_synonym_redirect`, `s6_updatable_view`.

`UPDATE customer_target SET is_active = 1` writes a column from **nothing**. The convention
settled on is that **a literal supplies a value and produces no edge**, because there is no
upstream to trace. `USER` and `SYSDATE` in `s6`'s audit insert are the same case.

That rule produced a finding in T3.6: `s1`'s only write is a literal, so
`DW_DIM_CUSTOMER_V2` correctly has no writer in scope, and the coverage statement reports it
as an orphan upstream rather than as a missing edge.

**But the `LITERAL` node kind itself is dead, and so are two others.** Counted across the
whole corpus:

| kind | emitted by the analyser | used in labels |
|---|---|---|
| `COLUMN` | yes (350 endpoints) | 425 |
| `RELATION` | yes (92) | 109 |
| `VARIABLE` | yes (34) | 40 |
| `LITERAL` | **never** | **never** |
| `BOUNDARY` | **never** | **never** |
| `PROCEDURE` | **never** | **never** |
| `STATEMENT` | internal use only | never |

Three of seven node kinds are declared and unused. **This document exists to say so.** A
field justified by "it seemed useful" is exactly what the opening paragraph promises to
attack, and the honest reading is that `LITERAL` was added in anticipation of a rule that
turned out to need *no node at all*. v1 should delete `LITERAL` and `PROCEDURE` unless
something concrete claims them.

**`BOUNDARY` is a different and worse problem — a real schema inconsistency.** The concept
is entirely load-bearing: 71 boundaries are declared in the current run, T3.6 counts them,
and "declaring where knowledge stops is the product" is the whole regulatory pitch. But they
are carried as a **list of free-text strings on the analysis result**, not as nodes in the
graph. So a boundary cannot be an edge endpoint, cannot be queried alongside the lineage it
bounds, and the coverage statement has to recover its structure by string-matching a shared
marker constant.

That is the largest single gap between what this schema *says* and what the code *does*, and
it is listed below as a v1 change rather than papered over here.

### 2 · `identity()` is not `match_key()`

**Forced by:** `b1_03_loops` (accumulation vs decay of the same variable), `b1_09_exception_handlers`
(the same write on the happy path and inside `WHEN OTHERS`), `s7_unexercised_branch` (the EU
filter and the APAC filter, character for character identical).

These are pairs of **genuinely different facts sharing a match key**. Under one key a
package can only ever be credited with one of each pair and the other is a permanent miss
no analyser could fix. ADR-0001 amendment 1 records the full argument; the schema
consequence is that the ledger keys on `identity()` and scoring keys on `match_key()`, and
they must not be conflated.

**The obvious fix was rejected on measurement.** Putting `guard` in the match key satisfies
amendment 1 by breaking ADR-0001 §5: a mis-stated guard would produce a miss *and* a false
positive, dragging string-comparison noise into the gate. T2.4 measured that noise at 0/5
purely on phrasing — `P_REGION <> 'EU'` against `NOT (p_region = 'EU')`.

**Origin was excluded on measurement too.** Label and analyser agree on the origin *unit*
for 160 of 170 matched edges but on the *line* for only 12. Requiring line would collapse
recall for a reason unrelated to lineage being right.

**The residual cost, stated rather than hidden.** Six packages need something the v0 match
key deliberately does not carry — `s2`, `b2_05`, `b1_09` need origin; `b1_03`, `s7` need
guard; `u1_loud_constructs` needs origin twice over, where the `$ELSE` arm and the naive
`XMLTABLE` binding are character for character the *control's required edge*.

> **Reconciled 2026-09-09 — half of this residual was already closed, and the other half
> does not close the way this section proposed.** Both corrections come from measurement,
> and both are recorded in **ADR-0001 amendment 1b**.
>
> **The guard half is closed.** `b1_03` and `s7` were resolved by amendment 1a's two-stage
> pairing, which landed in T3.0b before this document was written. Re-measured across all
> 258 labels: **zero labelled edges are unreachable** once guard pairs within a match-key
> group. The four packages whose labels collide on the match key — `b1_02`, `b1_03`,
> `b1_09`, `s7` — every one is separated by its guard.
>
> **The origin half was tried and rejected.** This section's own recommendation — *"v1
> should probably carry origin in the key with a unit-level comparison"* — was built behind
> `scoring.origin_in_dedup` and measured against the full corpus. **It caught no fabrication
> and manufactured five false positives.** `s2` and `b2_05` refuse the offending statement
> before it emits an edge, so there was never a second claim to separate; meanwhile a callee
> summarised at three call sites (`b1_08`) and a trigger edge inherited by its table's
> writer (`b1_09`, `s3`) each became false positives. **Multi-unit origin is the normal case
> for interprocedural summarisation and trigger inheritance, so origin cannot distinguish a
> second *route* to one fact from a second *fact*.**
>
> **What the residual actually needs** is a **direct origin assertion in the label format** —
> *"this edge must come from this unit"* — scored beside the forbidden-edge rules. That is a
> label-schema change, not a match-key change, and it leaves every number in the phase
> untouched.

### 3 · `origin` is mandatory, not optional

**Forced by:** nothing in the corpus — this one is a principle, and it is the only one.

A fact whose origin cannot be stated is not evidence. "Which line produced this" is the
first question anyone asks when challenging a trace, and a schema that lets the answer be
`None` guarantees that some edges will not have it.

**It also closed a whole category of coverage gap.** T3.6 was asked to count *unattributed
writers*. That signal **cannot arise** in this IR: an edge either names its unit and line or
does not exist. The coverage statement reports it as impossible rather than as zero,
because an always-empty list implies a check that never runs.

### 4 · `tx_from` and `valid_from` are fixed sentinels, not `now()`

**Forced by:** the reproducibility test.

Phase 0 has no ledger, so nothing supplies a real transaction time. A wall-clock default
makes two runs of the same analyser over the same corpus produce different bytes, and
byte-identical output is what lets a changed number be attributed to a changed input rather
than to noise. A real run overrides them from the run record.

**Bitemporality is in v0 on purpose, unused.** Edges are never mutated — they are
superseded: close the old validity interval, append the new one. An auditor never asks what
is true now; they ask whether it was true when you said it was, and only an append-only
record answers that. The architecture is explicit that retrofitting this later is a rewrite,
so the semantics are designed in now even though phase 0 never exercises them.

### 5 · `flow` is an axis, not a boolean or a subtype

**Forced by:** ADR-0001 §2, and every band-1 package.

`WHERE last_login > v_cutoff` is lineage — a policy table silently governing which rows
load is exactly the finding no table-level tool produces. But it is a **different claim**
from a value copy, and one number over both hides which half broke. The current run makes
that vivid: band 2 is 100%/69.4% on value and 100%/80.6% on filter. Blended, those become a
single number that describes neither.

### 6 · `unexercised` is `bool | None`, not `bool`

**Forced by:** T3.5b, against a live database.

The register wanted a separate axis from tier, and got one. What the corpus did *not*
predict is that two states are not enough. `None` means **no execution window was
examined**. Folding it into `False` claims every edge ran — the exact claim this axis exists
to avoid. Folding it into `True` reports the estate as dead code.

**Absence of a witness is not evidence of non-execution.** The current run carries 226 edges
at `None`, 6 at `True` and 6 at `False`, and the report renders the first group as *no
execution evidence* rather than as a count of edges that did not run.

### 7 · `band` is on the edge, not on the package

**Forced by:** `s4_partition_exchange`, `b2_01_dynamic_constant`.

One file routinely carries edges of several bands. `s4` is band 0 for its staging load and
band 2 for the exchange in the same procedure; `b2_01` recovers dynamic SQL that is
*analysed* exactly like static SQL but stays band 2, because the path runs through
`EXECUTE IMMEDIATE` whether or not we could see through it. ADR-0001 §6: the band is the
hardest construct on the edge's path. Banding by file would let band 0's score claim work it
did not do.

### 8 · `VARIABLE` is a node kind — variables are first-class

**Forced by:** `b1_01_local_variables`, `b1_07_package_variables`.

`ref_policy.window_days -> v_days -> v_cutoff` is two edges, not one. Collapsing to
endpoints hides *where* def-use broke, which is the entire diagnostic value of band 1 —
and band 1 is the band the phase gate is defined on.

### 9 · A boundary is a first-class output, not an error state

**Forced by:** `b2_06_db_links`, `s2_schema_context`, `u1_loud_constructs`.

A DB link, a refused schema, a wrapped body. Modelling any of these as an *error* would push
them out of the output and into a log, where nobody counts them — and T3.6 exists to count
exactly these. The current run declares 71.

The deviation from the spike's draft is that boundaries are **returned alongside the edges
and scored**, not raised. A package's answer key states the boundaries it must declare, and
a silently omitted one is a failure in the same way a missing edge is.

**Its modelling is unfinished** — see deviation 1. The concept is right and the
representation is a string list.

---

## What v0 does *not* have, and why

- **No path/edge distinction.** Phase 0 emits edges only. Path composition is a query-time
  operation over hops that each stand on their own evidence; composing at emission time is
  how an unprovable edge acquires a provable-looking origin (`s4`'s forbidden shortcut).
- **No confidence score.** Deliberate. See tier, above.
- **No row identity.** `:NEW` and `:OLD` are not distinguished — different rows of one
  relation, and the IR carries no row identity. Same limit `sq_06` recorded for self-joins
  before the analyser existed.
- **No column-level data type.** Nothing in the corpus needed it; adding it speculatively
  would be the exact failure this document is written to prevent.

---

## The four things most likely to be wrong

*Re-ranked 2026-09-09. The match key was #1 here until it was measured; it is now #4, and
the reason it moved is worth more than its old position was.*

1. **Boundaries are strings, not nodes.** The concept carries the regulatory pitch and the
   representation cannot be queried, cannot be an edge endpoint, and forces the coverage
   statement to recover structure by string-matching. Largest gap between what this schema
   claims and what the code does — and now the largest open item outright.
2. **`guard` is a string.** Compared by a four-rule normaliser that stops well short of a
   solver, on purpose — a half-clever normaliser that silently equates two different
   conditions is worse than an honest one that reports a difference. But a string is not a
   condition, and anything past those four rules needs a real representation. Note that
   guard is doing *more* load-bearing work than this document assumed: it is the only thing
   separating the four match-key collisions in the corpus (deviation 2, reconciled).
3. **`band` conflates difficulty with construct class.** Currently "the hardest construct on
   the path", doing two jobs: bucketing the score, and describing the analysis. Those may
   need to separate.
4. **The match key is under-specified — but not in the way this document claimed.**
   Demoted on measurement (ADR-0001 amendment 1b). The guard half of the residual was
   already closed by amendment 1a, and putting origin in the key was built, measured, and
   **rejected**: it caught nothing and cost five false positives. What remains is real but
   **latent** — `s2` and `b2_05` could in principle fabricate an edge identical to a
   legitimate one, and today they refuse it before emission instead. The fix is a direct
   origin assertion in the *label* format, which moves no numbers at all.

   **The general lesson is the one worth keeping:** this document called the match key "the
   most likely v1 change" from reading the code, and one measurement moved it to last.
   Everything else on this list is also unmeasured.

## Dead weight to remove in v1

`LITERAL` and `PROCEDURE` node kinds — declared, never emitted, never labelled. Listed here
rather than quietly deleted so the removal is a decision with a reason attached.
