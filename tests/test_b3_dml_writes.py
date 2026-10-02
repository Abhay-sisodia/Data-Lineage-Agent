"""Batch B3's fixes and decisions, pinned outside the stress files.

`stress/pg/b3_dml_writes/REPORT.md` has the evidence for each. One statement per test, the
smallest that shows the rule, and a control wherever the fix could over-reach.

* B3-04 / B3-07  UPDATE ... FROM and DELETE ... USING: the target join is structural (D-11)
* B3-05  a leading WITH reaches UPDATE / DELETE / MERGE
* B3-06  a row-valued SET writes to its named columns, never to an empty name
* B3-03 / D-16 / D-17  writable CTEs; RETURNING lands in <result>
* B3-08 / B3-09  ON CONFLICT DO UPDATE and MERGE arm conditions are analysed
* B3-01 / B3-02  aliased targets, omitted column lists, DEFAULT VALUES
* D-18 / B3-10 / B3-11 / B3-12  D-12 in SET and VALUES; aggregate phases
* PG-01  an UPDATE is counted
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
    result = _result(sql)
    assert result.refusals == [], [r.code.value for r in result.refusals]
    return {
        (str(e.source), str(e.target), e.flow.value, e.transform.value, e.phase.value)
        for e in result.edges
    }


def _flow(sql: str, flow: str) -> set[tuple[str, str]]:
    return {(f[0], f[1]) for f in _facts(sql) if f[2] == flow}


# --- B3-04 / B3-07: the target join is structural ---------------------------------------


def test_update_from_join_is_not_a_filter_and_from_values_resolve() -> None:
    """`c.id = d.customer_id` joins the target to FROM. It was reported as two filters, and
    `c.country_code` was unresolved, so the one real value was lost."""
    facts = _facts(
        "UPDATE core.dim_customer AS d SET country_code = c.country_code "
        "FROM staging.customers AS c WHERE c.id = d.customer_id AND c.status = 'A'"
    )
    assert facts == {
        ("column:customers.country_code", "column:dim_customer.country_code",
         "value", "identity", PRE),
        ("column:customers.status", "relation:dim_customer", "filter", "identity", PRE),
    }


def test_delete_using_join_is_not_a_filter() -> None:
    """The control is the second predicate: it removes rows, so it still filters."""
    assert _flow(
        "DELETE FROM core.fct_payment AS fp USING staging.payments AS p "
        "WHERE p.id = fp.payment_id AND p.status = 'REVERSED'",
        "filter",
    ) == {("column:payments.status", "relation:fct_payment")}


# --- B3-05: WITH before a write ----------------------------------------------------------


def test_a_leading_with_reaches_an_update() -> None:
    """`stale` was a dangling relation, and `stale.id` was emitted as if it were a column."""
    filters = _flow(
        "WITH stale AS (SELECT c.id FROM staging.customers AS c WHERE c.status = 'X') "
        "UPDATE core.dim_customer AS d SET is_current = false "
        "WHERE d.customer_id IN (SELECT s.id FROM stale AS s)",
        "filter",
    )
    assert ("column:customers.status", "relation:dim_customer") in filters
    assert not any("stale" in source for source, _ in filters)


# --- B3-06: row-valued SET ----------------------------------------------------------------


def test_row_valued_set_writes_each_named_column() -> None:
    """`SET (a, b) = (x, y)` wrote to `column:dim_store.` - a column with no name."""
    values = _flow(
        "UPDATE core.dim_store AS s SET (name, region) = (upper(s.name), s.country_code) "
        "WHERE s.status = 'OPEN'",
        "value",
    )
    assert values == {
        ("column:dim_store.name", "column:dim_store.name"),
        ("column:dim_store.country_code", "column:dim_store.region"),
    }


# --- B3-03 / D-16 / D-17: writable CTEs and RETURNING --------------------------------------


def test_a_writable_cte_nothing_reads_still_filters_its_own_table() -> None:
    """PostgreSQL runs the DELETE whether or not the CTE is read. It produced nothing at all,
    with no boundary - the silent R1. Nothing reaches <result>: `SELECT 1` reads no column."""
    facts = _facts(
        "WITH gone AS (DELETE FROM staging.payments AS p WHERE p.status = 'VOID' "
        "RETURNING p.id) SELECT 1"
    )
    assert facts == {("column:payments.status", "relation:payments", "filter", "identity", PRE)}


def test_a_writable_cte_predicate_filters_every_table_its_rows_reach() -> None:
    """D-17: the DELETE's predicate decides the rows deleted AND the rows inserted."""
    facts = _facts(
        "WITH gone AS (DELETE FROM staging.payments AS p WHERE p.status = 'VOID' "
        "RETURNING p.id, p.amount) "
        "INSERT INTO core.fct_payment (payment_id, amount) SELECT g.id, g.amount FROM gone AS g"
    )
    assert {(f[0], f[1]) for f in facts if f[2] == "filter"} == {
        ("column:payments.status", "relation:payments"),
        ("column:payments.status", "relation:fct_payment"),
    }
    assert ("column:payments.amount", "column:fct_payment.amount") in {
        (f[0], f[1]) for f in facts if f[2] == "value"
    }


