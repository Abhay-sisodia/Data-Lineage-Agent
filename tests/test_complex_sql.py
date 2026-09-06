"""Complex SQL: joins, CTEs, windows, deep nesting, set operations, self-joins.

The band-0/1 corpus was built around the spike's PROCEDURAL construct ladder. Measured,
it averages 0.7 tables per statement and contains not one JOIN. These cases test SQL
difficulty on its own axis, which is a different risk entirely - and one that was
untested until the numbers below existed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow, GroundTruth
from lineage.harness.scoring import score
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")
GROUND_TRUTH = Path("ground_truth")

CASES = [
    "sq_01_multi_join",
    "sq_02_cte_chain",
    "sq_03_window_functions",
    "sq_04_deep_nesting",
    "sq_05_set_operations",
    "sq_06_self_join",
    "sq_07_unqualified_ambiguity",
]


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def _score(name: str, dictionary: Dictionary):
    truth = GroundTruth.load(GROUND_TRUTH / f"{name}.yaml")
    truth.verify_against(CORPUS)
    result = analyse_source(
        (CORPUS / truth.package).read_text(encoding="utf-8"), dictionary, AnalysisConfig()
    )
    return score(truth, result.edges, result.boundaries), result


def _pairs(result) -> set[tuple[str, str]]:
    return {(str(e.source), str(e.target)) for e in result.edges}


# --- the aggregate gate --------------------------------------------------------------


@pytest.mark.parametrize("name", CASES)
def test_no_invented_edges_on_complex_sql(name: str, dictionary: Dictionary) -> None:
    """Precision must stay perfect as SQL gets harder.

    A false edge inside a filing is worse than a missing one, and complex SQL is exactly
    where plausible wrong answers come from.
    """
    report, _ = _score(name, dictionary)
    counts = report.cell(0, Flow.VALUE)
    assert counts.false_positives == 0, f"invented: {report.spurious_edges}"


@pytest.mark.parametrize("name", CASES)
def test_value_recall_on_complex_sql(name: str, dictionary: Dictionary) -> None:
    report, _ = _score(name, dictionary)
    counts = report.cell(0, Flow.VALUE)
    assert counts.recall == 1.0, f"missed: {report.missed_edges}"


# --- the three defects this band found ------------------------------------------------


def test_set_operation_arms_all_contribute(dictionary: Dictionary) -> None:
    """DEFECT 1, found by measurement: UNION arms produced nothing at all.

    Three arms read three different tables, so every output column has three sources.
    Binding is POSITIONAL, and `A UNION B UNION C` parses as Union(Union(A, B), C) - so
    the arms have to be flattened before positions line up, or only the outermost
    resolves and two whole feeds vanish silently.
    """
    _, result = _score("sq_05_set_operations", dictionary)
    pairs = _pairs(result)

    assert ("column:STG_ORDER_LINES.LINE_AMOUNT", "column:FCT_PRODUCT_SALES.GROSS_SALES") in pairs
    assert ("column:STG_RETURNS.REFUND_AMOUNT", "column:FCT_PRODUCT_SALES.GROSS_SALES") in pairs
    assert ("column:STG_PRODUCTS.LIST_PRICE", "column:FCT_PRODUCT_SALES.GROSS_SALES") in pairs


def test_window_partition_and_order_are_not_value_sources(dictionary: Dictionary) -> None:
    """DEFECT 2: PARTITION BY / ORDER BY columns were counted as value sources.

    `ROW_NUMBER() OVER (PARTITION BY period_month ORDER BY total DESC)` takes no argument
    at all - nothing supplies its value. Claiming a date determines a rank's VALUE is the
    same category error as treating a correlated predicate as a value source.
    """
    _, result = _score("sq_03_window_functions", dictionary)

    value_edges = {(str(e.source), str(e.target)) for e in result.edges if e.flow is Flow.VALUE}
    assert (
        "column:STG_ORDERS.ORDER_DATE",
        "column:FCT_PRODUCT_SALES.RANK_IN_MONTH",
    ) not in value_edges

    # But the ordering column's influence is still reported - as a filter.
    filters = {(str(e.source), str(e.target)) for e in result.edges if e.flow is Flow.FILTER}
    assert ("column:STG_ORDER_LINES.LINE_AMOUNT", "relation:FCT_PRODUCT_SALES") in filters


def test_lag_traces_to_the_column_not_the_row(dictionary: Dictionary) -> None:
    """`LAG(total)` reads another ROW of the same column.

    Lineage tracks which COLUMN a value came from, not which row, so the edge is real.
    """
    _, result = _score("sq_03_window_functions", dictionary)
    assert (
        "column:STG_ORDER_LINES.LINE_AMOUNT",
        "column:FCT_PRODUCT_SALES.PRIOR_MONTH",
    ) in _pairs(result)


def test_scalar_subquery_resolves_in_its_own_scope(dictionary: Dictionary) -> None:
    """DEFECT 3: a scalar subquery in the select list brings its own FROM.

    `(SELECT MAX(s.email) FROM stg_customer s WHERE s.cust_id = parent.cust_id)` resolves
    `s.email` against the subquery's scope, not the outer one. Looking only at the outer
    scope loses the edge entirely.
    """
    _, result = _score("sq_06_self_join", dictionary)
    assert (
        "column:STG_CUSTOMER.EMAIL",
        "column:DIM_CUSTOMER_HIER.ROOT_NAME",
    ) in _pairs(result)


# --- constructs that worked first time ------------------------------------------------


def test_five_table_join_resolves_every_source(dictionary: Dictionary) -> None:
    """Five tables and a LEFT JOIN, with column names repeated across them."""
    _, result = _score("sq_01_multi_join", dictionary)
    pairs = _pairs(result)

    assert (
        "column:STG_CATEGORIES.CATEGORY_NAME",
        "column:FCT_PRODUCT_SALES.CATEGORY_NAME",
    ) in pairs
    assert ("column:STG_RETURNS.REFUND_AMOUNT", "column:FCT_PRODUCT_SALES.REFUND_TOTAL") in pairs


def test_chained_ctes_resolve_through_two_hops(dictionary: Dictionary) -> None:
    """refund_amount reaches the target through two CTEs.

    An analyser stopping at the first CTE boundary loses it entirely.
    """
    _, result = _score("sq_02_cte_chain", dictionary)
    assert (
        "column:STG_RETURNS.REFUND_AMOUNT",
        "column:FCT_PRODUCT_SALES.NET_SALES",
    ) in _pairs(result)


def test_five_levels_of_renaming_survive(dictionary: Dictionary) -> None:
    """line_amount -> amt -> val -> v2 -> final_value -> net_sales."""
    _, result = _score("sq_04_deep_nesting", dictionary)
    assert (
        "column:STG_ORDER_LINES.LINE_AMOUNT",
        "column:FCT_PRODUCT_SALES.NET_SALES",
    ) in _pairs(result)


def test_unqualified_columns_bind_to_the_statement_scope(dictionary: Dictionary) -> None:
    """THE SILENT-FAILURE CASE OF THIS BAND.

    `status_code` exists on stg_customer and stg_returns too; `cust_id` on four other
    tables. Neither is qualified, and only one table in THIS join carries each. An
    analyser resolving against the whole dictionary rather than the statement's FROM
    scope binds them wrongly, and the result looks entirely normal.
    """
    _, result = _score("sq_07_unqualified_ambiguity", dictionary)
    filters = {(str(e.source), str(e.target)) for e in result.edges if e.flow is Flow.FILTER}

    assert ("column:STG_PRODUCTS.STATUS_CODE", "relation:FCT_PRODUCT_SALES") in filters
    assert ("column:STG_ORDERS.CUST_ID", "relation:FCT_PRODUCT_SALES") in filters

    wrong = {s for s, _ in filters if s.startswith(("column:STG_CUSTOMER.", "column:STG_RETURNS."))}
    assert not wrong, f"bound an unqualified column outside the statement's scope: {wrong}"
