# Week 3 Tracker — Undecidables and the Verdict

**Companion to** [Phase0_tracker.md](Phase0_tracker.md) and [Week2_tracker.md](Week2_tracker.md).

**Status:** not started · **Created:** 2026-09-06 · **Baseline commit:** `8772b46`
**Entering with:** band-1 value **95.7% / 100%** · 237 tests · 21 label sets, 149 edges

---

## Objective

**Close the phase with a defensible verdict.** Week 2 produced a number. Week 3 has to make
that number *mean* something by proving the analyser knows what it cannot do.

The spike doc's own words for this week: *dynamic SQL log-recovery experiment · the
unanalysable-construct classifier · full scoring run per band · IR v0 written down · go/no-go
against the kill criteria.*

Three of those five are partially done already (T2.8 measurement, T2.9 log recovery, the IR
built at T2.1). **What is genuinely unbuilt is the honesty machinery** — the classifier, the
declared boundaries, `unexercised`, and the coverage statement — plus band 2, which is at 0%.

---

## The two things that make this week different from week 2

1. **Week 2 optimised a number. Week 3 must resist optimising it.** The classifier's job is
   to *refuse*, and every refusal costs recall. A week-3 change that raises precision by
   abstaining more is not an improvement; the honest-abstention metric is pass/fail
   precisely so it cannot be traded against the gate.

2. **Nothing here is a percentage to maximise except band 2.** T3.1, T3.4 and T3.5 are
   correctness properties — zero silent guesses, every silent failure declared or passing.
   A 97% honest-abstention rate is a failure, not an A-minus.

---

## Sequencing rule, carried from weeks 1 and 2

**T3.0 comes first.** Fourteen packages — six band-2, eight silent-failure — have **no ground
truth at all**. They cannot be scored, so today they are invisible to every metric except
band 2's 0% recall, which rests on 11 edges labelled inside *other* packages' key files.

Label before analysing. It has caught something every single time:

| Week | What labelling first caught |
|---|---|
| 1 | A trigger writing `DIM_CUSTOMER.LIFETIME_VALUE` that reading the source never showed |
| 1 | `s5_positional_union` was a *loud* failure — Oracle rejected it, so it tested nothing |
| 2 | ADR-0001 amendment 1 — the match key collapses two genuinely different edges |
| 2 | Every band-1 branch needed its own precondition to be observable at all |

---

## T3.0 — Ground truth for band 2 and the silent suite *(do this first)*

The answer key for the fourteen packages that currently have none.

- [x] **T3.0a** Observe all six band-2 packages; record what each actually writes
- [x] **T3.0b** Observe all eight silent-failure packages — **including the wrong answer each
      one is designed to elicit**, recorded explicitly as the thing that must not appear
- [x] **T3.0c** Label `b2_01` dynamic constant, `b2_02` dynamic concatenated, `b2_03` DBMS_SQL
- [x] **T3.0d** Label `b2_04` metadata-driven ETL, `b2_05` triggers, `b2_06` DB links
- [x] **T3.0e** Label `s1`–`s8`, each with its expected *boundary* as well as its edges
- [x] **T3.0f** Record which labels observation could NOT settle, and why

**Done when:** all 35 corpus packages have a committed label set; every edge records its own
provenance; `pytest` hash-checks all of them; and the label count and provenance split are
stated explicitly rather than buried.

**New in this key: the negative label.** A silent-failure package is defined by the plausible
wrong edge it invites. `s1` invites `column:CUSTOMER_TARGET.*`; `s5` invites name-matched
UNION arms. **Write those down as forbidden edges**, so the harness can fail on their
*presence* rather than only on the true edge's absence. Today `test_band0.py` asserts a few
of these ad hoc; they belong in the key.

**Watch for:** `b2_06` (DB links) and parts of `b2_04` cannot be observed at all — there is
no far-side database and no config table populated. Those labels are `source_read` and the
expected output is a **boundary**, not an edge. Do not quietly relabel a boundary as a miss.

---

