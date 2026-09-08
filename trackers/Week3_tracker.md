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

## T3.0b — ADR-0001 amendment 1 in the scoring layer *(unplanned; T3.3 and T3.5 needed it)*

Amendment 1 was written in T2.0 and implemented in the IR at T2.1. The label format never
followed, and T3.0 turned that from a scoring tidy-up into a blocker: three silent failures
could not be expressed at all.

- [x] **T3.0b1** Two-stage matching — group by match key, pair within a group by guard
- [x] **T3.0b2** §5 preserved — a mis-stated guard still matches, reported only in the guard figure
- [x] **T3.0b3** Predictions deduplicated by scoring key before counting
- [x] **T3.0b4** Restore the four edges the v0 key could not hold
- [x] **T3.0b5** ADR-0001 amendment 1a written down, with the measurement behind it

**Status: CLOSED.** `tests/test_amendment_1.py` (8 tests); suite 295 → 303.

**The obvious implementation is wrong, and the wrong version passes a casual review.**
Putting the guard in the match key satisfies amendment 1 and breaks §5: a mis-stated guard
becomes a miss *and* a false positive, so guard-comparison noise lands directly on the
gate. T2.4 measured that noise at 0/5 on phrasing alone. `test_amendment_1.py` pins the
distinction explicitly, because nothing else would catch a regression to the easy version.

**Origin was excluded on evidence, not preference.** Label and analyser agree on the origin
**unit** for 160 of 170 matched edges and on the **line** for only 12. Requiring line
numbers — hand-read during labelling — would collapse recall for a reason unrelated to
lineage. Unit agrees far better, but its ten disagreements are a live modelling question:
when a view's predicate or a callee's expression is inlined, does the edge belong to the
caller or to the unit that wrote it? Putting an unsettled convention inside the gate would
move the headline number on a decision nobody has taken.

**Four edges restored**, each one a fact the corpus could not previously state:

| Package | The fact that had nowhere to go |
|---|---|
| `b1_09` | the `NO_DATA_FOUND` handler's filter edge — **already observed**, by giving a customer region `'XX'` |
| `b1_03` | the decay `v_total := v_total / 2`, identical to the accumulation but for its `WHILE` guard |
| `s7` | the APAC filter, identical to the EU one |
| `b1_02` | the APAC arm's correlation operand |

**`b1_02`'s restored edge raises precision**, so it is flagged in that file. The convention
— every operand of a predicate is a filter edge — was fixed in `b0_01` and applied
uniformly in `0b9a9e8`; this occurrence was skipped there for a mechanical reason, not a
judged one, because its key collided with the EU arm's and the duplicate validator would
have rejected it.

**Two defects surfaced that the old scoring had been hiding:**

1. **The analyser emits some edges twice.** Dict-keyed scoring silently kept one. They are
   genuinely one claim reached by two routes, so they are now deduplicated at the scoring
   boundary rather than in the analyser — the ledger legitimately wants both, each with its
   own origin.
2. **`b1_03`'s accumulation self-edge is classified `aggregated` and should be `derived`.**
   In `NVL(SUM(gross_amount), 0) + v_total`, `gross_amount` arrives through a SUM but
   `v_total` arrives through an addition. Transform class is per-source, not per-statement.
   Previously invisible: the wrong class collided with the right one under the v0 key.

| | before | after |
|---|---|---|
| band-1 filter recall | 88.9% | **90.9%** |
| band-1 value recall | 100% | **96.2%** — `b1_03`'s decay is now a target, and missed |
| band-1 value precision | 75.8% | 75.8% |
| guard accuracy | 88.2% of 17 | 100% of 22 |

**The gate did not move, and that is the correct outcome.** The eight false positives are
still the statement-text defect, untouched by any of this. Amendment 1 was never going to
fix the number — it made three untestable failures testable.

---

## T3.1 — The unanalysable-construct classifier

*The spike calls this out as a deliverable in its own right: "the classifier that says 'I
cannot resolve this construct' is itself a deliverable. never emit a guessed edge."*

