# Plan: the routine-semantics layer by meaning, not by form (branch `semantic-ir`)

Started 2026-10-16 from main at 6d08998 (the rollback point). Background: the baseline in
`.holdout-runs/baseline-ccaa793/`, the diagnosis in `docs/handoff-2026-10-16.md` section 3,
and the research in `reports/Procedural SQL column lineage.md`.

## The change

```
TODAY   reader stmt kind ──► one desugar branch per kind ──► no branch = refused, subtree lost
AFTER   reader stmt kind ──► LOWER to a primitive ──► one mechanism per primitive
                                                      no mechanism = OPAQUE: labelled
                                                      conservative edges, subtree kept
```

| Primitive | Meaning | Forms lowered to it |
|---|---|---|
| ASSIGN | target := expression | `:=`, declaration defaults, `r.f :=`, `NEW.f :=`, `OLD.f :=` |
| BIND | targets := a query's row | SELECT INTO (one or several targets, `NEW.f`), FETCH, FOR-over-query, DML RETURNING INTO |
| EMIT | the routine yields rows / a value | RETURN, RETURN QUERY, RETURN QUERY EXECUTE, RETURN NEXT, OUT/INOUT params, final query of a LANGUAGE sql / BEGIN ATOMIC body |
| EFFECT | statement writes a relation | INSERT / UPDATE / DELETE / MERGE / COPY (band 0, unchanged) |
| DYN | text that becomes SQL | EXECUTE, RETURN QUERY EXECUTE, FOR ... IN EXECUTE, OPEN ... FOR EXECUTE |
| CALL | another routine | scalar call, row source in FROM / LATERAL, CALL / PERFORM |
| BRANCH | decides which rows / writes | IF, CASE, WHILE, EXIT/CONTINUE WHEN, loop WHERE |
| OPAQUE | not mapped yet | anything else - conservative, labelled edges, never zero |

The parsers (pglast / libpg_query, our PL/pgSQL reader, SQLGlot) do not change.

## To do

| # | Task | Fixes | Status |
|---|---|---|---|
| T0 | Lineage contract: `\gset` variables, row-deciding join keys, R2, D-42 USING (`BIND_USING`) | H4, ~4.6 pts | decided 2026-10-16: `\gset` = BIND; `\set` substituted as psql does (D-36 amended). Pending: join keys, R2, USING |
| T1 | Gate script: all holdouts vs the baseline, thresholds, one verdict | go / no-go | done |
| T2 | `analysis/routine_ir.py`: the primitives and ONE lowering table from the reader's tree | the base | table + output shapes done; dispatch moves over with T3/T4 |
| T2b | Variables scoped per routine (5 of holdout4's 39 variable nodes are shared by unrelated routines) | precision | |
| T3 | EMIT: one routine output shape (signature + dictionary) and one row-source mechanism for every emit form | H5, H6, H7 | done: RETURN NEXT / OUT-param routines emit into an output relation; holdout4 keys 89.2% |
| T4 | ASSIGN / BIND to any target, NEW/OLD fields included | H8 | done: NEW.f := / INTO NEW.f write the trigger table; entry 3 32/32; column-level 98.8% |
| T5 | DYN: one fold for every position, `format()` positional args, `$q$` texts, named call arguments | H7 | done: holdout4 77.2% -> 84.6% (keys 79.8% -> 87.5%), holdout1/3 unchanged |
| T6 | BEGIN ATOMIC read as one statement, body as a LANGUAGE sql body | H2 | |
| T7 | OPAQUE: a statement not followed gives labelled conservative edges, not zero | cascade | |
| T8 | psql (static): COPY FROM STDIN data skipped, `FETCH :n`, `MOVE`, `\gset` per T0 | H1, H3, H4 | done: holdout4 keys 92.5%, column-level 98.2% (passes); H1 rest = join keys (T0) |
| T9 | Scan and ledger agree on every new form (holdout4's 8 mismatches) | scan gate | |
| T10 | Retire the form-specific branches T2-T7 replaced; full suite green | cleanup | |
| T11 | Oracle on PG17 (auto_explain, pg_depend), checklist v2 matrix | find gaps early | after go |
| T12 | Go / no-go: gate run, FINDINGS entry, merge or roll back | decision | |

## Gate (T1, T12)

Merge only if all of these hold; otherwise main stays at 6d08998.

| Check | Threshold |
|---|---|
| holdout1 recall / with keys | no regression from 97.5% / 99.6% |
| holdout3 recall / with keys | no regression from 93.5% / 95.3% |
| holdout4 recall with keys | >= 95% (baseline 79.8%) |
| column-level, every holdout | >= 98% (holdout4 baseline 95.7%) |
| scan vs ledger, every holdout | 0 mismatches |
| silent statements | 0 |
| full test suite | green; any changed expectation justified in the commit |

Holdout4 is no longer blind once we work against it. The gate proves the mechanisms; only
a blind holdout5 from the frozen brief (`docs/holdout-brief.md`) proves generality.
