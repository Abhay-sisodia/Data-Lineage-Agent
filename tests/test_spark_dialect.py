"""Finding PO-0: Spark / Databricks SQL is analysed as itself, not through PostgreSQL.

The purchase-order torture model (local, `stress/pg/torture_po`) is consolidated Databricks
SQL and used to run through the PostgreSQL path, the only one there was, with nothing
declaring it. These pin the dialect: it is registered and honours the folding contract,
Spark's functions arrive typed (so `SIZE(COLLECT_SET(x) OVER ...)` is an aggregate even at
the top level, where PostgreSQL had declared SIZE an unknown routine), nodes are
schema-qualified as under D-25, and there is no live catalogue to fall back to.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.dialects import SUPPORTED, for_name, validate
from lineage.dialects.spark import SparkCatalogueUnavailableError
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _result(sql: str, dialect: str = "databricks"):
    dictionary = Dictionary.load(BOOK).model_copy(update={"dialect": dialect})
    config = AnalysisConfig.load().model_copy(update={"dialect": dialect})
    return analyse_source(sql, dictionary, config, None)


def _edges(result) -> set[tuple[str, str, str, str]]:
    return {(e.flow.value, str(e.source), str(e.target), e.transform.value) for e in result.edges}


@pytest.mark.parametrize("name", ["spark", "databricks"])
def test_registered_and_folding_as_sqlglot_does(name: str) -> None:
    validate(name, SUPPORTED[name])
    assert for_name(name).fold("Cust_Id") == "cust_id"


@pytest.mark.parametrize("dialect", ["spark", "databricks"])
def test_collect_set_under_size_is_an_aggregate_at_the_top_level(dialect: str) -> None:
    result = _result(
        "SELECT SIZE(COLLECT_SET(o.customer_id) OVER (PARTITION BY o.store_id)) AS buyers "
        "FROM staging.orders AS o",
        dialect,
    )
    assert result.boundaries == []
    assert _edges(result) == {
        ("value", "column:staging.orders.customer_id", "column:<result>.buyers", "aggregated"),
        ("influence", "column:staging.orders.store_id", "column:<result>.buyers", "identity"),
    }


def test_spark_functions_are_read_as_what_they_are() -> None:
    result = _result(
        "SELECT date_format(o.order_ts, 'yyyyMMdd') AS d, TRY_CAST(o.store_id AS INT) AS s, "
        "HEX(MD5(o.status)) AS h, DATEDIFF(o.updated_at, o.created_at) AS age "
        "FROM staging.orders AS o"
    )
    assert result.boundaries == []
    assert _edges(result) == {
        ("value", "column:staging.orders.order_ts", "column:<result>.d", "derived"),
        ("value", "column:staging.orders.store_id", "column:<result>.s", "derived"),
        ("value", "column:staging.orders.status", "column:<result>.h", "derived"),
        ("value", "column:staging.orders.updated_at", "column:<result>.age", "derived"),
        ("value", "column:staging.orders.created_at", "column:<result>.age", "derived"),
    }


def test_same_named_tables_in_two_schemas_stay_two_nodes() -> None:
    """D-25 applies: `staging.ledger` and `core.ledger` must not merge into `ledger`."""
    result = _result(
        "SELECT s.amount AS staged, c.balance AS posted FROM staging.ledger AS s "
        "JOIN core.ledger AS c ON c.id = s.id"
    )
    sources = {str(e.source) for e in result.edges if e.flow.value == "value"}
    assert sources == {"column:staging.ledger.amount", "column:core.ledger.balance"}


def test_there_is_no_live_catalogue_to_fall_back_to() -> None:
    with pytest.raises(SparkCatalogueUnavailableError):
        for_name("databricks").catalogue.capture  # noqa: B018
