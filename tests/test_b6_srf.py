"""Batch B6's fixes and decisions, pinned outside the stress files.

`stress/pg/b6_srf_table_functions/REPORT.md` has the evidence for each. One statement per
test, the smallest that shows the rule, and a control wherever the fix could over-reach.

* B6-05  WITH ORDINALITY under LATERAL is analysed, not refused
* B6-01 / D-28  built-in SRFs in FROM: a series derives from its bounds; literals are silent
* B6-02  ROWS FROM binds each function's column by position
* B6-03  a single-column SRF is named by its table alias
* B6-04  regexp_split_to_table in the select list is an element of its argument
* B6-06  an SRF over a scalar subquery traces the subquery in its own scope
* D-26  the ordinal is influenced by its array; a filter on it filters from the array
* D-27  an unused SRF in FROM is structural
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


def _facts(sql: str) -> set[tuple[str, str, str, str]]:
    result = _result(sql)
    assert result.refusals == [], [r.reason for r in result.refusals]
    return {(str(e.source), str(e.target), e.flow.value, e.transform.value) for e in result.edges}


TAGS = "column:staging.orders.tags"


# --- B6-05 / D-26 ---------------------------------------------------------------------------


def test_ordinality_under_lateral_is_analysed_and_filters_from_the_array() -> None:
    """Referencing the ordinal used to refuse the whole statement ("Unknown column: n")."""
    facts = _facts(
        "INSERT INTO mart.channel_mix (channel) SELECT t.tag FROM staging.orders AS o "
        "CROSS JOIN LATERAL unnest(o.tags) WITH ORDINALITY AS t(tag, n) WHERE t.n <= 2"
    )
    assert facts == {
        (TAGS, "column:mart.channel_mix.channel", "value", "derived"),
        (TAGS, "relation:mart.channel_mix", "filter", "identity"),
    }


def test_the_ordinal_carries_influence_and_no_value() -> None:
    facts = _facts(
        "INSERT INTO mart.top_customers (position) SELECT t.n "
        "FROM staging.orders AS o, unnest(o.tags) WITH ORDINALITY AS t(tag, n)"
    )
    assert facts == {(TAGS, "column:mart.top_customers.position", "influence", "identity")}


# --- B6-01 / D-28 ---------------------------------------------------------------------------


def test_a_series_in_from_derives_from_its_bound_column() -> None:
    result = _result(
        "INSERT INTO mart.kpi_rollup (orders) SELECT g "
        "FROM core.dim_warehouse AS w, generate_series(1, w.capacity) AS g"
    )
    assert {(str(e.source), e.transform.value) for e in result.edges} == {
        ("column:core.dim_warehouse.capacity", "derived")
    }
    assert result.boundaries == []


def test_a_literal_series_has_no_edge_and_no_boundary() -> None:
    """A calendar table used to raise "row source is a function" for no loss at all."""
    result = _result(
        "INSERT INTO mart.sales_daily (sale_date) SELECT d::date "
        "FROM generate_series(DATE '2024-01-01', DATE '2024-01-31', INTERVAL '1 day') AS d"
    )
    assert result.edges == [] and result.boundaries == [] and result.refusals == []


def test_string_to_table_in_from_is_an_element_of_its_argument() -> None:
    assert _facts(
        "INSERT INTO mart.kpi_rollup (segment) SELECT s.part "
        "FROM staging.customers AS c, string_to_table(c.full_name, ' ') AS s(part)"
    ) == {
        ("column:staging.customers.full_name", "column:mart.kpi_rollup.segment", "value", "derived")
    }


# --- B6-02 / B6-03 / B6-04 ------------------------------------------------------------------


def test_rows_from_binds_each_function_by_position() -> None:
    facts = _facts(
        "INSERT INTO mart.top_customers (region, full_name) SELECT r.a, r.b "
        "FROM staging.orders AS o, ROWS FROM (unnest(o.tags), unnest(ARRAY[o.currency::text])) "
        "AS r(a, b)"
    )
    assert facts == {
        (TAGS, "column:mart.top_customers.region", "value", "derived"),
        (
            "column:staging.orders.currency",
            "column:mart.top_customers.full_name",
            "value",
            "derived",
        ),
    }


def test_a_table_alias_names_a_single_column_srf() -> None:
    assert _facts(
        "INSERT INTO mart.kpi_rollup (region) SELECT v "
        "FROM staging.customers AS c, unnest(ARRAY[c.country_code]) AS v"
    ) == {
        (
            "column:staging.customers.country_code",
            "column:mart.kpi_rollup.region",
            "value",
            "derived",
        )
    }


def test_regexp_split_to_table_in_the_select_list() -> None:
    assert _facts(
        "INSERT INTO mart.kpi_rollup (country_code) "
        "SELECT regexp_split_to_table(c.email, '@') FROM staging.customers AS c"
    ) == {
        (
            "column:staging.customers.email",
            "column:mart.kpi_rollup.country_code",
            "value",
            "derived",
        )
    }


# --- B6-06 ----------------------------------------------------------------------------------


def test_an_srf_over_a_scalar_subquery_traces_the_subquery() -> None:
    facts = _facts(
        "INSERT INTO mart.product_performance (category_name) "
        "SELECT unnest((SELECT array_agg(p.brand) FROM staging.products AS p))"
    )
    assert facts == {
        (
            "column:staging.products.brand",
            "column:mart.product_performance.category_name",
            "value",
            "aggregated",
        ),
    }


# --- controls -------------------------------------------------------------------------------


def test_an_unused_srf_in_from_is_structural() -> None:
    """D-27: the unnest repeats orders and drops untagged ones, and names no column."""
    assert _facts(
        "INSERT INTO mart.sales_daily (gross_amount) SELECT o.amount "
        "FROM staging.orders AS o, unnest(o.tags) AS t(tag)"
    ) == {
        (
            "column:staging.orders.amount",
            "column:mart.sales_daily.gross_amount",
            "value",
            "identity",
        )
    }


def test_a_user_defined_srf_is_still_declared_by_its_name() -> None:
    result = _result(
        "INSERT INTO mart.kpi_rollup (level) SELECT x.k "
        "FROM staging.orders AS o, analytics.explode_payload(o.payload) AS x(k text)"
    )
    assert result.edges == []
    assert {b.subject for b in result.boundaries} == {"TABLE(ANALYTICS.EXPLODE_PAYLOAD)"}
