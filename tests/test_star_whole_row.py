"""Finding F0-02: `a.*` used as a value is the whole row `a`, as D-20 already reads `a`.

`row_to_json(o.*)`, `(o.*)::text`, `md5((o.*)::text)`, `ROW(o.*)` - the hash and audit
columns of real loads - were declared `unresolved_identifier` (a column named `*`) with no
edge, while the same calls over `o` were traced. `a.*` in an expression now becomes the
whole-row reference before `qualify`; a select-list or RETURNING `a.*` still expands.
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


@pytest.mark.parametrize(
    ("starred", "plain"),
    [
        ("row_to_json(f.*)", "row_to_json(f)"),
        ("to_jsonb(f.*)", "to_jsonb(f)"),
        ("(f.*)::text", "f::text"),
        ("md5((f.*)::text)", "md5(f::text)"),
        ("ROW(f.*)", "ROW(f)"),
    ],
)
def test_a_starred_row_reads_as_the_row(starred: str, plain: str) -> None:
    query = "SELECT {} AS j FROM staging.fx_rates AS f"
    expected = _facts(query.format(plain))
    assert len(expected) == 3
    assert _facts(query.format(starred)) == expected


def test_a_starred_derived_table_row_reads_its_projections() -> None:
    sql = "SELECT md5((x.*)::text) AS h FROM (SELECT o.id, o.amount FROM staging.orders AS o) AS x"
    assert {source for _, source, _, _ in _facts(sql)} == {
        "column:staging.orders.id",
        "column:staging.orders.amount",
    }


def test_the_star_is_the_row_even_when_a_column_shares_the_alias() -> None:
    """`balance.*` is always the row; bare `balance` is the column when one exists."""
    sql = "SELECT row_to_json({}) AS j FROM core.ledger AS balance"
    assert len(_facts(sql.format("balance.*"))) == 4
    assert {source for _, source, _, _ in _facts(sql.format("balance"))} == {
        "column:core.ledger.balance"
    }


def test_a_select_list_star_still_expands() -> None:
    facts = _facts("SELECT f.* FROM staging.fx_rates AS f")
    assert (
        "value",
        "column:staging.fx_rates.currency",
        "column:<result>.currency",
        "identity",
    ) in facts
