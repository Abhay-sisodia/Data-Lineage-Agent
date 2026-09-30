# Parse capability, and what is still open

**The question this document answers: given a PostgreSQL query, can the analyser read it?**

That is deliberately narrower than "is the lineage correct". Parsing is a precondition -
nothing downstream runs without it - and it fails in a way that is easy to measure and hard
to argue about. Lineage correctness is a separate question with its own instruments (ground
truth keys, metamorphic pairs) and its own open items, listed at the end and kept apart on
purpose.

Last measured 2026-09-30 against SQLGlot 30.18.0, postgres dialect. The construct probes in
§2 and §3 date from 2026-09-24 and are unchanged; §1, §4, §5 and §6 were re-measured.

---

## 1. The short answer

| Corpus | Shape | Parses? | Result |
|---|---|---|---|
| GL account combination | 913 lines, 6 CTEs, 30 segments | yes | 164 / 164 edges correct |
| Status dimension | 921 lines, 28 CTEs, 25 union arms | yes | 58 / 58 correct |
| PO line consolidated | 1,914 lines, 34 CTEs, 31 tables | yes | 810 edges, all endpoints verified |
| Shipment-margin torture | 2,963 lines, 70 CTEs, one statement | **no** | 0 edges, declared parse failure |
| Shipment-margin torture, rewritten | 2,920 lines, 85 CTEs | yes | 334 edges, coverage 1.0 |
| The same, with one `JSON_TABLE` put back into one CTE | 2,932 lines, 85 CTEs | yes | 329 edges — **was 0 before PG-20** |

The three production queries parse and produce verified lineage. The one file that does not
parse is **synthetic and adversarial** - written specifically to exercise grammar corners,
not to resemble a warehouse model.

The rewritten variant's figure was 301 when this document was first written and is 334 now;
the difference is the PG-03 and PG-21 fixes landing after that measurement, not a change of
file. The last row is PG-20's measurement and is the reason this section is no longer only
about parsing: that file PARSED in both states, and the difference between 0 edges and 329
was entirely **what the analyser was willing to say about what it had read**.

## 2. How that was measured

A single unparseable clause fails an entire statement, and a modern analytical query is
ONE statement. So "the file failed" says nothing about how much of the language is
supported. Two probes separate those:

(This is now true of **parse failures only**. A *refusal* — a construct that parses and the
analyser declines to interpret — is no longer statement-wide when the construct is confined
to a CTE; that was PG-20, and §4 records it. A parse failure cannot be narrowed the same
way, because there is no tree to narrow against.)

**Probe A - construct coverage.** 138 constructs drawn from the torture file, each reduced
to the smallest statement that carries it, so a failure names exactly one thing.

```
parsed  107 / 138   (78%)
failed   31 / 138
```

**Probe B - fidelity.** Parsing is not enough: a construct that parses but silently drops an
identifier is far more dangerous than one that fails loudly. The 40 highest-risk survivors
were re-checked for whether they keep every table, column and alias the SQL names.

```
identifiers survive  40 / 40   (100%)  - zero silent loss
```

**This is the result that matters.** `JSON_TABLE` with `NESTED PATH`, `XMLTABLE`,
`ROWS FROM`, `TABLESAMPLE`, `GROUPING SETS`, `GROUP BY DISTINCT`, inheriting named windows,
`DISTINCT ON`, whole-row references, 2-D array subscripts and `WITH RECURSIVE` inside a
scalar subquery all parse, and not one of them loses an identifier. **No construct in this
corpus parses and is then modelled wrong.** Everything that fails, fails loudly.

Probe scripts are not in the repo; they are reproducible from the recipe above.

## 3. What does not parse, and where the gap lives

All 31 failures are **upstream in SQLGlot**, not in this codebase. They cluster into six
families, which is more useful than a flat list because each family is one grammar area.

| Family | Constructs | Count |
|---|---|---|
| **SQL/JSON (PG 16/17)** | `JSON_VALUE`/`JSON_QUERY`/`JSON_EXISTS` with `RETURNING`, `PASSING`, `ON EMPTY`, `ON ERROR`, wrappers, `OMIT QUOTES`; `JSON()`; `JSON_SCALAR`; `JSON_SERIALIZE`; `JSON_ARRAYAGG ... ORDER BY`; `JSON_ARRAYAGG` over a window | 9 |
| **XML** | `XMLPI`, `XMLCOMMENT`, `XMLROOT ... STANDALONE`, `XMLSERIALIZE ... INDENT`, `XMLEXISTS ... PASSING BY VALUE`, `XMLTABLE` with `XMLNAMESPACES` | 6 |
| **`USING` in three unrelated roles** | `ORDER BY x USING <`, `array_agg(... ORDER BY x USING >)`, `JOIN ... USING (c) AS alias`, `CYCLE ... TO <v>` | 4 |
| **Prefix operators** | `\|/`, `\|\|/`, `@`, `?-`, `?\|`, `@-@`, `#` | 3 |
| **Type syntax** | `int4 '5'`, `CAST(... AS text ARRAY[4])`, `CAST(... AS integer[2][2])`, `national character`, `nchar varying` | 5 |
| **Misc** | `TABLE` inside a set operation, `EXISTS (TABLE t)`, `U&"..."` identifiers, `SUBSTRING ... SIMILAR ... ESCAPE`, `COLLATION FOR`, `AT TIME ZONE INTERVAL`, `AT LOCAL` | 4 |

