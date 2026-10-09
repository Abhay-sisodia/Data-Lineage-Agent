# Final PostgreSQL acceptance suite: consolidated findings

**2026-10-07**, against the analyser at commit `f46f8e0`, unchanged throughout. Nothing has
been fixed yet: this list is the input to the fix round.

```
.\.venv\Scripts\python.exe stress/pg/final/run.py                         all 15 tests
.\.venv\Scripts\python.exe stress/pg/final/probe_limitations.py           the Step 0 probes
```

## 1. Where the analyser stands

| Tier | Tests | Lines | Facts | Found | False positives | Missed | Pass (closed world) |
|---|---|---|---|---|---|---|---|
| Easy (E1–E5) | 5 | 442 | 230 | 230 | 0 | 0 | 5 / 5 |
| Medium (M1–M5) | 5 | 905 | 366 | 363 | 0 | 3 | 3 / 5 |
| Hard (H1–H5) | 5 | 2,289 | 773 | 762 | 0 | 11 | 2 / 5 |
| **All** | **15** | **3,636** | **1,369** | **1,355** | **0** | **14** | **10 / 15** |

- **Precision 100%, recall 99.0%**, after D-56 (2026-10-07) relabelled E2's one disputed
  filter phase.
- **Before D-56:** E2 counted 1 false positive and 1 miss, both the same fact
  (`po_header.created_at`) with the phase the key then held. The key was relabelled under
  D-56, not to fit the analyser; FE-01's defect is the single-key case, which the suite does
  not contain.
- **Refusals and declarations:**
  - 1 refusal, expected and correct (TRUNCATE);
  - 1 boundary, expected and correct (D-53);
  - **no unexpected refusal or boundary in any test**;
  - all 20 metamorphic groups identical;
  - none of the 70 forbidden edges produced.
- **Every one of the 15 misses is silent,** with nothing declared. Each traces to one of the
  findings below, isolated by a probe.

Of the user's two bars:
- **"No false positives":** met. No invented edge anywhere.
- **"Analysed or clearly refused":** not yet met. FH-01..FH-04, FM-01..FM-03 and F0-01 are
  silent losses: neither analysed nor refused.

## 2. Risk-ranked fix list