- [x] **T3.1a** Enumerate the refusal taxonomy — one code per reason, closed list
- [x] **T3.1b** Detect and declare every construct in the corpus the analyser cannot resolve
- [x] **T3.1c** **Cross-check flags against emitted edges** — zero edges may exist for any
      statement the classifier flagged
- [x] **T3.1d** Measure the false-abstention rate: statements refused that were analysable
- [x] **T3.1e** Loud-failure constructs from the register get explicit refusals, not crashes:
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

### CLOSED — commit `t3.1`, `measurements/t3_1_refusals.json`

**Result.** 11 refusals across the corpus in 4 codes · **0 edges from any refused
statement** · **1 false abstention (9.1%)** · gate unchanged at **96.2% / 96.2%** ·
375 tests (was 346).

| | |
|---|---|
| Taxonomy | 9 codes, each with a written meaning a test asserts exists |
| Corpus | new package `u1_loud_constructs.sql` — 7 constructs, 1 control, 1 key |
| Coverage | **94.3% → 75.6%**, and that fall is the design working |

**Parse coverage fell 19 points and that is the deliverable, not a regression.** Refusing
is never free: every classifier refusal counts one more statement seen and one fewer
analysed. A classifier that abstained from everything would report 0% coverage rather than
100% precision. The floor is 70% and the number now sits 5.6 points above it — worth
saying plainly, because the next construct added to the register costs coverage too.

**Three defects the classifier found in the analyser, all by measurement:**

1. **`CONNECT BY` emitted three `x -> x` self-edges.** It parses cleanly, so the analyser
   walked the select list and produced edges that look exactly like an ordinary projection.
   One of them was `CONNECT_BY_ROOT root_name -> root_name [identity]`, where identity is
   the one transform class it certainly is not. This is why the classifier **gates** band 0
   rather than auditing it afterwards: an audit means the guessed edge was already built
   and something has to remember to throw it away.
2. **Conditional compilation emitted BOTH arms.** Two contradictory answers, each stated as
   fact, and nothing in the file says which one is in the compiled unit — `PLSQL_CCFLAGS`
   decides that at compile time. `$IF` is the one construct here that poisons its
   *neighbours* rather than itself, so it is refused as a line span.
3. **Wrapped PL/SQL was invisible to a statement-level pass.** It produces no statement at
   all, which is backwards — failing to parse is *more* reason to refuse, not less. Hence
   the source-level sweep. It also derails the grammar for every unit declared after it,
   which is why `u1`'s control sits above the wrapped body: with the control below it, the
   file proved nothing.

**One design decision reversed by the corpus.** The first draft of the construct list
refused any statement touching a DB link. `b2_06` refuted it: the local half of
`INSERT INTO dim_customer ... FROM remote_customer@crm_link` is fully provable — target
columns, projection and filter are all in the local text — and refusing it would have
destroyed seven labelled edges to buy nothing. **A remote reference is a boundary node, not
a refused statement.** `REMOTE_OBJECT` stays in the taxonomy with that written into it.

**The one false abstention is real and is left standing.** `s6`'s
`INSERT ... VALUES (:NEW.cust_id, USER, SYSDATE)` is refused by band 0 at line 39, and the
key has an edge there. The trigger analyser *does* produce that edge — but at a
body-relative origin line, so the harness cannot see the recovery and reports
`0 recovered`. Two analysers numbering lines against different origins is a modelling seam,
not a scoring bug, and it is recorded rather than papered over.

**`end_line` exists because the first version of the metric read 0.0%.** A refusal is
recorded at the line a statement *starts* on; a labelled edge carries the line its own
expression sits on. `b2_06` labels four projections at lines 21, 22 and 24 of a statement
beginning at line 20. Addressing a refusal by its first line alone made both T3.1 checks
silently vacuous.

**Also new:** a sixth kill criterion — *any edge produced from a refused statement* —
evaluated as pass/fail rather than as a percentage. A 97% honest-abstention rate is a
failure, not an A-minus.

