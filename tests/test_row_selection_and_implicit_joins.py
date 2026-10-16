"""Decisions D-9 and D-11, and finding B1-03, pinned outside the stress batch.

* **D-9** - a column that decides WHICH row survives is a filter: DISTINCT ON keys and
  tie-breakers, the ORDER BY of a row limit, and the partition/order of a window whose
  result is filtered. Before it, "the latest row per customer" left no trace at all.
* **D-11** - an EQUALITY between two inner-joined relations, written in WHERE, is the join
  and is structural, exactly as in ON (D-5). Equality only: a non-equality is B1-Q2, open.
* **B1-03** - `percentile_cont ... WITHIN GROUP (ORDER BY x)`: x is the aggregated value.
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _filters(sql: str) -> set[tuple[str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    return {(str(e.source), e.phase.value) for e in result.edges if e.flow.value == "filter"}


def _values(sql: str) -> set[tuple[str, str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    return {
        (str(e.source), str(e.target), e.transform.value)
        for e in result.edges
        if e.flow.value == "value"
    }


PRE, POST = "pre-aggregation", "post-aggregation"


# --- D-9 --------------------------------------------------------------------------------


def test_distinct_on_keys_and_tie_breakers_are_filters() -> None:
    assert _filters(
        "INSERT INTO mart.top_customers (customer_id, revenue) "
        "SELECT DISTINCT ON (o.customer_id) o.customer_id, o.amount FROM staging.orders AS o "
        "ORDER BY o.customer_id, o.order_ts DESC"
    ) == {("column:staging.orders.customer_id", PRE), ("column:staging.orders.order_ts", PRE)}


def test_a_filtered_rank_filters_on_its_partition_and_order() -> None:
    """The same rows as the DISTINCT ON above, so the same filters (B1 T5 pair 10)."""
    assert _filters(
        "INSERT INTO mart.top_customers (customer_id, revenue) "
        "SELECT x.customer_id, x.amount FROM ("
        "  SELECT o.customer_id, o.amount, ROW_NUMBER() OVER "
        "    (PARTITION BY o.customer_id ORDER BY o.order_ts DESC) AS rn "
        "  FROM staging.orders AS o) AS x WHERE x.rn = 1"
    ) == {("column:staging.orders.customer_id", PRE), ("column:staging.orders.order_ts", PRE)}


def test_an_ordinal_under_a_limit_is_the_projection_it_numbers() -> None:
    """`ORDER BY 2` is SUM(o.amount): an aggregate, so the cut acts post-aggregation."""
    assert _filters(
        "INSERT INTO mart.channel_mix (channel, revenue) "
        "SELECT o.channel, SUM(o.amount) FROM staging.orders AS o "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
    ) == {("column:staging.orders.amount", POST)}


def test_an_output_name_under_a_limit_wins_over_an_input_column() -> None:
    """PostgreSQL reads a bare ORDER BY name as the OUTPUT column first: `cost` here is
    weight_kg, while the WHERE's `cost` is the input column."""
    assert _filters(
        "INSERT INTO mart.top_customers (customer_id, revenue) "
        "SELECT sh.order_id, sh.weight_kg AS cost FROM staging.shipments AS sh "
        "WHERE cost > 0 ORDER BY cost DESC LIMIT 20"
    ) == {("column:staging.shipments.cost", PRE), ("column:staging.shipments.weight_kg", PRE)}


def test_no_row_limit_or_no_ordering_decides_nothing() -> None:
    """The negatives D-9 names: ORDER BY alone removes no rows; LIMIT alone keeps
    arbitrary ones; an unfiltered rank is plain influence."""
    assert (
        _filters(
            "INSERT INTO mart.sales_daily (units) "
            "SELECT i.on_hand FROM staging.inventory_snapshots AS i ORDER BY i.reserved"
        )
        == set()
    )
    assert (
        _filters(
            "INSERT INTO mart.sales_daily (sale_date) SELECT d.calendar_date FROM core.dim_date AS d LIMIT 10"
        )
        == set()
    )
    assert (
        _filters(
            "INSERT INTO mart.top_customers (customer_id, position) "
            "SELECT o.customer_id, ROW_NUMBER() OVER (ORDER BY o.order_ts) FROM staging.orders AS o"
        )
        == set()
    )


def test_a_scalar_subquerys_limit_picks_a_value_not_rows() -> None:
    """`(SELECT ... ORDER BY y LIMIT 1)` in a projection decides a VALUE; it removes no
    row from the target, so it is not a D-9 filter."""
    assert (
        _filters(
            "INSERT INTO mart.top_customers (customer_id, revenue) "
            "SELECT c.id, (SELECT p.amount FROM staging.payments AS p "
            "              ORDER BY p.paid_at DESC LIMIT 1) "
            "FROM staging.customers AS c"
        )
        == set()
    )


# --- D-11 -------------------------------------------------------------------------------


def test_a_comma_join_equality_is_structural() -> None:
    assert _filters(
        "INSERT INTO mart.sales_daily (sale_date, gross_amount) "
        "SELECT f.order_date, f.gross_amount FROM core.fct_order_line AS f, core.dim_store AS s "
        "WHERE f.store_id = s.id AND s.status = 'OPEN'"
    ) == {("column:core.dim_store.status", PRE)}


def test_a_non_equality_between_relations_is_still_a_filter() -> None:
    """D-11 is equality only; whether this is structural is B1-Q2. Widening D-11 to every
    comparison moved stress_01's self-join predicate, which its key labels a filter."""
    assert _filters(
        "INSERT INTO mart.sales_daily (sale_date) "
        "SELECT f.order_date FROM core.fct_order_line AS f, core.dim_store AS s "
        "WHERE f.store_id = s.id AND f.order_date >= s.opened_on"
    ) == {("column:core.fct_order_line.order_date", PRE), ("column:core.dim_store.opened_on", PRE)}


def test_an_equality_touching_an_outer_joins_nullable_side_stays_a_filter() -> None:
    """After a LEFT JOIN, `WHERE s.status = c.status` removes rows the join kept."""
    assert _filters(
        "INSERT INTO mart.kpi_rollup (region) "
        "SELECT c.mode FROM core.dim_carrier AS c "
        "LEFT JOIN core.fct_shipment AS s ON s.carrier_id = c.id WHERE s.status = c.status"
    ) == {("column:core.fct_shipment.status", PRE), ("column:core.dim_carrier.status", PRE)}


def test_an_equality_under_or_is_logic_not_a_join() -> None:
    assert ("column:core.dim_store.id", PRE) in _filters(
        "INSERT INTO mart.sales_daily (sale_date) "
        "SELECT f.order_date FROM core.fct_order_line AS f, core.dim_store AS s "
        "WHERE f.store_id = s.id OR s.status = 'X'"
    )


# --- B1-03 ------------------------------------------------------------------------------


def test_an_ordered_set_aggregate_is_aggregated() -> None:
    assert _values(
        "INSERT INTO mart.carrier_scorecard (carrier_id, p50_transit_days) "
        "SELECT s.carrier_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY s.transit_days) "
        "FROM core.fct_shipment AS s GROUP BY s.carrier_id"
    ) == {
        ("column:core.fct_shipment.carrier_id", "column:mart.carrier_scorecard.carrier_id", "identity"),
        (
            "column:core.fct_shipment.transit_days",
            "column:mart.carrier_scorecard.p50_transit_days",
            "aggregated",
        ),
    }
