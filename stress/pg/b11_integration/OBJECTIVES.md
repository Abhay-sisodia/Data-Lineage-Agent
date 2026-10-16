# Batch B11 — Integration: objective cards

**Status:** drafted 2026-10-04. Decisions D-43 and D-44 were taken before any key was
written. The SQL and keys are written blind, then run; results are in `REPORT.md`.

**Scope (programme plan §1, row B11):** production-shaped warehouse pipelines that mix the
constructs of every earlier batch (B1–B10). Run last, as a check that the parts hold
together. **No new construct is introduced**, and every node and convention is as decided,
D-1 to D-42.

## Decisions taken 2026-10-04, before any B11 key was written

### D-43 · B11-QA — the pipelines avoid shapes whose convention is still open

B11 measures whether the DECIDED rules compose. Where a real pipeline would reach for a
shape whose convention is undecided, the files use an equivalent decided spelling instead.
Each place is listed here, so the open questions stay visible.

| Open question | What a pipeline would write | What B11 writes instead |
|---|---|---|
| semi- / anti-join match keys (B2 P5, P6) | SCD2 change detection by `EXISTS` / `NOT IN` | changed rows chosen by a scalar-subquery watermark in WHERE (decided: a filter, B2) |
| non-equality join conditions (B1-Q2) | `c.segment IS DISTINCT FROM d.segment` across a join | the watermark above; no cross-relation inequality anywhere |
| a single-relation predicate inside an inner join's ON | `JOIN d ON d.id = c.id AND d.is_current` | `d.is_current` moved to WHERE |
| a loop UPDATE's `WHERE t.k = r.f` (B8, awaiting confirmation) | a cursor loop that updates by key | loops that INSERT; updates are set-based |
| GROUP BY influence onto a window computed over the aggregate | `RANK() OVER (… ORDER BY SUM(x))` projected | rankings are filtered (D-9) and not projected |

### D-44 · B11-QB — five pipelines, not the T1–T5 gradient

| File | Pipeline |
|---|---|
| **T1 Models** | a dbt-style staging-to-mart model set: cleaning views, a CTE-heavy CTAS fact over the views, a grouped mart load from the fact table, a top-N with a filtered ranking, and a materialized view with REFRESH |
| **T2 SCD2** | a slowly-changing customer dimension: a temp table of changed rows, the close-out UPDATE … FROM, the insert of new versions with a lookup subquery, and a PL/pgSQL procedure that MERGEs the mart and logs per region in a loop |
| **T3 Semi-structured** | a fact build from document input: a new partition, loaded from jsonb extractions; unnest WITH ORDINALITY; jsonb_to_recordset; a trigger on the fact; a read of the partitioned parent |
| **T4 Legacy deploy** | a messy psql deploy: `\set` / `\echo` / `\i`, a search_path, quoted mixed-case names, PREPARE / EXECUTE, nested comments, `E''` and dollar quotes, constant dynamic SQL in a DO block |
| **T5 Twice** | must-equal pairs: a view vs a CTE; set-based vs a FOR loop; a direct UPDATE vs PREPARE + EXECUTE; three jsonb extraction spellings; SELECT INTO TEMP vs CREATE TEMP TABLE AS. Then one realistic incremental load procedure with a watermark variable |

## Harness

Unchanged from B8: keyspecs use `origin <unit> <band>` and `var:<name>` where procedural
code is involved, and `triage.py` prints every band. Each routine opens with a DECLARE, as
in B8, so the per-statement view lands each write on its own line.