| Rank | ID | Grade | Finding | Blast radius |
|---|---|---|---|---|
| 1 | ~~**F0-01**~~ **fixed 2026-10-07** | R1 | Statements SQLGlot keeps as a raw `Command` vanish: statements seen 0, nothing declared. Affected: `CREATE MATERIALIZED VIEW … WITH [NO] DATA` / `TABLESPACE`, `CREATE RECURSIVE VIEW`, `CREATE VIEW … WITH CHECK OPTION`, `CREATE TEMP TABLE … ON COMMIT DROP AS`. | A whole statement's lineage, and coverage reads complete. `ON COMMIT DROP` and `WITH DATA` are common in production scripts. |
| 2 | ~~**FH-01**~~ **fixed 2026-10-07** (H1 194 -> 201) | R1 | Under `WITH RECURSIVE`, a CTE named like the base table it reads (`item_master AS (… FROM lgx.item_master)`) is taken for a self-reference and loses all lineage. | Every column of the CTE. Naming a dedup CTE after its table is a common style (the shipment sample does it). |
| 3 | ~~**FH-04**~~ **fixed 2026-10-07** (H1 201 -> 202) | R1 | GROUP BY influence of set-operation arms is dropped when the set operation is a derived table or a CTE (kept at top level). | Every UNION-of-aggregates CTE (waterfalls, pivots, "stacked" metrics). |
| 3a | ~~**FH-05**~~ **fixed 2026-10-07** | R1 | A bare `EXCEPT` / `INTERSECT` at script level vanishes: seen 0, nothing declared (`UNION` and the `INSERT` form work). Found while fixing FH-04. | Every set-difference report query. |
| 4 | ~~**FH-02**~~ **fixed 2026-10-07** (H4 159 -> 160) | R1 | `MERGE … WHEN NOT MATCHED THEN INSERT VALUES (…)` with no column list gives nothing. | Every column only the INSERT arm writes. |
| 5 | ~~**FH-03**~~ **fixed 2026-10-07** (H2 154 -> 156) | R1 | A ranking cut (`rn = 1`) in an INNER JOIN's ON is not a filter (it is in WHERE). | The partition and ordering filters of a common dedup-join idiom. |
| 6 | ~~**FM-01**~~ **fixed 2026-10-07** (M4 46 -> 47) | R1 | An ordinality column used as a DISTINCT ON or LIMIT ordering is dropped: no filter on the main path (`ORDER BY u.n LIMIT 5` gives nothing), no influence on the null side. | "First element of the array" picks. |
| 6a | ~~**FM-02**~~ **fixed 2026-10-07** (M3 44 -> 45) | R1 | CYCLE's implicit guard gives no filter on the cycle column (D-54); `pg_grammar._restate_search_cycle` adds the mark and path, not the guard. | Every recursive CTE written with CYCLE. |
| 6b | ~~**FM-03**~~ **fixed 2026-10-07** (M3 45 -> 46) | R1 | `COUNT(*)` over an already-grouped CTE does not inherit the inner GROUP BY's influence (D-55). | Counts of groups (islands, sessions, buckets). |
| 7 | ~~**F0-04**~~ **fixed 2026-10-07** | R2 | `CREATE [UNIQUE] INDEX` is refused; it should be counted with no lineage (as D-33 treats CREATE TABLE). | Parse coverage of DDL-heavy scripts. |
| 8 | ~~**F0-02**~~ **fixed 2026-10-07** | R3 | `t.*` as a whole-row value (`row_to_json(o.*)`, `(o.*)::text`) is declared unresolved with no edges; `to_jsonb(o)` works (D-20). | Hash and audit columns. |
| 9 | ~~**F0-07**~~ **fixed 2026-10-07** | R3 | A parenthesised `(VALUES …)` set-operation arm is refused (unparenthesised works). | Rare. |
| 10 | ~~**F0-03**~~ **fixed 2026-10-07** | R4 | About 24 built-ins are declared `source_unavailable` as if they were unread user functions. Among them: `to_tsvector` / `to_tsquery` / `plainto_tsquery` / `setweight` / `ts_rank_cd`, `xmlattributes`, `jsonb_array_length`, `pg_typeof`, `current_setting`, `array_fill`, `trim_array`, `unistr`, `cosd`, `atan2d`, `point`, `convert_to`, `pg_input_is_valid`, `random_normal`, `statement_timestamp`, `transaction_timestamp`. The edges are right; the declaration is false. | Every closed-world run that uses one: an unexpected boundary. |
| 10a | ~~**FH-06**~~ **fixed 2026-10-07** | R4 | A scalar subquery over a derived table holding a window declares the window's PARTITION BY / ORDER BY columns unresolved; the edges are right. Found while fixing FH-03. | Noise boundaries on a common correlated-lookup shape. |
| 11 | ~~**F0-05**~~ **fixed 2026-10-07** | R4 | Spurious `unresolved_identifier` for `SYSTEM_USER`, and for `"default"` in `COLLATE pg_catalog."default"`. | Noise boundaries. |
| 12 | ~~**FE-01**~~ **fixed 2026-10-07** | R4 | Under a row limit over an aggregated query, a grouping-key sort key on its own is reported pre-aggregation. D-56 makes every sort key there post; beside an aggregate key it already is. | One attribute of a true edge. |
| 13 | ~~**F0-08**~~ **fixed 2026-10-08** | R4 | A refusal is located at the first comment line above its statement, not at the statement. | Report line numbers. |
| 14 | **F0-06** | R4 | `UPDATE … SET (a, no_such_col) = (…)` writes edges into a column the target lacks, undeclared (invalid SQL; PostgreSQL rejects it). | Suspect-SQL hygiene only. |

## 3. Decisions taken (2026-10-07, recorded in `stress/FINDINGS.md`)

| ID | Decision | Effect |
|---|---|---|
| **D-54** (FM-Q1) | CYCLE's implicit guard is a FILTER on the cycle column, like an explicit `WHERE NOT x = ANY (path)`. | FM-02, an R1 fix in `pg_grammar._restate_search_cycle`. M3's key already held this reading. |
| **D-55** (FM-Q2) | An inner GROUP BY influences an aggregate over its groups one level up, `COUNT(*)` included. | FM-03, an influence-walker fix. M3's key already held this reading. |
| **D-56** (FE-01) | A row limit (LIMIT / FETCH / OFFSET, DISTINCT ON) over an aggregated query is post-aggregation for every sort key; a WHERE stays per column (D-6). | E2's key relabelled (`created_at` filter: post); E2 now passes. FE-01 narrows to the single-key case. |

## 4. Mine: key errors

| ID | What |
|---|---|
| KE-01 | E2: the blind key missed the influence of `string_agg(… ORDER BY currency_code)`'s own ORDER BY (B1 S11). The analyser was right; the key was corrected and marked. |

## 5. Readings the analyser confirmed (no longer open)

- A `ROW_NUMBER() … rn = 1` dedup, or a `rank <= 3` cut, inside a LEFT-joined CTE gives
  INFLUENCE on what that side feeds (D-13 / D-45 read as an ordering cut). Flagged in E1 and
  H1; agreed both times.
- D-45's exception holds through a NOT IN. A CTE read both through a LEFT join and inside the
  row-deciding NOT IN keeps its WHERE as a filter only (H1 `site_geo`).

## 6. Not reached by the suite

By design (LIMITATIONS.md):
- the SQLGlot parse limits X-1..X-5;
- PL/pgSQL;
- psql meta-commands.

The suite also avoided the shapes whose findings the probes had already isolated (F0-*),
so the tests measure what remains.
