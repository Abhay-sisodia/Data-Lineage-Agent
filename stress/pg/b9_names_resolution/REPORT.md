# Batch B9 — Names and resolution: report

**Run 2026-10-03**, against the analyser at commit `5fa6b2c` (B4 complete), unchanged.
Every key was written before the analyser first saw its file, and decisions D-23 to D-25
were taken before any key. **Nothing has been fixed.** This is the review point.

```
.\.venv\Scripts\python.exe stress/pg/triage.py b9_names_resolution
```

## 1. Headline

The keys follow D-25: every node is schema-qualified (`staging.ledger.amount`). The analyser
still names nodes by bare table name, so **the strict run finds 0 of 181 statement-level
facts.** That is D-25 not yet implemented, and it hides everything else. To see the other
defects, the same keys were re-scored with the schema stripped from every node (the scratch
tool `bare_view.py`). Stripping merges `staging.ledger` and `core.ledger` facts, so that
view deduplicates them; it is a diagnostic, not a score.

| Test | Statements | Refused | Strict, per statement | Schema-stripped, per statement |
|---|---|---|---|---|
| T1 Breadth | 17 | 2 | 0 / 43 | 23 / 43 |
| T2 Depth | 7 | 2 | 0 / 28 | 16 / 28 |
| T3 Interaction | 8 | 3 | 0 / 32 | 16 / 31 |
| T4 Adversarial | 11 | 2 | 0 / 26 | 12 / 26 |
| T5 Metamorphic | 21 | 4 | 0 / 52 | 36 / 52 |
| **B9 total** | **64** | **13** | **0 / 181** | **103 / 180 (57%)** |

**13 of 64 statements are refused, every one of them valid PostgreSQL.** This is the
weakest batch so far, and almost all of it comes from three defects (§2).

**What held**, in the schema-stripped view:

- an alias reused at four levels, and a CTE named like a real table;
- GROUP BY resolving a bare name to the **input** column and ORDER BY to the **output**
  column;
- a window partitioned by a USING-merged column, and same-named columns in GROUP BY and
  HAVING;
- `"O"` beside `o`, an unqualified `id` binding to the inner query, aliases named like a
  schema, a column or a keyword, and unquoted `user` read as a function;
- upper-case unquoted names folding, quoted lower-case names matching, and three-part
  column names;
- RIGHT, FULL and NATURAL merged columns, `t.*` of `t.*`, and COLLATE as derived.

## 2. Findings, by root cause

### Analyser defects

| ID | What | Where | Grade |
|---|---|---|---|
| **B9-01** | **The SQLGlot schema is keyed by bare table name, so the last same-named table wins.** `core.ledger` (declared after `staging.ledger`) replaces `staging.ledger`'s columns. Every statement reading `staging.ledger.amount`, `currency` or `posted_at` is refused "Unknown column". Worse, a column the two tables share (`id`, `account`) binds to whichever table won, and bare node names make the wrong binding invisible. Found while setting up the schema: a second `orders` table refused 79 B1–B4 tests until the new tables were renamed. | T1 S5; T2 S2, S4; T3 S5, S6, S8; T4 S8, S9; T5 P1, R1 (10 refusals) | **R2, unsound refusal; latent R0** |
| **B9-02** | **A quoted mixed-case relation is declared "not in dictionary".** Table lookup folds `"LegacyFeed"` to `legacyfeed`; the quote is lost before folding. Every statement over the legacy feed loses all its edges, with a boundary whose reason is wrong. | T1 S10; T2 S3; T3 S7; T4 S3, S4, S5; T5 R1 | R3, declared loss with a false reason |
| **B9-03** | **`SELECT *` into an INSERT with no column list is refused for arity** ("4 selected into 1"). B3's omitted-list fix counts `*` as one projection when choosing how many target columns to bind. A regression introduced in B3, by me. | T1 S13; T5 P4 A | R2, unsound refusal |
| **B9-04** | **`x.*` over a USING join yields the merged column instead of `x`'s own.** `SELECT x.* … JOIN x USING (id)` gave `customers.id → customer_id` where `x.id` is `orders.customer_id`. B1-02's merged-column rule is applied to a qualified star, where PostgreSQL keeps each side's column. | T1 S14 | **R0, wrong edge** |