def test_returning_lands_in_result() -> None:
    """D-16. The returned value is the written row's, so it comes from the target column."""
    assert ("column:channel_mix.channel", "column:<result>.channel") in _flow(
        "INSERT INTO mart.channel_mix (channel) SELECT o.channel FROM staging.orders AS o "
        "RETURNING channel",
        "value",
    )


# --- B3-08 / B3-09: the upsert forms ------------------------------------------------------


def test_on_conflict_do_update_set_and_where_are_analysed() -> None:
    """EXCLUDED is the proposed row; DO UPDATE's WHERE filters the target (both were lost)."""
    facts = _facts(
        "INSERT INTO mart.customer_summary AS cs (customer_id, lifetime_value) "
        "SELECT o.customer_id, SUM(o.amount) FROM staging.orders AS o GROUP BY o.customer_id "
        "ON CONFLICT (customer_id) DO UPDATE SET lifetime_value = EXCLUDED.lifetime_value "
        "WHERE cs.status <> 'FROZEN'"
    )
    assert ("column:orders.amount", "column:customer_summary.lifetime_value", "value",
            "aggregated", PRE) in facts
    assert ("column:customer_summary.status", "relation:customer_summary", "filter",
            "identity", PRE) in facts
    # D-15: the conflict target is structural - customer_id is never a filter.
    assert not any(f[2] == "filter" and f[0].endswith(".customer_id") for f in facts)


def test_a_merge_arm_condition_is_a_filter_and_on_is_not() -> None:
    filters = _flow(
        "MERGE INTO core.dim_customer AS t USING staging.customers AS s ON t.customer_id = s.id "
        "WHEN MATCHED AND s.status = 'CLOSED' THEN DELETE "
        "WHEN MATCHED THEN UPDATE SET email = s.email",
        "filter",
    )
    assert filters == {("column:customers.status", "relation:dim_customer")}


# --- B3-01 / B3-02: target spellings ------------------------------------------------------


def test_an_aliased_target_keeps_its_schema() -> None:
    """`mart.top_customers AS tc` was looked up as `core.top_customers` and refused."""
    assert ("column:customers.full_name", "column:top_customers.full_name") in _flow(
        "INSERT INTO mart.top_customers AS tc (customer_id, full_name) "
        "SELECT c.id, c.full_name FROM staging.customers AS c",
        "value",
    )


def test_an_omitted_column_list_binds_the_first_columns_in_table_order() -> None:
    """Two values fill the first two of six columns. Read as all six, it was refused."""
    assert _flow(
        "INSERT INTO mart.channel_mix SELECT o.order_ts::date, o.channel FROM staging.orders AS o",
        "value",
    ) == {
        ("column:orders.order_ts", "column:channel_mix.period_month"),
        ("column:orders.channel", "column:channel_mix.channel"),
    }


