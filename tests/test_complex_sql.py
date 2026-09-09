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
from lineage.analysis.refusal import RefusalCode
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


# --- S1-02: a top-level set operation is an INSERT ... SELECT ----------------------------


def test_top_level_union_all_is_analysed_not_refused(dictionary: Dictionary) -> None:
    """Found by stress 1. Both arms were lost and the reason named a clause not present.

    sqlglot gives `INSERT INTO t SELECT ... UNION ALL SELECT ...` an `exp.Union` where the
    plain form has an `exp.Select`, and band 0 treated everything that was not a Select as
    `VALUES`. `s5_positional_union` could not catch it because it wraps its UNION in a
    subquery, which keeps the INSERT's expression a Select - so the form real ETL writes
    was the one form never covered.
    """
    source = """CREATE OR REPLACE PROCEDURE union_writer IS
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT o.cust_id, TRUNC(o.order_date, 'MM'), SUM(o.gross_amount), COUNT(*)
      FROM stg_orders o
     GROUP BY o.cust_id, TRUNC(o.order_date, 'MM')
    UNION ALL
    SELECT r.cust_id, SYSDATE, -SUM(r.refund_amount), COUNT(*)
      FROM stg_returns r
     GROUP BY r.cust_id;
END union_writer;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    assert result.refusals == []
    sources = {str(e.source) for e in result.edges if str(e.target) == "column:FCT_REVENUE.CUST_ID"}
    assert sources == {"column:STG_ORDERS.CUST_ID", "column:STG_RETURNS.CUST_ID"}


def test_intersect_and_minus_fail_the_same_way_they_used_to(dictionary: Dictionary) -> None:
    """Stress 2 asked whether the defect was UNION-specific. It was not."""
    source = """CREATE OR REPLACE PROCEDURE setop_writer IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT s.cust_id, s.last_login FROM stg_customer s
    INTERSECT
    SELECT r.cust_id, SYSDATE FROM stg_returns r;
END setop_writer;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    assert result.refusals == []
    assert any(str(e.target) == "column:TMP_RECENT.CUST_ID" for e in result.edges)


def test_a_minus_arm_constrains_and_does_not_supply(dictionary: Dictionary) -> None:
    """The semantic half of the fix, and the reason arms are not treated uniformly.

    A `UNION` arm ADDS rows, so it supplies the values of the rows it contributes.
    `MINUS` only REMOVES rows from the first arm - no value in the result ever came from
    it. Treating every arm as a feed would claim `gtt_stage.cust_id -> gtt_stage.cust_id`
    here, a value edge for rows that were specifically excluded.
    """
    source = """CREATE OR REPLACE PROCEDURE minus_writer IS
BEGIN
    INSERT INTO gtt_stage (cust_id, period_month, amount)
    SELECT o.cust_id, TRUNC(o.order_date, 'MM'), o.gross_amount FROM stg_orders o
    MINUS
    SELECT g.cust_id, g.period_month, g.amount FROM gtt_stage g;
END minus_writer;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    into_cust_id = {
        (str(e.source), e.flow.value)
        for e in result.edges
        if str(e.target) in ("column:GTT_STAGE.CUST_ID", "relation:GTT_STAGE")
    }
    assert ("column:STG_ORDERS.CUST_ID", "value") in into_cust_id
    assert ("column:GTT_STAGE.CUST_ID", "filter") in into_cust_id
    assert ("column:GTT_STAGE.CUST_ID", "value") not in into_cust_id


def test_three_armed_union_keeps_every_arm(dictionary: Dictionary) -> None:
    """sqlglot nests set operations left-associatively, so flattening has to recurse."""
    source = """CREATE OR REPLACE PROCEDURE three_arms IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT s.cust_id, s.last_login FROM stg_customer s
    UNION ALL
    SELECT o.cust_id, o.order_date FROM stg_orders o
    UNION ALL
    SELECT r.cust_id, SYSDATE FROM stg_returns r;
END three_arms;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    sources = {str(e.source) for e in result.edges if str(e.target) == "column:TMP_RECENT.CUST_ID"}
    assert sources == {
        "column:STG_CUSTOMER.CUST_ID",
        "column:STG_ORDERS.CUST_ID",
        "column:STG_RETURNS.CUST_ID",
    }


# --- S2-03: PIVOT and UNPIVOT with a literal column list are decidable -------------------