### The decorations that cost the torture file everything

Narrowed by decomposition, because the first write-up of this was wrong in both halves:

```
CYCLE id SET c                              OK
CYCLE id SET c USING p                      OK
CYCLE id SET c TO 'Y'                       FAIL   <- the breaking token
SEARCH ... SET ord  then SELECT             OK
SEARCH ... SET ord  then , next_cte         FAIL   <- the position the file uses
SEARCH BREADTH FIRST BY id, pid SET ord     FAIL   <- multi-column BY
```

So it is `TO <value>`, the cycle-mark clause - not `CYCLE`, and not `USING`. And `SEARCH`
parses only when it is the last thing before the final `SELECT`.

### There is no single blocker

Peeling them off moves the error rather than clearing it:

```
line   59   CYCLE ... TO 'Y' DEFAULT 'N'
line   59   SEARCH ... followed by a comma
line  168   ORDER BY h.version_no USING >
line  764   JSON_VALUE(... RETURNING timestamp with time zone ...)
            ... and more behind those
```

Supporting `CYCLE` would move the failure to line 168, not produce lineage. Roughly 60
rewrite rules were needed to make the file parse end to end.

## 3a. A refusal that was CORRECT, recorded so it is not re-investigated

`another_stress_test.txt` (3,711 lines, 134 CTEs, 16 base tables) **parses cleanly** and is
then refused:

```
NAME_NOT_RESOLVED  could not qualify names: Unknown column: amount
```

That refusal is right, and the SQL is wrong. `lateral_hub` projects eight columns -
`entity_id`, `positive_count`, `negative_count`, `distinct_kinds`, `token_count`,
`first_token`, `json_keys`, `series_sum` - and `amount` is not among them, because it takes
only `s.entity_id` from `seed_orchid`. The next CTE then reads `p.amount` from it eight
times. PostgreSQL would reject the same statement with `column p.amount does not exist`.

Confirmed by repair rather than by reading: adding `s.amount` to `lateral_hub` moves the
error to `Unknown column: payload` - the same defect, next column. The generator wrote a
narrowing hub and 111 downstream stages that assume the full upstream shape survives it.

**Two things worth keeping from this.** First, parse success and name resolution are
different stages, and only the first is what section 2 measures - this file counts as a
PARSE success. Second, a static check of CTE references could verify only 82 of 522 of
them; the other 440 sit behind a `SELECT *`, whose shape is not decidable from the text. So
there may be more such errors in that file, and `amount` and `payload` are simply the first
two the resolver reached.

## 4. What the codebase does own

Four parse-adjacent behaviours are ours, and all four are now correct:

* **The failure is declared, never silent.** A `parse_failure` boundary is raised and
  `statements_analysed` falls, so a reader can see that nothing was claimed.
* **The refusal reason is true of the dialect** (PG-17, fixed). PostgreSQL dollar-quoting
  was being reported as Oracle conditional compilation.
* **The refusal is no larger than the construct** (PG-20, fixed). A construct confined to
  one CTE refuses that CTE; the other 84 are analysed. The refused body is REMOVED rather
  than distrusted, because a construct the classifier refuses is not thereby one SQLGlot
  declines to interpret — `CONNECT BY` parses cleanly and emits three `x -> x` self-edges.
  A statement refused in part is counted as `partial_statements`, a category of its own.
* **`rewrite.py` stays small.** `CYCLE` was considered for it and REJECTED: the clause
  defines `org_is_cycle`, which the query then filters on, so stripping it would silently
  narrow lineage. That belongs in the refusal register instead.

---

## 5. Open items, and the risk each one carries

### How risk is graded here

Not by how broken something looks. By **what a reader of the output would wrongly believe**,
which is the project's own failure taxonomy:

