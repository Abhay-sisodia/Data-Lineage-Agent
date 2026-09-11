"""The transform classifier has ONE home, and band 1 gets the same answer as band 0.

Stress finding S2-08. `band0` and `defuse` each carried their own `_transform_of` with
their own constant tuples, and S2-05 - DECODE is CASE written differently, so it is
`conditional` - was applied to band 0 and never reached band 1. A finding recorded as fixed
was half-fixed, and nothing reported it because no package in the corpus or either stress
file happens to reach a DECODE through def-use.

That last clause is the reason these tests exist rather than a measurement: the defect was
real, cost nothing measurable, and would have stayed invisible until a package that hit it
was written. The test is the only durable statement of the rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlglot

from lineage.analysis import band0, defuse
from lineage.analysis.procedure import analyse_source
from lineage.analysis.transforms import transform_of
from lineage.config import AnalysisConfig
from lineage.ir.model import Transform
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def test_both_bands_use_the_same_classifier_object() -> None:
    """Not "the same rule" - the same function. Two copies is what went wrong.

    Asserting identity rather than behaviour is deliberate: two separately-defined
    functions can agree on every case a test happens to list and still drift on the next
    one added, which is exactly the history here.
    """
    assert band0._transform_of is transform_of
    assert defuse._transform_of is transform_of
    assert band0._combine is defuse._combine
    assert band0.CONDITIONAL_EXPRESSIONS is defuse.CONDITIONAL_EXPRESSIONS
    assert band0.AGGREGATE_FUNCTIONS is defuse.AGGREGATE_FUNCTIONS
    assert band0.VALUE_SELECTING_WINDOW_FUNCTIONS is defuse.VALUE_SELECTING_WINDOW_FUNCTIONS


def test_s2_05_now_reaches_band_1(dictionary: Dictionary) -> None:
    """A DECODE and a two-column COALESCE read through def-use, into variables.

    Measured against the pre-S2-08 `defuse.py`: all three of these edges were `derived`.
    `defuse.CONDITIONAL_EXPRESSIONS` was still the pre-S2-05 `(exp.Case, exp.If)` pair, and
    the COALESCE-versus-NVL rule - the part of S2-05 that needed a rule rather than a list -
    did not exist in band 1 at all.

    A wrong transform is a MISS on both sides under ADR-0001 §4, so each of these was
    costing a false positive AND a false negative in any package that reached it.
    """
    source = """CREATE OR REPLACE PROCEDURE band1_decode IS
    v_flag   NUMBER;
    v_region VARCHAR2(10);
BEGIN
    SELECT DECODE(status_code, 'A', 1, 0) INTO v_flag
      FROM stg_customer WHERE cust_id = 1;

    SELECT COALESCE(email, region) INTO v_region
      FROM stg_customer WHERE cust_id = 1;
END band1_decode;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())
    into_variables = {
        (str(e.source), str(e.target)): e.transform
        for e in result.edges
        if str(e.target).startswith("variable:")
    }

    assert into_variables[("column:STG_CUSTOMER.STATUS_CODE", "variable:V_FLAG")] is (
        Transform.CONDITIONAL
    ), "DECODE reached through def-use is still classified as derived"
    assert into_variables[("column:STG_CUSTOMER.EMAIL", "variable:V_REGION")] is (
        Transform.CONDITIONAL
    )
    assert into_variables[("column:STG_CUSTOMER.REGION", "variable:V_REGION")] is (
        Transform.CONDITIONAL
    )


def test_the_nvl_distinction_reaches_band_1_too(dictionary: Dictionary) -> None:
    """The half of S2-05 that was a rule rather than a list, checked on the band-1 path.

    `NVL(x, 0)` is one column and a constant floor: the value flows through unchanged
    whenever it exists, and the literal is null-safety rather than business logic. Calling
    it conditional would tell a reader there is a branch in the rule when the only branch
    is a null guard - and it costs a real phase-0 label (`sq_06`).
    """
    source = """CREATE OR REPLACE PROCEDURE band1_nvl IS
    v_value NUMBER;
BEGIN
    SELECT NVL(lifetime_value, 0) + 1 INTO v_value
      FROM dim_customer WHERE cust_id = 1;
END band1_nvl;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())
    edges = {
        (str(e.source), str(e.target)): e.transform
        for e in result.edges
        if str(e.target).startswith("variable:")
    }
    assert edges[("column:DIM_CUSTOMER.LIFETIME_VALUE", "variable:V_VALUE")] is Transform.DERIVED


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("DECODE(status_code, 'A', 1, 0)", Transform.CONDITIONAL),
        ("NULLIF(region, 'XX')", Transform.CONDITIONAL),
        ("GREATEST(gross_amount, discount_amt)", Transform.CONDITIONAL),
        ("NVL2(region, 1, 0)", Transform.CONDITIONAL),
        ("COALESCE(email, region)", Transform.CONDITIONAL),
        ("NVL(lifetime_value, 0)", Transform.DERIVED),
        ("SUM(line_amount)", Transform.AGGREGATED),
        ("FIRST_VALUE(product_name) OVER (PARTITION BY product_id)", Transform.DERIVED),
        ("cust_id", Transform.IDENTITY),
    ],
)
def test_the_shared_classifier_covers_every_rule_the_register_settled(
    expression: str, expected: Transform
) -> None:
    """S2-05, S2-07 and the COALESCE rule, in one table against one function.

    Before S2-08 this table could only have been written twice, once per module, and the
    second copy would have failed on five of the nine rows.
    """
    parsed = sqlglot.parse_one(f"SELECT {expression} FROM t", dialect="oracle").selects[0]
    assert transform_of(parsed) is expected
