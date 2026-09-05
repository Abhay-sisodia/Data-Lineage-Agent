"""T0.1 toolchain smoke tests.

The T0.1 gate: one command parses a trivial procedure through ANTLR, one statement
through SQLGlot, and Oracle XE accepts a package and returns its statements from V$SQL.

These are deliberately shallow. They prove the toolchain is wired up, nothing about
whether the analysis is any good — that is what the scoring harness is for.
"""

from __future__ import annotations

import importlib.util
from typing import Any
from uuid import uuid4

import pytest
import sqlglot
from sqlglot import exp

TRIVIAL_PROCEDURE = """
CREATE OR REPLACE PROCEDURE refresh_active_flag(p_region VARCHAR2) IS
  v_days   NUMBER;
  v_cutoff DATE;
BEGIN
  SELECT window_days INTO v_days FROM ref_policy WHERE region = p_region;
  v_cutoff := SYSDATE - v_days;
  INSERT INTO tmp_recent
  SELECT cust_id, last_login FROM stg_customer WHERE last_login > v_cutoff;
END;
"""

ANTLR_PARSER_MODULE = "lineage.parsing.generated.PlSqlParser"


def _module_available(name: str) -> bool:
    """True if the module can be imported.

    `find_spec` raises rather than returning None when a *parent* package is missing,
    which is exactly the state before the parser has been generated.
    """
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def test_sqlglot_parses_one_statement() -> None:
    """SQLGlot handles a single statement: the band 0 workhorse."""
    statement = sqlglot.parse_one(
        "INSERT INTO tmp_recent "
        "SELECT cust_id, last_login FROM stg_customer WHERE last_login > :cutoff",
        dialect="oracle",
    )
    assert isinstance(statement, exp.Insert)

    target = statement.this
    assert isinstance(target, exp.Table)
    assert target.name == "tmp_recent"

    sources = {t.name for t in statement.find_all(exp.Table)}
    assert "stg_customer" in sources


def test_sqlglot_reports_the_variable_it_cannot_resolve() -> None:
    """The handoff point between the two tools, and the reason both exist.

    SQLGlot sees `v_cutoff` in the WHERE clause as a column reference, because within a
    single statement that is the only thing it could be. It is actually a PL/SQL local
    variable. That unresolved-looking reference is precisely the marker that dataflow
    analysis has to pick up — so this test pins the behaviour we build on rather than
    asserting the parser is wrong.
    """
    statement = sqlglot.parse_one(
        "INSERT INTO tmp_recent "
        "SELECT cust_id, last_login FROM stg_customer WHERE last_login > v_cutoff",
        dialect="oracle",
    )
    referenced = {c.name for c in statement.find_all(exp.Column)}
    assert "v_cutoff" in referenced, (
        "SQLGlot no longer surfaces the unbound reference; def-use handoff needs revisiting"
    )


@pytest.mark.skipif(
    not _module_available(ANTLR_PARSER_MODULE),
    reason="ANTLR PL/SQL parser not generated yet - run scripts/generate_parser.py (T0.1)",
)
def test_antlr_parses_a_trivial_procedure() -> None:
    """ANTLR handles the program: statement boundaries, declarations, control flow."""
    from lineage.parsing.plsql import parse_program

    program = parse_program(TRIVIAL_PROCEDURE)
    assert program.error_count == 0, f"parse errors: {program.errors}"
    assert program.procedure_names == ["refresh_active_flag"]
    # Two SQL statements plus one assignment, captured as text and not interpreted here.
    assert len(program.statements) == 3


@pytest.mark.requires_oracle
def test_oracle_xe_accepts_a_package_and_logs_it(oracle_connection: Any) -> None:
    """Oracle XE round trip: compile a procedure, execute it, find it in V$SQL.

    This closes the third leg of T0.1 and is the prerequisite for the log-recovery
    sub-spike (T3.3) — without a real query log there is nothing to recover from.

    It also takes an early reading on the attribution signal the sub-spike depends on
    most: whether MODULE and ACTION survive into V$SQL. They are the strongest way to
    tie a logged statement back to the procedure that emitted it, and the blind spot
    register warns they are often absent when a scheduler drives the call. Here we set
    them explicitly, which is the favourable case — if they did not survive even this,
    the sub-spike would be in trouble before it started.
    """
    marker = f"lineage_smoke_{uuid4().hex[:8]}"
    procedure = f"p_smoke_{uuid4().hex[:8]}"
    module, action = "LINEAGE_SPIKE", "T0.1_SMOKE"

    with oracle_connection.cursor() as cursor:
        try:
            cursor.execute(f"""
                CREATE OR REPLACE PROCEDURE {procedure} IS
                  v_count NUMBER;
                BEGIN
                  SELECT COUNT(*) INTO v_count FROM dual WHERE '{marker}' IS NOT NULL;
                END;
            """)

            # Compilation errors are recorded, not raised - a procedure that does not
            # compile would otherwise look like one with no lineage.
            cursor.execute(
                "SELECT line, text FROM user_errors WHERE name = :name",
                name=procedure.upper(),
            )
            assert cursor.fetchall() == [], "procedure failed to compile"

            cursor.execute(
                "BEGIN DBMS_APPLICATION_INFO.SET_MODULE(:m, :a); END;",
                m=module,
                a=action,
            )
            cursor.execute(f"BEGIN {procedure}; END;")

            cursor.execute(
                """
                SELECT sql_text, module, action, executions
                  FROM v$sql
                 WHERE sql_text LIKE :pattern
                   AND sql_text NOT LIKE '%v$sql%'
                """,
                pattern=f"%{marker}%",
            )
            rows = cursor.fetchall()
        finally:
            cursor.execute(f"DROP PROCEDURE {procedure}")

    assert rows, "the executed statement did not appear in V$SQL"

    sql_text, captured_module, captured_action, executions = rows[0]
    assert marker in sql_text
    assert executions >= 1
    assert captured_module == module, (
        f"MODULE not carried into V$SQL (got {captured_module!r}) - "
        "the primary attribution signal for T3.3 would be unavailable"
    )
    assert captured_action == action
