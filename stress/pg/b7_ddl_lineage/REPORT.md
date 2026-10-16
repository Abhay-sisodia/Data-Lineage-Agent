# Batch B7 — DDL that creates lineage: report

**Run 2026-10-03**, against the analyser at commit `911d2a9` (B5 complete), unchanged.
Every key was written before the analyser first saw its file, and decisions D-31 to D-34
were taken before any key. **Nothing has been fixed.** This is the review point.

```
.\.venv\Scripts\python.exe stress/pg/triage.py b7_ddl_lineage
```

## 1. Headline

**Strict: 2 of 200 statement-level facts found, 60 false positives, 15 refusals.** This is
the weakest batch by far, and most of it is one naming defect. An object created in the
script (`CREATE TABLE mart.ch_rev AS …`) is written to as `ch_rev`, not `mart.ch_rev`:
D-25's schema reaches only relations the dictionary knows (B7-01). Nearly every
miss / false-positive pair differs by that name alone.

To see the defects behind it, the keys were re-scored with created objects named bare (the
scratch tool `created_bare.py`; a diagnostic, not a score):

| Test | Statements | Refused | Strict, per statement | Created objects bare, per statement |
|---|---|---|---|---|
| T1 Breadth | 18 | 6 | 0 / 66 | 18 / 66 |
| T2 Depth | 9 | 0 | 0 / 33 | 3 / 33 |
| T3 Interaction | 10 | 2 | 0 / 27 | 10 / 27 |
| T4 Adversarial | 13 | 4 | 0 / 17 | 6 / 17 |
| T5 Metamorphic | 16 | 3 | 2 / 57 | 18 / 57 |
| **B7 total** | **66** | **15** | **2 / 200** | **55 / 200 (28%)** |

The statement counts also undercount. REFRESH, ATTACH PARTITION and DETACH PARTITION are
**not counted at all** (B7-08).

**What held**, by the bare view:

- CREATE TABLE AS with and without a column list, including the GROUP BY influence and
  the WHERE filter;
- CREATE VIEW, a view's column list and CREATE MATERIALIZED VIEW, each as a write into
  the new object (but see B7-01 for its name);
- CREATE OR REPLACE VIEW's own lineage;
- INSERT into a table declared in the script with an explicit column list;
- pairs P1 and P5.

## 2. Findings, by root cause

### Analyser defects

| ID | What | Where | Grade |
|---|---|---|---|
| **B7-04** | **A temp table's unqualified name binds to the REAL table of that name.** `CREATE TEMP TABLE orders …; INSERT INTO orders SELECT … FROM staging.payments` is reported as a write into **`staging.orders`**, the permanent table, through D-24's unique-name binding, because nothing knows pg_temp holds an `orders`. The next statement, reading the temp `orders`, is refused ("Unknown column: note"). | T4 S6b, S6 | **R0, wrong edge into a real table** |
| **B7-01** | **Objects the dictionary does not know are named bare.** A CTAS, view, materialized view, temp table or partition created in the script is the node `ch_rev`, not `mart.ch_rev`; a temp table is not `pg_temp.…`. D-25 is applied only where `Dictionary.resolve` succeeds. | every create in T1–T5 | **R0, wrong node identity** (60 false positives) |
| **B7-02** | **SELECT … INTO writes to `<result>`.** PostgreSQL's SELECT INTO is CREATE TABLE AS, but it is analysed as a bare query, with an "INTO arity mismatch" boundary. | T1 S4; T5 P2 A | **R0, wrong target** |
| **B7-05** | **CREATE TABLE IF NOT EXISTS … AS on an existing table still emits its edges.** PostgreSQL skips it. | T4 S8 | **R0, wrong edge** (forbidden) |
| **B7-06** | **CREATE TABLE AS … WITH NO DATA emits edges**, though no rows move (my application in OBJECTIVES.md). | T1 S3 | R0 if the application stands |
| **B7-07** | **INSERT … OVERRIDING SYSTEM VALUE does not parse** (SQLGlot). The statement is declared `parse_failure` and not counted. | T1 S20 | R3 |
| **B7-08** | **REFRESH MATERIALIZED VIEW, ALTER TABLE … ATTACH PARTITION and DETACH PARTITION are silent**: not counted, not refused, nothing declared. D-32 and D-34 give the first two lineage, so their silence is a silent loss. | T1 S11, S17, S19; T5 P4 B, R1d | **R1, silent** |