**Status: CLOSED. The number fell hard, exactly as forecast, and for two different reasons
that had to be separated before either could be believed.**

**The key: 21 → 35 packages, 149 → 231 edges.** Bands 135 / 43 / 53. Provenance 90 observed,
21 adjudicated, **120 `source_read`**. 19 edges flagged `unexercised`, 25 forbidden rules,
10 declared boundaries. `tests/test_forbidden_edges.py` (13 tests); suite 237 → 295 green.

### The measurement, before and after

| band / flow | before (149 edges) | after (231 edges) |
|---|---|---|
| 0 value | 100% / 100% | **100% / 100%** |
| 0 filter | 100% / 90.9% | 97.3% / 85.7% |
| **1 value — THE GATE** | **95.7% / 100%** | **75.8% / 100%** |
| 1 filter | 100% / 86.7% | 100% / 88.9% |
| 2 value | n/a / 0% | n/a / 0% (30 edges) |
| 2 filter | n/a / 0% | n/a / 0% (23 edges) |

**The band-1 precision kill criterion is TRIGGERED at 75.8%.** It should not be acted on
yet, and the reason is in the next section — but it is recorded as triggered rather than
explained away, because the whole point of writing kill criteria down in advance is that
they are not renegotiated when they fire.

### Finding 1 — every new false positive is one defect, and it is statement plumbing

All eight new band-1 false positives are the same shape:

```
b2_02   P_COLUMN -> V_SQL     b2_03   V_STMT  -> V_STMT
b2_02   P_REGION -> V_SQL     b2_03   V_CURSOR-> V_ROWS
b2_02   P_SUFFIX -> V_SQL     b2_04   V_SQL   -> V_SQL
```

Def-use follows string concatenation into a variable that holds **statement text**, and
emits it as data lineage. `v_sql := 'UPDATE ' || p_column || ' = 1'` is a true def-use fact
and not a lineage fact: `p_column` supplies part of a *statement*, and nothing downstream
carries its value into a column.

The distinction is not "variables are noise" — ADR-0001 §3 makes variables first-class, and
`ref_policy.window_days -> v_days -> v_cutoff -> row filter` is exactly the chain this phase
exists to trace. The line is **data-carrying versus text-carrying**: `v_cutoff` ends up
selecting rows, `v_sql` ends up at `EXECUTE IMMEDIATE`. **T3.2 owns this.**

### Finding 2 — the band-2 denominator tripled, and that is the honest part

Band 2 went from 11 labelled edges to **53**, still at 0% recall. Nothing got worse; the
gap was always this size and was previously being measured against a fraction of itself.

### Finding 3 — observation cannot settle everything, and three cases proved it

| Package | What observation could not decide |
|---|---|
| `s8` | **identity vs aggregated are indistinguishable on this seed data.** Three orders in three distinct customer-months means every GROUP BY group holds one row, so `SUM(x) = x` — same row count, same values, same candidate sources. ADR-0001 §4 makes a wrong transform class a MISS, so those two labels are `source_read` and stay that way |
| `s4` | The staging load is invisible after the fact — the exchange had already emptied the table by snapshot time. Observation sees end states, not intermediate ones |
| `b2_02` | `IS_ACTIVE` showed nothing on the first run because the rows already held 1. Idempotent writes are invisible; re-run after nulling the column |

### Finding 4 — the config table asserts an edge that has never existed

`b2_04`'s mapping 3 declares `STG_CUSTOMER.EMAIL -> DIM_CUSTOMER.EMAIL`, is enabled, and
fails on **every** run: the generated INSERT omits the NOT NULL `cust_id`, and
`WHEN OTHERS THEN NULL` swallows it without trace. It is labelled **forbidden**, not
missing. Reading configuration as evidence reports *intent*, and intent is precisely what
this platform must never present as mechanics.

### Finding 5 — the match key is under-specified, and now four packages say so

Three wrong answers **cannot be expressed as forbidden edges at all**, because each has the
same `(source, target, flow, transform)` key as a correct one:

| Package | The inexpressible failure | Missing from the key |
|---|---|---|
| `s2` | statement 2 bound against the local schema | origin |
| `s7` | the APAC filter, identical to the EU one | guard |
| `b2_05` | trigger edges attached to the caller | origin |
| `b2_02` | the trigger's filter edge duplicating line 30's | origin |

ADR-0001 amendment 1 is implemented in `ir/model.py` and still absent from `harness/labels.py`.
It was one known false positive when week 3 opened; it is now **the reason three silent
failures cannot be tested at all**. That reframes it from a scoring tidy-up into a
correctness gap, and T3.3 and T3.5 both need it before they can assert anything.

### What went right

**Zero forbidden edges produced, across 25 rules.** The analyser committed none of the named
wrong answers — no synonym misbinding, no temp-table fusion in either direction, no
name-matched UNION arm, no correlated predicate read as a value source. That is a real
result and it is invisible in precision and recall, which is why the mechanism was worth
building.

**One rule did miss.** `s6`'s view-as-target rule guessed which source column would appear
and guessed wrong — the analyser emits the view on *both* ends. A second rule was added
after seeing the output, flagged as such: adding a forbidden rule can only lower a score,
never raise it, which is the test to apply to any post-hoc change to an answer key.

---

## T3.1 — The unanalysable-construct classifier

*The spike calls this out as a deliverable in its own right: "the classifier that says 'I
cannot resolve this construct' is itself a deliverable. never emit a guessed edge."*

- [ ] **T3.1a** Enumerate the refusal taxonomy — one code per reason, closed list
- [ ] **T3.1b** Detect and declare every construct in the corpus the analyser cannot resolve
- [ ] **T3.1c** **Cross-check flags against emitted edges** — zero edges may exist for any
      statement the classifier flagged
- [ ] **T3.1d** Measure the false-abstention rate: statements refused that were analysable
- [ ] **T3.1e** Loud-failure constructs from the register get explicit refusals, not crashes:
      `MODEL`, `PIVOT` with a subquery column list, `MATCH_RECOGNIZE`, `CONNECT BY`,
      conditional compilation, `XMLTABLE`/`JSON_TABLE`, wrapped PL/SQL

**Done when:** 100% of injected known-unresolvable constructs are flagged; **zero** edges
exist for any flagged statement, verified by cross-checking flags against emitted edges
rather than by inspection; the false-abstention rate is measured and reported.

**The verification method is the point.** "I inspected the output and it looked right" is how
this passes while being broken. The check must be mechanical: for every flagged statement id,
assert the edge set produced from it is empty.

**Watch for the two-sided failure.** Refusing too little is a silent guess. Refusing too much
is a quiet recall collapse that *looks* like discipline. Both need a number.

---

## T3.2 — Dynamic SQL, constant string

- [ ] **T3.2a** Constant propagation through the CFG to the `EXECUTE IMMEDIATE` site
- [ ] **T3.2b** Parse the recovered text normally and emit edges at mechanism `AST`
- [ ] **T3.2c** Refuse — loudly — where the string is not fully constant on every path
- [ ] **T3.2d** Concatenation of constants folded; concatenation with a variable refused

**Done when:** `b2_01_dynamic_constant` produces edges at mechanism `AST`, indistinguishable
from static SQL, **and `b2_02_dynamic_concatenated` produces none** — only a declared boundary.

> Cheap win — do this before reaching for the log.

**The trap:** partially-constant strings. `'UPDATE ' || v_table || ' SET x = 1'` has constant
fragments and a variable table name. Folding what is available and guessing the rest is
exactly the plausible wrong answer this phase keeps finding. Must-analysis, not may.

---

## T3.3 — Triggers *(the band-2 gap, worth 11 edges)*

Not a separate task in the plan — it sits in the band-2 ladder row and in the register's
recoverable band. Broken out because **it is the single largest measured gap in the engine**.