**Recurring theme, sixth instance.** `u1` has two named wrong answers that cannot be
written down: the `$ELSE` arm and the naive `XMLTABLE` binding are both character for
character the control's required edge, differing only in origin. After `s2`, `b2_05`,
`b1_09` (origin) and `b1_03`, `s7` (guard), this is the strongest case yet for amending the
match key — here the right and wrong answers come from the same analyser on the same run.

---

## T3.2 — Dynamic SQL, constant string

- [x] **T3.2a** Constant propagation through the CFG to the `EXECUTE IMMEDIATE` site
- [x] **T3.2b** Parse the recovered text normally and emit edges at mechanism `AST`
- [x] **T3.2c** Refuse — loudly — where the string is not fully constant on every path
- [x] **T3.2d** Concatenation of constants folded; concatenation with a variable refused
- [x] **T3.2e** Statement carriers separated from data-carrying variables

**Done when:** `b2_01_dynamic_constant` produces edges at mechanism `AST`, indistinguishable
from static SQL, **and `b2_02_dynamic_concatenated` produces none** — only a declared boundary.

> Cheap win — do this before reaching for the log.

**The trap:** partially-constant strings. `'UPDATE ' || v_table || ' SET x = 1'` has constant
fragments and a variable table name. Folding what is available and guessing the rest is
exactly the plausible wrong answer this phase keeps finding. Must-analysis, not may.

---

**Status: CLOSED. THE GATE IS BACK ABOVE THE FLOOR — 75.8% → 96.2% — AND BAND 2 IS OFF
ZERO FOR THE FIRST TIME.** `src/lineage/analysis/dynamic.py`,
`tests/test_dynamic_sql.py` (22 tests); suite 303 → 325.

| band / flow | before | after |
|---|---|---|
| **1 value — THE GATE** | **75.8%** / 96.2% | **96.2% / 96.2%** |
| 2 value | n/a / 0% | **100% / 13.3%** |
| 2 filter | n/a / 0% | **100% / 17.4%** |
| band-1 value false positives | 8 | **1** |
| whole-corpus false positives | 10 | **2** |

The kill criterion is no longer triggered.

### The two jobs, and they pull in opposite directions

**Recover what is knowable.** Constant propagation in source order, so
`v_stmt := v_stmt || '...'` works — by the time the self-reference is read the accumulated
value is known. Recovered statements are analysed by exactly the same code as written ones:
same parser, same dispatch, mechanism `AST`. What changes is the text, not the treatment.

**Refuse everything else, loudly.** Constant-ness is a MUST property. One unknown operand
and the whole expression is unknown, because half a statement resolves to a *different*
statement. An assignment inside a branch or loop poisons its target permanently — a
constant that only sometimes holds is not a constant.

| Package | Outcome |
|---|---|
| `b2_01` | both statements recovered; 5 edges, all band 2 |
| `b2_03` | recovered — the text is provably constant despite the API |
| `s4` | the `EXCHANGE PARTITION` DDL recovered (turning it into an edge is T3.4b) |
| `b2_02` | **refused**, both sites, with a named reason |
| `b2_04` | **refused** — the `WHERE` clause is appended inside an `IF` |

### The eight false positives were one defect: text is not data

`p_column -> v_sql`, `v_stmt -> v_stmt`, `v_cursor -> v_rows` are all true def-use facts
and none is a lineage fact. A variable holding statement TEXT is not a variable carrying
data, and def-use cannot tell them apart.

**The line is data-carrying versus text-carrying, not "variables are noise."** ADR-0001 §3
makes variables first-class and `window_days -> v_days -> v_cutoff -> row filter` is the
chain this phase exists to trace. `v_cutoff` ends up selecting rows; `v_sql` ends up at
`EXECUTE IMMEDIATE`. A test pins both halves, because suppressing variables wholesale would
delete band 1 and score beautifully.

**Carriers are identified by where they are USED, not by name or type** — the same
reasoning that stopped `tmp_recent` being called temporary at T2.5. A DBMS_SQL cursor
handle is included: `v_rows := DBMS_SQL.EXECUTE(v_cursor)` derives the row count from the
statement's effect, which no static edge can express.

