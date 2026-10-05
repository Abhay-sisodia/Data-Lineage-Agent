"""Torture-test findings TT-1, TT-4 and PO-1, pinned outside the (uncommitted) torture folders.

* TT-1  a PostgreSQL system column (`ctid`, `xmin`, `tableoid` ...) is a value with no
        source; it used to refuse the whole statement, or be declared unresolved
* TT-4  a statement SQLGlot cannot parse is counted and refused; it used to be skipped,
        so a file holding only that statement reported nothing seen
* PO-1  Spark's `COLLECT_SET` / `COLLECT_LIST` are aggregates, not scalar calls
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _edges(result) -> set[tuple[str, str, str, str]]:
    return {(e.flow.value, str(e.source), str(e.target), e.transform.value) for e in result.edges}


# --- TT-1 ---------------------------------------------------------------------------------


def test_a_system_column_tie_breaker_no_longer_refuses_the_statement() -> None:
    result = _result(
        "SELECT DISTINCT ON (o.id) o.id, o.amount FROM staging.orders AS o ORDER BY o.id, o.ctid;"
    )
    assert result.refusals == []
    assert result.boundaries == []
    assert _edges(result) == {
        ("value", "column:staging.orders.id", "column:<result>.id", "identity"),
        ("value", "column:staging.orders.amount", "column:<result>.amount", "identity"),
        ("filter", "column:staging.orders.id", "relation:<result>", "identity"),
    }


def test_a_projected_system_column_has_no_source() -> None:
    result = _result("SELECT amount, xmin, tableoid::regclass AS src FROM staging.orders;")
    assert result.refusals == []
    assert result.boundaries == []
    assert _edges(result) == {
        ("value", "column:staging.orders.amount", "column:<result>.amount", "identity"),
    }


def test_a_cte_column_named_like_a_system_column_is_an_ordinary_column() -> None:
    """The control: `xmin` here is the CTE's own column, and it traces."""
    result = _result(
        "WITH c AS (SELECT o.amount AS xmin FROM staging.orders AS o)\nSELECT c.xmin FROM c;"
    )
    assert _edges(result) == {
        ("value", "column:staging.orders.amount", "column:<result>.xmin", "identity"),
    }


# --- TT-4 ---------------------------------------------------------------------------------


def test_an_unparseable_statement_is_counted_and_refused_once() -> None:
    result = _result("SELECT o.id FROM staging.orders AS o ORDER BY o.id USING >;")
    assert result.statements_seen == 1
    assert result.parse_coverage == 0.0
    assert [r.code.value for r in result.refusals] == ["PARSE_FAILED"]
    assert [b.kind.value for b in result.boundaries] == ["refusal"]


def test_it_costs_one_statement_of_coverage_beside_a_readable_one() -> None:
    result = _result(
        "SELECT o.id FROM staging.orders AS o;\n"
        "SELECT o.id FROM staging.orders AS o ORDER BY o.id USING >;"
    )
    assert (result.statements_seen, result.statements_analysed) == (2, 1)


# --- PO-1 ---------------------------------------------------------------------------------


def test_collect_set_over_a_window_is_aggregated() -> None:
    """The PO model's shape: `SIZE(COLLECT_SET(...) OVER (...))` inside a CTE."""
    result = _result(
        "WITH b AS (\n"
        "    SELECT SIZE(COLLECT_SET(o.customer_id) OVER (PARTITION BY o.store_id)) AS buyers\n"
        "    FROM staging.orders AS o\n"
        ")\nSELECT b.buyers FROM b;"
    )
    assert _edges(result) == {
        ("value", "column:staging.orders.customer_id", "column:<result>.buyers", "aggregated"),
        ("influence", "column:staging.orders.store_id", "column:<result>.buyers", "identity"),
    }


def test_collect_list_under_group_by_brings_its_grouping_influence() -> None:
    result = _result(
        "SELECT o.store_id, COLLECT_LIST(o.amount) AS amounts "
        "FROM staging.orders AS o GROUP BY o.store_id;"
    )
    assert (
        "value",
        "column:staging.orders.amount",
        "column:<result>.amounts",
        "aggregated",
    ) in _edges(result)
    assert (
        "influence",
        "column:staging.orders.store_id",
        "column:<result>.amounts",
        "identity",
    ) in _edges(result)
