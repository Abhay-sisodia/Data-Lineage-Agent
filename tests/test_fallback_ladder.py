"""The fallback ladder (plan step 4, D-60): PostgreSQL SQLGlot cannot read loses no context.

rung 1  restated by PostgreSQL's own tree      -> full lineage
rung 2  the unreadable CTE refused on its own  -> the rest of the statement analysed
rung 3  refused, columns named by the tree     -> `mention` edges
rung 4  refused, tables named by the tree      -> `opaque` edges
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.export import from_document, to_document
from lineage.graph.model import build_batch, statement_tables
from lineage.ir.model import Flow
from lineage.parsing.pg_restate import restate
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "grammar" / "dictionary.json"


@pytest.fixture(scope="module")
def book() -> Dictionary:
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    return Dictionary.load(BOOK)


def _run(sql: str, book: Dictionary):
    return analyse_source(sql, book, AnalysisConfig(dialect="postgres"))


def _pairs(edges) -> set[tuple[str, str, str]]:
    return {(str(e.source), str(e.target), e.flow.value) for e in edges}


# --- rung 1 ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "restated"),
    [
        (
            "UPDATE t SET b = a.b AND a.v < 1, c = 'x, y' FROM a",
            "UPDATE t SET b = (a.b AND a.v < 1), c = 'x, y' FROM a",
        ),
        (
            "INSERT INTO t SELECT * FROM a ON CONFLICT (id) DO UPDATE SET b = t.b OR t.c",
            "INSERT INTO t SELECT * FROM a ON CONFLICT (id) DO UPDATE SET b = (t.b OR t.c)",
        ),
        (
            "MERGE INTO t USING a ON t.id = a.id WHEN MATCHED THEN "
            "UPDATE SET b = a.b AND (a.v OR a.w)",
            "MERGE INTO t USING a ON t.id = a.id WHEN MATCHED THEN "
            "UPDATE SET b = (a.b AND (a.v OR a.w))",
        ),
        ("UPDATE t SET b = NOT a.b FROM a", "UPDATE t SET b = NOT a.b FROM a"),
        (
            "SELECT n FROM a, JSON_TABLE(a.j, '$ ? (@ == \")\")' COLUMNS (n text PATH '$.n'))",
            "SELECT n FROM a, JSON_TABLE(a.j, '$ ? (@ == \")\")' COLUMNS (n text PATH '$.n'))"
            " AS json_table",
        ),
    ],
)
def test_rung1_restates_exactly_the_span_postgresql_parsed(sql: str, restated: str) -> None:
    assert restate(sql) == restated


def test_rung1_holdout_g1_is_analysed_in_full(book) -> None:
    result = _run(
        "UPDATE tgt.t AS t\nSET b = a.b AND abs(a.v) < 0.01\nFROM src.a AS a\nWHERE a.id = t.id;",
        book,
    )
    assert result.refusals == []
    assert {("column:src.a.b", "column:tgt.t.b"), ("column:src.a.v", "column:tgt.t.b")} <= {
        (s, t) for s, t, _ in _pairs(result.edges)
    }
    assert [e.status for e in result.ledger] == ["column_level"]


# --- rung 2 ---------------------------------------------------------------------------------

CTE = """INSERT INTO tgt.t (v, k)
WITH good AS (
    SELECT a.id, a.v FROM src.a AS a
),
bad AS (
    SELECT a.id, g.x
    FROM src.a AS a, LATERAL ROWS FROM (generate_series(1, 2), unnest(a.arr)) AS g(x, y)
)
SELECT good.v, bad.x FROM good JOIN bad ON bad.id = good.id;
"""


def test_rung2_refuses_only_the_cte_sqlglot_cannot_read(book) -> None:
    result = _run(CTE, book)
    assert ("column:src.a.v", "column:tgt.t.v", "value") in _pairs(result.edges)
    assert [(r.scope, r.line, r.end_line) for r in result.refusals] == [("cte", 5, 8)]
    assert [e.status for e in result.ledger] == ["partial"]
    assert not any("could not parse the statement" in str(b) for b in result.boundaries)


# --- rungs 3 and 4 --------------------------------------------------------------------------


def test_rung3_a_refused_write_keeps_the_columns_it_names(book) -> None:
    result = _run(
        "INSERT INTO tgt.t (id, v, k)\n"
        "SELECT a.id, a.v * 2, g.x\n"
        "FROM src.a AS a, LATERAL ROWS FROM (generate_series(1, 2), unnest(a.arr)) AS g(x, y)\n"
        "WHERE a.b AND a.w > 0;",
        book,
    )
    assert result.edges == []
    assert _pairs(result.fallback) == {
        ("column:src.a.id", "column:tgt.t.id", "mention"),
        ("column:src.a.v", "column:tgt.t.v", "mention"),
        ("column:src.a.arr", "relation:tgt.t", "mention"),
        ("column:src.a.b", "relation:tgt.t", "mention"),
        ("column:src.a.w", "relation:tgt.t", "mention"),
    }
    assert [e.status for e in result.ledger] == ["unclassified"]


# A declaration the PL/pgSQL reader cannot read still refuses the routine whole (D-61
# refuses single STATEMENTS alone; a header or declaration has no statement to isolate).
ROUTINE = """CREATE PROCEDURE tgt.reload(p integer) LANGUAGE plpgsql AS $$
DECLARE k ALIAS FOR p;
BEGIN
  INSERT INTO tgt.t (k) SELECT b.k FROM src.b AS b;
  INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a;
END $$;
"""


def test_rung4_a_refused_routine_keeps_its_tables_and_never_its_name(book) -> None:
    result = _run(ROUTINE, book)
    assert result.refusals and result.edges == []
    assert _pairs(result.fallback) == {
        ("relation:src.a", "relation:tgt.t", "opaque"),
        ("relation:src.b", "relation:tgt.t", "opaque"),
    }
    assert [e.status for e in result.ledger] == ["table_level"]
    assert statement_tables(ROUTINE, "public") == {"src.a", "src.b", "tgt.t"}  # holdout G5


def test_fallback_edges_reach_the_graph_and_stay_out_of_the_scored_edges(book) -> None:
    result = _run(ROUTINE, book)
    assert all(e.flow is Flow.OPAQUE for e in result.fallback)
    document = to_document(
        result,
        source_path="r.sql",
        source_text=ROUTINE,
        dialect="postgres",
        config_fingerprint="c",
        dictionary_fingerprint="d",
    )
    batch = build_batch([from_document(document)])
    assert len(batch.relationships["OPAQUE"]) == 2
    assert "FEEDS" not in batch.relationships
