"""Finding FH-04: a set operation inside a CTE or derived table lost its arms' influence.

The grouping and window walkers descend into a source by NAME (`_projection_named`). For a
UNION that returned the first arm's projection paired with the UNION's own scope, which has
no GROUP BY and no window, so every arm's grouping and ordering influence was dropped - but
only once the union sat in a CTE or derived table; at the top level the arms are walked
directly (final suite H1, `cust_net_margin`). The arms are now bound by position, as values
already were (`_trace_through_set_operation`), and a constraining arm (EXCEPT / INTERSECT)
still supplies no influence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _influence(sql: str, target: str) -> set[str]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {
        str(e.source).split(":", 1)[1]
        for e in result.edges
        if e.flow.value == "influence" and str(e.target).endswith(f".{target}")
    }


ARMS = (
    "SELECT o.customer_id AS k, SUM(o.amount) AS total FROM staging.orders AS o "
    "GROUP BY o.customer_id "
    "UNION ALL "
    "SELECT p.order_id, SUM(p.amount) FROM staging.payments AS p GROUP BY p.order_id"
)
BOTH_GROUPINGS = {"staging.orders.customer_id", "staging.payments.order_id"}


@pytest.mark.parametrize(
    "sql",
    [
        ARMS,
        f"SELECT u.k, u.total FROM ({ARMS}) AS u",
        f"WITH u AS ({ARMS}) SELECT u.k, u.total FROM u",
    ],
    ids=["top level", "derived table", "CTE"],
)
def test_every_arms_grouping_reaches_the_aggregate_wherever_the_union_sits(sql: str) -> None:
    assert _influence(sql, "total") == BOTH_GROUPINGS


def test_every_arms_window_reaches_the_ranking_through_a_cte() -> None:
    sql = (
        "WITH u AS (\n"
        "    SELECT o.id,\n"
        "           ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.order_ts) AS rn\n"
        "    FROM staging.orders AS o\n"
        "    UNION ALL\n"
        "    SELECT p.id, ROW_NUMBER() OVER (PARTITION BY p.order_id ORDER BY p.paid_at)\n"
        "    FROM staging.payments AS p\n"
        ")\n"
        "SELECT u.id, u.rn FROM u"
    )
    assert _influence(sql, "rn") == {
        "staging.orders.customer_id",
        "staging.orders.order_ts",
        "staging.payments.order_id",
        "staging.payments.paid_at",
    }


def test_a_constraining_arm_supplies_no_influence() -> None:
    """Convention (a): an EXCEPT arm decides which rows survive and supplies no value."""
    sql = (
        "SELECT x.k, x.total FROM ("
        "SELECT o.customer_id AS k, SUM(o.amount) AS total FROM staging.orders AS o "
        "GROUP BY o.customer_id "
        "EXCEPT "
        "SELECT p.order_id, SUM(p.amount) FROM staging.payments AS p GROUP BY p.order_id"
        ") AS x"
    )
    assert _influence(sql, "total") == {"staging.orders.customer_id"}
