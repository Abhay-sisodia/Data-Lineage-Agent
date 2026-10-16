"""D-52 (2026-10-07): a semi- or anti-join's match key is a FILTER, both columns.

`WHERE o.customer_id IN (SELECT v.id ...)`, `EXISTS (... WHERE v.id = o.customer_id)` and
their negations remove outer rows, so both match columns decide which rows land. A JOIN +
DISTINCT spelling of the same rows gives no key edge, because D-5 calls a join key
plumbing: B2's P5 and P6 differ BY DESIGN, and that difference is pinned here too, so a
change to either rule shows up as a decision rather than a drift.

Inside a scope that cannot remove a result row (D-45) the same keys are values instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _edges(sql: str) -> set[tuple[str, str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [], result.refusals
    assert result.boundaries == [], result.boundaries
    return {
        (e.flow.value, str(e.source).split(":", 1)[1], str(e.target).split(":", 1)[1])
        for e in result.edges
    }


KEYS = {
    ("filter", "staging.orders.customer_id", "<result>"),
    ("filter", "staging.customers.id", "<result>"),
}

SPELLINGS = {
    "IN": "o.customer_id IN (SELECT c.id FROM staging.customers AS c)",
    "= ANY": "o.customer_id = ANY (SELECT c.id FROM staging.customers AS c)",
    "EXISTS": "EXISTS (SELECT 1 FROM staging.customers AS c WHERE c.id = o.customer_id)",
    "NOT IN": "o.customer_id NOT IN (SELECT c.id FROM staging.customers AS c)",
    "<> ALL": "o.customer_id <> ALL (SELECT c.id FROM staging.customers AS c)",
    "NOT EXISTS": "NOT EXISTS (SELECT 1 FROM staging.customers AS c WHERE c.id = o.customer_id)",
}


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_every_spelling_makes_both_match_columns_filters(spelling: str) -> None:
    edges = _edges(f"SELECT o.id FROM staging.orders AS o WHERE {SPELLINGS[spelling]};")
    assert edges >= KEYS, spelling
    assert edges - KEYS == {("value", "staging.orders.id", "<result>.id")}


def test_a_predicate_inside_the_subquery_is_a_filter_beside_the_key() -> None:
    edges = _edges(
        "SELECT o.id FROM staging.orders AS o WHERE EXISTS (SELECT 1 FROM staging.customers "
        "AS c WHERE c.id = o.customer_id AND c.segment = 'VIP');"
    )
    assert edges >= KEYS | {("filter", "staging.customers.segment", "<result>")}


def test_in_a_cte_on_the_main_path_it_is_still_a_filter() -> None:
    edges = _edges(
        "WITH vip_orders AS (SELECT o.id FROM staging.orders AS o "
        "WHERE o.customer_id IN (SELECT c.id FROM staging.customers AS c))\n"
        "SELECT v.id FROM vip_orders AS v;"
    )
    assert edges >= KEYS


def test_under_a_null_supplying_side_the_keys_are_values() -> None:
    """D-45: the LEFT JOIN keeps every order, so the semi-join decides a value, not a row."""
    edges = _edges(
        "WITH vip_orders AS (SELECT o.id, o.amount FROM staging.orders AS o "
        "WHERE o.customer_id IN (SELECT c.id FROM staging.customers AS c))\n"
        "SELECT p.id, v.amount FROM staging.payments AS p "
        "LEFT JOIN vip_orders AS v ON v.id = p.order_id;"
    )
    assert ("value", "staging.customers.id", "<result>.amount") in edges
    assert not any(e[0] == "filter" and e[1] == "staging.customers.id" for e in edges)


def test_the_join_spelling_differs_by_design() -> None:
    """P5: the same rows through JOIN + DISTINCT - the key is plumbing (D-5), no filter."""
    edges = _edges(
        "SELECT DISTINCT o.id FROM staging.orders AS o "
        "JOIN staging.customers AS c ON c.id = o.customer_id;"
    )
    assert not any(e[0] == "filter" for e in edges)
