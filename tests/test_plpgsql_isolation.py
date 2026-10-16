"""PL/pgSQL, plan step 5: one statement the reader cannot follow costs that statement only.

Before step 5 a single unreadable statement - a TRUNCATE, a `SELECT * INTO` a record, a
CASE - refused the whole routine "so nothing half-read is reported", and every write in it
disappeared from the lineage. Now the statement is refused alone, at its own lines, and the
routine around it is read; the ledger says `partial`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "grammar" / "dictionary.json"


@pytest.fixture(scope="module")
def book() -> Dictionary:
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    return Dictionary.load(BOOK)


def _run(sql: str, book: Dictionary):
    return analyse_source(sql, book, AnalysisConfig(dialect="postgres"))


def _pairs(result) -> set[tuple[str, str]]:
    return {(str(e.source), str(e.target)) for e in result.edges}


RELOAD = """CREATE PROCEDURE tgt.reload() LANGUAGE plpgsql AS $$
DECLARE c refcursor;
BEGIN
  INSERT INTO tgt.t (k) SELECT b.k FROM src.b AS b;
  OPEN c FOR EXECUTE 'SELECT 1';
  INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a;
END $$;
"""


def test_an_unreadable_statement_is_refused_alone_and_the_routine_is_read(book) -> None:
    result = _run(RELOAD, book)
    assert {("column:src.b.k", "column:tgt.t.k"), ("column:src.a.v", "column:tgt.t.v")} <= _pairs(
        result
    )
    assert [(r.line, r.end_line) for r in result.refusals] == [(5, 5)]
    assert "the rest of tgt.reload is analysed" in result.refusals[0].reason
    assert [e.status for e in result.ledger] == ["partial"]


def test_select_star_into_a_record_traces_its_fields(book) -> None:
    """Holdout gap G2: `SELECT * INTO rec`, then `rec.field` - in either IF arm."""
    result = _run(
        """CREATE FUNCTION tgt.f(p boolean) RETURNS void LANGUAGE plpgsql AS $$
DECLARE r src.b%ROWTYPE;
BEGIN
  IF p THEN
    SELECT * INTO STRICT r FROM src.b AS b WHERE b.id = 1;
  ELSE
    SELECT * INTO r FROM src.b AS b WHERE b.k = 2;
  END IF;
  INSERT INTO tgt.t (v, n) VALUES (r.v, r.n);
END $$;
""",
        book,
    )
    assert result.refusals == []
    assert {("column:src.b.v", "column:tgt.t.v"), ("column:src.b.n", "column:tgt.t.n")} <= _pairs(
        result
    )


@pytest.mark.parametrize(
    "body",
    [
        "CASE p WHEN 1 THEN INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a; ELSE NULL; END CASE;",
        "FOR r IN EXECUTE 'SELECT a.v FROM src.a AS a' LOOP INSERT INTO tgt.t (v) VALUES (r.v); "
        "END LOOP;",
        "OPEN c FOR SELECT a.id, a.v FROM src.a AS a; FETCH c INTO r; "
        "INSERT INTO tgt.t (v) VALUES (r.v); CLOSE c;",
    ],
    ids=["case", "for-in-execute", "open-for-fetch-into-record"],
)
def test_forms_that_used_to_refuse_the_routine_are_read(body: str, book) -> None:
    sql = (
        "CREATE FUNCTION tgt.g(p int) RETURNS void LANGUAGE plpgsql AS $$\n"
        "DECLARE r record; c refcursor;\nBEGIN\n" + body + "\nEND $$;\n"
    )
    result = _run(sql, book)
    assert result.refusals == []
    assert ("column:src.a.v", "column:tgt.t.v") in _pairs(result)


def test_a_scalar_function_passes_its_argument_through(book) -> None:
    """`RETURN x * 2`: the caller's argument is a derived value of the result."""
    result = _run(
        """CREATE FUNCTION tgt.twice(x numeric) RETURNS numeric LANGUAGE plpgsql AS $$
BEGIN
  RETURN x * 2;
END $$;
INSERT INTO tgt.t (v) SELECT tgt.twice(a.v) FROM src.a AS a;
""",
        book,
    )
    assert {(str(e.source), str(e.target), e.transform.value) for e in result.edges} == {
        ("column:src.a.v", "column:tgt.t.v", "derived")
    }


def test_an_out_parameter_is_the_value_returned(book) -> None:
    result = _run(
        """CREATE FUNCTION tgt.total(OUT s numeric) LANGUAGE plpgsql AS $$
BEGIN
  SELECT sum(a.v) INTO s FROM src.a AS a;
END $$;
INSERT INTO tgt.t (v) SELECT tgt.total();
""",
        book,
    )
    assert result.refusals == []
    assert ("column:src.a.v", "column:tgt.t.v") in _pairs(result)