def test_default_values_is_counted_and_empty_not_refused() -> None:
    result = _result("INSERT INTO mart.channel_mix DEFAULT VALUES")
    assert result.refusals == []
    assert result.edges == []
    assert result.statements_seen == 1


# --- D-18 / B3-10 / B3-11 / B3-12 ---------------------------------------------------------


def test_a_set_scalar_subquery_predicate_is_a_value_not_a_filter() -> None:
    """D-18: D-12 in SET. The correlation decides the value written, not which rows change."""
    facts = _facts(
        "UPDATE core.dim_customer AS d SET full_name = (SELECT c.full_name "
        "FROM staging.customers AS c WHERE c.id = d.customer_id)"
    )
    assert not any(f[2] == "filter" for f in facts)
    assert ("column:customers.id", "column:dim_customer.full_name", "value", "conditional",
            PRE) in facts


def test_a_values_scalar_subquery_resolves() -> None:
    """B3-12: `o.id` inside a VALUES row was declared unresolved."""
    assert _flow(
        "INSERT INTO mart.channel_mix (channel, orders) VALUES ('web', "
        "(SELECT COUNT(DISTINCT o.id) FROM staging.orders AS o WHERE o.channel = 'web'))",
        "value",
    ) == {
        ("column:orders.id", "column:channel_mix.orders"),
        ("column:orders.channel", "column:channel_mix.orders"),
    }


def test_a_rank_ordered_by_an_inline_aggregate_is_post_aggregation() -> None:
    """B3-10: `ORDER BY SUM(o.amount)` ranks aggregated rows, so `rn <= 10` cuts after."""
    facts = _facts(
        "INSERT INTO mart.top_customers (region, customer_id) SELECT r.region, r.customer_id "
        "FROM (SELECT d.region, o.customer_id, ROW_NUMBER() OVER (PARTITION BY d.region "
        "ORDER BY SUM(o.amount) DESC) AS rn FROM staging.orders AS o "
        "JOIN core.dim_customer AS d ON d.customer_id = o.customer_id "
        "GROUP BY d.region, o.customer_id) AS r WHERE r.rn <= 10"
    )
    assert {(f[0], f[4]) for f in facts if f[2] == "filter"} == {
        ("column:orders.amount", POST),
        ("column:dim_customer.region", POST),
    }


def test_a_rank_ordered_by_a_plain_column_stays_pre_aggregation() -> None:
    """The control: no aggregate in the window, so the cut is on base rows."""
    facts = _facts(
        "INSERT INTO mart.top_customers (region, customer_id) SELECT r.region, r.customer_id "
        "FROM (SELECT d.region, d.customer_id, ROW_NUMBER() OVER (PARTITION BY d.region "
        "ORDER BY d.first_order_date) AS rn FROM core.dim_customer AS d) AS r WHERE r.rn <= 10"
    )
    assert {f[4] for f in facts if f[2] == "filter"} == {PRE}


def test_an_update_predicate_through_an_aggregate_is_post_aggregation() -> None:
    """B3-11: B2-06's rule on the UPDATE path."""
    facts = _facts(
        "UPDATE core.dim_customer AS d SET status = 'VIP' WHERE (SELECT SUM(o.amount) "
        "FROM staging.orders AS o WHERE o.customer_id = d.customer_id) > 1000"
    )
    assert ("column:orders.amount", "relation:dim_customer", "filter", "identity",
            POST) in facts


# --- PG-01 --------------------------------------------------------------------------------


def test_an_update_is_counted() -> None:
    """An UPDATE was analysed and never counted: its table had a writer no number showed."""
    result = _result("UPDATE core.dim_store AS s SET region = s.country_code")
    assert result.statements_seen == 1
    assert result.edges != []