### Decided, not yet implemented (expected)

| Decision | Effect | Where |
|---|---|---|
| **D-23** follow SET search_path | Unqualified names under a SET still go to the default schema. `events_log` under `SET search_path TO mart` is declared dangling. The assumption boundary lands at the first unqualified name even when it runs under a SET (T2 at line 86, not 111; T4 at 94, not 106; T5 at 24, where the key has none). | T1 S5, S8; T2 S5; T4 S9; T5 P1 B, R1 |
| **D-24** bind a unique name | `shipments` (only in staging) is declared dangling. The ambiguous `events_log` is declared dangling too, which is right by accident: the declaration is the same, but its reason is "not in dictionary" where it should be "ambiguous". | T1 S2, S3; T4 S10 |
| **D-25** schema-qualified nodes | every fact in the strict view | all |

### Metamorphic pairs (T5, schema-stripped)

P2, P3 and P5 to P10 are equal. **P1** is equal only because both halves are refused
(B9-01). **P4** differs because `SELECT *` is refused while the explicit list passes
(B9-03).

## 3. Recommended fix order (for agreement, not yet started)

| # | Item | Why here |
|---|---|---|
| 1 | **B9-01** key the SQLGlot schema by schema and table | the root of 10 refusals and a latent wrong binding; every multi-schema database is exposed. It must land before D-23 and D-24, which choose *which* same-named table is meant |
| 2 | **B9-04** a qualified star keeps each side's column | the only wrong edge; small, and next to B1-02 |
| 3 | **B9-03** count `*` by its expansion in the omitted-list width | my B3 regression; small |
| 4 | **B9-02** keep the quoted case of relation names through lookup | a whole class of real legacy objects |
| 5 | **D-23, D-24** the search_path and unique-name rules | builds on 1; changes which boundary appears where |
| 6 | **D-25** schema-qualified nodes for PostgreSQL | last, because it relabels every B1–B4 key, pin and regression test mechanically, and B9 can only be scored strictly once it lands. Done as one mechanical change with its own before and after: every count must be unchanged |

## 4. Coverage checklist (programme plan §1, row B9)

| Construct | Exercised |
|---|---|
| quoted and case-folded identifiers | T1 S10, S11; T2 S3; T3 S7; T4 S1, S3–S5, S11; T5 P2, P3 |
| search_path and schema qualifiers | T1 S1–S9; T2 S5, S6; T4 S9, S10; T5 P1, R1 |
| alias shadowing and reuse | T2 S1, S2; T3 S6; T4 S1, S2, S6, S7 |
| `*` and `t.*` | T1 S13, S14; T2 S3, S7; T4 S8; T5 P4, P5 |
| USING and NATURAL merging | T1 S14, S15a–c; T2 S4; T3 S3; T5 P6, P7, P10 |
| same-named columns across tables | T3 S4, S5; T4 S2 |
| COLLATE (deferred from B4) | T1 S16 |

**Deferred:** `set_config('search_path', …)` and function-level `SET` clauses (B8,
procedural); temp-table shadowing through `pg_temp` (B7); quoted schema names that differ
from their unquoted form (the schema has none).

## 5. Corrections while designing, stated

- **The schema gained objects (decision B9-QC).** The first attempt added `core.orders` and
  `mart.payments`. That broke 79 B1–B4 tests, through B9-01. Those two tables were replaced
  by new same-named pairs (`ledger`, `events_log`), and every B1–B4 pin and regression test
  passes again. The breakage is recorded as B9-01's evidence.
- **Node naming became a decision (D-25) after the cards were drafted.** Planning the
  search_path tests showed that bare node names cannot tell two same-named tables apart, so
  no key could express search_path order. The question was put before any key was written.
- **Keys are schema-qualified while the analyser is not**, so the strict score is 0 by
  construction. The schema-stripped view exists only to make the other defects visible.
  Once D-25 lands, the strict score is the score.
- **Several unqualified references assume a role-level search_path** (T1 S1–S3, S9; T2 S6;
  T4 S10). Without one, PostgreSQL would reject them. That assumption is exactly what
  `context_dependent_binding` declares.
