# Batch B5 — Semi-structured data: report

**Run 2026-10-03**, against the analyser at commit `3fa00f0` (B6 complete), unchanged.
Every key was written before the analyser first saw its file, and decisions D-29 and D-30
were taken before any key. **Nothing has been fixed.** This is the review point.

```
.\.venv\Scripts\python.exe stress/pg/triage.py b5_semi_structured
```

## 1. Headline

| Test | Statements | Value TP/FP/FN | Filter TP/FP/FN | Influence TP/FP/FN | Per statement |
|---|---|---|---|---|---|
| T1 Breadth | 20 | 27 / 2 / 17 | 3 / 0 / 2 | 2 / 0 / 4 | 32 / 55 |
| T2 Depth | 7 | 5 / 0 / 6 | 0 / 0 / 2 | 2 / 0 / 2 | 7 / 17 |
| T3 Interaction | 8 | 15 / 0 / 2 | 4 / 0 / 0 | 1 / 0 / 2 | 21 / 25 |
| T4 Adversarial | 11 | 12 / 0 / 2 | 4 / 0 / 0 | — | 16 / 18 |
| T5 Metamorphic | 17 | 12 / 0 / 2 | 3 / 0 / 0 | 7 / 0 / 0 | 29 / 35 |
| **B5 total** | **63** | **71 / 2 / 29** | **14 / 0 / 4** | **12 / 0 / 8** | **105 / 150 (70%)** |

**Two false positives, six refusals, 33 boundaries.** Nothing is silent: every loss is
refused or declared. Most of the declarations say a built-in is "not in this analysis".

**What held.** D-29 (column-level) already holds for the core of jsonb:

- `->`, `->>`, `#>>`, subscripts, and a key taken from another column;
- `jsonb_build_object` and `_array`, `jsonb_set`, `||` and `-`;
- every predicate in WHERE: `@>`, `?`, `?|`, `?&`, `@?` and IS JSON;
- `jsonb_object_agg`;
- the hstore operators;
- a document built two CTEs down and extracted above, carrying both source columns;
- an ON CONFLICT accumulator merging documents;
- a jsonpath test inside a scalar subquery (D-12);
- every T4 trap except the jsonpath variable;
- pairs P1–P3, P6 and P8.

## 2. Findings, by root cause

### Analyser defects

| ID | What | Where | Grade |
|---|---|---|---|
| **B5-01** | **JSON set-returning functions in FROM are "row source is a function"**: `jsonb_array_elements(_text)`, `jsonb_each(_text)`, `jsonb_to_recordset` and hstore's `each()`. They are B6's case for built-ins B6 did not list. A literal document (T4 S6) still raises the boundary. | T1 S8, S9, S11, S19; T2 S3, S6, S7; T4 S6; T5 P4 A, P5 B | R3, declared loss with a false reason |
| **B5-02** | **Built-ins declared `source_unavailable`**: `JSON_VALUE`, `JSON_QUERY`, `jsonb_object_keys`, `jsonb_path_query(_first)`, `jsonb_array_elements_text` in the select list, `xpath` and `array_to_json`. | T1 S5, S10, S12, S16; T2 S4; T4 S9; T5 P4 B, P7 B | R3 |
| **B5-03** | **An ordered aggregate SQLGlot does not type** (`jsonb_agg`, `xmlagg`) reports its ORDER BY column as an **aggregated value**. B1 settled that it is influence only, and `string_agg`, `array_agg` and `json_agg` get it right. | T1 S7b, S17 | **R0, wrong edge** (both false positives) |
| **B5-04** | **A JSON_TABLE with a PASSING clause does not parse** in SQLGlot (`parse_failure`). It is refused with the other JSON_TABLEs below, but even once D-30 lands it needs its own handling. | T1 S14 | R3 |

### Decided, not yet implemented (expected)

| Decision | Effect | Where |
|---|---|---|
| **D-30** analyse JSON_TABLE / XMLTABLE | every one is refused SHAPE_NOT_DECIDABLE, including the CTE that holds one (T2 S2, whose CTE refusal then dangles its readers) | T1 S13–S15; T2 S2; T3 S2; T5 P5 A |
| **D-29** column-level | already holds wherever the construct itself is supported | — |

### Metamorphic pairs (T5)

P1–P3, P6 and P8 are equal. **P4 and P5 are equal only because both halves are lost**
(B5-01, B5-02, D-30). **P7 differs**: `array_to_json(array_agg(…))` loses its value
(B5-02), while `jsonb_agg` keeps it.

## 3. Recommended fix order (for agreement, not yet started)

