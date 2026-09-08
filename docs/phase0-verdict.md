# Phase 0 · Go / no-go

**Date:** 2026-09-08 · **Task:** T3.8 · **Status:** signed
**Measurement of record:** `measurements/t3_6_full_run.json`
**Engine:** `3c5b95b` · 36 packages · 258 labelled edges · 439 tests

---

## Verdict: **GO**, conditional on one thing that has not happened yet.

The question this phase existed to answer was narrow and falsifiable: **does column-level
lineage through real procedural Oracle PL/SQL reach ≥95% band-1 value-flow precision against
hand-labelled ground truth?** Below that number the regulatory positioning is dead, because
a filing that is wrong 1 time in 15 is worse than no filing.

**It reaches 96.2%.** Every kill criterion written down before the work started now has a
measured number beside it, and none has triggered.

**And that number is a ceiling, not an estimate.** The corpus is synthetic and I wrote it,
knowing what the analyser would have to handle. No production package was ever obtained.
Until one is, this result licenses *continuing*, not selling. Those two caveats are the
verdict, not a footnote to it — they are restated in full below because a verdict whose
qualifications live in an appendix is a verdict designed to be quoted without them.

---

## Every kill criterion, re-evaluated (T3.8a)

| Criterion | End of week 2 | **Final** | Verdict |
|---|---|---|---|
| Band-1 value precision < 95% → regulatory positioning dead | 95.7% | **96.2%** | **PASS** |
| Band-1 value recall < 85% | 100% | **96.2%** | **PASS** |
| Parse coverage < 70% on real code → parser strategy wrong | 94.7% | **76.1%** | **PASS** |
| Dynamic SQL > 30% of statements **and** unrecoverable | not measured | **5.0% of statements, 50% recovered** | **PASS** |
| Interprocedural analysis does not terminate at a usable depth | terminates | terminates, cap declared | **PASS** |
| *(new, T3.1)* Any edge produced from a refused statement | — | **0 of 11 refusals** | **PASS** |
| *(new, T3.6)* Tier C or D above 70% → evidence story fails | — | **0.0%** | **PASS** |

Seven criteria, seven passes, nothing pending. Two of them did not exist when the phase
started and were added because the work exposed a way to fail that nobody had written down.

**Three of these numbers moved in the wrong direction and are still passes.** Saying so
matters more than the passes:

- **Parse coverage fell 94.7% → 76.1%**, and that fall is the T3.1 classifier working.
  Refusing is not free: every refusal costs exactly one statement of coverage. A classifier
  that abstained from everything would report 0% coverage rather than 100% precision. The
  floor is 70% and there are **5.6 points of headroom** — the next construct added to the
  refusal register costs coverage too, and this is the criterion closest to triggering.
- **Band-1 recall fell 100% → 96.2%** because the denominator changed. Week 2 measured
  against 149 edges; this measures against 258, including the fourteen hardest packages.
- **Band-1 precision has one point of headroom.** 96.2% against a floor of 95% is one false
  positive from a much harder conversation.

---

## What the number actually says

| band / flow | precision | recall |
|---|---|---|
| 0 value | 100% | 100% |
| 0 filter | 100% | 88.4% |
| **1 value — the gate** | **96.2%** | **96.2%** |
| 1 filter | 100% | 90.9% |
| 2 value | 100% | 69.4% |
| 2 filter | 100% | 80.6% |

**One false positive corpus-wide** (`b1_03`, a transform class computed per statement where
it should be per source). **Zero forbidden edges produced** — of 29 rules naming the exact
wrong answer each adversarial package was built to elicit, none fired.

**Band 2 is the honest weak spot.** 100% precision and 69.4% recall means: everything it
says is right, and it stays quiet about a third of what is there. That is the correct
direction for the failure to point — a missing edge is survivable, an invented one is not —
but a third is a lot, and closing it is phase-1 work.

---

## The caveats, stated in the verdict (T3.8c)

**1 · The corpus is synthetic and self-authored.** 38 files, written by me, knowing what
the analyser would have to handle. I mitigated this where I could — the database is the
referee for ground truth (ADR-0001 §1), labels are written before the analyser that scores
them, and 29 forbidden-edge rules name the wrong answers in advance — but none of that
removes the fundamental problem: **real legacy PL/SQL is consistently worse than anything
anyone writes on purpose.** Every number here is an upper bound.

**2 · No real production package was ever obtained.** The plan named this as the
highest-value input available. It has human latency, it did not arrive, and no amount of
engineering substitutes for it. **This is the single largest unknown in the verdict.** The
first honest test of this analyser has not been run.