def test_static_pivot_traces_its_transposed_columns(dictionary: Dictionary) -> None:
    """Found by stress 2. Both forms were refused although their shape is fixed by the text.

    The register's own reason admitted it — "the literal-list form is decidable in
    principle but is not implemented" — which made it a false abstention (T3.1d), costing
    parse coverage on top of the lost edges.

    Note what un-refusing alone would have produced: the pass-through columns trace and the
    transposed ones silently do not, because their sources live in the pivot clause rather
    than the select list. A partial answer where the missing part is the whole point of the
    construct is worse than the refusal, which is why this needed implementing.
    """
    source = """CREATE OR REPLACE PROCEDURE pivot_writer IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, gross_sales, net_sales)
    SELECT period_month, product_id, gbp, usd
      FROM (
            SELECT TRUNC(o.order_date, 'MM') AS period_month,
                   l.product_id              AS product_id,
                   o.currency                AS currency,
                   l.line_amount             AS line_amount
              FROM stg_orders o
              JOIN stg_order_lines l ON l.order_id = o.order_id
           )
     PIVOT (SUM(line_amount) FOR currency IN ('GBP' AS gbp, 'USD' AS usd));
END pivot_writer;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    assert result.refusals == []
    by_target = {
        (str(e.source), str(e.target), e.transform.value)
        for e in result.edges
        if e.flow is Flow.VALUE
    }
    # Each pivoted output column is fed by the aggregate's argument.
    assert (
        "column:STG_ORDER_LINES.LINE_AMOUNT",
        "column:FCT_PRODUCT_SALES.GROSS_SALES",
        "aggregated",
    ) in by_target
    assert (
        "column:STG_ORDER_LINES.LINE_AMOUNT",
        "column:FCT_PRODUCT_SALES.NET_SALES",
        "aggregated",
    ) in by_target
    # The FOR column decides WHICH output column a row lands in: filter, not value.
    assert any(
        str(e.source) == "column:STG_ORDERS.CURRENCY" and e.flow is Flow.FILTER
        for e in result.edges
    )
    assert not any(
        str(e.source) == "column:STG_ORDERS.CURRENCY" and e.flow is Flow.VALUE for e in result.edges
    )


def test_unpivot_feeds_one_column_from_several(dictionary: Dictionary) -> None:
    """UNPIVOT is the reverse of every other construct here: many inputs, one output.

    `measure` is a generated label naming which input each row came from, and has no
    upstream at all.
    """
    source = """CREATE OR REPLACE PROCEDURE unpivot_writer IS
BEGIN
    INSERT INTO gtt_stage (cust_id, period_month, amount)
    SELECT u.cust_id, TRUNC(SYSDATE, 'MM'), u.amount
      FROM (SELECT f.cust_id, f.net_amount AS net_amount, f.order_count AS order_count
              FROM fct_revenue f)
    UNPIVOT (amount FOR measure IN (net_amount, order_count)) u;
END unpivot_writer;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    assert result.refusals == []
    feeding_amount = {
        str(e.source) for e in result.edges if str(e.target) == "column:GTT_STAGE.AMOUNT"
    }
    assert feeding_amount == {
        "column:FCT_REVENUE.NET_AMOUNT",
        "column:FCT_REVENUE.ORDER_COUNT",
    }


def test_pivot_with_a_subquery_column_list_is_still_refused(dictionary: Dictionary) -> None:
    """The over-refusal guard. Only the DECIDABLE form was un-refused.

    With a subquery IN-list the output columns ARE the data, so the statement's shape is
    not fixed by its text and no positional binding is possible. `u1_pivot_subquery` still
    refuses, and the phase-0 refusal count is unchanged at 11.
    """
    source = """CREATE OR REPLACE PROCEDURE pivot_dynamic IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, gross_sales, net_sales)
    SELECT period_month, product_id, a, b
      FROM (SELECT TRUNC(o.order_date, 'MM') AS period_month, l.product_id AS product_id,
                   o.currency AS currency, l.line_amount AS line_amount
              FROM stg_orders o JOIN stg_order_lines l ON l.order_id = o.order_id)
     PIVOT (SUM(line_amount) FOR currency IN (SELECT currency FROM stg_orders));
END pivot_dynamic;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    assert [r.code for r in result.refusals] == [RefusalCode.SHAPE_NOT_DECIDABLE]
