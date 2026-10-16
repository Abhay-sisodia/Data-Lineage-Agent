"""Finding FH-05: a bare `EXCEPT` or `INTERSECT` at script level vanished.

The script front end named a parsed `Union` a `query_block` but not an `Except` or an
`Intersect`, so band 0 skipped those statements BEFORE counting them: statements seen 0, no
refusal, no boundary, no lineage. The same query under `INSERT ... SELECT` was analysed
in full. Every set operation is now a query block, and a bare one gives the lineage its
`INSERT` form does, onto `<result>`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"

QUERY = (
    "SELECT o.customer_id FROM staging.orders AS o WHERE o.status = 'x' "
    "{op} SELECT p.order_id FROM staging.payments AS p WHERE p.amount > 0"
)


def _facts(sql: str, target: str) -> set[tuple[str, str, str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.statements_seen == 1
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {
        (e.flow.value, str(e.source), str(e.target).replace(target, "<out>"), e.transform.value)
        for e in result.edges
    }


@pytest.mark.parametrize("op", ["UNION", "UNION ALL", "EXCEPT", "EXCEPT ALL", "INTERSECT"])
def test_a_bare_set_operation_gives_its_insert_forms_lineage(op: str) -> None:
    bare = _facts(QUERY.format(op=op), "<result>")
    inserted = _facts(f"INSERT INTO mart.t_x (customer_id) {QUERY.format(op=op)}", "t_x")
    assert bare, "a set operation at script level must not vanish"
    assert bare == inserted


@pytest.mark.parametrize("op", ["EXCEPT", "INTERSECT"])
def test_a_constraining_arm_filters_and_supplies_no_value(op: str) -> None:
    facts = _facts(QUERY.format(op=op), "<result>")
    assert (
        "value",
        "column:staging.payments.order_id",
        "column:<out>.customer_id",
        "identity",
    ) not in facts
    assert ("filter", "column:staging.payments.order_id", "relation:<out>", "identity") in facts