| Grade | Shape | Why it sits there |
|---|---|---|
| **R1** | **Silent loss** | The output looks clean. A column reads as having no upstream, which is indistinguishable from a genuine finding *about the data* — and is exactly the confusion this product exists to prevent. Nothing in the numbers moves. |
| **R2** | **Unsound refusal decision** | A refusal can be invented or missed, so both the abstentions and the edges are affected, and neither is loud. |
| **R3** | **Declared loss** (false abstention, T3.1d) | Edges are lost and the loss is stated. Costly, never wrong. A reader can see the abstention. |
| **R4** | **Measurement distortion** | The lineage is right and the numbers describing it are not. Dangerous only when the numbers are what someone signs. |
| **R5** | **Unmade decision** | No defect. A choice nobody has taken, where taking it either way is defensible and *not* taking it is the risk. |

**There is one R1 and one R2 open.** Everything else is R3 or lower.

### 5a. Affects whether a file can be read at all

| # | Item | Risk if left open | |
|---|---|---|---|
| **PG-23** | `_strip_noise` desynchronises on `E'O\'Reilly'`, and every literal after it in the file is mis-blanked | **The classifier reads a corrupted view of the file from that point on**, so it can both invent a refusal (a construct name inside a string it failed to blank) and miss one (a real construct it blanked by mistake). Both dialects, silent either way. Measured: over-spans a CTE by 150 lines and loses 12 of 81 CTEs from the scan. No shipped corpus contains such a literal, which is why it never surfaced | **R2** |
| **PG-18** | `SEARCH` / `CYCLE ... TO <v>` fail the parse | Lost file, loudly. Upstream SQLGlot, and the failure is declared with `statements_analysed 0` against `seen 1`, so nothing is claimed. Cannot go in `rewrite.py` — these DEFINE columns the query then filters on | **R3** |
| **S2-01** | `MERGE` with a `DELETE` arm fails to parse | Same shape, Oracle side | **R3** |
| — | The 31 unparseable constructs in §3 | Not individually registered: upstream, declared, and clustered into six grammar areas rather than 31 independent problems | **R3** |
| ~~PG-20~~ | ~~One `JSON_TABLE` in one CTE of 85 refuses a 2,948-line query whole~~ | **FIXED 2026-09-30.** Measured cost was 0 edges where 329 were available. The refusal now names the CTE, which is REMOVED so it can neither be trusted nor contribute | — |

### 5b. Affects lineage quality once a file parses

| # | Item | Risk if left open | |
|---|---|---|---|
| **PG-05** | `unnest(...)` in `FROM` drops the unnested element with **no boundary at all** | **The only known column-level silence in the PostgreSQL path.** `generate_series` in the same position declares `dangling_reference`, so the machinery exists and `unnest` slips past it. A reader sees a column with no upstream and has no way to know the analyser simply did not look | **R1** |
| **P-02** | A `DELETE`'s predicate never sees variables | Silent, but narrower: the identical `UPDATE` keeps them, so the shape is known and the fix is bounded | **R1** |
| **PG-22** | A provable column inside a refused CTE is refused with it | **New, and created by PG-20's fix.** `s.id AS ship_id` beside a `JSON_TABLE` loses a true edge. Deliberate — the classifier's refusal does not imply SQLGlot declines the body, and `CONNECT BY` proves bodies can emit guesses — so the safe direction was chosen and the cost registered | **R3** |
| **PG-04** | `jsonb_build_object` is opaque to its own arguments | The refusal is honest; only its classification is wrong. Nothing is claimed that is not true | **R3** |
| **S4-13** | A cursor `FOR` loop carries none of the cursor query's predicates | Lost filter edges, Oracle side | **R3** |
| **PG-15** | `FILTER (WHERE c)` and `SUM(CASE WHEN c ...)` give different lineage for the same number | **Needs a decision from the owner, not a fix.** Currently `xfail`, so the disagreement is pinned and cannot drift unnoticed. The risk of leaving it is that two spellings of one computation keep producing two answers and neither is wrong yet | **R5** |

### 5c. Affects the measurement rather than the answer

| # | Item | Risk if left open | |
|---|---|---|---|
| **PG-19** | A whole-file parse failure reports `statements_seen = 0`, `coverage = None` | **An unreadable file drops out of the coverage denominator instead of lowering it**, so a batch in which a file failed to parse scores the same as one where that file did not exist. This is the measurement-side twin of the thing the corpus is meant to detect. Touches signed phase-0 numbers, so it needs its own before/after run | **R4** |
| **PG-06** | The scope contract is stated per STATEMENT, the losses are per COLUMN | `probe_postgres` asks "did this statement produce edges?", and PG-04/05 answer yes while losing a column — so **the probe cannot see the R1 items above.** The instrument is blind in exactly the direction that matters | **R4** |
| **PG-01** | An `UPDATE` is analysed and never counted | `statements_seen` understates in both dialects, so parse coverage is computed over the wrong denominator. An `UPDATE` that also produced no edge would be fully silent | **R4** |
| **PG-08** | `CREATE_TRIGGER_PG`'s reason never reaches the reader | The `(unit, line)` dedup discards the better message, so the refusal is correct and its explanation is the weaker of the two available | **R4** |
| **S1-05** | The label format cannot express five facts in one file | Key-writing limitation, so some ground truth cannot be stated at all — and unstatable truth is unmeasurable | **R4** |