| # | Item | Why here |
|---|---|---|
| 1 | **B5-03** ordered aggregates | the only wrong edges; recognise an ORDER BY inside any aggregate's arguments, not just in the typed ones |
| 2 | **B5-01** JSON SRFs in FROM | B6's restatement (`analysis/srf.py`) with the JSON and hstore SRFs listed. A function with a column-definition list (`jsonb_to_recordset … AS r(a int, b text)`) gives every listed column from its argument |
| 3 | **B5-02** built-ins | list them (`analysis/builtins.py`) |
| 4 | **D-30, B5-04** JSON_TABLE / XMLTABLE | restated the same way, as a LATERAL subquery whose columns are derived from the document and the PASSING columns, with ordinality as a ROW_NUMBER ordered by the document (D-26). PASSING needs the text read before SQLGlot, since it cannot parse it. The refusal for these constructs leaves the register |

## 4. Coverage checklist (programme plan §1, row B5)

| Construct | Exercised |
|---|---|
| jsonb operators and subscripts | T1 S1, S2, S4, S6; T2 S1; T3 S1, S3, S7, S8; T4 S1–S4, S7, S8; T5 P1–P3, P6 |
| builders, modifiers, SQL/JSON functions | T1 S3–S5; T2 S1, S7; T3 S5, S6; T4 S9 |
| aggregates (jsonb_agg, jsonb_object_agg, xmlagg) | T1 S7a, S7b, S17; T2 S7; T5 P7 |
| jsonb SRFs (elements, each, keys, to_recordset, path_query) | T1 S8–S12; T2 S3, S6, S7; T4 S6; T5 P4, P5 |
| JSON_TABLE (ordinality, PASSING) and XMLTABLE | T1 S13–S15; T2 S2; T3 S2; T5 P5 |
| xpath and xml builders | T1 S16, S17; T2 S4 |
| hstore (operators, subscript, each, hstore_to_jsonb) | T1 S18, S19; T2 S5; T4 S11; T5 P8 |

**Deferred:** `jsonb_populate_record(set)` (it needs a composite type; the schema has
none), `JSON_TABLE` `NESTED PATH` (exercised only in the card; no statement uses it),
`xmlnamespaces`, and `json_*` (non-b) functions beyond `json_agg`.

## 5. Corrections while designing, stated

- **The T5 card first listed `hstore -> 'k'` vs `(each(h)).value`.** That is not an
  equivalence (`each` produces rows), and it was replaced by `->` vs a subscript before the
  run.
- **The schema gained two tables (decision B5-QC)**, and every B1–B9 pin passed after the
  rebuild.
- **The files run to 96–211 lines.** The scope is wide but shallow per construct.

## 6. Fixes applied, 2026-10-03 — in the agreed order

| # | Item | Change | Where |
|---|---|---|---|
| 1 | B5-03 | An ORDER BY inside any built-in aggregate is influence, not only inside the aggregates SQLGlot types: `jsonb_agg` and `xmlagg` parse as Anonymous | `band0._window_influence_columns` |
| 2 | B5-01 | B6's restatement covers the JSON and hstore SRFs, by output shape. One-column functions take PostgreSQL's default name (`value`, or the alias). `jsonb_each` and `each()` give `key` and `value`. A record function (`jsonb_to_recordset … AS r(a int, b text)`) gives each column its column-definition list names | `analysis/srf.py` |
| 3 | B5-02 | SQL/JSON (`JSON_VALUE`, `JSON_QUERY`, `JSON_EXISTS`), jsonpath, the JSON and hstore SRFs in the select list, `xpath`, `hstore_to_jsonb` and `array_to_json` are built-ins | `analysis/builtins.py` |
| 4 | D-30, B5-04 | JSON_TABLE and XMLTABLE are restated **in the source text**, before any front end, classifier or SQLGlot reads it, as `LATERAL (SELECT …)` with one projection per declared column. Each column reads the document and every PASSING expression; an ordinality column is a ROW_NUMBER ordered by the document (D-26); NESTED PATH columns are flattened. The line count is kept. A shape the scanner cannot read is left as written, and so is still refused by name | new `analysis/document_tables.py`, called from `procedure.analyse_source` |

The source text is the one place D-30 could live. A first attempt restated each statement
inside band 0. It produced the edges, but `classify_program` scans the raw source
separately: the old refusal came back and every such statement was counted twice. That
version was replaced before anything was committed.

### Result

**B5: 150 of 150 per statement and per file, 0 false positives, 0 refusals, 0 boundaries,
all eight pairs equal.** Pinned in `tests/test_pg_batches.py`. The regression tests are in
`tests/test_b5_semi_structured.py`: 9 of 11 fail on the unfixed source, and the 2 that pass
are controls (a JSON_TABLE inside a string literal is left alone, and a literal key names
no column).

### What the fixes reached outside this batch

* **The PG-20 tests (`test_cte_scoped_refusal.py`) swapped their poisoned construct.** They
  used JSON_TABLE as the example of a construct refused inside one CTE, and D-30 made it
  analysable. They now use a PIVOT over a subquery, still SHAPE_NOT_DECIDABLE. The tests are
  about the refusal mechanism, and all 19 pass unchanged in what they assert.
* **The XMLTABLE / JSON_TABLE refusal stays in the register** for Oracle, and as the
  PostgreSQL fallback for any shape the restatement cannot read.
