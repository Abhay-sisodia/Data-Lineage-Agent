"""semantic-ir T8: the psql layer - `\\gset` is a BIND, `\\set` is substituted, and the data
rows of `COPY ... FROM STDIN` are not SQL (holdout4 H1, H3, H4)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.parsing import psql
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "grammar" / "dictionary.json"


@pytest.fixture(scope="module")
def book() -> Dictionary:
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    return Dictionary.load(BOOK)


def _run(sql: str, book: Dictionary):
    return analyse_source(sql, book, AnalysisConfig(dialect="postgres"))


def _reaches(result, source: str, target: str) -> bool:
    edges: dict[str, set[str]] = {}
    for edge in result.edges:
        edges.setdefault(str(edge.source), set()).add(str(edge.target))
    seen, todo = {source}, [source]
    while todo:
        for nxt in edges.get(todo.pop(), ()):
            if nxt == target or (target.startswith("relation:") and nxt == target):
                return True
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return False


GSET = """SELECT b.k, b.n AS cap FROM src.b AS b WHERE b.id = 1
\\gset rc_

INSERT INTO tgt.t (id, v)
SELECT a.id, a.v + :rc_cap FROM src.a AS a
WHERE a.k = :'rc_k';
"""


def test_a_gset_variable_carries_the_lineage_of_its_column(book) -> None:
    """holdout4 H4: `\\gset rc_` then `:rc_x` - the variable is a BIND of the query's row."""
    result = _run(GSET, book)
    assert result.refusals == []
    assert _reaches(result, "column:src.b.n", "column:tgt.t.v")  # a value through :rc_cap
    assert any(
        str(e.source) == "column:src.b.k"
        and str(e.target).startswith("column:pg_temp.psql_gset_l")
        and str(e.target).endswith(".rc_k")
        for e in result.edges
    )
    filters = {str(e.source) for e in result.edges if e.flow.value == "filter"}
    assert any("psql_gset_l" in source and source.endswith(".rc_k") for source in filters)


def test_a_gset_nothing_uses_writes_nothing() -> None:
    """D-37's control: a `\\gset` whose variables no statement reads restates to nothing."""
    text, _ = psql.restate("SELECT max(o.amount) AS top FROM staging.orders AS o \\gset\n")
    assert text.strip() == ""


def test_set_variables_are_substituted_as_psql_does() -> None:
    """holdout4 H3: `\\set n 5` then `FETCH :n FROM c` - PostgreSQL never sees `:n`."""
    text, _ = psql.restate(
        "\\set n 5\n\\set name 'it''s'\nFETCH :n FROM c;\nSELECT :'name', :\"name\";\n"
    )
    lines = text.split("\n")
    assert lines[2] == "FETCH 5 FROM c;"
    assert lines[3] == "SELECT 'it''s', \"it's\";"
    assert len(lines) == 5  # every line kept


def test_copy_from_stdin_data_rows_are_not_sql(book) -> None:
    """holdout4 H1: the CSV rows after `COPY ... FROM STDIN` were read as SQL and swallowed
    the statement after the `\\.` line."""
    sql = (
        "COPY src.b (id, k) FROM STDIN WITH (FORMAT csv);\n"
        "1,2\n"
        "3,4\n"
        "\\.\n"
        "INSERT INTO tgt.t (k) SELECT b.k FROM src.b AS b;\n"
    )
    text, _ = psql.restate(sql)
    assert text.split("\n")[1:4] == ["   ", "   ", "  "]
    result = _run(sql, book)
    assert result.refusals == []  # inline rows are literals, not an outside source
    assert ("column:src.b.k", "column:tgt.t.k") in {
        (str(e.source), str(e.target)) for e in result.edges
    }
