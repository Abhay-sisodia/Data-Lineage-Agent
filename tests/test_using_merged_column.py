"""The merged column of `USING` / `NATURAL` is the one PostgreSQL uses (finding B1-02).

SQLGlot's `qualify` writes every merged column as `COALESCE(left.k, right.k)`. PostgreSQL
does that only for a FULL join: INNER and LEFT take the left input, RIGHT the right. The
generated COALESCE read as a genuine choice between sources - a `conditional` edge from
both sides - so `USING (k)` disagreed with the `ON a.k = b.k` it abbreviates (B1 T5 P1).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _sources(sql: str) -> set[tuple[str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    return {(str(e.source), e.transform.value) for e in result.edges if e.flow.value == "value"}


def _using(join: str) -> str:
    return (
        "INSERT INTO mart.top_customers (customer_id) SELECT order_id "
        f"FROM staging.shipments AS sh {join} staging.payments AS p USING (order_id)"
    )


@pytest.mark.parametrize(
    ("join", "expected"),
    [
        ("JOIN", {("column:staging.shipments.order_id", "identity")}),
        ("LEFT JOIN", {("column:staging.shipments.order_id", "identity")}),
        ("RIGHT JOIN", {("column:staging.payments.order_id", "identity")}),
        (
            "FULL JOIN",
            {
                ("column:staging.shipments.order_id", "conditional"),
                ("column:staging.payments.order_id", "conditional"),
            },
        ),
    ],
)
def test_the_merged_column_follows_the_join_type(join: str, expected: set) -> None:
    assert _sources(_using(join)) == expected


def test_using_agrees_with_the_on_it_abbreviates() -> None:
    on = (
        "INSERT INTO mart.top_customers (customer_id) SELECT sh.order_id "
        "FROM staging.shipments AS sh JOIN staging.payments AS p ON p.order_id = sh.order_id"
    )
    assert _sources(_using("JOIN")) == _sources(on)


def test_a_coalesce_written_by_hand_is_left_alone() -> None:
    """The control. Over an inner join `COALESCE(sh.order_id, p.order_id)` is pointless but
    it is what was WRITTEN; the analyser must not edit the query it is describing."""
    written = (
        "INSERT INTO mart.top_customers (customer_id) "
        "SELECT COALESCE(sh.order_id, p.order_id) "
        "FROM staging.shipments AS sh JOIN staging.payments AS p ON p.order_id = sh.order_id"
    )
    assert _sources(written) == {
        ("column:staging.shipments.order_id", "conditional"),
        ("column:staging.payments.order_id", "conditional"),
    }


def test_natural_join_takes_the_left_input() -> None:
    sql = (
        "INSERT INTO mart.kpi_rollup (region) SELECT region "
        "FROM (SELECT wh.region, wh.capacity FROM core.dim_warehouse AS wh) AS w "
        "NATURAL JOIN (SELECT st.region, st.opened_on FROM core.dim_store AS st) AS s"
    )
    assert _sources(sql) == {("column:core.dim_warehouse.region", "identity")}
