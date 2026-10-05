"""Finding TE-2: a bare SELECT declares the search-path assumption its names rest on (D-23).

The entity torture test (`stress/pg/torture_entity`, local) named fifteen tables unqualified
in one bare SELECT. They bound to the default schema and nothing said so: the check that
declares `context_dependent_binding` looked only at the statement kinds band 0 writes from,
and a top-level query - analysed into `<result>` since D-8 - is not one of them.
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


def _kinds(sql: str) -> list[str]:
    return [b.kind.value for b in _result(sql).boundaries]


def test_an_unqualified_bare_select_declares_its_binding() -> None:
    result = _result("SELECT f.weight_kg FROM fct_shipment AS f;")
    assert [b.kind.value for b in result.boundaries] == ["context_dependent_binding"]
    assert {(str(e.source), str(e.target)) for e in result.edges} == {
        ("column:core.fct_shipment.weight_kg", "column:<result>.weight_kg")
    }


def test_a_qualified_bare_select_assumes_nothing() -> None:
    """The control."""
    assert _kinds("SELECT f.weight_kg FROM core.fct_shipment AS f;") == []


def test_a_cte_name_is_not_an_unqualified_relation() -> None:
    """B1-06 still holds for a bare SELECT: only the CTE is unqualified here."""
    sql = "WITH s AS (SELECT f.weight_kg FROM core.fct_shipment AS f)\nSELECT s.weight_kg FROM s;"
    assert _kinds(sql) == []


def test_under_a_search_path_the_binding_is_certain() -> None:
    sql = "SET search_path TO core;\nSELECT f.weight_kg FROM fct_shipment AS f;"
    assert _kinds(sql) == []
