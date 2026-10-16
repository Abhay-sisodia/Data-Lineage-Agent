"""`WITH ... INSERT` is the same statement as `INSERT ... WITH ... SELECT` (finding B1-01).

PostgreSQL's usual spelling of a CTE-fed write puts the WITH first. SQLGlot attaches it to
the `Insert` node, the analysis read only the query, and every CTE reference reached
`qualify` as a bare table: no edges, the CTE declared "relation not in dictionary". 84 of
batch B1's 115 misses. The tests are metamorphic - both spellings must agree - plus the
assertion that they agree on something rather than on nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _facts(sql: str) -> set[tuple[str, ...]]:
    return {
        (str(e.source), str(e.target), e.flow.value, e.transform.value, e.phase.value)
        for e in _result(sql).edges
    }


CTES = """c AS (
    SELECT s.region AS r, SUM(f.net_amount) AS rev
    FROM core.fct_order_line AS f JOIN core.dim_store AS s ON s.id = f.store_id
    WHERE f.status = 'COMPLETE'
    GROUP BY s.region
)"""

PAIRS = {
    "plain": (
        f"WITH {CTES} INSERT INTO mart.kpi_rollup (region, revenue) SELECT c.r, c.rev FROM c",
        f"INSERT INTO mart.kpi_rollup (region, revenue) WITH {CTES} SELECT c.r, c.rev FROM c",
    ),
    "set operation": (
        f"WITH {CTES} INSERT INTO mart.kpi_rollup (region, revenue) "
        "SELECT c.r, c.rev FROM c UNION ALL SELECT w.region, 0 FROM core.dim_warehouse AS w",
        f"INSERT INTO mart.kpi_rollup (region, revenue) WITH {CTES} "
        "SELECT c.r, c.rev FROM c UNION ALL SELECT w.region, 0 FROM core.dim_warehouse AS w",
    ),
}


@pytest.mark.parametrize("name", sorted(PAIRS))
def test_both_spellings_give_the_same_lineage(name: str) -> None:
    before, after = PAIRS[name]
    assert _facts(before) == _facts(after)


def test_the_with_first_spelling_actually_produces_the_lineage() -> None:
    """Stated separately: equality also holds if both spellings lose everything."""
    facts = _facts(PAIRS["plain"][0])
    assert ("column:core.dim_store.region", "column:mart.kpi_rollup.region", "value", "identity",
            "pre-aggregation") in facts
    assert ("column:core.fct_order_line.net_amount", "column:mart.kpi_rollup.revenue", "value",
            "aggregated", "pre-aggregation") in facts
    assert ("column:core.fct_order_line.status", "relation:mart.kpi_rollup", "filter", "identity",
            "pre-aggregation") in facts
    assert not [b for b in _result(PAIRS["plain"][0]).boundaries
                if b.kind.value == "dangling_reference"]


def test_ctes_on_both_levels_are_all_in_scope() -> None:
    """`WITH c ... INSERT ... WITH d AS (SELECT ... FROM c) SELECT ... FROM d` - the inner
    CTE reads the outer one, so the outer must be in scope where the inner is analysed."""
    facts = _facts(
        f"WITH {CTES} INSERT INTO mart.kpi_rollup (region) "
        "WITH d AS (SELECT c.r AS q FROM c) SELECT d.q FROM d"
    )
    assert ("column:core.dim_store.region", "column:mart.kpi_rollup.region", "value", "identity",
            "pre-aggregation") in facts


def test_a_cte_name_is_not_a_search_path_dependency() -> None:
    """B1-06: a CTE reference has no schema and needs none - it resolves to the statement's
    own WITH. Every CTE query used to carry a context_dependent_binding boundary for it."""
    qualified = f"WITH {CTES} INSERT INTO mart.kpi_rollup (region) SELECT c.r FROM c"
    assert [b.kind.value for b in _result(qualified).boundaries] == []


def test_a_genuinely_unqualified_table_still_is() -> None:
    """The control: the boundary itself is right, it was only firing on the wrong names."""
    unqualified = (
        "WITH c AS (SELECT s.region AS r FROM dim_store AS s) "
        "INSERT INTO mart.kpi_rollup (region) SELECT c.r FROM c"
    )
    assert [b.kind.value for b in _result(unqualified).boundaries] == ["context_dependent_binding"]