### One deliberate departure from the ladder

The construct ladder says DBMS_SQL is **"log recovery only. No static route exists."**
`b2_03`'s text is assembled across four assignments, every one a literal, so constant
propagation recovers it in full. Refusing would be abstention on account of the API's
reputation rather than for a reason, and T3.1 measures false abstention. The general case
— piecewise parsing, dynamic binding — remains out of reach and unattempted. Its key still
labels the edges, so the recall gap stays visible.

The recovered text keeps its `:b_region` bind variable, which confirms the sub-spike's
open assumption: **a bind variable hides a value, not structure.**

### Two false positives remain corpus-wide

| Package | Edge | Owner |
|---|---|---|
| `b1_03` | `V_TOTAL -> V_TOTAL [value/aggregated]` | transform class is computed per statement and should be per source — in `NVL(SUM(x),0) + v_total`, `x` arrives through a SUM and `v_total` through an addition |
| `s6` | `V_CUSTOMER_EDITABLE.REGION -> V_CUSTOMER_EDITABLE` | the view is resolved on neither end — T3.4c |

---

## T3.3 — Triggers *(the band-2 gap, worth 11 edges)*

Not a separate task in the plan — it sits in the band-2 ladder row and in the register's
recoverable band. Broken out because **it is the single largest measured gap in the engine**.

- [x] **T3.3a** Parse `CREATE TRIGGER` bodies as their own analysis units
- [x] **T3.3b** Attach the resulting edges to the **table**, not to the caller
- [x] **T3.3c** Any statement writing that table inherits the trigger's edges
- [x] **T3.3d** `:NEW` / `:OLD` correlation names bound to the triggering row
- [x] **T3.3e** Trigger timing and event carried as evidence (`BEFORE INSERT`, etc.)
- [x] **T3.3f** `INSTEAD OF` triggers on updatable views — silent failure `s6`

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

**Status: CLOSED. BAND 2 GOES FROM 0% TO 57.1% VALUE / 76.5% FILTER, AT 100% PRECISION.**
`src/lineage/analysis/triggers.py`, `tests/test_triggers.py` (21 tests); suite 325 → 346.

| band / flow | entering week 3 | now |
|---|---|---|
| 2 value | n/a / **0%** | **100% / 57.1%** |
| 2 filter | n/a / **0%** | **100% / 76.5%** |
| 0 filter | 100% / 90.9% | 97.4% / 88.1% |
| **1 value — THE GATE** | 95.7% / 100% | **96.2% / 96.2%** |
| false positives, whole corpus | — | **2** (both pre-existing, both owned elsewhere) |

### Triggers come from the DICTIONARY, not from source files

That is the whole design, and everything else follows. `trg_recent_audit` is declared in
`b2_05_triggers.sql` and fires for **seven** procedures in this corpus, none of which
mentions it. A file that inherits a trigger has no text in it to parse.

`ALL_TRIGGERS` is now captured alongside synonyms and views — table, timing, event and
body — and `triggers_on()` resolves through synonyms, so a write to `customer_target`
still finds the base object's triggers. Two silent failures would otherwise compound.

**Attaching to the caller is wrong twice over**, and neither error shows as a gap: it
credits one procedure with a write it never issued *and* misses the other six.
`dim_customer.lifetime_value` then has a writer, or no writer, and both read as findings.

### The correlation names are the analysis problem

In `WHERE cust_id = :NEW.cust_id` both operands look like `dim_customer.cust_id`. One is;
the other is a column of `tmp_recent`, which the statement never names. `:NEW` and `:OLD`
are registered as scope **correlations** — qualifiers bound to a relation in scope without
appearing in any FROM clause.

**Order matters, and getting it wrong collapses the predicate.** Correlations are merged
*after* the statement's own relations, so a bare unqualified name still binds to the
statement. Merged first, both operands resolved to the triggering table and deduplicated
into a single self-edge — the predicate's other half gone, silently.

