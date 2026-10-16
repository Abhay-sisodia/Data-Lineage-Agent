"""Decision D-6: a filter's phase is decided by what it does, not by its keyword.

D-2 created `FilterPhase` to express WHEN a filter acts, and its argument is about
consequences: `WHERE quantity > 0` discards line items before they are summed,
`HAVING SUM(quantity) > 0` discards products whose total came to zero. Same column, same
flow, different results.

A `WHERE` over a value an inner query already aggregated has the SECOND behaviour and the
FIRST keyword. Classifying by keyword put two filters with identical consequences on
opposite sides of the distinction D-2 exists to draw - so D-6 classifies by the path
instead: **a predicate acts post-aggregation when the column it constrains reaches it
through an aggregate.**

Dialect-independent, and tested in both. The decision was found by a PostgreSQL package and
changed three Oracle edges in `stress_04_depth_at_scale`, which is the whole reason a
convention has to reach every key at once (S2-11).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.band0 import analyse_source as band0_analyse
from lineage.config import AnalysisConfig
from lineage.ir.model import FilterPhase, Flow
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def postgres() -> Dictionary:
    return Dictionary.load(ROOT / "stress" / "dictionary_postgres.json")


@pytest.fixture(scope="module")
def oracle() -> Dictionary:
    return Dictionary.load(ROOT / "corpus" / "dictionary.json")


def phases_for(sql: str, book: Dictionary, source_column: str) -> set[str]:
    config = AnalysisConfig.load().model_copy(update={"dialect": book.dialect})
    result = band0_analyse(sql, book, config)
    return {
        edge.phase.value
        for edge in result.edges
        if edge.flow == Flow.FILTER and str(edge.source).endswith(source_column)
    }


def test_a_where_over_an_aggregated_cte_acts_post_aggregation(postgres: Dictionary) -> None:
    """The case D-6 was decided on, from `stress_pg_04_query_complexity`.

    `buyers` is COUNT(DISTINCT ...) computed in the CTE. The outer WHERE cannot run until
    that count exists, so it removes rows AFTER aggregation - the defining property of
    post-aggregation, and the thing the keyword cannot see.
    """
    sql = """
        INSERT INTO gtt_stage (cust_id, amount)
        WITH agg AS (
            SELECT o.cust_id AS cust_id,
                   SUM(o.gross_amount) AS total,
                   COUNT(DISTINCT o.order_id) AS buyers
              FROM stg_orders o
             GROUP BY o.cust_id)
        SELECT a.cust_id, a.total FROM agg a WHERE a.buyers >= 3;
    """
    assert phases_for(sql, postgres, ".order_id") == {FilterPhase.POST_AGGREGATION.value}


def test_a_where_on_a_raw_column_still_acts_pre_aggregation(postgres: Dictionary) -> None:
    """The other half, and the one that makes the test above mean something.

    Without this, a fix that marked EVERY filter post-aggregation would pass the first
    assertion and destroy the distinction D-2 built. The two have to be checked together.
    """
    sql = """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, o.gross_amount FROM stg_orders o WHERE o.currency = 'GBP';
    """
    assert phases_for(sql, postgres, ".currency") == {FilterPhase.PRE_AGGREGATION.value}


def test_both_phases_can_appear_in_one_statement(postgres: Dictionary) -> None:
    """A raw predicate and an aggregated one in the same query keep different phases.

    This is what D-6 preserves and a blunt rule would flatten: the phase is a property of
    how each COLUMN reaches its filter, not of the statement.
    """
    sql = """
        INSERT INTO gtt_stage (cust_id, amount)
        WITH agg AS (
            SELECT o.cust_id AS cust_id, SUM(o.gross_amount) AS total
              FROM stg_orders o
             WHERE o.currency = 'GBP'
             GROUP BY o.cust_id)
        SELECT a.cust_id, a.total FROM agg a WHERE a.total > 100;
    """
    assert phases_for(sql, postgres, ".currency") == {FilterPhase.PRE_AGGREGATION.value}
    assert phases_for(sql, postgres, ".gross_amount") == {FilterPhase.POST_AGGREGATION.value}


def test_having_is_unchanged(postgres: Dictionary) -> None:
    """D-2's original case must still hold. D-6 widens the rule; it does not replace it."""
    sql = """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, SUM(o.gross_amount) FROM stg_orders o
         GROUP BY o.cust_id HAVING SUM(o.gross_amount) > 0;
    """
    assert phases_for(sql, postgres, ".gross_amount") == {FilterPhase.POST_AGGREGATION.value}


def test_the_rule_is_dialect_independent(oracle: Dictionary) -> None:
    """The same construct in Oracle, where D-6 moved three edges in `stress_04`.

    `WHERE g.revenue_total > 0` over a derived table that aggregates the column. Oracle is
    where the decision has to be checked hardest: it was found in PostgreSQL, and a phase
    rule that fired in only one dialect would make the two benchmarks answer differently
    about the same SQL.

    Wrapped in a procedure because the Oracle frontend parses a PROGRAM: a bare statement
    produces nothing at all, which is a property of the frontend and not of this rule.
    """
    sql = """
        CREATE OR REPLACE PROCEDURE p_phase_check IS
        BEGIN
            INSERT INTO fct_revenue_stage (cust_id, net_amount)
            SELECT g.cust_id, g.revenue_total
              FROM (SELECT r.cust_id AS cust_id, SUM(r.net_amount) AS revenue_total
                      FROM fct_revenue r
                     GROUP BY r.cust_id) g
             WHERE g.revenue_total > 0;
        END p_phase_check;
        /
    """
    phases = phases_for(sql, oracle, ".NET_AMOUNT")
    assert phases, "no filter edge at all - the assertion below would pass vacuously"
    assert phases == {FilterPhase.POST_AGGREGATION.value}
