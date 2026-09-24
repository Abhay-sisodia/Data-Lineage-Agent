# Parse capability, and what is still open

**The question this document answers: given a PostgreSQL query, can the analyser read it?**

That is deliberately narrower than "is the lineage correct". Parsing is a precondition -
nothing downstream runs without it - and it fails in a way that is easy to measure and hard
to argue about. Lineage correctness is a separate question with its own instruments (ground
truth keys, metamorphic pairs) and its own open items, listed at the end and kept apart on
purpose.

Last measured 2026-09-24 against SQLGlot 30.18.0, postgres dialect.

---

## 1. The short answer

| Corpus | Shape | Parses? | Result |
|---|---|---|---|
| GL account combination | 913 lines, 6 CTEs, 30 segments | yes | 164 / 164 edges correct |
| Status dimension | 921 lines, 28 CTEs, 25 union arms | yes | 58 / 58 correct |
| PO line consolidated | 1,914 lines, 34 CTEs, 31 tables | yes | 810 edges, all endpoints verified |
| Shipment-margin torture | 2,963 lines, 70 CTEs, one statement | **no** | 0 edges, declared parse failure |
| Shipment-margin torture, rewritten | 2,920 lines, 85 CTEs | yes | 301 edges, coverage 1.0 |

The three production queries parse and produce verified lineage. The one file that does not
parse is **synthetic and adversarial** - written specifically to exercise grammar corners,
not to resemble a warehouse model.

## 2. How that was measured

A single unparseable clause refuses an entire statement, and a modern analytical query is
ONE statement. So "the file failed" says nothing about how much of the language is
supported. Two probes separate those:

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

Three parse-adjacent behaviours are ours, and all three are now correct:

* **The failure is declared, never silent.** A `parse_failure` boundary is raised and
  `statements_analysed` falls, so a reader can see that nothing was claimed.
* **The refusal reason is true of the dialect** (PG-17, fixed). PostgreSQL dollar-quoting
  was being reported as Oracle conditional compilation.
* **`rewrite.py` stays small.** `CYCLE` was considered for it and REJECTED: the clause
  defines `org_is_cycle`, which the query then filters on, so stripping it would silently
  narrow lineage. That belongs in the refusal register instead.

---

## 5. Open items

Split by whether the item affects **parsing** or **lineage quality**, since those are
different questions and the first is the one currently in focus.

### 5a. Affects whether a file can be read at all

| # | Item | Why it matters |
|---|---|---|
| **PG-18** | `SEARCH` / `CYCLE ... TO` fail the parse | Upstream SQLGlot. Declared loudly, so survivable. Needs either refusal-register entries naming each construct, or upstream support |
| **PG-20** | One `JSON_TABLE` in one CTE out of 85 refuses a 2,948-line query whole | **The highest-leverage open item.** The refusal unit is the STATEMENT and a modern query is one statement, so any single unsupported construct discards 84 readable CTEs. Third finding pointing here, with PG-06 and PG-16 |
| **S2-01** | `MERGE` with a `DELETE` arm fails to parse | Oracle side |
| — | The 31 unparseable constructs above | Not individually registered; they are upstream and declared |

### 5b. Affects lineage quality once a file parses

| # | Item | Shape |
|---|---|---|
| **PG-05** | `unnest(...)` in `FROM` drops its column with NO boundary | `silent-loss` — the only known column-level silence in the PostgreSQL path |
| **P-02** | A `DELETE`'s predicate never sees variables | `silent-loss` — the identical `UPDATE` keeps them |
| **PG-03** | Columns projected through a `LATERAL` alias are unresolved | Declared, not silent |
| **PG-04** | `jsonb_build_object` is opaque to its own arguments | Refusal is honest, classification is wrong |
| **S4-13** | A cursor `FOR` loop carries none of the cursor query's predicates | Oracle side |
| **PG-15** | `FILTER (WHERE c)` and `SUM(CASE WHEN c ...)` give different lineage for the same number | **Needs a decision**, not a fix. Currently `xfail` |

### 5c. Affects the measurement rather than the answer

| # | Item | Shape |
|---|---|---|
| **PG-01** | An `UPDATE` is analysed and never counted | `statements_seen` understates |
| **PG-06** | Scope contract is stated per STATEMENT, losses are per COLUMN | The probe cannot see PG-03/04/05 |
| **PG-19** | A whole-file parse failure reports `statements_seen = 0`, `coverage = None` | An unreadable file drops out of the coverage denominator instead of lowering it. Touches signed phase-0 numbers, so it needs its own measurement |
| **PG-08** | `CREATE_TRIGGER_PG`'s reason never reaches the reader | The `(unit, line)` dedup discards the better message |
| **S1-05** | Label format cannot express five facts in one file | Key-writing limitation |

### 5d. Product-level, not analyser-level

Raised and deliberately not treated as findings:

* **Schema acquisition ladder** — live capture / supplied DDL / catalog export / inference
  from SQL / nothing. Each rung has a different failure mode and only one is dangerous: a
  dictionary that is *wrong* rather than *thin*. `require_fingerprint` exists for this and
  nothing forces it.
* **Dialect selection** — currently defaults silently, which is the one behaviour to rule
  out. Ask, sniff from syntax, or infer from the connection.
* **ADR-0003** covering both was offered and is not yet written.

### 5e. Housekeeping

* Nothing has been pushed to the remote; all commits are local on `temporary_frontend`.
* `.claude/settings.json` was swept into commit `e3bc0b2` by a `git add -A`.
* `databricks` is not registered in the dialect seam.
* **Row selection by rank** (`rn = 1`, `DISTINCT ON`, `ORDER BY ... LIMIT`) has now appeared
  in four production and synthetic files and is still unconventioned — neither the keys nor
  the analyser label it, so nothing measures it either way.

---

## 6. Verification state

```
suite                840 passed, 1 xfailed (PG-15)
oracle probe         63 constructs: DECLARED 4, EDGES 50, REFUSED 6, SILENT 3
postgres probe       44 constructs: 44/44 agreed with expectation, EDGES 35, REFUSED 6
```

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