### 5d. Product-level, not analyser-level

Raised and deliberately not treated as findings. All **R5**: the risk is the absence of a
decision, not a defect in code.

* **Schema acquisition ladder** — live capture / supplied DDL / catalog export / inference
  from SQL / nothing. Each rung has a different failure mode and only one is dangerous: a
  dictionary that is *wrong* rather than *thin*. A thin dictionary produces declared
  boundaries; a wrong one produces confident edges to the wrong columns. `require_fingerprint`
  exists for this and **nothing forces it**, which is the actual risk.
* **Dialect selection** — currently defaults silently, which is the one behaviour to rule
  out, because PG-17 showed what a mis-set dialect does: an Oracle-only construct refusing
  readable PostgreSQL. Ask, sniff from syntax, or infer from the connection.
* **ADR-0003** covering both was offered three times and is still unwritten. Leaving it
  unwritten means the two decisions above get taken implicitly, by whoever next touches the
  code.

### 5e. Housekeeping

| Item | Risk if left open |
|---|---|
| **Nothing has been pushed to the remote** — every commit is local on `temporary_frontend` | The whole of this work, findings register included, exists on one machine. This is the largest *practical* risk on the page and the cheapest to retire |
| `.claude/settings.json` was swept into commit `e3bc0b2` by a `git add -A` | Local tooling config in project history; flagged and never resolved |
| `databricks` is not registered in the dialect seam | A Databricks file would be analysed under another dialect's register — PG-17's exact shape, now foreseeable rather than discovered |
| **Row selection by rank** (`rn = 1`, `DISTINCT ON`, `ORDER BY ... LIMIT`) is unconventioned after four sightings | Neither the keys nor the analyser label it, so **nothing measures it either way** — the state in which a disagreement cannot be detected, which is how PG-10 and PG-11 went unnoticed |
| Phase 0 has not been re-measured since 2026-09-25 | `scripts/exercise_corpus.py` needs the Oracle container. Three fixes have landed since (PG-03, PG-20, and the refusal dedup widening), each verified by proxy but none against the signed grid |

---

## 6. Verification state

```
suite                862 passed, 2 skipped, 1 xfailed (PG-15)
oracle probe         63 constructs: DECLARED 4, EDGES 50, REFUSED 6, SILENT 3
postgres probe       44 constructs: 44/44 agreed with expectation, EDGES 35, REFUSED 6
```

The 2 skips are `test_dictionary` and `test_smoke_toolchain`, which need the Oracle
container; it is not running, so **those two tests and the phase-0 re-measurement below are
unverified in this state rather than passing.** Saying so is the point of this section.

**Phase 0**, from the signed `measurements/phase0_final.json` (commit `9aa8c48`):

```
band-1 value precision   96.2%   (floor 95%)    PASS
band-1 value recall      96.2%   (floor 85%)    PASS
guard accuracy           100%                   PASS
parse coverage           76.1%   (floor 70%)    PASS
edges from refusals      0 of 11                PASS
tier C/D                 0.0%                   PASS
dynamic SQL              5.0% of statements, 50% recovered   PASS
```

All seven kill criteria PASS.

Two notes on reading these. **Parse coverage is 76.1% in the signed file and 81.6% today** -
the difference is two DELIBERATE moves under decision D-1 (`INSERT ... VALUES`, then
`DELETE`/`UPDATE`), each recorded in the register with its own before/after. The signed file
is the baseline, not the current state. And **parse coverage here is the Oracle corpus
figure**, which is a different population from the PostgreSQL numbers in section 1; the two
are not comparable and should not be averaged.

The `cells` grid has stayed byte-identical through every fix except where the register says
otherwise, which is the property that makes "Phase 0 has not moved" a checkable claim rather
than a reassurance.

**And it has not been checked since 2026-09-25.** `scripts/exercise_corpus.py` connects to
the Oracle container to build its dictionary, and the container is down, so the three
changes that have landed since (PG-03, PG-20, and the refusal dedup widening) are verified
by proxy only: the full suite, `test_refusals.py` including
`test_all_seven_constructs_in_the_corpus_are_flagged`, and both construct probes. Those are
good proxies and they are not the signed grid. The claim on this page is therefore
"unchanged as far as everything that can run here can tell", which is weaker than the
sentence above and is the honest one until the container is up.