- **Two key errors were caught before the run was scored.** T3 S6 and T4 S6 stated facts
  already stated by S5 and S2, which the loader rejects. Both became `repeat` lines.
  `keygen.py` now rejects a duplicate at compile time and names the line, instead of
  leaving it to the loader.
- **Two scratch tools** resolve `{span:…}` tokens (a statement's span start, which the
  unit-level boundary uses) and build the schema-stripped view. Neither changes a key.

## 6. Fixes applied, 2026-10-03 — in the agreed order

| # | Item | Change | Where |
|---|---|---|---|
| 1 | B9-01 | The SQLGlot schema is keyed by schema, then table. Every unqualified relation is bound to a schema **before** `qualify`, so SQLGlot never has to choose (and never refuses a whole statement as ambiguous) | `band0._schema_for`; new `analysis/relations.py` (`bind`) |
| 2 | B9-04 | Beside a USING or NATURAL join, `x.*` is expanded to x's own columns before `qualify`, which would otherwise merge them | `relations.expand_qualified_stars` |
| 3 | B9-03 | The omitted-list width is unknown while a `*` hides it, so the full table list meets the expanded star | `pg_dml._query_width` |
| 4 | B9-02 | Lookups keep a quoted name's case (`relations.identifier`, which replaces 13 node folds in band0), and the dictionary matches an exact name before folding. Two defects this exposed were also fixed: a quoted RETURNING name was folded into `<result>`, and an unquoted `user` bound to the column `"user"`. It now reads as CURRENT_USER | `relations.identifier`, `session_keywords`; `Dictionary.resolve`; `band0` result naming |
| 5 | D-23, D-24 | `SET` / `RESET search_path` are read in script order and carried to the binder in a context variable. The assumption boundary follows the same path, so a statement under a SET declares nothing. An ambiguous name is declared under its bare name, with the schemas listed | `relations.search_path_change`, `SEARCH_PATH`; `band0.analyse_source`, `_trace`; `procedure._context_dependent_names` |
| 6 | D-25 | A PostgreSQL relation is named `schema.table` in the graph, at all five places a relation is resolved: sources in `_trace`, and the INSERT, DELETE and MERGE targets and the def-use map. Oracle is unchanged | `band0._relation_name` |

### Result

**B9, scored strictly against the schema-qualified keys: 181 of 181 per statement and per
file, 0 false positives, 0 refusals, all ten pairs equal.** Every boundary is where the
keys put it: the assumption at T1's first statement, T2's S6 and T4's S10, and nothing in
T5. The ambiguous `events_log` is declared by its bare name in T1 S3 and T4 S10. Pinned in
`tests/test_pg_batches.py`.

The regression tests are in `tests/test_b9_names.py`, and all 11 fail on the unfixed
source. The control, which checks that an unqualified USING column is still the left
input's, fails there only because of D-25's naming; its B1-02 behaviour is unchanged.

### What the fixes reached outside this batch

* **D-25 relabelled every B1–B4 key mechanically:** 2,479 column names plus every filter target, each table's schema taken
  from the dictionary. Every name was unique, so nothing needed a guess. **Every pinned
  count is unchanged**, every per-statement total is complete, and the seam pairs differ
  exactly as before.
* **Unit tests:** about 190 name literals in 15 PostgreSQL test files were qualified the same
  way. Two files with hand-built dictionaries were edited by hand. One of them
  (`test_lateral_projection`) had a fixture whose object `gold.tgt` claimed owner `silver`.
  Bare names hid that, and the fixture now takes the owner from the key. One other test
  (`test_columnless_relation`) now asserts the nested schema shape.
* **Oracle stress: identical to HEAD.** On Oracle, a schema the binder supplies does not
  count as written, so an Oracle multitable insert with no column list is still declared
  rather than bound by position. Found by the suite, gated on purpose.
* **`keygen.py`** now rejects a duplicate fact at compile time and names the line.
* **`docs/ir-v0.md`** no longer says node names are upper-cased (false since the PostgreSQL
  work) and states D-25.
