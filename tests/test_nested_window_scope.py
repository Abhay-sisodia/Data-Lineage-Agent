"""Finding FH-06: a window inside a scalar subquery's derived table was traced in the wrong scope.

`_window_influence_columns` finds windows with `find_all`, which crosses into nested queries,
so `(SELECT MAX(r.amount) FROM (SELECT ..., ROW_NUMBER() OVER (PARTITION BY p.order_id ...))
AS r ...)` had `p.order_id` traced against the OUTER query, where `p` does not exist, and
declared `unresolved_identifier` - beside edges that were right. Only the query's own
windows are read at its level now; nested ones are reached through their own scopes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"

RANKED = (
    "(SELECT p.order_id, p.amount, ROW_NUMBER() OVER "
    "(PARTITION BY p.order_id ORDER BY p.paid_at DESC) AS rn FROM staging.payments AS p)"
)
RANK_SOURCES = {"staging.payments.order_id", "staging.payments.paid_at"}


def _influence(sql: str) -> set[str]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {str(e.source).split(":", 1)[1] for e in result.edges if e.flow.value == "influence"}


@pytest.mark.parametrize(
    ("inner", "expected"),
    [
        ("SELECT MAX(r.amount) FROM {R} AS r WHERE r.order_id = o.id", set()),
        ("SELECT MAX(r.amount) FROM {R} AS r WHERE r.order_id = o.id AND r.rn = 1", RANK_SOURCES),
        ("SELECT MAX(r.rn) FROM {R} AS r WHERE r.order_id = o.id", RANK_SOURCES),
        (
            "SELECT MAX(r.amount) FROM staging.orders AS x JOIN {R} AS r "
            "ON r.order_id = x.id AND r.rn = 1 WHERE x.customer_id = o.customer_id",
            RANK_SOURCES,
        ),
    ],
    ids=["no cut", "WHERE cut", "reads the rank", "ON cut (FH-03)"],
)
def test_a_window_in_a_scalar_subquery_s_derived_table_declares_nothing(
    inner: str, expected: set[str]
) -> None:
    sql = f"SELECT o.id, ({inner.format(R=RANKED)}) AS v FROM staging.orders AS o"
    assert _influence(sql) == expected


def test_a_scalar_subquery_s_own_window_still_reads_the_outer_row() -> None:
    sql = (
        "SELECT o.id, (SELECT SUM(p.amount) OVER (PARTITION BY o.customer_id ORDER BY p.paid_at) "
        "FROM staging.payments AS p WHERE p.order_id = o.id LIMIT 1) AS v FROM staging.orders AS o"
    )
    assert _influence(sql) == {"staging.orders.customer_id", "staging.payments.paid_at"}
