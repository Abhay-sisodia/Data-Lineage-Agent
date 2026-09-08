"""Run the corpus under a stated protocol, so there is something to witness (T3.5b).

    .\\.venv\\Scripts\\python.exe scripts\\exercise_corpus.py

**Kept separate from `capture_witness.py` on purpose.** If capture also caused the
executions it recorded, the witness would be a record of its own footprints, and
"unexercised" would mean "the capture script did not think to call it" rather than "the
estate never ran it". Two scripts, one causal direction.

**The protocol is the evidence.** `unexercised` is a claim about what did *not* run, so
what ran has to be written down rather than left to whoever ran the script. Three rules:

1. **Every compiled procedure is called once**, in dependency order, so absence from the
   witness means something.
2. **`s7_unexercised_branch` is called with `'EU'` and nothing else.** That is the entire
   design of the case: the APAC and YEAR_END arms must stay unexercised, and calling them
   "just to be thorough" would destroy the only unexercised evidence in the corpus.
3. **`b1_02` and the other branchy procedures are called once per branch**, because an
   edge inside an unvisited branch of an ordinary procedure would otherwise look
   unexercised for a reason that is an artefact of this script rather than a fact about
   the code.

A procedure that raises still executed, and its statements up to the failure are
legitimately in the log. Failures are reported rather than swallowed, because a
procedure that failed early leaves a partial witness and the reader needs to know.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lineage.oracle import OracleSettings, connect

ROOT = Path(__file__).resolve().parent.parent

# (call, why this call and not another). The second half is the part that matters: a bare
# list of calls is a script, a list with reasons is a protocol somebody can audit.
PROTOCOL: list[tuple[str, str]] = [
    ("b0_insert_select", "the baseline load"),
    ("b0_merge", "MERGE, both arms"),
    ("b0_select_star", "SELECT * expansion"),
    ("b0_nested_views", "views on views"),
    ("b0_alias_chains", "alias chains"),
    ("b1_local_variables('EU')", "variables carrying values across statements"),
    ("b1_if_case('EU', 1)", "the EU branch, strict"),
    ("b1_if_case('APAC', 0)", "the other branch - an ordinary conditional, not s7's case"),
    ("b1_loops", "accumulation across iterations"),
    ("b1_cursor_for_loop('EU')", "implicit cursor"),
    ("b1_explicit_cursor", "explicit cursor, FETCH INTO"),
    ("b1_temp_tables", "session scratch"),
    ("pkg_policy_state.load_policy('EU')", "package state, written"),
    ("pkg_policy_state.apply_policy", "package state, read in another unit"),
    ("b1_nested_calls", "interprocedural"),
    ("b1_exception_handlers('EU')", "the happy path"),
    ("b1_exception_handlers('BOOM')", "the handler - both are real paths through the code"),
    ("b2_dynamic_constant", "EXECUTE IMMEDIATE, constant text"),
    ("b2_dynamic_concatenated('EU', 'last_login', '')", "concatenated text"),
    ("b2_dbms_sql('EU')", "DBMS_SQL"),
    ("b2_metadata_driven_etl", "config-table-driven"),
    ("b2_triggers", "trigger side effects"),
    ("s1_synonym_redirect", "synonym"),
    ("s3_temp_writer_a", "shared scratch, first writer"),
    ("s3_temp_writer_b", "shared scratch, second writer"),
    ("s4_partition_exchange", "DDL that moves data"),
    ("s5_positional_union", "positional UNION arms"),
    ("s6_write_through_view", "INSTEAD OF trigger"),
    (
        "s7_unexercised_branch('EU')",
        "EU ONLY. The APAC and YEAR_END arms stay unexercised - that is the case, and "
        "calling them would destroy the only unexercised evidence in the corpus",
    ),
    ("s8_identity_copy", "a plain copy"),
    ("s8_aggregated_copy", "SUM over the same endpoints"),
    ("s8_conditional_copy", "CASE over the same endpoints"),
    ("sq_multi_join", "complex SQL band"),
    ("sq_cte_chain", "complex SQL band"),
    ("sq_window_functions", "complex SQL band"),
    ("sq_deep_nesting", "complex SQL band"),
    ("sq_set_operations", "complex SQL band"),
    ("sq_self_join", "complex SQL band"),
    ("sq_unqualified", "complex SQL band"),
]

NOT_RUN = {
    "b2_db_links": "the link target does not exist - parse-only by design",
    "s2_schema_context": "the reporting schema was deliberately never created",
    "u1_*": "parse-only; several constructs reference shapes the seed schema does not carry",
}


def run(cursor: Any, call: str) -> str | None:
    try:
        cursor.execute(f"BEGIN {call}; END;")
    except Exception as exc:
        return str(exc).splitlines()[0]
    return None


def main() -> None:
    connection = connect(OracleSettings())
    failures: list[tuple[str, str]] = []

    with connection.cursor() as cursor:
        for call, why in PROTOCOL:
            error = run(cursor, call)
            connection.commit()
            status = "ok " if error is None else "ERR"
            print(f"  [{status}] {call:<48} {why[:56]}")
            if error:
                failures.append((call, error))

    print(f"\n{len(PROTOCOL) - len(failures)}/{len(PROTOCOL)} calls completed")
    for call, error in failures:
        # A procedure that raised still executed. Its statements up to the failure are in
        # the log and belong in the witness; the reader just needs to know it stopped.
        print(f"  FAILED {call}: {error}")

    print("\nDeliberately not run:")
    for name, why in NOT_RUN.items():
        print(f"  {name:<24} {why}")


if __name__ == "__main__":
    main()
