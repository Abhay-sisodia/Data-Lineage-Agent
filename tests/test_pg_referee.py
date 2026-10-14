"""PostgreSQL's own parser as referee (plan step 2): verdicts, set-aside, and the folder scan."""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.analysis.refusal import RefusalCode
from lineage.config import AnalysisConfig
from lineage.coverage.grammar import load_rows
from lineage.coverage.scan import checklist_outcomes, scan_source
from lineage.parsing.pg_referee import constructs, set_aside, verdict
from lineage.resolution.dictionary import Dictionary

GRAMMAR = Path(__file__).resolve().parent.parent / "stress" / "pg" / "grammar"
SPARK = "INSERT INTO tgt.t (ts) SELECT TIMESTAMP('1900-01-01') FROM src.a AS a"


@pytest.fixture(scope="module")
def book() -> Dictionary:
    return Dictionary.load(GRAMMAR / "dictionary.json")


def test_verdict_names_the_statement_and_its_tables() -> None:
    v = verdict(
        "WITH s AS (SELECT b.k FROM src.b AS b) "
        "UPDATE tgt.t AS t SET v = a.v FROM src.a AS a, s WHERE a.id = t.id"
    )
    assert (v.valid, v.kind, v.writes, v.reads) == (
        True,
        "UpdateStmt",
        ["tgt.t"],
        ["src.a", "src.b"],
    )


def test_spark_spelling_is_not_postgresql() -> None:
    assert not verdict(SPARK).valid


def test_a_psql_variable_does_not_make_a_statement_invalid() -> None:
    """psql substitutes `:lo` / `:tbl` before the server sees the statement."""
    assert verdict("INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a WHERE a.id > :lo").valid
    assert verdict("INSERT INTO tgt.t (v) SELECT x.v FROM :tbl AS x").valid


def test_set_aside_blanks_only_the_rejected_statement_and_keeps_every_line() -> None:
    source = f"INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a;\n{SPARK}\n  ;\nSELECT 1;\n"
    kept, rejected = set_aside(source)
    assert [r.line for r in rejected] == [2]
    assert kept.count("\n") == source.count("\n")
    assert "TIMESTAMP" not in kept and "SELECT a.v" in kept


def test_a_statement_postgresql_rejects_is_refused_by_name_never_analysed(book) -> None:
    source = f"INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a;\n{SPARK};\n"
    result = analyse_source(source, book, AnalysisConfig(dialect="postgres"))
    refused = [r for r in result.refusals if r.line == 2]
    assert [r.code for r in refused] == [RefusalCode.PARSE_FAILED]
    assert "PostgreSQL's own parser rejects" in refused[0].reason
    assert not [e for e in result.edges if e.origin.line == 2]
    assert [str(e.source) for e in result.edges] == ["column:src.a.v"]


@pytest.mark.parametrize(
    "row",
    [r for r in load_rows() if r.id.startswith("extra:") and r.id in constructs(r.sql)],
    ids=lambda r: r.id,
)
def test_a_named_combination_is_recognised_in_its_own_snippet(row) -> None:
    """The combinations the scan names (G1, G2, TRUNCATE in a routine ...) - each detector
    recognises the very snippet its checklist row was measured on."""
    assert row.id in constructs(row.sql)


def test_the_detectors_cover_the_known_holdout_gaps() -> None:
    detected = {
        r.id for r in load_rows() if r.id.startswith("extra:") and r.id in constructs(r.sql)
    }
    assert {
        "extra:boolean_value_in_update_set",
        "extra:plpgsql_select_star_into_record",
        "extra:plpgsql_truncate_then_reload",
        "extra:sql_function_body",
    } <= detected


def _predict(sql: str) -> list[tuple[int, str]]:
    return [(s.line, s.prediction) for s in scan_source(sql, "x.sql", checklist_outcomes())]


def test_scan_predicts_each_kind_of_statement() -> None:
    sql = "\n".join(
        [
            "INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a;",
            "UPDATE tgt.t AS t SET b = a.b AND a.v < 1 FROM src.a AS a WHERE a.id = t.id;",
            "DROP TABLE IF EXISTS tgt.old;",
            f"{SPARK};",
            "CREATE INDEX t_v ON tgt.t (v);",
        ]
    )
    assert _predict(sql) == [
        (1, "column-level"),
        (2, "gap"),
        (3, "effect"),
        (4, "not valid PostgreSQL"),
        (5, "no lineage"),
    ]


def test_scan_names_the_gap_and_its_checklist_row() -> None:
    sql = "UPDATE tgt.t AS t SET b = a.b AND a.v < 1 FROM src.a AS a WHERE a.id = t.id;"
    (scan,) = scan_source(sql, "x.sql", checklist_outcomes())
    assert any(gap.startswith("extra:boolean_value_in_update_set") for gap in scan.gaps)
    assert any(gap.startswith("SQLGlot cannot parse it") for gap in scan.gaps)
