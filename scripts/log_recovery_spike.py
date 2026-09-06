"""Run the dynamic-SQL log-recovery experiment (T2.9).

    .\\.venv\\Scripts\\python.exe scripts\\log_recovery_spike.py

Executes the band-2 dynamic-SQL procedures against the real database, reads what actually
ran out of V$SQL, and measures what fraction can be tied back to the procedure that
emitted it.

**Both cases are measured, and the second is the one that matters.**

* FAVOURABLE — the procedure sets MODULE/ACTION itself. This is what a well-instrumented
  codebase looks like, and week 0 already confirmed the attributes survive into V$SQL.
* UNFAVOURABLE — nothing sets them, which is what the blind spot register warns is common
  when a scheduler drives the call. Measuring only the favourable case would produce a
  number that flatters the architecture exactly where it is weakest.

The output is a percentage. Below roughly 60% the coverage story for compliance work gets
uncomfortable, and the plan is explicit that this needs to be known in week three rather
than discovered later.

KNOWN LIMITATION OF THIS EXPERIMENT: V$SQL is a shared pool that survives across runs, so
a statement executed by an earlier run is still there. Counts here are therefore slightly
polluted by history. The correct method for a real collector is to snapshot EXECUTIONS
before and after and count only statements whose execution count increased; first-load
time does not work, because a cached statement keeps its original timestamp when it is
re-executed. This does not affect the headline finding, which is about whether an emitter
can be identified at all.
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.logrecovery import (
    Emitter,
    LoggedStatement,
    literal_fragments,
    recover,
)
from lineage.oracle import OracleSettings, connect

ROOT = Path(__file__).resolve().parent.parent
BAND2 = ROOT / "corpus" / "adversarial" / "band2"

# The dynamic-SQL procedures, and the calls that exercise them.
FAVOURABLE = [
    ("b2_dynamic_concatenated", "b2_dynamic_concatenated('EU', 'is_active', 'recent')"),
    ("b2_dbms_sql", "b2_dbms_sql('EU')"),
    ("b2_metadata_driven_etl", "b2_metadata_driven_etl"),
]

# Sets no MODULE/ACTION at all - the scheduler-driven shape.
UNFAVOURABLE = [("b2_dynamic_constant", "b2_dynamic_constant")]

SOURCES = {
    "b2_dynamic_concatenated": "b2_02_dynamic_concatenated.sql",
    "b2_dbms_sql": "b2_03_dbms_sql.sql",
    "b2_metadata_driven_etl": "b2_04_metadata_driven_etl.sql",
    "b2_dynamic_constant": "b2_01_dynamic_constant.sql",
}


def _emitters(units: list[str]) -> list[Emitter]:
    emitters = []
    for unit in units:
        source = (BAND2 / SOURCES[unit]).read_text(encoding="utf-8")
        emitters.append(
            Emitter(
                unit=unit.upper(),
                fragments=literal_fragments(source),
                action=unit,  # the procedures set ACTION to their own name
            )
        )
    return emitters


def _run(cursor, calls: list[tuple[str, str]], clear_module: bool) -> None:
    for _, call in calls:
        if clear_module:
            # Simulate a scheduler invocation: no session attributes at all.
            cursor.execute("BEGIN DBMS_APPLICATION_INFO.SET_MODULE(NULL, NULL); END;")
        try:
            cursor.execute(f"BEGIN {call}; END;")
        except Exception as exc:
            print(f"  ({call} raised {str(exc).splitlines()[0]})")


def _read_log(cursor, patterns: list[str]) -> list:
    from lineage.analysis.logrecovery import LoggedStatement

    found = []
    for pattern in patterns:
        cursor.execute(
            """
            SELECT sql_id, sql_text, module, action, executions,
                   TO_DATE(first_load_time, 'YYYY-MM-DD/HH24:MI:SS'), parsing_schema_name
              FROM v$sql
             WHERE sql_text LIKE :pattern
               AND sql_text NOT LIKE '%v$sql%'
               AND parsing_schema_name = USER
            """,
            pattern=pattern,
        )
        for row in cursor.fetchall():
            found.append(
                LoggedStatement(
                    sql_id=row[0],
                    sql_text=str(row[1]),
                    module=row[2],
                    action=row[3],
                    executions=row[4] or 0,
                    first_load_time=row[5],
                    parsing_schema=row[6],
                )
            )
    # One row per sql_id.
    unique = {s.sql_id: s for s in found}
    return sorted(unique.values(), key=lambda s: s.sql_id)


def _report(title: str, statements: list, emitters: list[Emitter]) -> float | None:
    print(f"\n{title}")
    print("-" * len(title))
    if not statements:
        print("  no dynamic statements found in the log")
        return None

    report = recover(statements, emitters)
    for attribution in report.attributions:
        text = " ".join(attribution.statement.sql_text.split())[:64]
        unit = attribution.unit or "UNATTRIBUTED"
        mark = "OK  " if attribution.high_confidence else "weak"
        print(f"  [{mark}] {unit:<26} via {attribution.signal.value:<16} {text}")

    rate = report.rate
    print(f"\n  statements            {report.total}")
    print(f"  high-confidence       {report.high_confidence}")
    print(f"  attribution rate      {'n/a' if rate is None else f'{rate * 100:.1f}%'}")
    print(f"  by signal             {report.by_signal()}")
    print(f"  truncated statements  {report.truncated}")
    return rate


def main() -> None:
    settings = OracleSettings.from_env()
    print(f"connecting to {settings.dsn} as {settings.user}")

    # The materialised statements these procedures build at runtime.
    patterns = [
        "UPDATE dim_customer SET %",
        "INSERT INTO tmp_%",
        "INSERT INTO TMP_%",
        "UPDATE dim_customer SET lifetime_value%",
    ]

    with connect(settings) as connection, connection.cursor() as cursor:
        cursor.execute("BEGIN DBMS_APPLICATION_INFO.SET_MODULE(NULL, NULL); END;")

        print("\nexecuting the FAVOURABLE case (procedures set MODULE/ACTION)")
        _run(cursor, FAVOURABLE, clear_module=False)
        connection.commit()
        favourable = _read_log(cursor, patterns)
        favourable_rate = _report(
            "FAVOURABLE - session attributes present",
            favourable,
            _emitters([u for u, _ in FAVOURABLE]),
        )

        print("\nexecuting the UNFAVOURABLE case (nothing sets MODULE/ACTION)")
        _run(cursor, UNFAVOURABLE, clear_module=True)
        connection.commit()
        unfavourable = [
            s for s in _read_log(cursor, patterns) if s.sql_id not in {f.sql_id for f in favourable}
        ]
        unfavourable_rate = _report(
            "UNFAVOURABLE - scheduler-driven, no session attributes",
            unfavourable,
            _emitters([u for u, _ in UNFAVOURABLE]),
        )

        # THE HONEST UNFAVOURABLE TEST.
        #
        # The run above proves little: one statement, built entirely from literals, with
        # a single candidate emitter. Shape matching cannot be wrong when there is
        # nothing to confuse it with.
        #
        # This is the real question. Take the statements whose true emitter is KNOWN
        # (module/action identified them), strip those attributes, and re-attribute by
        # shape alone with every emitter competing. Then check whether the answer is
        # RIGHT - not merely present. An attribution rate means nothing without it.
        truth = {
            a.statement.sql_id: a.unit
            for a in recover(favourable, _emitters([u for u, _ in FAVOURABLE])).attributions
            if a.signal.value == "module/action"
        }
        stripped = [
            LoggedStatement(
                sql_id=s.sql_id,
                sql_text=s.sql_text,
                module=None,
                action=None,
                executions=s.executions,
                first_load_time=s.first_load_time,
                parsing_schema=s.parsing_schema,
            )
            for s in favourable
            if s.sql_id in truth
        ]
        all_emitters = _emitters([u for u, _ in FAVOURABLE] + [u for u, _ in UNFAVOURABLE])

        print("\nSTRIPPED - real statements, session attributes removed, all emitters competing")
        print("-" * 78)
        stripped_report = recover(stripped, all_emitters)
        correct = wrong = unattributed = 0
        for attribution in stripped_report.attributions:
            expected = truth[attribution.statement.sql_id]
            if not attribution.attributed:
                verdict, unattributed = "UNATTRIBUTED", unattributed + 1
            elif attribution.unit == expected:
                verdict, correct = "correct", correct + 1
            else:
                verdict, wrong = f"WRONG (really {expected})", wrong + 1
            text = " ".join(attribution.statement.sql_text.split())[:52]
            print(f"  {verdict:<26} -> {attribution.unit or '-':<24} {text}")

        total = len(stripped)
        print(f"\n  statements            {total}")
        print(f"  correctly attributed  {correct}")
        print(f"  WRONGLY attributed    {wrong}   <- a wrong emitter is worse than none")
        print(f"  unattributed          {unattributed}")
        if total:
            print(f"  shape-only accuracy   {correct / total * 100:.1f}%")
        stripped_rate = correct / total if total else None

    print("\n" + "=" * 72)
    print("VERDICT")
    if stripped_rate is not None:
        print(f"  shape-only     {stripped_rate * 100:.1f}%  (the number that matters)")
    for label, rate in [("favourable", favourable_rate), ("unfavourable", unfavourable_rate)]:
        if rate is None:
            print(f"  {label:<14} not measured")
            continue
        verdict = "workable" if rate >= 0.60 else "BELOW the 60% comfort threshold"
        print(f"  {label:<14} {rate * 100:.1f}%  {verdict}")
    print(
        "\n  The unfavourable case is the one that decides the coverage story: it is what\n"
        "  a scheduler-driven estate looks like, and the register warns it is common."
    )


if __name__ == "__main__":
    main()