`NEW` and `OLD` are deliberately **not** distinguished: they are different rows of one
relation and the IR carries no row identity, the same limit `sq_06` recorded for
self-joins before the analyser existed.

### Three defects found by measurement

1. **A MERGE fired no triggers at all.** `fires_on("INSERT UPDATE")` tested the caller's
   whole event string against the trigger's, so a MERGE — which claims both events because
   which arm a row takes is not statically decidable — matched nothing.
2. **The INSTEAD OF trigger emitted view columns on both ends**, which is the exact edge
   `s6`'s key forbids. Its `:NEW` row belongs to a *view*; unresolved, `dim_customer`
   appears to have no writer. View columns are now followed to their base column.
3. **Every trigger became a fusion hazard.** `dim_customer` looked like a shared scratch
   table the moment trigger analysis landed. A trigger is not an independent unit competing
   for a relation — it runs as part of somebody else's write, always. A coverage statement
   full of false hazards is one nobody reads.

### Eleven label corrections, every one of which raises the score

Stated in full because the direction is uniform and that deserves scrutiny rather than a
quiet commit. Each is justified by a convention fixed *before* the analyser existed.

| Change | Packages | Justification |
|---|---|---|
| Added the trigger predicate's other operand | 7 keys | Every operand of a predicate is a filter edge — fixed in `b0_01`, applied in `0b9a9e8`, and stated explicitly in `b2_05`'s key, which was written before any trigger analysis |
| Added the trigger stanza | `b1_09`, `s2`, `s3` | Genuine omissions; `s3`'s was **observed** in both runs and simply never written down |
| Added trigger edges as `unexercised` | `b2_06` | The original note confused two things: every edge in that key is a statement about source marked unexercised, and the trigger edges are the same kind of statement |
| Added the INSTEAD OF self-edge | `s6` | Same shape as `trg_recent_audit`'s `lifetime_value` self-edge, labelled since week 1 — omitting it was an inconsistency, not a judgement |
| **Removed** `trg_customer_default`'s two edges | `b2_05` | They were placed by *declaration site*. The trigger fires `BEFORE INSERT ON dim_customer` and nothing in `b2_triggers` inserts there. `b0_02`'s MERGE does, and that key has carried the edge correctly since week 1 |

That last one is the important one: **keeping it would have made `b2_05` score well for the
wrong reason.** An analyser reading triggers out of the local file would have matched it,
and then missed the same trigger for every other writer of `dim_customer`.

### One modelling gap, recorded rather than fudged

**Inherited trigger edges lose the caller's guard.** `b1_09` writes `tmp_recent` only in
its `WHEN OTHERS` handler, so the trigger's edges should carry that guard; inheritance
happens per *relation a source writes*, not per statement, so they arrive unguarded.
Fixing it means inheriting per statement, which is larger than T3.3.

The key marks those edges `unexercised: true` and says so.

### And the fifth package to hit the match key

`b1_09`'s trigger predicate operand shares a match key **and** a guard with its own
line-24 filter edge, differing only by band and origin. The label validator rejected the
duplicate outright. That is now three packages needing origin — `s2`, `b2_05`, `b1_09` —
against two needing guard, on a key that deliberately carries neither.

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

- [x] **T3.4a** Score all eight against the T3.0 key
- [x] **T3.4b** `s4` — treat `ALTER TABLE … EXCHANGE PARTITION` as lineage-bearing, or declare
- [x] **T3.4c** `s6` — resolve views to base tables through the captured view text
- [x] **T3.4d** `s7` — closed by T3.5
- [x] **T3.4e** Every case becomes a permanent regression test **the day it is written**

**Done when:** every case either passes or its failure is explicitly declared in the report —
and each has a named test. A declared failure is an acceptable outcome here; a silent one is
not.

### CLOSED — `tests/test_silent_suite.py`, 36 tests

**Six of eight score clean; the other two are declared, not skipped.**

| # | Before | After |
|---|---|---|
| s1 | 1 miss | 1 miss — **declared** |
| s2 · s3 · s5 · s7 | clean | clean |
| **s4** | **4 missed, no writer at all** | **clean** |
| **s6** | **1 miss, 1 FP, 1 forbidden edge produced** | **clean** |
| s8 | 1 miss | 1 miss — **declared** |

