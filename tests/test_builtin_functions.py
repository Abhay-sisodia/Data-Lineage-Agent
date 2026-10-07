"""A built-in SQLGlot has no class for is still a built-in (findings B1-04 and PG-04).

Untyped functions parse as `exp.Anonymous`, which the call-site rule read as a user
routine whose source is out of coverage. `cardinality(array_agg(x))` and
`jsonb_build_object('k', x)` lost `x` behind a `source_unavailable` boundary that named a
built-in as if it were someone's package.
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _facts(sql: str) -> set[tuple[str, str, str, str]]:
    return {
        (str(e.source), str(e.target), e.flow.value, e.transform.value) for e in _result(sql).edges
    }


def test_cardinality_of_an_aggregate_keeps_the_aggregate_and_its_grouping() -> None:
    """B1-04 as found in T1 S11: both the value edge and the GROUP BY influence were lost."""
    facts = _facts(
        "INSERT INTO mart.carrier_scorecard (carrier_id, shipments) "
        "SELECT f.carrier_id, cardinality(array_agg(f.order_id)) "
        "FROM core.fct_shipment AS f GROUP BY f.carrier_id"
    )
    assert (
        "column:core.fct_shipment.order_id",
        "column:mart.carrier_scorecard.shipments",
        "value",
        "aggregated",
    ) in facts
    assert (
        "column:core.fct_shipment.carrier_id",
        "column:mart.carrier_scorecard.shipments",
        "influence",
        "identity",
    ) in facts


def test_jsonb_build_object_is_transparent_to_its_arguments() -> None:
    """PG-04: the refusal was honest, the classification wrong - the value is the argument's."""
    sql = (
        "INSERT INTO mart.kpi_rollup (level) "
        "SELECT jsonb_build_object('n', s.name)::text FROM core.dim_store AS s"
    )
    assert _facts(sql) == {("column:core.dim_store.name", "column:mart.kpi_rollup.level", "value", "derived")}
    assert not _result(sql).boundaries


def test_a_builtin_aggregate_is_an_aggregate() -> None:
    """`jsonb_agg` read as a scalar would be B1-03's mistake again: wrong transform, and
    the GROUP BY's influence silently dropped."""
    facts = _facts(
        "INSERT INTO mart.kpi_rollup (region, level) "
        "SELECT s.region, jsonb_agg(s.name)::text FROM core.dim_store AS s GROUP BY s.region"
    )
    assert ("column:core.dim_store.name", "column:mart.kpi_rollup.level", "value", "aggregated") in facts
    assert ("column:core.dim_store.region", "column:mart.kpi_rollup.level", "influence", "identity") in facts


def test_a_user_function_is_still_declared() -> None:
    """The control that keeps the catalogue honest: an unknown name is not a built-in.

    It is DECLARED, which a built-in never is. Since D-53 (2026-10-07) its argument is also
    a derived value - the edge rests on the body nobody read, which is what the boundary
    says; before, the argument was withheld here while the same call in a CTE claimed it.
    """
    result = _result(
        "INSERT INTO mart.kpi_rollup (level) SELECT my_udf(s.name) FROM core.dim_store AS s"
    )
    assert {(str(e.source), str(e.target), e.transform.value) for e in result.edges} == {
        ("column:core.dim_store.name", "column:mart.kpi_rollup.level", "derived")
    }
    assert [(b.kind.value, b.subject) for b in result.boundaries] == [
        ("source_unavailable", "MY_UDF")
    ]
