"""Interprocedural summaries (T2.7).

A scalar UDF inside a SELECT looks like a column expression and contains a query.
Without summarising the callee, a regulated column has no traceable origin at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.interproc import build_summaries
from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.ir.model import Transform
from lineage.parsing.plsql import parse_program
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")
BAND1 = CORPUS / "adversarial" / "band1"


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


@pytest.fixture(scope="module")
def nested_edges(dictionary: Dictionary) -> list:
    source = (BAND1 / "b1_08_nested_calls.sql").read_text(encoding="utf-8")
    return analyse_source(source, dictionary, AnalysisConfig()).edges


def _pairs(edges: list) -> set[tuple[str, str]]:
    return {(str(e.source), str(e.target)) for e in edges}


# --- the chain resolves --------------------------------------------------------------


def test_three_deep_call_chain_resolves(nested_edges: list) -> None:
    """b1_nested_calls -> fn_net_amount -> fn_discount_rate.

    Without inlining the summaries, fct_revenue.net_amount appears to come from nowhere:
    a function call with no visible source.
    """
    pairs = _pairs(nested_edges)
    assert ("column:STG_ORDERS.GROSS_AMOUNT", "column:FCT_REVENUE.NET_AMOUNT") in pairs
    assert ("column:STG_ORDERS.DISCOUNT_AMT", "column:FCT_REVENUE.NET_AMOUNT") in pairs


def test_call_arguments_are_not_value_sources(nested_edges: list) -> None:
    """THE PLAUSIBLE WRONG ANSWER this task removes.

    `fn_net_amount(order_id)` contains the column order_id, so a naive walk emits
    `order_id -> net_amount`. It type-checks, it reads sensibly, and it is false: an
    order id selects WHICH ROW the callee reads, it does not determine a net amount.
    """
    pairs = _pairs(nested_edges)
    assert ("column:STG_ORDERS.ORDER_ID", "column:FCT_REVENUE.NET_AMOUNT") not in pairs


def test_call_argument_becomes_filter_influence(nested_edges: list) -> None:
    """What the argument really does: select the callee's row."""
    filters = {(str(e.source), str(e.target)) for e in nested_edges if e.flow.value == "filter"}
    assert ("column:STG_ORDERS.ORDER_ID", "relation:STG_ORDERS") in filters


def test_package_state_chain_spans_two_procedures(dictionary: Dictionary) -> None:
    """b1_07: state written by one call is read by another, with no parameter passing."""
    source = (BAND1 / "b1_07_package_variables.sql").read_text(encoding="utf-8")
    pairs = _pairs(analyse_source(source, dictionary, AnalysisConfig()).edges)

    assert (
        "column:REF_POLICY.WINDOW_DAYS",
        "variable:PKG_POLICY_STATE.G_WINDOW_DAYS",
    ) in pairs
    assert (
        "variable:PKG_POLICY_STATE.G_WINDOW_DAYS",
        "variable:PKG_POLICY_STATE.G_CUTOFF",
    ) in pairs
    assert ("variable:PKG_POLICY_STATE.G_CUTOFF", "relation:TMP_RECENT") in pairs


# --- transform combination -----------------------------------------------------------


def test_weakest_transform_wins_across_different_paths(dictionary: Dictionary) -> None:
    """Two combination rules, and confusing them gives a wrong answer.

    Along ONE path the strongest transform wins. Across DIFFERENT paths to the same
    column the weakest wins: gross_amount reaches the result unconditionally through
    v_gross and conditionally through the rate, so it is derived, not conditional.
    Reporting it conditional would claim it only sometimes contributes.
    """
    source = (BAND1 / "b1_08_nested_calls.sql").read_text(encoding="utf-8")
    summaries = build_summaries(parse_program(source), dictionary, depth_cap=6)
    net = summaries["FN_NET_AMOUNT"].consolidated()
    by_column = {item.column: item.transform for item in net}

    assert by_column["STG_ORDERS.GROSS_AMOUNT"] is Transform.DERIVED
    assert by_column["STG_ORDERS.DISCOUNT_AMT"] is Transform.CONDITIONAL


def test_discount_only_contributes_conditionally(nested_edges: list) -> None:
    """`CASE WHEN gross_amount = 0 THEN 0 ELSE discount_amt/gross_amount END`.

    discount_amt contributes only when gross_amount is non-zero. Calling that derived
    claims an unconditional contribution it does not make.
    """
    edge = next(
        e
        for e in nested_edges
        if str(e.source) == "column:STG_ORDERS.DISCOUNT_AMT"
        and str(e.target) == "column:FCT_REVENUE.NET_AMOUNT"
    )
    assert edge.transform is Transform.CONDITIONAL


# --- limits are declared, never silently applied --------------------------------------


def test_depth_cap_produces_a_declared_boundary(dictionary: Dictionary) -> None:
    """Beyond the cap: a counted boundary, never a guessed edge."""
    source = (BAND1 / "b1_08_nested_calls.sql").read_text(encoding="utf-8")
    summaries = build_summaries(parse_program(source), dictionary, depth_cap=1)

    truncated = [s for s in summaries.values() if s.truncated]
    assert truncated, "the depth cap was not reached or not recorded"
    assert any("depth cap" in (s.boundary() or "") for s in truncated)


def test_recursion_terminates(dictionary: Dictionary) -> None:
    """A self-calling function must not unroll forever."""
    source = """
    CREATE OR REPLACE FUNCTION fn_recursive(p_id NUMBER) RETURN NUMBER IS
      v_x NUMBER;
    BEGIN
      SELECT gross_amount INTO v_x FROM stg_orders WHERE order_id = p_id;
      RETURN v_x + fn_recursive(p_id - 1);
    END;
    """
    summaries = build_summaries(parse_program(source), dictionary, depth_cap=6)
    assert "FN_RECURSIVE" in summaries
    assert summaries["FN_RECURSIVE"].truncated, "recursion was not declared"


def test_unknown_callee_is_truncated_not_guessed(dictionary: Dictionary) -> None:
    """A call to something outside this source is a boundary, not an invented edge."""
    source = """
    CREATE OR REPLACE PROCEDURE t_external IS
    BEGIN
      INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
      SELECT cust_id, order_date, some_other_package.compute(order_id), 1 FROM stg_orders;
    END;
    """
    result = analyse_source(source, dictionary, AnalysisConfig())
    invented = [
        e
        for e in result.edges
        if str(e.target) == "column:FCT_REVENUE.NET_AMOUNT"
        and str(e.source) == "column:STG_ORDERS.ORDER_ID"
    ]
    assert not invented, "an unknown callee's argument was treated as a value source"
