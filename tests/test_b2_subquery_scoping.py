"""Batch B2's fixes and decisions, pinned outside the stress files.

`stress/pg/b2_subqueries_ctes_setops/REPORT.md` has the evidence for each. One statement per
test, the smallest that shows the rule, and a control wherever the fix could over-reach.

* B2-08  a name in a subquery resolves INNERMOST first - a shadowing alias wins
* B2-02 / B2-03 / B2-09  a nested query supplies values only through its projection
* D-12 / D-13  a SELECT-list scalar subquery's predicate is a value, its ordered pick influence
* D-14  a predicate that cannot remove a result row is not a filter
* B2-01  a column passed through a scalar subquery unchanged is identity
* B2-04 / B2-05  D-9 over a whole set operation, and inside a LATERAL
* B2-06  a column reaching its predicate through a subquery's aggregate is post-aggregation
* B2-07 / B2-10  set operations and recursive CTEs inside subqueries resolve, and declare nothing
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"
PRE, POST = "pre-aggregation", "post-aggregation"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _facts(sql: str) -> set[tuple[str, str, str, str, str]]:
    return {
        (str(e.source), str(e.target), e.flow.value, e.transform.value, e.phase.value)
        for e in _result(sql).edges
    }


def _filters(sql: str) -> set[tuple[str, str]]:
    return {(f[0], f[4]) for f in _facts(sql) if f[2] == "filter"}


# --- B2-08: innermost-first ------------------------------------------------------------


def test_a_shadowing_inner_alias_is_the_inner_relation() -> None:
    """Inside the EXISTS, `o` is payments. Bound outward it was a wrong edge from orders."""
    filters = _filters(
        "INSERT INTO mart.channel_mix (channel) SELECT o.channel FROM staging.orders AS o "
        "WHERE EXISTS (SELECT 1 FROM staging.payments AS o WHERE o.order_id = o.id)"
    )
    assert filters == {("column:payments.order_id", PRE), ("column:payments.id", PRE)}


def test_a_genuine_correlation_still_resolves_outward() -> None:
    """The control: a name the inner query does not have binds to the outer one."""
    assert ("column:orders.store_id", PRE) in _filters(
        "INSERT INTO mart.channel_mix (channel) SELECT o.channel FROM staging.orders AS o "
        "WHERE EXISTS (SELECT 1 FROM staging.shipments AS s WHERE s.carrier_id = store_id)"
    )


# --- B2-02 / B2-03 / B2-09: a nested query's value is its projection --------------------


def test_an_ordering_and_a_join_inside_a_scalar_subquery_are_not_values() -> None:
    facts = _facts(
        "INSERT INTO mart.customer_summary (customer_id, lifetime_value) "
        "SELECT c.id, (SELECT p.amount FROM staging.payments AS p "
        "  JOIN staging.orders AS o ON o.id = p.order_id "
        "  WHERE o.customer_id = c.id ORDER BY p.paid_at DESC LIMIT 1) "
        "FROM staging.customers AS c"
    )
    values = {(f[0], f[3]) for f in facts if f[2] == "value"}
    assert ("column:payments.paid_at", "derived") not in values, "B2-02: the ordering is not in it"
    assert not {s for s, _ in values} & {"column:orders.id", "column:payments.order_id"}, (
        "B2-03: a join key is structural (D-5)"
    )
    assert (
        "column:payments.paid_at",
        "column:customer_summary.lifetime_value",
        "influence",
        "identity",
        PRE,
    ) in facts, "D-13: the ordered pick is influence"


def test_an_exists_ordering_decides_nothing() -> None:
    """B2-09: whether a row exists does not depend on which row is first, LIMIT or not."""
    filters = _filters(
        "INSERT INTO mart.product_performance (product_id) SELECT p.product_key "
        "FROM core.dim_product AS p WHERE EXISTS (SELECT 1 FROM core.fct_order_line AS f "
        "WHERE f.product_key = p.product_key ORDER BY f.order_date DESC LIMIT 1)"
    )
    assert ("column:fct_order_line.order_date", PRE) not in filters


# --- D-12 / D-14 ------------------------------------------------------------------------


def test_a_scalar_subquerys_predicate_is_a_value_of_its_column() -> None:
    facts = _facts(
        "INSERT INTO mart.customer_summary (customer_id, lifetime_value) "
        "SELECT c.id, (SELECT SUM(p.amount) FROM staging.payments AS p "
        "WHERE p.order_id = c.id AND p.status = 'SETTLED') FROM staging.customers AS c"
    )
    for column in ("payments.order_id", "customers.id", "payments.status"):
        assert (
            f"column:{column}",
            "column:customer_summary.lifetime_value",
            "value",
            "aggregated",
            PRE,
        ) in facts
    assert not [f for f in facts if f[2] == "filter"], "no row of the target is removed"


def test_a_lookup_keys_are_conditional_and_its_column_identity() -> None:
    """D-12 for a lookup, and B2-01: the looked-up column arrives unchanged."""
    facts = _facts(
        "INSERT INTO mart.top_customers (customer_id, full_name) SELECT o.id, "
        "(SELECT c.full_name FROM staging.customers AS c WHERE c.id = o.customer_id) "
        "FROM staging.orders AS o"
    )
    assert (
        "column:customers.full_name",
        "column:top_customers.full_name",
        "value",
        "identity",
        PRE,
    ) in facts
    assert (
        "column:customers.id",
        "column:top_customers.full_name",
        "value",
        "conditional",
        PRE,
    ) in facts


def test_a_left_joined_sides_predicate_is_a_value_of_what_it_feeds() -> None:
    """D-14: under a LEFT JOIN the derived table's WHERE cannot remove an outer row."""
    facts = _facts(
        "INSERT INTO mart.sales_daily (orders, net_amount) SELECT o.id, lp.amt "
        "FROM staging.orders AS o LEFT JOIN (SELECT p.order_id, SUM(p.amount) AS amt "
        "FROM staging.payments AS p WHERE p.status = 'SETTLED' GROUP BY p.order_id) AS lp "
        "ON lp.order_id = o.id"
    )
    assert (
        "column:payments.status",
        "column:sales_daily.net_amount",
        "value",
        "aggregated",
        PRE,
    ) in facts
    assert ("column:payments.status", PRE) not in _filters(
        "INSERT INTO mart.sales_daily (orders, net_amount) SELECT o.id, lp.amt "
        "FROM staging.orders AS o LEFT JOIN (SELECT p.order_id, SUM(p.amount) AS amt "
        "FROM staging.payments AS p WHERE p.status = 'SETTLED' GROUP BY p.order_id) AS lp "
        "ON lp.order_id = o.id"
    )


