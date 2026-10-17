"""semantic-ir T0, option C: row-deciding mode - an inner join's keys are filters.

Default (D-5): a join key only matches rows and gives no edge. Row-deciding mode
(`AnalysisConfig.row_deciding_keys`, or LINEAGE_ROW_DECIDING_KEYS=1): the key equality of
an INNER join decides which target rows survive, so its columns are filters; an OUTER join's
keys never remove a row and stay structural."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "grammar" / "dictionary.json"
DEFAULT = AnalysisConfig(dialect="postgres")
DECIDING = AnalysisConfig(dialect="postgres", row_deciding_keys=True)


@pytest.fixture(scope="module")
def book() -> Dictionary:
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    return Dictionary.load(BOOK)


def _filters(sql: str, book: Dictionary, config: AnalysisConfig) -> set[str]:
    result = analyse_source(sql, book, config)
    return {str(e.source) for e in result.edges if e.flow.value == "filter"}


DELETE_USING = "DELETE FROM tgt.t AS t USING src.b AS b WHERE b.k = t.k AND b.n = 0;"
INNER = "INSERT INTO tgt.t (id, v) SELECT a.id, a.v FROM src.a AS a JOIN src.b AS b ON b.k = a.k;"
LEFT = (
    "INSERT INTO tgt.t (id, v) SELECT a.id, b.v FROM src.a AS a LEFT JOIN src.b AS b ON b.k = a.k;"
)


def test_default_keeps_d5_keys_give_no_edge(book) -> None:
    assert "column:src.b.k" not in _filters(DELETE_USING, book, DEFAULT)
    assert "column:src.b.k" not in _filters(INNER, book, DEFAULT)


def test_row_deciding_mode_makes_inner_join_keys_filters(book) -> None:
    """holdout4: which ADT events a `DELETE ... USING corrections` removes depends on the
    corrections' keys; an inner join keeps only the rows whose key matches."""
    assert {"column:src.b.k", "column:tgt.t.k"} <= _filters(DELETE_USING, book, DECIDING)
    assert {"column:src.a.k", "column:src.b.k"} <= _filters(INNER, book, DECIDING)


def test_an_outer_joins_keys_stay_structural_in_either_mode(book) -> None:
    assert "column:src.b.k" not in _filters(LEFT, book, DECIDING)
    assert "column:src.a.k" not in _filters(LEFT, book, DECIDING)


def test_the_mode_is_in_the_declared_limits() -> None:
    assert DECIDING.declared_limits()["row_deciding_keys"] is True
    assert DEFAULT.declared_limits()["row_deciding_keys"] is False
