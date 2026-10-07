"""Finding F0-04: CREATE INDEX / SEQUENCE / SCHEMA / TYPE were refused; they move no data.

They reached the CTAS path and were refused UNSUPPORTED_CONSTRUCT - three of them as "CREATE
TABLE without AS SELECT" - each costing a statement of parse coverage for DDL that writes no
row. They are now counted with no lineage, as a plain CREATE TABLE is (D-33). A partial
index's WHERE chooses which rows are indexed, not which rows exist, so it is no filter.
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


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE INDEX ix ON staging.orders (customer_id)",
        "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ix ON staging.orders "
        "USING btree (lower(status)) INCLUDE (amount) WHERE amount > 0",
        "CREATE SEQUENCE core.order_seq",
        "CREATE SCHEMA reporting",
        "CREATE TYPE core.mood AS ENUM ('ok', 'sad')",
    ],
    ids=["index", "partial expression index", "sequence", "schema", "type"],
)
def test_ddl_that_holds_no_rows_is_counted_with_no_lineage(ddl: str) -> None:
    result = _result(ddl)
    assert result.statements_seen == 1
    assert result.refusals == [] and result.boundaries == [] and result.edges == []


def test_the_statements_around_an_index_are_unaffected() -> None:
    write = "INSERT INTO core.ledger (id, balance) SELECT o.id, o.amount FROM staging.orders AS o"
    alone = _result(write)
    with_index = _result(f"CREATE INDEX ix ON staging.orders (customer_id);\n{write}")
    assert with_index.statements_seen == 2 and with_index.refusals == []
    assert {(str(e.source), str(e.target)) for e in with_index.edges} == {
        (str(e.source), str(e.target)) for e in alone.edges
    }
