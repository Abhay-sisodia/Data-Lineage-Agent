"""Finding FM-01: an ordinal used as a DISTINCT ON or LIMIT ordering was dropped.

`WITH ORDINALITY`'s column has no value source (D-26) - it numbers the array's elements - so
the row-choice path (D-9), which only TRACED its ordering columns, found nothing for
`ORDER BY u.n LIMIT 5`: no filter on the main path, no influence on a null side. A WHERE cut
on the same ordinal reached the array through the window walk and was right. The ordering
path now takes the same step (final suite M4, `ship_to_email`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"

NUMBERED = (
    "(SELECT o.id, u.tag, u.n FROM staging.orders AS o "
    "CROSS JOIN LATERAL unnest(o.tags) WITH ORDINALITY AS u (tag, n))"
)
ARRAY = "staging.orders.tags"


def _sources(sql: str, flow: str) -> set[str]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {str(e.source).split(":", 1)[1] for e in result.edges if e.flow.value == flow}


@pytest.mark.parametrize(
    "shape",
    [
        "SELECT x.id, x.tag FROM {q} AS x WHERE x.n = 1",
        "SELECT DISTINCT ON (x.id) x.id, x.tag FROM {q} AS x ORDER BY x.id, x.n",
        "SELECT x.id, x.tag FROM {q} AS x ORDER BY x.n LIMIT 5",
    ],
    ids=["WHERE", "DISTINCT ON", "LIMIT"],
)
def test_an_ordinal_cut_filters_on_its_array_however_written(shape: str) -> None:
    assert ARRAY in _sources(shape.format(q=NUMBERED), "filter")


@pytest.mark.parametrize(
    "inner",
    [
        "SELECT x.id, x.tag FROM {q} AS x WHERE x.n = 1",
        "SELECT DISTINCT ON (x.id) x.id, x.tag FROM {q} AS x ORDER BY x.id, x.n",
    ],
    ids=["WHERE", "DISTINCT ON"],
)
def test_on_a_null_side_the_ordinal_is_influence(inner: str) -> None:
    sql = (
        f"SELECT c.id, f.tag FROM staging.customers AS c "
        f"LEFT JOIN ({inner.format(q=NUMBERED)}) AS f ON f.id = c.id"
    )
    assert ARRAY in _sources(sql, "influence")
    assert ARRAY not in _sources(sql, "filter")


def test_an_unlimited_order_by_still_chooses_nothing() -> None:
    """D-9: without a row limit an ORDER BY removes no row, ordinal or not."""
    sql = f"SELECT x.id, x.tag FROM {NUMBERED} AS x ORDER BY x.n"
    assert ARRAY not in _sources(sql, "filter")
