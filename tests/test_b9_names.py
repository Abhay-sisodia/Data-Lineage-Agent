"""Batch B9's fixes and decisions, pinned outside the stress files.

`stress/pg/b9_names_resolution/REPORT.md` has the evidence for each. One statement per test,
the smallest that shows the rule, and a control wherever the fix could over-reach.

* B9-01  two same-named tables in two schemas keep their own columns
* B9-02  a quoted mixed-case relation resolves; an unquoted `user` is a function
* B9-03  `INSERT INTO t SELECT *` with no column list is not refused
* B9-04  `x.*` beside a USING join is x's own columns
* D-23 / D-24  SET search_path is followed; a unique name binds; an ambiguous one is declared
* D-25  PostgreSQL nodes are schema-qualified
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


def _values(sql: str) -> set[tuple[str, str]]:
    result = _result(sql)
    assert result.refusals == [], [r.reason for r in result.refusals]
    return {(str(e.source), str(e.target)) for e in result.edges if e.flow.value == "value"}


# --- B9-01 / D-25 ---------------------------------------------------------------------------


def test_same_named_tables_keep_their_own_columns() -> None:
    """core.ledger used to replace staging.ledger's columns: `amount` was "Unknown"."""
    assert _values(
        "INSERT INTO mart.kpi_rollup (revenue) SELECT l.amount FROM staging.ledger AS l"
    ) == {("column:staging.ledger.amount", "column:mart.kpi_rollup.revenue")}


def test_a_copy_between_same_named_tables_is_not_a_self_edge() -> None:
    """D-25: with bare names this read `ledger.account -> ledger.account`."""
    assert (
        "column:staging.ledger.account",
        "column:core.ledger.account",
    ) in _values(
        "INSERT INTO core.ledger (id, account) SELECT s.id, s.account FROM staging.ledger AS s"
    )


# --- B9-02 ----------------------------------------------------------------------------------


def test_a_quoted_mixed_case_relation_resolves_and_keeps_its_case() -> None:
    values = _values(
        'INSERT INTO mart."FeedSummary" ("Total", total) '
        'SELECT f."Amount", f.amount FROM staging."LegacyFeed" AS f'
    )
    assert values == {
        ("column:staging.LegacyFeed.Amount", "column:mart.FeedSummary.Total"),
        ("column:staging.LegacyFeed.amount", "column:mart.FeedSummary.total"),
    }


def test_unquoted_user_is_the_function_and_quoted_user_is_the_column() -> None:
    assert _values(
        'INSERT INTO mart.kpi_rollup (level, segment) SELECT user, f."user" '
        'FROM staging."LegacyFeed" AS f'
    ) == {("column:staging.LegacyFeed.user", "column:mart.kpi_rollup.segment")}


# --- B9-03 / B9-04 --------------------------------------------------------------------------


def test_select_star_with_no_column_list_binds_every_column() -> None:
    values = _values("INSERT INTO core.ledger SELECT * FROM core.ledger")
    assert len(values) == 4
    assert ("column:core.ledger.balance", "column:core.ledger.balance") in values


def test_a_qualified_star_beside_using_keeps_its_own_column() -> None:
    """x.id is orders.customer_id; the merged USING column is customers.id."""
    values = _values(
        "INSERT INTO mart.top_customers (customer_id, revenue) SELECT x.* "
        "FROM staging.customers AS c "
        "JOIN (SELECT o.customer_id AS id, o.amount FROM staging.orders AS o) AS x USING (id)"
    )
    assert values == {
        ("column:staging.orders.customer_id", "column:mart.top_customers.customer_id"),
        ("column:staging.orders.amount", "column:mart.top_customers.revenue"),
    }


def test_the_unqualified_merged_column_is_still_the_left_inputs() -> None:
    """The control: B1-02 is untouched for the merged name itself."""
    assert (
        "column:staging.customers.id",
        "column:mart.top_customers.customer_id",
    ) in _values(
        "INSERT INTO mart.top_customers (customer_id) SELECT id FROM staging.customers AS c "
        "JOIN (SELECT o.customer_id AS id FROM staging.orders AS o) AS x USING (id)"
    )


# --- D-23 / D-24 ----------------------------------------------------------------------------


def test_search_path_order_decides_and_declares_nothing() -> None:
    result = _result(
        "SET search_path TO staging, core;\n"
        "INSERT INTO mart.kpi_rollup (revenue) SELECT l.amount FROM ledger AS l;"
    )
    assert {(str(e.source), str(e.target)) for e in result.edges} == {
        ("column:staging.ledger.amount", "column:mart.kpi_rollup.revenue")
    }
    assert result.boundaries == []


def test_reset_returns_to_the_default_schema_and_declares_it() -> None:
    result = _result(
        "SET search_path TO staging;\nRESET search_path;\n"
        "INSERT INTO mart.kpi_rollup (segment) SELECT l.status FROM ledger AS l;"
    )
    assert {str(e.source) for e in result.edges} == {"column:core.ledger.status"}
    assert [b.kind.value for b in result.boundaries] == ["context_dependent_binding"]


def test_a_name_unique_to_one_schema_binds_to_it() -> None:
    assert _values(
        "INSERT INTO mart.carrier_scorecard (carrier_id) SELECT s.carrier_id FROM shipments AS s"
    ) == {("column:staging.shipments.carrier_id", "column:mart.carrier_scorecard.carrier_id")}


def test_an_ambiguous_name_is_declared_and_emits_nothing() -> None:
    result = _result("INSERT INTO mart.kpi_rollup (level) SELECT e.kind FROM events_log AS e")
    assert result.edges == []
    assert ("dangling_reference", "EVENTS_LOG") in {
        (b.kind.value, b.subject.upper()) for b in result.boundaries
    }
