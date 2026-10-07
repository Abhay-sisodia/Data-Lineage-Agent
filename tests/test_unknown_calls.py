"""D-53 (2026-10-07, finding B4-05): a call whose body is not in the analysis.

Its arguments are derived values of the result, and the call is declared
`source_unavailable`, because that edge rests on a body nobody read. Before D-53 the top
level withheld the arguments (and in `x + f(y)` lost `y`), while the same call inside a CTE
or derived table claimed them with no declaration at all: one call, two answers, decided
by where it was written. A summarised call is unchanged - its arguments only select the
row the callee reads.
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


def _facts(sql: str) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    result = _result(sql)
    edges = {(str(e.source).split(":", 1)[1], e.transform.value) for e in result.edges}
    declared = {(b.kind.value, b.subject) for b in result.boundaries}
    return edges, declared


SPELLINGS = {
    "top level": "SELECT myschema.calc(o.amount) AS v FROM staging.orders AS o;",
    "in a CTE": (
        "WITH x AS (SELECT myschema.calc(o.amount) AS v FROM staging.orders AS o)\n"
        "SELECT x.v FROM x;"
    ),
    "in a derived table": (
        "SELECT x.v FROM (SELECT myschema.calc(o.amount) AS v FROM staging.orders AS o) AS x;"
    ),
}


@pytest.mark.parametrize("where", SPELLINGS)
def test_one_call_one_answer_wherever_it_is_written(where: str) -> None:
    assert _facts(SPELLINGS[where]) == (
        {("staging.orders.amount", "derived")},
        {("source_unavailable", "CALC")},
    )


def test_an_unknown_call_inside_an_expression_keeps_its_argument() -> None:
    edges, declared = _facts(
        "SELECT o.amount + myschema.calc(o.store_id) AS v FROM staging.orders AS o;"
    )
    assert edges == {("staging.orders.amount", "derived"), ("staging.orders.store_id", "derived")}
    assert declared == {("source_unavailable", "CALC")}


def test_a_built_in_is_never_declared() -> None:
    edges, declared = _facts(
        "SELECT jsonb_build_object('a', o.amount) AS v FROM staging.orders AS o;"
    )
    assert edges == {("staging.orders.amount", "derived")}
    assert declared == set()
