"""A quoted identifier is a different name from its folded twin (finding B1-05).

`"Region"` and `region` are two columns in PostgreSQL: quoting keeps the case. The
projection lookup compared upper-cased names, so `x.region` bound to the `"Region"`
projection - a confident wrong edge, no boundary, found by B1's adversarial file (T4 S13).
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _edges(sql: str) -> set[tuple[str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    return {(str(e.source), str(e.target)) for e in result.edges if e.flow.value == "value"}


TWINS = """
INSERT INTO mart.kpi_rollup (region, segment)
SELECT x."Region", x.region
FROM (SELECT d.segment AS "Region", d.region AS region FROM core.dim_customer AS d) AS x
"""


def test_each_twin_resolves_to_its_own_projection() -> None:
    assert _edges(TWINS) == {
        ("column:dim_customer.segment", "column:kpi_rollup.region"),
        ("column:dim_customer.region", "column:kpi_rollup.segment"),
    }


def test_the_order_of_the_twins_does_not_matter() -> None:
    """Before the fix the FIRST case-blind match won, so swapping the projections moved
    the error rather than removing it. Both orders must give the same answer."""
    swapped = TWINS.replace(
        'd.segment AS "Region", d.region AS region', 'd.region AS region, d.segment AS "Region"'
    )
    assert _edges(swapped) == _edges(TWINS)


def test_a_lone_quoted_alias_still_resolves_case_blind_when_unambiguous() -> None:
    """The fallback the fix keeps: a quoted mixed-case alias with no twin, read unquoted in
    a context that folds names, is still the only candidate. Removing the fallback would
    turn every such reference into an unresolved boundary."""
    edges = _edges(
        """
        INSERT INTO mart.kpi_rollup (region)
        SELECT x."Region"
        FROM (SELECT d.segment AS "Region" FROM core.dim_customer AS d) AS x
        """
    )
    assert edges == {("column:dim_customer.segment", "column:kpi_rollup.region")}


def test_the_same_holds_through_a_set_operation() -> None:
    """The set-operation path binds by position after finding the name in the first arm;
    it had its own copy of the case-blind comparison."""
    edges = _edges(
        """
        INSERT INTO mart.kpi_rollup (region, segment)
        SELECT u."Region", u.region
        FROM (
            SELECT d.segment AS "Region", d.region AS region FROM core.dim_customer AS d
            UNION ALL
            SELECT w.status, w.region FROM core.dim_warehouse AS w
        ) AS u
        """
    )
    assert ("column:dim_customer.segment", "column:kpi_rollup.region") in edges
    assert ("column:dim_customer.region", "column:kpi_rollup.segment") in edges
    assert ("column:dim_customer.segment", "column:kpi_rollup.segment") not in edges
