"""Finding FE-01 / decision D-56: a row limit over an aggregated query is post for every key.

`ORDER BY spend_year LIMIT n` over a GROUP BY counts and removes GROUPS - no filter on the raw
rows reproduces it, as for HAVING. It read pre-aggregation when the grouping key was the
only sort key (and post beside an aggregate key), because the phase came from the sort
columns' own paths. The phase now comes from whether the query groups; a WHERE stays per
column (D-6), and a query whose only aggregate is a window keeps its rows and stays pre.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _filter_phases(sql: str) -> dict[str, str]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {
        str(e.source).split(".")[-1]: e.phase.value
        for e in result.edges
        if e.flow.value == "filter"
    }


GROUPED = "SELECT o.customer_id, SUM(o.amount) AS t FROM staging.orders AS o"


@pytest.mark.parametrize(
    "sql",
    [
        f"{GROUPED} GROUP BY o.customer_id ORDER BY o.customer_id LIMIT 5",
        f"{GROUPED} GROUP BY o.customer_id ORDER BY 1 FETCH FIRST 5 ROWS ONLY",
        f"{GROUPED} GROUP BY o.customer_id ORDER BY o.customer_id OFFSET 5",
        "SELECT DISTINCT ON (o.customer_id) o.customer_id, COUNT(*) AS n "
        "FROM staging.orders AS o GROUP BY o.customer_id, o.status ORDER BY o.customer_id",
    ],
    ids=["LIMIT", "FETCH", "OFFSET", "DISTINCT ON"],
)
def test_a_grouping_key_alone_under_a_row_limit_is_post(sql: str) -> None:
    assert _filter_phases(sql)["customer_id"] == "post-aggregation"


def test_beside_an_aggregate_key_it_was_already_post() -> None:
    sql = f"{GROUPED} GROUP BY o.customer_id ORDER BY t DESC, o.customer_id LIMIT 5"
    assert _filter_phases(sql) == {"amount": "post-aggregation", "customer_id": "post-aggregation"}


def test_a_where_stays_per_column() -> None:
    sql = f"{GROUPED} WHERE o.status = 'x' GROUP BY o.customer_id ORDER BY 1 LIMIT 5"
    assert _filter_phases(sql) == {"status": "pre-aggregation", "customer_id": "post-aggregation"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT o.customer_id FROM staging.orders AS o ORDER BY o.customer_id LIMIT 5",
        "SELECT o.customer_id, SUM(o.amount) OVER () AS t FROM staging.orders AS o "
        "ORDER BY o.customer_id LIMIT 5",
    ],
    ids=["no aggregate", "window aggregate only"],
)
def test_a_query_that_keeps_its_rows_stays_pre(sql: str) -> None:
    assert _filter_phases(sql) == {"customer_id": "pre-aggregation"}