`KNOWN_INCOMPLETE` in the test module names both survivors with a reason, and the suite
asserts the list is *exactly* right in both directions: a case that regresses fails, and a
case that starts passing also fails until someone removes it deliberately. A list that only
caught regressions would quietly rot into a list of things nobody rechecked.

**Corpus-wide effect:**

| | before | after |
|---|---|---|
| band 0 filter precision | 97.4% | **100%** |
| band 2 value recall | 58.3% | **69.4%** |
| band 2 filter recall | 77.8% | **80.6%** |
| false positives, whole corpus | 2 | **1** |
| forbidden edges produced | 1 | **0** |

**T3.4b — the exchange.** `ALTER TABLE … EXCHANGE PARTITION` moves an entire dataset with
no `INSERT` anywhere, so `fct_revenue_part` was reported as having **no writer** — worse
than a missing edge, because it reads as a positive finding. Now `src/lineage/analysis/ddl.py`,
bound **positionally** (there is no select list to read names from) and banded **2**, since
in this corpus it also sits inside an `EXECUTE IMMEDIATE`. Two things it refuses to do:
compose `stg_orders → fct_revenue_part` across the two hops (the key forbids it), and zip
mismatched shapes — Oracle rejects an exchange between mismatched tables, so a mismatch
means the *dictionary* is stale and guessing would produce four confident wrong edges.
The reverse direction of the swap is declared rather than emitted.

**T3.4c — the view.** The fix was a **third** copy of a view-resolution rule, which is the
real finding. Band 0 inlined view text, the trigger analyser rewrote `:NEW`, and an
ordinary `UPDATE v_customer_editable` went through neither. All three now share
`src/lineage/resolution/views.py`: a view is a *naming* fact, so it belongs beside the
synonym resolver, and the third copy of a naming rule is the one that disagrees with the
other two. New rule: **the relation end follows the column end**, so
`V_CUSTOMER_EDITABLE.REGION -> relation:V_CUSTOMER_EDITABLE` becomes
`DIM_CUSTOMER.REGION -> relation:DIM_CUSTOMER` rather than a half-resolved hybrid. The join
view's **row correspondence** — which per-column resolution genuinely cannot carry — is
declared.

**The two declared failures.** `s1` and `s8` each miss one *filter* edge; both packages'
value edges and transform classes — the thing each case actually tests — are correct.
Left open rather than patched, because the fix is a general question about when a
predicate operand becomes a filter influence, and answering it inside a silent-failure
task would be optimising the number rather than closing the case.

---

## T3.5 — `unexercised` as a first-class edge state

*Register: "Not a tier — a separate axis. An edge can be Tier A (provably in the code) and
never observed running. That combination is itself a finding, and nobody else reports it."*

- [x] **T3.5a** Model as a separate axis on the IR edge, orthogonal to tier
- [x] **T3.5b** Populate from observation — an edge whose statement never appeared in a run
- [x] **T3.5c** Harness reports the count
- [x] **T3.5d** `s7_unexercised_branch` produces a Tier A **and** unexercised edge

**Done when:** an edge can be Tier A *and* never observed running, and the harness reports the
count.

### CLOSED except T3.5b — `tests/test_unexercised.py`, 10 tests

**The axis is three-state, and the third state is the whole task.**

| value | meaning |
|---|---|
| `False` | seen executing inside the window |
| `True` | a window was examined and it never appeared |
| **`None`** | **no window was examined — nothing is known either way** |

Two states force a default and **both defaults are lies.** `False` claims every edge ran,
which is the exact claim this axis exists to avoid. `True` reports the whole estate as dead
code. **Absence of a witness is not evidence of non-execution**, so `IREdge.unexercised` is
now `bool | None`, defaulting to `None`.

