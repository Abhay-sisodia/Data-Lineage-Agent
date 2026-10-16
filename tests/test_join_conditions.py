"""D-48 to D-51 (2026-10-06): ON conditions, non-equality joins, grouping(), and paths.

* D-48 - a non-key condition in an ON follows the join's side: on an inner join a FILTER,
  on an outer join a conditional VALUE of what the null-supplying side feeds. Until then
  D-5 made the whole ON structural, and `JOIN d ON d.id = c.id AND d.is_current` lost
  `is_current` without a trace.
* D-49 (B1-Q2) - a non-equality between two relations is a filter, both operands, in ON or
  WHERE alike.
* D-50 (B1-Q1) - grouping() carries no lineage.
* D-51 (TQ-6) - one source reaching one target by several paths is one value fact, at the
  hardest transform.
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _edges(sql: str) -> set[tuple[str, str, str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [], result.refusals
    return {
        (
            e.flow.value,
            str(e.source).split(":", 1)[1],
            str(e.target).split(":", 1)[1],
            e.transform.value,
        )
        for e in result.edges
    }


# --- D-48 -----------------------------------------------------------------------------------


def test_a_one_relation_condition_in_an_inner_on_is_a_filter() -> None:
    edges = _edges(
        "SELECT o.id, c.full_name FROM staging.orders AS o "
        "JOIN staging.customers AS c ON c.id = o.customer_id AND c.status = 'ACTIVE';"
    )
    assert ("filter", "staging.customers.status", "<result>", "identity") in edges
    assert ("filter", "staging.customers.id", "<result>", "identity") not in edges


def test_in_a_left_join_it_is_a_value_of_the_null_side() -> None:
    edges = _edges(
        "SELECT o.id, c.full_name FROM staging.orders AS o "
        "LEFT JOIN staging.customers AS c ON c.id = o.customer_id AND c.status = 'ACTIVE';"
    )
    assert ("value", "staging.customers.status", "<result>.full_name", "conditional") in edges
    assert not any(e[0] == "filter" and e[1] == "staging.customers.status" for e in edges)
    assert not any(e[1] == "staging.customers.status" and e[2] == "<result>.id" for e in edges)


def test_inside_an_exists_an_inner_on_condition_filters() -> None:
    edges = _edges(
        "SELECT c.id FROM staging.customers AS c WHERE EXISTS (SELECT 1 FROM staging.orders AS o "
        "JOIN staging.payments AS p ON p.order_id = o.id AND p.status = 'PAID' "
        "WHERE o.customer_id = c.id);"
    )
    assert ("filter", "staging.payments.status", "<result>", "identity") in edges
    assert ("filter", "staging.payments.order_id", "<result>", "identity") not in edges


def test_two_aliases_differing_only_by_quoting_are_two_relations() -> None:
    """B9 T4's trap: `o` and `"O"` - the equality between them is still the join key."""
    edges = _edges(
        'SELECT "O".id FROM staging.orders AS "O" '
        'JOIN staging.payments AS o ON o.order_id = "O".id;'
    )
    assert not any(e[0] == "filter" for e in edges)


# --- D-49 -----------------------------------------------------------------------------------


def test_a_range_join_is_a_filter_on_both_operands() -> None:
    edges = _edges(
        "SELECT f.order_line_id, c.segment FROM core.fct_order_line AS f "
        "JOIN core.dim_customer AS c ON c.customer_key = f.customer_key "
        "AND f.order_date >= c.valid_from AND f.order_date < c.valid_to;"
    )
    for column in (
        "core.fct_order_line.order_date",
        "core.dim_customer.valid_from",
        "core.dim_customer.valid_to",
    ):
        assert ("filter", column, "<result>", "identity") in edges, column
    assert ("filter", "core.dim_customer.customer_key", "<result>", "identity") not in edges


def test_the_same_inequality_in_where_gives_the_same_filters() -> None:
    on = _edges(
        "SELECT a.id FROM staging.customers AS a JOIN staging.customers AS b "
        "ON b.country_code = a.country_code AND b.signup_ts < a.signup_ts;"
    )
    where = _edges(
        "SELECT a.id FROM staging.customers AS a JOIN staging.customers AS b "
        "ON b.country_code = a.country_code WHERE b.signup_ts < a.signup_ts;"
    )
    assert {e for e in on if e[0] == "filter"} == {e for e in where if e[0] == "filter"}


# --- D-50 -----------------------------------------------------------------------------------


def test_grouping_carries_no_lineage() -> None:
    edges = _edges(
        "SELECT CASE grouping(s.region, s.country_code) WHEN 0 THEN 'country' ELSE 'up' END "
        "AS level, s.region, count(*) AS n FROM core.dim_store AS s "
        "GROUP BY ROLLUP (s.region, s.country_code);"
    )
    assert not any(e[2] == "<result>.level" for e in edges)


# --- D-51 -----------------------------------------------------------------------------------


def test_one_source_by_several_paths_is_one_fact_at_the_hardest() -> None:
    edges = _edges(
        "WITH t AS (SELECT o.customer_id, sum(o.amount) AS total FROM staging.orders AS o "
        "GROUP BY o.customer_id)\n"
        "SELECT jsonb_build_object('a', o.amount, 'c', CASE WHEN o.amount > 0 THEN 1 END, "
        "'t', t.total) AS doc FROM staging.orders AS o JOIN t ON t.customer_id = o.customer_id;"
    )
    amount = {e[3] for e in edges if e[1] == "staging.orders.amount" and e[0] == "value"}
    assert amount == {"aggregated"}
