"""D-45, D-46 and findings TS-1, TS-2: what a scope that cannot remove a row decides.

D-45 (2026-10-06) makes D-12 and D-14 reach all the way down: inside a scalar subquery, a
null-supplying side or a one-row LATERAL, every filter of what that scope reads is a VALUE
of the column it feeds, and every DISTINCT ON / LIMIT ordering there is INFLUENCE - except
in a scope the statement also reads where it CAN remove rows (a filter there, and only
there), and except a nested scope that itself cannot remove rows (its correlation filters
nothing). The rule lived in two implementations that had drifted; it now has one,
`band0._value_deciding_sweep`. D-46 makes a GROUP BY behind an aggregate influence a
window ordered by it. TS-1 and TS-2 come from the self-written torture test D1.
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


def _edges(result, target: str | None = None) -> set[tuple[str, str, str]]:
    return {
        (e.flow.value, str(e.source).split(":", 1)[1], e.transform.value)
        for e in result.edges
        if target is None or str(e.target) == f"column:<result>.{target}"
    }


# --- D-45 -----------------------------------------------------------------------------------

_NESTED = (
    "WITH paid AS (SELECT p.order_id, p.amount FROM staging.payments AS p "
    "WHERE p.status = 'PAID'),\n"
    "per_order AS (SELECT pd.order_id, sum(pd.amount) AS paid FROM paid AS pd "
    "GROUP BY pd.order_id)\n"
)


def test_a_filter_two_ctes_down_a_null_side_is_a_value() -> None:
    result = _result(
        _NESTED + "SELECT o.id, po.paid FROM staging.orders AS o "
        "LEFT JOIN per_order AS po ON po.order_id = o.id;"
    )
    assert ("value", "staging.payments.status", "aggregated") in _edges(result, "paid")
    assert result.boundaries == []


def test_the_same_cte_on_the_main_path_stays_a_filter_only() -> None:
    """`paid` also feeds the result through an inner join: its WHERE is a filter there."""
    result = _result(
        _NESTED + "SELECT o.id, po.paid, pd.amount FROM staging.orders AS o "
        "JOIN paid AS pd ON pd.order_id = o.id "
        "LEFT JOIN per_order AS po ON po.order_id = o.id;"
    )
    assert ("value", "staging.payments.status", "aggregated") not in _edges(result, "paid")
    assert ("filter", "staging.payments.status", "identity") in _edges(result)


def test_a_nested_one_row_lateral_correlation_does_not_leak() -> None:
    result = _result(
        "WITH side AS (\n"
        "    SELECT c.id, c.full_name, x.n FROM staging.customers AS c\n"
        "    LEFT JOIN LATERAL (SELECT count(*) AS n FROM staging.orders AS o "
        "WHERE o.customer_id = c.id) AS x ON TRUE\n"
        ")\n"
        "SELECT o.id, s.full_name FROM staging.orders AS o "
        "LEFT JOIN side AS s ON s.id = o.customer_id;"
    )
    assert _edges(result, "full_name") == {("value", "staging.customers.full_name", "identity")}


def test_a_nested_distinct_on_is_influence() -> None:
    result = _result(
        "WITH latest AS (SELECT DISTINCT ON (p.order_id) p.order_id, p.amount "
        "FROM staging.payments AS p ORDER BY p.order_id, p.paid_at DESC)\n"
        "SELECT o.id, l.amount FROM staging.orders AS o "
        "LEFT JOIN latest AS l ON l.order_id = o.id;"
    )
    edges = _edges(result, "amount")
    assert ("influence", "staging.payments.paid_at", "identity") in edges
    assert ("value", "staging.payments.amount", "identity") in edges


def test_a_nested_in_inside_a_scalar_subquery_keeps_the_subquery_transform() -> None:
    """The IN is reached by its parent's sweep: no second, `conditional` copy."""
    result = _result(
        "SELECT c.id, (SELECT sum(p.amount) FROM staging.payments AS p "
        "WHERE p.order_id IN (SELECT o.id FROM staging.orders AS o "
        "WHERE o.customer_id = c.id)) AS paid FROM staging.customers AS c;"
    )
    edges = _edges(result, "paid")
    assert ("value", "staging.orders.customer_id", "aggregated") in edges
    assert ("value", "staging.orders.customer_id", "conditional") not in edges


# --- D-46 -----------------------------------------------------------------------------------


def test_grouping_behind_an_aggregate_influences_a_window_ordered_by_it() -> None:
    result = _result(
        "WITH s AS (SELECT o.store_id, sum(o.amount) AS revenue FROM staging.orders AS o "
        "GROUP BY o.store_id)\n"
        "SELECT s.store_id, RANK() OVER (ORDER BY s.revenue DESC) AS r FROM s;"
    )
    assert _edges(result, "r") == {
        ("influence", "staging.orders.amount", "identity"),
        ("influence", "staging.orders.store_id", "identity"),
    }


# --- TS-1, TS-2 -----------------------------------------------------------------------------


def test_a_scalar_subquery_brings_its_grouping_influence() -> None:
    result = _result(
        "WITH by_store AS (SELECT o.store_id, sum(o.amount) AS revenue FROM staging.orders AS o "
        "GROUP BY o.store_id)\n"
        "SELECT c.id, (SELECT max(b.revenue) FROM by_store AS b) AS best FROM staging.customers c;"
    )
    assert _edges(result, "best") == {
        ("value", "staging.orders.amount", "aggregated"),
        ("influence", "staging.orders.store_id", "identity"),
    }


def test_a_distinct_on_key_in_a_two_relation_scope_is_bound() -> None:
    """With a GROUP BY, `qualify` rewrites `DISTINCT ON (o.customer_id)` to the bare output
    name, which bound to neither relation and was declared unresolved (torture D1)."""
    result = _result(
        "WITH pick AS (SELECT DISTINCT ON (o.customer_id) o.customer_id, p.method, "
        "sum(p.amount) AS total FROM staging.orders AS o "
        "JOIN staging.payments AS p ON p.order_id = o.id "
        "GROUP BY o.customer_id, p.method ORDER BY o.customer_id, 3 DESC)\n"
        "SELECT pick.customer_id, pick.method, pick.total FROM pick;"
    )
    assert result.boundaries == []
    assert ("filter", "staging.orders.customer_id", "identity") in _edges(result)
