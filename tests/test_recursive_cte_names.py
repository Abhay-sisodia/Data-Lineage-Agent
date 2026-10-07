"""Finding FH-01: under WITH RECURSIVE, a CTE named like the base table it reads lost everything.

`WITH RECURSIVE item_master AS (SELECT ... FROM ONLY lgx.item_master)` - a dedup CTE named
after its table, the style of the shipment sample - was taken for a recursive
self-reference because only the NAME was compared. Every column read through it vanished
with nothing declared (final suite H1, 7 facts). A CTE name is never schema-qualified, so a
qualified table is never the self-reference; an unqualified one still is.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _facts(sql: str) -> set[tuple[str, str, str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {(e.flow.value, str(e.source), str(e.target), e.transform.value) for e in result.edges}


READ_THROUGH = (
    "{with_} {name} AS (SELECT c.id, c.name FROM ONLY core.dim_category AS c)\n"
    "SELECT p.product_key, k.name FROM core.dim_product AS p\n"
    "LEFT JOIN {name} AS k ON k.id = p.category_id"
)


@pytest.mark.parametrize(
    ("with_", "name"),
    [("WITH RECURSIVE", "dim_category"), ("WITH", "dim_category"), ("WITH RECURSIVE", "cats")],
)
def test_a_cte_named_like_its_table_reads_the_table(with_: str, name: str) -> None:
    facts = _facts(READ_THROUGH.format(with_=with_, name=name))
    assert ("value", "column:core.dim_category.name", "column:<result>.name", "identity") in facts


GENUINE = (
    "WITH RECURSIVE {name} AS (\n"
    "    SELECT c.id, c.name AS root FROM core.dim_category AS c WHERE c.parent_id IS NULL\n"
    "    UNION ALL\n"
    "    SELECT ch.id, t.root FROM core.dim_category AS ch\n"
    "    JOIN {name} AS t ON ch.parent_id = t.id\n"
    ")\n"
    "SELECT x.id, x.root FROM {name} AS x"
)


def test_an_unqualified_self_reference_is_still_the_recursion() -> None:
    """The control: named like the table or not, the recursive arm reads the CTE (B2-10)."""
    assert _facts(GENUINE.format(name="dim_category")) == _facts(GENUINE.format(name="tree"))