- [ ] **T3.3a** Parse `CREATE TRIGGER` bodies as their own analysis units
- [ ] **T3.3b** Attach the resulting edges to the **table**, not to the caller
- [ ] **T3.3c** Any statement writing that table inherits the trigger's edges
- [ ] **T3.3d** `:NEW` / `:OLD` correlation names bound to the triggering row
- [ ] **T3.3e** Trigger timing and event carried as evidence (`BEFORE INSERT`, etc.)
- [ ] **T3.3f** `INSTEAD OF` triggers on updatable views — silent failure `s6`

**Done when:** band-2 recall is no longer 0%; `trg_recent_audit`'s write to
`DIM_CUSTOMER.LIFETIME_VALUE` is produced from an insert into `tmp_recent`; and the edge
carries the trigger as its origin rather than the calling procedure.

**Why attach to the table.** The register is explicit: *"parse separately and attach to the
table, not the caller. Any statement touching that table inherits the trigger's edges."*
Attaching to the caller is wrong twice over — it misses every *other* writer of that table,
and it claims the caller performed a write it never issued.

**Expect this to move the ceiling, not just the score.** Trigger edges are inherited, so a
mistake here multiplies across every writer rather than staying local. Precision risk is
higher than anywhere else in the phase.

---

## T3.4 — Silent-failure adversarial suite

The register's instruction: *"Build the adversarial corpus around the silent list, not the
loud one. Loud failures announce themselves during testing. Silent ones surface when a
customer catches you."*

All eight packages exist. **None is scored end to end**, and only three are defended.

| # | Package | State entering week 3 |
|---|---|---|
| s1 | Synonym redirect | Defended at T1.4; asserted by test |
| s2 | Schema context | Defended at T1.4 |
| s3 | Shared temp tables | Defended at T2.5; zero fused edges asserted |
| s4 | Partition exchange | **Not handled** — DDL is not read as lineage-bearing |
| s5 | Positional `UNION` | Defended at the complex-SQL band (`sq_05`) |
| s6 | Updatable views / `INSTEAD OF` | **Not handled** — view text captured, never expanded |
| s7 | Unexercised branches | **Not handled** — see T3.5 |
| s8 | Aggregation vs row-level | Defended by the transform class in ADR-0001 §4 |

- [ ] **T3.4a** Score all eight against the T3.0 key
- [ ] **T3.4b** `s4` — treat `ALTER TABLE … EXCHANGE PARTITION` as lineage-bearing, or declare
- [ ] **T3.4c** `s6` — resolve views to base tables through the captured view text
- [ ] **T3.4d** `s7` — closed by T3.5
- [ ] **T3.4e** Every case becomes a permanent regression test **the day it is written**

**Done when:** every case either passes or its failure is explicitly declared in the report —
and each has a named test. A declared failure is an acceptable outcome here; a silent one is
not.

---

## T3.5 — `unexercised` as a first-class edge state

*Register: "Not a tier — a separate axis. An edge can be Tier A (provably in the code) and
never observed running. That combination is itself a finding, and nobody else reports it."*

- [ ] **T3.5a** Model as a separate axis on the IR edge, orthogonal to tier
- [ ] **T3.5b** Populate from observation — an edge whose statement never appeared in a run
- [ ] **T3.5c** Harness reports the count
- [ ] **T3.5d** `s7_unexercised_branch` produces a Tier A **and** unexercised edge

**Done when:** an edge can be Tier A *and* never observed running, and the harness reports the
count.

**The failure this prevents:** the EU path ran 9,120 times and looks strong; the APAC branch
exists in code, never ran in the window, and looks weak or absent. Demoting it by tier says
*we are unsure it is real*. It is provably real — it has simply never fired. Those are
different facts, and only one of them is honest.

**Already half-true in the corpus and unrecorded:** week 2's labels carry `unexercised: true`
on the exception-handler and rare-branch edges. The IR has nowhere to put it.

---

## T3.6 — Full scoring run

- [ ] **T3.6a** Precision, recall, parse coverage **per band** across all 35 packages
- [ ] **T3.6b** Honest-abstention check as pass/fail
- [ ] **T3.6c** Tier distribution examined explicitly
- [ ] **T3.6d** Boundary counts: declared, and the three "count what you were never given"
      signals — dangling references, unattributed writers, orphan upstream