### Decided, not yet implemented (expected)

| Decision | Effect | Where |
|---|---|---|
| **D-31** in-script objects registered; views inlined | a later read of a created view, table, materialized view or temp table is "relation not in dictionary". Every inlined-view fact downstream is lost, including the UPDATE and MERGE that read a view (T3 S3, S4) | T1 S5, S7, S9, S12; T2 S2–S9; T3 S2–S5b; T4 S2, S4, S5, S7; T5 P3 A, R1b, R1c, R1e |
| **D-32** REFRESH re-derives | silent (B7-08) | T1 S11; T5 R1d |
| **D-33** plain CREATE TABLE counted; generated columns | refused "CREATE TABLE without AS SELECT": **15 refusals**, every generated column lost | T1 S13, S15, S17a, S18, S20a; T3 S6, S7; T4 S5b, S6a, S9; T5 P1, P4 |
| **D-34** partition / child → parent | no edges: PARTITION OF and INHERITS are refused (D-33), ATTACH is silent (B7-08) | T1 S16–S18; T3 S7; T5 P4 |

### Metamorphic pairs (T5)

P1 and P5 are equal, but only with created objects named bare (B7-01 hits both halves
alike). **P2 differs** (B7-02: SELECT INTO to `<result>`). **P3 differs** (D-31: the view
read is lost; the derived table works). **P4 is equal only because both halves are
empty** (D-33, D-34, B7-08).

## 3. Recommended fix order (for agreement, not yet started)

| # | Item | Why here |
|---|---|---|
| 1 | **D-31 with B7-01 and B7-04**: an in-script catalog | one mechanism. CREATE, DROP and ALTER register objects in script order, with their schema (pg_temp for temp tables) and columns. The binder (`relations.bind`) checks it first (pg_temp ahead of everything), which fixes B7-04. Created objects are named `schema.table` (B7-01). Views are inlined from their registered definition, as dictionary views are; tables are nodes |
| 2 | **B7-02, B7-05, B7-06** | SELECT INTO restated as CTAS; IF NOT EXISTS against the dictionary or catalog; WITH NO DATA emits nothing (and still registers) |
| 3 | **D-33** CREATE TABLE | counted, no refusal; generated columns give derived edges from the columns they read; LIKE and INHERITS register their columns |
| 4 | **D-34, B7-08** partitions and REFRESH | PARTITION OF, ATTACH and INHERITS give child → parent identity, and DETACH is counted. REFRESH re-emits the registered definition (D-32), or is declared when it isn't known |
| 5 | **B7-07** OVERRIDING SYSTEM VALUE | strip the clause before parsing; it changes nothing about lineage |

## 4. Coverage checklist (programme plan §1, row B7)

| Construct | Exercised |
|---|---|
| CREATE TABLE AS (column list, WITH NO DATA, IF NOT EXISTS), SELECT INTO | T1 S1–S4; T2 S5; T4 S8; T5 P1, P2 |
| views: column list, on views, CREATE OR REPLACE, DROP, `*` | T1 S6–S9; T2 S1–S4; T3 S1, S5; T4 S1–S5, S7; T5 P3, P5, R1b |
| materialized views and REFRESH | T1 S10–S12; T2 S6; T5 R1c, R1d |
| temp tables, pg_temp shadowing | T1 S4, S5; T2 S7–S9; T4 S6; T5 R1a |
| generated and identity columns, OVERRIDING SYSTEM VALUE | T1 S13, S20; T3 S6; T4 S9 |
| PARTITION OF, ATTACH, DETACH, INHERITS, LIKE | T1 S15–S19; T3 S7; T5 P4 |

**Deferred:** ALTER TABLE … RENAME (an object renamed mid-script); views with `WITH CHECK
OPTION` or security barriers (no lineage difference); `CREATE TABLE … AS EXECUTE` (B10,
prepared statements); and composite types (`CREATE TYPE`, which `jsonb_populate_record`
would need, deferred from B5).