def test_an_inner_joined_sides_predicate_is_still_a_filter() -> None:
    """The control: the same derived table INNER JOINed CAN remove an order."""
    assert ("column:payments.status", PRE) in _filters(
        "INSERT INTO mart.sales_daily (orders, net_amount) SELECT o.id, lp.amt "
        "FROM staging.orders AS o JOIN (SELECT p.order_id, SUM(p.amount) AS amt "
        "FROM staging.payments AS p WHERE p.status = 'SETTLED' GROUP BY p.order_id) AS lp "
        "ON lp.order_id = o.id"
    )


def test_a_merge_using_a_left_joined_side_keeps_its_predicate_as_a_value() -> None:
    """Found by diffing the Oracle stress packages: the MERGE path traced through the side
    without carrying its predicate, so a filter became a SILENT loss rather than a value."""
    facts = _facts(
        "MERGE INTO mart.sales_daily AS t USING (SELECT o.id, lp.amt FROM staging.orders AS o "
        "LEFT JOIN (SELECT p.order_id, SUM(p.amount) AS amt FROM staging.payments AS p "
        "WHERE p.status = 'SETTLED' GROUP BY p.order_id) AS lp ON lp.order_id = o.id) AS x "
        "ON (t.orders = x.id) WHEN MATCHED THEN UPDATE SET net_amount = x.amt"
    )
    assert any(
        f[0] == "column:payments.status"
        and f[1] == "column:sales_daily.net_amount"
        and f[2] == "value"
        for f in facts
    )


# --- B2-04 / B2-05: D-9 completed ------------------------------------------------------


def test_a_limit_over_a_whole_union_filters_every_arm() -> None:
    assert _filters(
        "INSERT INTO mart.top_customers (customer_id, revenue) "
        "SELECT p.order_id, p.amount FROM staging.payments AS p UNION ALL "
        "SELECT r.order_line_id, r.amount FROM staging.returns AS r ORDER BY 2 DESC LIMIT 20"
    ) == {("column:payments.amount", PRE), ("column:returns.amount", PRE)}


def test_a_limit_inside_a_lateral_is_a_filter_and_its_grouping_reaches_the_column() -> None:
    sql = (
        "INSERT INTO mart.product_performance (product_id, units) SELECT t.pid, t.qty "
        "FROM core.dim_store AS s CROSS JOIN LATERAL (SELECT f.product_key AS pid, "
        "SUM(f.quantity) AS qty FROM core.fct_order_line AS f WHERE f.store_id = s.id "
        "GROUP BY f.product_key ORDER BY SUM(f.quantity) DESC LIMIT 5) AS t"
    )
    assert ("column:fct_order_line.quantity", POST) in _filters(sql)
    assert (
        "column:fct_order_line.product_key",
        "column:product_performance.units",
        "influence",
        "identity",
        PRE,
    ) in _facts(sql)


# --- B2-06 ------------------------------------------------------------------------------


def test_a_column_compared_through_a_subquery_aggregate_is_post_aggregation() -> None:
    filters = _filters(
        "INSERT INTO mart.sales_daily (net_amount) SELECT f.net_amount "
        "FROM core.fct_order_line AS f "
        "WHERE f.unit_price >= (SELECT AVG(p.list_price) FROM core.dim_product AS p)"
    )
    assert ("column:dim_product.list_price", POST) in filters
    assert ("column:fct_order_line.unit_price", PRE) in filters


# --- B2-07 / B2-10 ----------------------------------------------------------------------


def test_an_in_over_an_intersect_keeps_its_first_arms_key() -> None:
    result = _result(
        "INSERT INTO mart.product_performance (product_id) SELECT f.product_key "
        "FROM core.fct_order_line AS f WHERE f.product_key IN ("
        "SELECT p.product_key FROM core.dim_product AS p WHERE p.brand = 'ACME' INTERSECT "
        "SELECT p2.product_key FROM core.dim_product AS p2 WHERE p2.status = 'ACTIVE')"
    )
    assert "column:dim_product.product_key" in {
        str(e.source) for e in result.edges if e.flow.value == "filter"
    }
    assert result.boundaries == []


def test_a_recursive_ctes_self_reference_declares_nothing() -> None:
    result = _result(
        "WITH RECURSIVE tree AS ("
        "  SELECT c.id, c.department FROM core.dim_category AS c WHERE c.parent_id IS NULL"
        "  UNION ALL"
        "  SELECT ch.id, t.department FROM core.dim_category AS ch"
        "  JOIN tree AS t ON ch.parent_id = t.id) "
        "INSERT INTO mart.kpi_rollup (region) SELECT tr.department FROM tree AS tr"
    )
    assert result.boundaries == []
    assert ("column:dim_category.department", "column:kpi_rollup.region") in {
        (str(e.source), str(e.target)) for e in result.edges
    }