**Why that matters more here than anywhere else in the system.** Everywhere else a bad
attribution creates a wrong *edge*, and precision catches it. Here a missing attribution
creates a **silent negative** — "this never ran", asserted about a unit the log could not
see — and a migration team might act on it by deleting code. So the rule is absolute:
**no coverage, no verdict**, enforced by `ExecutionWitness.ran()` returning `None` rather
than by anyone remembering to be careful.

**The window travels with the verdict.** A year-end path absent from 30 days says almost
nothing; the same path absent from 18 months is a real finding. `window` is a required
field and the harness prints it beside every count.

**Scored, but never inside the gate.** `unexercised_total` counts what could be compared;
`unexercised_unknown` counts what could not; `unexercised_accuracy` returns `None` — read
as **NOT MEASURED, never 0%**. Scoring an axis the analyser was given no evidence for would
turn a missing input into an analyser failure. It stays out of precision for the same
reason guards do: whether a statement ran is a fact about the *estate*, not about the
analysis, and letting it move the gate would make the headline number depend on how busy
last month was.

### T3.5b — CLOSED against a live database, and it cost four corrections

`evidence/witness.json` is real, captured from `V$SQL` after `scripts/exercise_corpus.py`
ran 37 procedures under a written protocol. Result: **100% axis agreement on 12 comparable
edges**, 6 claimed unexercised, 220 with no execution evidence.

**Two scripts, one causal direction.** `exercise_corpus.py` causes executions;
`capture_witness.py` only reads the log. Merged, the witness would be a record of its own
footprints and "unexercised" would mean "the capture script did not think to call it".

**The protocol is the evidence.** `s7_unexercised_branch` is called with `'EU'` and nothing
else — the APAC and YEAR_END arms must stay unexercised, and calling them "to be thorough"
would destroy the only unexercised evidence in the corpus.

**Four things the live database taught, every one of them by producing a wrong answer
first.** None was anticipated; all four are now tests.

| # | What happened | Why it is dangerous |
|---|---|---|
| 1 | **`V$SQL` is memory, not a log.** Coverage fell 5 units → 1 in six minutes as the pool aged out. | The first script took `--months`. That was fiction: nothing older than the last restart exists to be found. |
| 2 | **`MODULE`/`ACTION` is sticky.** `s7` set it and never cleared it; every later statement in the session — all of `s8`, all of `sq_*` — is tagged `s7`. | The strongest signal in the ladder credited `s7` with `INSERT INTO fct_product_sales`, which it does not contain. |
| 3 | **Erasing literals equated different statements.** `TRUNC(d,'YYYY')` matched `TRUNC(d,'MM')`. | It reported `s7`'s YEAR_END arm as **exercised when it had never been called** — destroying the case. |
| 4 | **DDL is absent from `V$SQL` entirely.** The `EXCHANGE PARTITION` ran and appears nowhere. | `s4` is a silent failure *because* DML-only reading misses the exchange. The witness would then call the movement of a whole regulated dataset **dead code**. |

**And a fifth, which overturned the coverage rule itself.** Every cursor tagged
`s4_partition_exchange` turned out to belong to `s5`, `s6` or `s7`, while `s4`'s own INSERT
had aged out seconds after running. So a unit now earns a verdict **only if the pool still
holds at least one of its statements** — otherwise absence describes the shared pool, not
the estate. Three units that demonstrably ran are reported as *no verdict* for exactly that
reason.

**What survives is a two-part rule, and the weaker half does the work.** Coverage comes
from the action tag (it exists only because that unit executed `SET_MODULE`, so the unit
really ran). The per-statement sighting comes from **shape matched inside a resident unit**
— which is precisely where T2.9 left shape after demoting it: a corroborating constraint,
never an attributor.

**Only 5 of 37 procedures call `DBMS_APPLICATION_INFO` at all.** The blind-spot register
predicted this exactly, and it caps what this evidence source can ever cover.

**Standing limitation for the verdict.** Every negative this witness produces is bounded by
an instance uptime measured in minutes. A production capture needs `DBA_HIST_SQLSTAT` (AWR,
separately licensed) or a scheduled job that persists `V$SQL` before it ages out. That is a
cost line for phase 1, not a detail.

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