## 5. Corrections while designing, stated

- **The T5 card first listed "a temp table vs a CTE".** By D-31 they are not equivalent: a
  temp table is a node, so lineage takes two hops. It was replaced by "a view column list
  vs projection aliases" before any key was written.
- **Two applications are mine, stated in OBJECTIVES.md:** temp tables are named
  `pg_temp.<name>`, and WITH NO DATA moves no rows. Overrule either and its facts change.
- **The schema is unchanged.** Every object B7 needs is created by the scripts themselves,
  which is the point of the batch.

## 6. Fixes applied, 2026-10-04 — in the agreed order

**One mechanism carries most of it: a script catalog.** `analysis/catalog.py` keeps an
overlay of the dictionary and updates it statement by statement. CREATE registers an
object with its schema, its columns and, for a view, its definition; DROP removes it.
Every resolver already reads the dictionary, so the overlay is all they need. A created
object resolves, is named `schema.table` (D-25), and a view is inlined by the existing
view inliner. The catalog also says what each DDL statement contributes by itself.

| # | Item | Change | Where |
|---|---|---|---|
| 1 | D-31, B7-01, B7-04 | the catalog. A temp table is registered in `pg_temp`, which `relations.schema_for` puts ahead of every schema. A view is registered as a table while its own CREATE is analysed (a target, never inlined into itself), then as a view with its text. The view inliner now resolves schema-qualified names (`mart.v_paid` went through the default schema and was missed) and never expands a write's target | `catalog.py`; `band0` statement loop, `_inline_views`; `relations.schema_for` |
| 2 | B7-02, B7-05, B7-06 | SELECT … INTO is restated as CREATE TABLE AS, and def-use stands aside for it (its INTO-variables reading raised "arity mismatch"). IF NOT EXISTS against a known object, and WITH NO DATA, are counted with no edges | `catalog`; `defuse.analyse_statement` |
| 3 | D-33 | a plain CREATE TABLE is counted, not refused. Each GENERATED column gives derived edges from the columns its expression reads (the transform ladder, D-19, included). LIKE contributes columns, not lineage | `catalog._create_table` |
| 4 | D-34, B7-08, D-32 | PARTITION OF, INHERITS and ATTACH give child → parent identity for the shared columns. DETACH is counted. REFRESH is re-analysed as an INSERT of the materialized view's stored query, or counted and declared `source_unavailable` when the definition is not known | `catalog._attach`, `_refresh`; `band0` loop |
| 5 | B7-07 | `OVERRIDING {SYSTEM|USER} VALUE` is blanked in the source before parsing, same length so every line and column stays put | `procedure.analyse_source` |

The assumption boundary (`context_dependent_binding`) no longer fires for an unqualified
name that a temp table holds, because pg_temp is searched first and the binding is
certain. `triage.py` now recognises ALTER, REFRESH and DROP as the start of a statement;
before, it attributed an ATTACH's edges to the wrong line.

### Result

**B7: 202 of 202 per statement, 0 false positives, 0 refusals, 0 boundaries, all five pairs
equal.** Pinned in `tests/test_pg_batches.py`. The regression tests are in
`tests/test_b7_ddl_lineage.py`: all 12 fail on the unfixed source. The unknown-REFRESH test
is a control for the declaration, but it fails there too, because the unfixed analyser was
silent on REFRESH (B7-08).

### What the fixes reached outside this batch

* **Key correction (T5):** the `CREATE VIEW mart.v_seg` before pair P3 writes lineage into
  the view and was not keyed. Its two facts were added after the first run, marked in the
  keyspec. Nothing else in any key moved.
* **A defect of mine was caught on the way:** a heredoc turned `\b` into a backspace
  character in the OVERRIDING pattern, so it never matched. It was found because T1 S20
  still missed, and it was fixed before commit.
* **Oracle:** unchanged. The view inliner change (schema-qualified names, targets
  skipped) is the one part that reaches Oracle. Against `911d2a9`, the Oracle stress
  packages' edges are identical and `run_stress.py` scores are identical; the only
  output difference is where one SQLGlot parser warning is printed.
