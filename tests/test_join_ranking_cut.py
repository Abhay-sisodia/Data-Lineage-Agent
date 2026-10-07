"""Finding FH-03: a ranking cut in an INNER JOIN's ON was not a filter.

`JOIN r ON r.k = o.id AND r.rn = 1` over `ROW_NUMBER() OVER (PARTITION BY .. ORDER BY ..)`
keeps the first row per partition exactly as `WHERE r.rn = 1` does - D-48 makes an inner
join's non-key ON condition a filter, and D-9 makes a cut on a rank a filter on what
partitions and orders it. In WHERE both edges came out; in ON neither did, in a SELECT or
an UPDATE ... FROM (final suite H2). Below a value-only scope the same cut is influence, as
its WHERE form already was.
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
CUT_SOURCES = {"staging.payments.order_id", "staging.payments.paid_at"}


def _edges(sql: str, flow: str) -> set[tuple[str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {
        (str(e.source).split(":", 1)[1], str(e.target))
        for e in result.edges
        if e.flow.value == flow
    }


def _sources(edges: set[tuple[str, str]]) -> set[str]:
    return {source for source, _ in edges}


@pytest.mark.parametrize(
    "join",
    [
        f"JOIN {RANKED} AS r ON r.order_id = o.id AND r.rn = 1",
        f"JOIN {RANKED} AS r ON r.order_id = o.id WHERE r.rn = 1",
    ],
    ids=["ON", "WHERE"],
)
def test_a_ranking_cut_filters_on_its_window_in_on_as_in_where(join: str) -> None:
    filters = _edges(f"SELECT o.id, r.amount FROM staging.orders AS o {join}", "filter")
    assert _sources(filters) >= CUT_SOURCES


def test_a_ranking_cut_in_an_update_from_join_filters_the_target() -> None:
    sql = (
        f"WITH r AS {RANKED},\n"
        "pr AS (SELECT o.id, r.amount FROM staging.orders AS o\n"
        "       JOIN r ON r.order_id = o.id AND r.rn = 1)\n"
        "UPDATE core.ledger AS l SET balance = pr.amount FROM pr WHERE pr.id = l.id"
    )
    filters = _edges(sql, "filter")
    assert {(source, "relation:core.ledger") for source in CUT_SOURCES} <= filters


def test_below_a_value_only_scope_the_cut_is_influence() -> None:
    """D-14: inside a null-supplying LATERAL the cut decides which value, not which row."""
    sql = (
        "SELECT o.id, l.amount FROM staging.orders AS o LEFT JOIN LATERAL ("
        "SELECT r.amount FROM staging.orders AS x "
        f"JOIN {RANKED} AS r ON r.order_id = x.id AND r.rn = 1 "
        "WHERE x.id = o.id) AS l ON TRUE"
    )
    assert _sources(_edges(sql, "influence")) >= CUT_SOURCES
    assert not CUT_SOURCES & _sources(_edges(sql, "filter"))