**3 · 145 of 258 labels are `source_read`** — 56%, read from source rather than settled by
execution. That share got *worse* through week 3 exactly as predicted, because the hardest
packages to exercise are the ones where a wrong label costs most. 92 are `observed` and 21
`adjudicated`. The answer key is not independent of me in the way the precision figure
implicitly claims.

**4 · Execution evidence is bounded by an instance uptime measured in minutes.** T3.5b
captured a real witness and learned four things the hard way: `V$SQL` is memory rather than
a log and ages out in minutes; `MODULE`/`ACTION` is a sticky session attribute that tagged
`s7` with statements it does not contain; erasing literals when matching statement shapes
reported an unexercised branch as exercised; and DDL is absent from `V$SQL` entirely, which
would have reported `s4`'s regulated data movement as dead code. All four are now defended.
**Only 5 of 37 procedures set the instrumentation at all.** A production capture needs AWR
(`DBA_HIST_SQLSTAT`, separately licensed) or a job that persists `V$SQL` before it ages out.
**That is a phase-1 cost line, not a detail.**

**5 · Log recovery is conditional on instrumentation the customer either has or does not.**
Statement-shape attribution was demoted from high confidence on evidence: with session
attributes stripped it attributed 2 of 5 statements to the *wrong* procedure and identified
none correctly. Without `MODULE`/`ACTION` the system attributes nothing — by design.

**6 · Third-party corpora were never fetched.** The plan named OFBiz and ERPNext; neither
contains Oracle PL/SQL. No external validation of any kind has been performed.

**7 · The IR's match key is under-specified, and six packages say so.** `s2`, `b2_05`,
`b1_09` and `u1` need origin in the key; `b1_03` and `s7` need guard. In `u1` the wrong
answer is character-for-character the *required* answer, differing only by line number, so
it cannot even be written down as a forbidden rule. Fixing it changes every number in this
phase, which is why it is recorded rather than done. See `docs/ir-v0.md`.

---

## What was proven, and what merely was not disproven

**Proven, with a number behind it:**

- Column-level lineage through procedural PL/SQL is achievable at the precision the
  regulatory pitch requires — on this corpus.
- The evidence discipline is implementable, not just describable. 100% of edges are Tier A,
  the refusal classifier emits zero edges from statements it flagged, and the coverage
  statement is generated from the run rather than written.
- The hard constructs are tractable. Triggers attach to the table and are inherited by every
  writer; constant dynamic SQL is recovered and stays band 2; `EXCHANGE PARTITION` is
  lineage-bearing; views resolve to base tables from three different directions through one
  shared resolver.
- **The silent-failure failure mode is real and catchable.** Six of eight adversarial cases
  score clean, and each was caught by *labelling before analysing* — which found something
  every single time.

**Not disproven, which is a weaker claim and should read as one:**

- That this survives contact with real code.
- That 96.2% holds on a corpus somebody else wrote.
- That the `source_read` share can be driven down without a much larger observation harness.

---

## The condition on the GO

**Obtain one real production PL/SQL package and re-run this measurement before any further
feature work.** Not two weeks of hardening first — the measurement first. If band-1 value
precision holds above 95% on code nobody here wrote, the positioning is real. If it does
not, everything above is an artefact of a corpus I authored, and better to learn that from
one package than from a quarter of engineering.

Second, in order: **decide the match key** (deviation 2 in `docs/ir-v0.md`), because it
moves every number and every month of delay makes it more expensive.

> **Addendum, 2026-09-09 — the second item is now decided, and the answer was no.**
> The verdict body above is left exactly as signed; this note records what happened next.
>
> The match key was measured rather than argued (ADR-0001 amendment 1b). **Putting origin in
> it was rejected**: it caught no fabrication and cost five false positives, because
> multi-unit origin is the normal output of interprocedural summarisation and trigger
> inheritance. The guard half of the residual was already closed by amendment 1a. **Every
> number in this verdict is unchanged** — the measurement was re-run and every cell is
> identical.
>
> So caveat 7 below stands but shrinks: the hole is real and **latent**, not live, and the
> fix is a direct origin assertion in the *label* format rather than a match-key change.
> **The condition on the GO — one real production package — is unaffected and still
> outstanding.** It is now the only thing ahead of feature work.

---

## Signed

Analysis, corpus, ground truth and implementation: **Claude (Opus 5)**, working with
**Abhay Sisodia**, 2026-08 to 2026-09-08.

Reproduce with:

```
.\.venv\Scripts\python.exe -m lineage.cli measure --witness evidence/witness.json
```

Every figure above comes from that command at commit `3c5b95b`. The measurement carries its
own corpus, dictionary and config fingerprints, so a changed number can always be attributed
to a changed input rather than to drift.
