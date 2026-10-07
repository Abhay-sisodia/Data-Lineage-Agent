"""Finding F0-05: `SYSTEM_USER` and a schema-qualified collation were declared unresolved.

`SYSTEM_USER` (PostgreSQL 16) is a session value like `CURRENT_USER`; it was read as a
column. `x COLLATE pg_catalog."default"` parsed with the collation as a column
`pg_catalog.default`. Both were declared `unresolved_identifier` beside correct edges. The
quoted `"system_user"` stays a column, as PostgreSQL has it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _facts(sql: str) -> set[tuple[str, str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {(e.flow.value, str(e.source), str(e.target)) for e in result.edges}


def test_system_user_is_a_session_value() -> None:
    assert _facts("SELECT o.id, SYSTEM_USER AS u FROM staging.orders AS o") == {
        ("value", "column:staging.orders.id", "column:<result>.id")
    }


@pytest.mark.parametrize("collation", ['pg_catalog."default"', '"default"', 'pg_catalog."C"'])
def test_a_collation_is_a_name_not_a_column(collation: str) -> None:
    facts = _facts(f"SELECT o.status COLLATE {collation} AS s FROM staging.orders AS o")
    assert facts == {("value", "column:staging.orders.status", "column:<result>.s")}


def test_a_quoted_system_user_is_still_a_column() -> None:
    sql = 'SELECT f."system_user" FROM (SELECT o.id AS system_user FROM staging.orders AS o) AS f'
    assert ("value", "column:staging.orders.id", "column:<result>.system_user") in _facts(sql)