- [ ] **T3.6e** Coverage statement generated from the run, not written by hand

**Done when:** all of the above are reported per band. **If 70% of edges land in Tier C or D,
the lineage technically works but the evidence story doesn't — flag it.**

**Expect the number to fall, and say so before it does.** T3.0 roughly doubles the label set
by adding the fourteen *hardest* packages. Band-1 value precision measured against 149 edges
and against ~250 edges are not the same claim, and the second is the one that counts.

---

## T3.7 — IR v0 written down

- [ ] **T3.7a** Schema documented as a straw-man to argue with
- [ ] **T3.7b** Rationale for keeping `mechanism` and `tier` separate
- [ ] **T3.7c** **Every deviation from the spike's draft schema explained by something the
      corpus actually contained**

**Done when:** the document exists and each deviation is justified by real code, not preference.

**Deviations to account for so far** (all forced by the corpus, all already implemented):

| Deviation | Forced by |
|---|---|
| `Literal` added as a node type | `b0_05`, constants written to columns |
| `identity()` ≠ `match_key()` | `b1_03` accumulation vs decay; `b1_09` happy path vs handler |
| `origin` mandatory, not optional | a fact whose origin cannot be stated is not evidence |
| `tx_from` a fixed sentinel, not `now()` | byte-identical reproducibility |
| `flow` (value / filter) as an axis | ADR-0001 §2 — filter influence is a different fact |
| `unexercised` as its own axis | T3.5 — pending |

---

## T3.8 — Go / no-go against the kill criteria

Written down before the work started, so the result is a measurement rather than an argument.

| Kill criterion | Measured (end of wk 2) | Verdict | Final |
|---|---|---|---|
| Band-1 precision < 95% → regulatory positioning dead | 95.7% | PASS | |
| Parse coverage < 70% on real code → parser strategy wrong | 94.7% | PASS | |
| Dynamic SQL > 30% **and** unrecoverable | not >30%; recoverability conditional | PARTIAL | |
| Interprocedural analysis doesn't terminate | terminates, cap declared | PASS | |

- [ ] **T3.8a** Re-evaluate every criterion against the T3.6 run
- [ ] **T3.8b** Signed one-page verdict
- [ ] **T3.8c** State the caveats *in the verdict*, not in an appendix

**Done when:** each criterion has a measured number beside it and a signed one-page verdict
exists.

**The caveats that must appear in the verdict, not below it:**

- The corpus is **synthetic and self-authored**. It was written knowing what the analyser
  must handle. Every number is a ceiling.
- **No real production package** was obtained. The plan named this as the highest-value
  input; it has human latency and it did not arrive.
- **Log recovery is conditional on instrumentation** the customer either has or does not.
  Without `MODULE`/`ACTION` it attributes nothing — by design, after it was measured
  attributing 2 of 5 to the wrong procedure.
- Third-party corpora were **never fetched**; the plan's named sources (OFBiz, ERPNext) do
  not contain Oracle PL/SQL.

---

## Standing rules

- **Never emit a guessed edge.** Refuse, declare, count.
- **Score per band and per flow.** Never blend.
- **Label before you analyse.** Not after.
- **Read any result as a ceiling.** Real legacy code is consistently worse.
- **New for week 3: abstention is not free.** Every refusal is measured as a refusal.

## Open risks entering the week

- **Band 2 is 0%** and T3.0 is about to make its denominator much larger.
- **The band-1 gate has 0.7 points of headroom.** Trigger edges are inherited by every writer
  of a table, so a trigger precision error is not local.
- **`harness/labels.py` still keys on `match_key()`** — ADR-0001 amendment 1 is implemented in
  the IR only. Fixing it is a scoring-format change and must be reported as one.
- **79 of 149 labels are `source_read`.** T3.0's fourteen packages are the least observable in
  the corpus, so that share will get worse, not better.
