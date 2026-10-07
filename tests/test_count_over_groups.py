"""Finding FM-03 / decision D-55: an inner GROUP BY influences a COUNT(*) over its groups.

`SELECT COUNT(*) FROM islands` counts islands, and `islands`' GROUP BY decides how many there
are. `MAX(i.days)` over the same CTE carried the grouping through `days`; a column-less
`COUNT(*)` reads no column, so the walk never left its own scope and the inner grouping was
lost (final suite M3, `island_count`).

The first cut of the fix emitted 44 false positives across the final suite, each pinned
below as a negative: a window function is not such an aggregate, a COUNT(*) inside a scalar
subquery counts its own rows, and a CTE that is merely in view is not a source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"

GROUPED = (
    "g AS (SELECT o.customer_id, o.status FROM staging.orders AS o "
    "GROUP BY o.customer_id, o.status)"
)
GROUPING = {"staging.orders.customer_id", "staging.orders.status"}


def _influence(sql: str, target: str) -> set[str]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {
        str(e.source).split(":", 1)[1]
        for e in result.edges
        if e.flow.value == "influence" and str(e.target) == f"column:<result>.{target}"
    }


@pytest.mark.parametrize("count", ["COUNT(*)", "COUNT(1)"])
def test_a_count_over_a_grouped_cte_inherits_its_grouping(count: str) -> None:
    sql = f"WITH {GROUPED} SELECT {count} AS n FROM g AS x"
    assert _influence(sql, "n") == GROUPING


def test_a_pass_through_source_is_looked_through() -> None:
    sql = (
        f"WITH {GROUPED}, f AS (SELECT g.customer_id FROM g WHERE g.status <> 'x') "
        "SELECT COUNT(*) AS n FROM f"
    )
    assert _influence(sql, "n") == GROUPING


def test_the_count_matches_what_max_over_the_same_cte_carries() -> None:
    sql = f"WITH {GROUPED} SELECT COUNT(*) AS n, MAX(x.customer_id) AS m FROM g AS x"
    assert _influence(sql, "n") == GROUPING


def test_a_window_function_is_not_a_count_over_groups() -> None:
    sql = f"WITH {GROUPED} SELECT x.customer_id, COUNT(*) OVER () AS n FROM g AS x"
    assert _influence(sql, "n") == set()


def test_a_count_in_a_scalar_subquery_counts_its_own_rows() -> None:
    sql = (
        f"WITH {GROUPED} SELECT x.customer_id, "
        "(SELECT COUNT(*) FROM staging.payments AS p WHERE p.order_id = x.customer_id) AS n "
        "FROM g AS x"
    )
    assert _influence(sql, "n") == set()


def test_a_cte_merely_in_view_is_not_counted() -> None:
    sql = (
        f"WITH {GROUPED}, h AS (SELECT COUNT(*) AS n FROM staging.payments AS p) SELECT h.n FROM h"
    )
    assert _influence(sql, "n") == set()
