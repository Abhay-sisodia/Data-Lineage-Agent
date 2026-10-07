"""Finding FM-02 / decision D-54: CYCLE's implicit guard is a filter on the cycle columns.

`CYCLE id SET is_cycle USING path` stops the recursive term from extending a row whose `id`
is already on its path, so `id` decides which rows the CTE holds - a filter. The restatement
added the mark and path columns and nothing that reads like the guard, so the filter was
missing (final suite M3). Each recursive arm now carries a predicate on the cycle columns as
that arm computes them, ANDed into its WHERE; the anchor is never cut.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.parsing.pg_grammar import restate
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"

TREE = (
    "WITH RECURSIVE tree (id, name) AS (\n"
    "    SELECT c.id, c.name FROM core.dim_category AS c WHERE c.parent_id IS NULL\n"
    "    UNION ALL\n"
    "    SELECT ch.id, ch.name FROM core.dim_category AS ch\n"
    "    JOIN tree AS t ON ch.parent_id = t.id\n"
    "    {where}\n"
    "){cycle}\n"
    "SELECT x.id, x.name FROM tree AS x"
)
CYCLE = " CYCLE id SET is_cycle USING path"
GUARD = ("filter", "column:core.dim_category.id", "relation:<result>", "identity")


def _facts(sql: str) -> set[tuple[str, str, str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {(e.flow.value, str(e.source), str(e.target), e.transform.value) for e in result.edges}


@pytest.mark.parametrize(
    "where",
    ["", "WHERE ch.name <> 'x'", "WHERE ch.name <> 'x' OR ch.department = 'y'"],
    ids=["no WHERE", "WHERE", "WHERE with OR"],
)
def test_the_cycle_guard_adds_exactly_a_filter_on_the_cycle_column(where: str) -> None:
    plain = _facts(TREE.format(where=where, cycle=""))
    cycled = _facts(TREE.format(where=where, cycle=CYCLE))
    assert GUARD not in plain
    assert cycled - plain == {GUARD}
    assert plain <= cycled


def test_the_guard_restatement_keeps_lines_and_leaves_the_anchor_alone() -> None:
    source = TREE.format(where="WHERE ch.name <> 'x'", cycle=CYCLE)
    restated = restate(source)
    assert restated.count("\n") == source.count("\n")
    anchor, recursive = restated.split("UNION ALL")
    assert "IS NOT NULL" not in anchor
    assert "ROW(ch.id) IS NOT NULL" in recursive
