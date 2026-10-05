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
    result = _result("SELECT o.id FROM staging.orders AS o ORDER BY;")
    assert result.statements_seen == 1
    assert result.parse_coverage == 0.0
    assert [r.code.value for r in result.refusals] == ["PARSE_FAILED"]
    assert [b.kind.value for b in result.boundaries] == ["refusal"]


def test_it_costs_one_statement_of_coverage_beside_a_readable_one() -> None:
    result = _result(
        "SELECT o.id FROM staging.orders AS o;\nSELECT o.id FROM staging.orders AS o ORDER BY;"
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


# --- TT-2 ---------------------------------------------------------------------------------

_WINDOWS = (
    "FROM staging.orders AS o "
    "WINDOW a AS (PARTITION BY o.customer_id), b AS (a ORDER BY o.order_ts)"
)
_INHERITED = {
    ("value", "column:staging.orders.amount", "column:<result>.prev", "derived"),
    ("influence", "column:staging.orders.customer_id", "column:<result>.prev", "identity"),
    ("influence", "column:staging.orders.order_ts", "column:<result>.prev", "identity"),
}


def test_a_window_inheriting_a_named_window_keeps_its_partition() -> None:
    assert _edges(_result(f"SELECT LAG(o.amount) OVER b AS prev {_WINDOWS};")) == _INHERITED


def test_an_inline_window_inheriting_a_named_window_keeps_its_partition() -> None:
    sql = f"SELECT LAG(o.amount) OVER (a ORDER BY o.order_ts) AS prev {_WINDOWS};"
    assert _edges(_result(sql)) == _INHERITED


def test_the_spelled_out_window_agrees() -> None:
    """The control: the same window written in full."""
    sql = (
        "SELECT LAG(o.amount) OVER (PARTITION BY o.customer_id ORDER BY o.order_ts) AS prev "
        "FROM staging.orders AS o;"
    )
    assert _edges(_result(sql)) == _INHERITED


# --- TT-3 ---------------------------------------------------------------------------------


def test_a_lateral_values_column_binds_by_position() -> None:
    result = _result(
        "SELECT o.id, u.label, u.val FROM staging.orders AS o\n"
        "CROSS JOIN LATERAL (VALUES ('amount', o.amount), ('store', o.store_id)) "
        "AS u (label, val);"
    )
    assert result.boundaries == []
    assert _edges(result) == {
        ("value", "column:staging.orders.id", "column:<result>.id", "identity"),
        ("value", "column:staging.orders.amount", "column:<result>.val", "identity"),
        ("value", "column:staging.orders.store_id", "column:<result>.val", "identity"),
    }


def test_regexp_matches_in_from_is_a_built_in_row_source() -> None:
    result = _result(
        "SELECT (rm.m)[1] AS code FROM staging.orders AS o\n"
        "LEFT JOIN LATERAL regexp_matches(o.discount_code, '([A-Z]+)', 'g') AS rm (m) ON true;"
    )
    assert result.boundaries == []
    assert _edges(result) == {
        ("value", "column:staging.orders.discount_code", "column:<result>.code", "derived"),
    }


def test_generate_subscripts_with_ordinality_reads_its_array() -> None:
    result = _result(
        "SELECT g.idx, g.n FROM staging.orders AS o\n"
        "CROSS JOIN LATERAL generate_subscripts(o.tags, 1) WITH ORDINALITY AS g (idx, n);"
    )
    assert result.boundaries == []
    assert _edges(result) == {
        ("value", "column:staging.orders.tags", "column:<result>.idx", "derived"),
        ("influence", "column:staging.orders.tags", "column:<result>.n", "identity"),
    }


_TREE = (
    "WITH RECURSIVE tree AS (\n"
    "    SELECT o.id, o.customer_id, 1 AS lvl FROM staging.orders AS o\n"
    "    WHERE o.status = 'open'\n"
    "    UNION ALL\n"
    "    SELECT c.id, c.customer_id, t.lvl + 1 FROM staging.orders AS c\n"
    "    JOIN tree AS t ON c.customer_id = t.id\n"
    ")"
)


def test_a_scalar_subquery_over_a_recursive_cte_declares_nothing() -> None:
    """The anchor's WHERE is reached through SQLGlot's source-less self-reference shell."""
    result = _result(
        f"{_TREE},\nx AS (SELECT s.id, (SELECT max(t.lvl) FROM tree AS t "
        "WHERE t.id = s.customer_id) AS depth FROM staging.orders AS s)\n"
        "SELECT x.id, x.depth FROM x;"
    )
    assert result.boundaries == []
    assert ("value", "column:staging.orders.status", "column:<result>.depth", "aggregated") in (
        _edges(result)
    )


def test_a_lateral_in_the_recursive_arm_reads_the_self_reference() -> None:
    result = _result(
        "WITH RECURSIVE tree AS (\n"
        "    SELECT o.id, o.customer_id FROM staging.orders AS o\n"
        "    UNION\n"
        "    SELECT k.id, k.customer_id FROM tree AS t\n"
        "    CROSS JOIN LATERAL (SELECT * FROM staging.orders AS x "
        "WHERE x.customer_id = t.id) AS k\n"
        ")\nSELECT tree.id FROM tree;"
    )
    assert result.boundaries == []
    assert ("value", "column:staging.orders.id", "column:<result>.id", "identity") in _edges(result)


def test_json_object_returning_names_a_type_not_a_column() -> None:
    result = _result(
        "SELECT JSON_OBJECT('k' VALUE o.amount RETURNING jsonb) AS doc FROM staging.orders AS o;"
    )
    assert result.boundaries == []
    assert _edges(result) == {
        ("value", "column:staging.orders.amount", "column:<result>.doc", "derived"),
    }
