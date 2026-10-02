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
    SELECT DECODE(status_code, 'A', region, email) INTO v_flag
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
    # Two columns since D-19 (B4-02): `DECODE(status_code, 'A', 1, 0)` is now derived by
    # decision, so the band-1 check needs a DECODE that genuinely chooses between columns.
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
        ("DECODE(status_code, 'A', region, email)", Transform.CONDITIONAL),
        ("NULLIF(region, country_code)", Transform.CONDITIONAL),
        ("GREATEST(gross_amount, discount_amt)", Transform.CONDITIONAL),
        ("NVL2(region, email, status_code)", Transform.CONDITIONAL),
        ("COALESCE(email, region)", Transform.CONDITIONAL),
        # D-19 (B4-02): one column against constants is derived in every conditional form.
        ("DECODE(status_code, 'A', 1, 0)", Transform.DERIVED),
        ("NULLIF(region, 'XX')", Transform.DERIVED),
        ("NVL2(region, 1, 0)", Transform.DERIVED),
        ("COALESCE(a.x, b.x)", Transform.CONDITIONAL),
        ("COALESCE(x, x)", Transform.DERIVED),
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


# --- S2-08's other half: LAG and LEAD, under D-3's rule ----------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        # Reads another ROW of the same column and computes nothing over a set. D-3's rule,
        # which is why this is the rule applied rather than a new decision.
        ("LAG(line_amount) OVER (ORDER BY order_date)", Transform.DERIVED),
        ("LEAD(line_amount) OVER (ORDER BY order_date)", Transform.DERIVED),
        # The aggregate is INSIDE the window, so the ladder keeps `aggregated` whatever LAG
        # is classified as. THIS IS THE ONLY SHAPE THE CORPUS CONTAINS - both LAG instances
        # in every package are this - which is why moving LAG cost nothing and why the
        # bare-column rows above are the whole content of the change.
        ("LAG(SUM(line_amount)) OVER (ORDER BY order_date)", Transform.AGGREGATED),
        # Still an aggregation: it computes a total over a set, and OVER is not what makes
        # it one. The half of D-3's rule that is easy to break.
        ("SUM(line_amount) OVER (ORDER BY order_date)", Transform.AGGREGATED),
        ("COUNT(*) OVER ()", Transform.AGGREGATED),
    ],
)
def test_lag_and_lead_select_a_value_rather_than_computing_one(
    expression: str, expected: Transform
) -> None:
    """S2-08's second half. Unmeasurable in the corpus, so the test is the whole statement.

    Every key labelled LAG `aggregated`, but no key argued for it: `sq_03`'s own note reads
    "LAG(total) reads another ROW of the same column", which is the case for `derived`. The
    label recorded what the analyser did.

    A bare-column `LAG(line_amount)` is the form real reporting SQL writes and appears
    nowhere in 38 packages - so if this rule is ever reversed, this test is what should fail
    rather than a score moving six months later.
    """
    parsed = sqlglot.parse_one(f"SELECT {expression} FROM t", dialect="oracle").selects[0]
    assert transform_of(parsed) is expected


# ======================================================================================
# Decision D-7: the ladder ranks the PATH, not the expression.
# ======================================================================================


def _paths(sql: str, book: Dictionary) -> dict[tuple[str, str], str]:
    config = AnalysisConfig.load().model_copy(update={"dialect": book.dialect})
    result = band0.analyse_source(sql, book, config)
    return {
        (str(e.source), str(e.target)): e.transform.value
        for e in result.edges
        if e.flow.value == "value"
    }


@pytest.fixture(scope="module")
def postgres() -> Dictionary:
    return Dictionary.load(Path("stress") / "dictionary_postgres.json")


def test_a_mixed_expression_gives_each_operand_its_own_transform(
    postgres: Dictionary,
) -> None:
    """The case D-7 was decided on, from `stress_pg_04_query_complexity`.

    Only the third operand passes through an aggregate. Before D-7 all three were reported
    `aggregated`, which told a reader that `net_amount` is a sum of `gross_amount` - and it
    is not, it is one row's value with two things subtracted from it.

    All three are asserted together: a fix that reported `derived` for everything would
    satisfy the first two and be just as wrong as what it replaced.
    """
    sql = """
        INSERT INTO fct_revenue (cust_id, net_amount)
        SELECT o.cust_id,
               o.gross_amount
                 - COALESCE(o.discount_amt, 0)
                 - COALESCE((SELECT SUM(r.refund_amount)
                               FROM stg_returns r
                              WHERE r.order_id = o.order_id), 0)
          FROM stg_orders o;
    """
    seen = _paths(sql, postgres)
    target = "column:fct_revenue.net_amount"
    assert seen[("column:stg_orders.gross_amount", target)] == Transform.DERIVED.value
    assert seen[("column:stg_orders.discount_amt", target)] == Transform.DERIVED.value
    assert seen[("column:stg_returns.refund_amount", target)] == Transform.AGGREGATED.value


def test_an_aggregate_one_level_up_still_makes_every_operand_aggregated(
    postgres: Dictionary,
) -> None:
    """The shape that must NOT change - and the reason `stress_pg_05` was unaffected.

    `SUM(gross - discount)` wraps the mixed expression, so both operands genuinely reach
    the target through an aggregate and the two readings of the ladder agree. D-7 narrows
    the answer only where the readings differ; an implementation that narrowed it here as
    well would break the commonest shape in the corpus.
    """
    sql = """
        INSERT INTO fct_revenue (cust_id, net_amount)
        SELECT o.cust_id, SUM(o.gross_amount - COALESCE(o.discount_amt, 0))
          FROM stg_orders o GROUP BY o.cust_id;
    """
    seen = _paths(sql, postgres)
    target = "column:fct_revenue.net_amount"
    assert seen[("column:stg_orders.gross_amount", target)] == Transform.AGGREGATED.value
    assert seen[("column:stg_orders.discount_amt", target)] == Transform.AGGREGATED.value


def test_a_conditional_branch_reaches_only_the_columns_inside_it(
    postgres: Dictionary,
) -> None:
    """The same rule for the middle rung of the ladder, which no package exercises.

    `CASE WHEN ... THEN x END + y`: only `x` is inside the branch. Before D-7 both operands
    were `conditional`, which says there is business logic on `y`'s path when there is
    arithmetic and nothing else.
    """
    sql = """
        INSERT INTO fct_revenue (cust_id, net_amount)
        SELECT o.cust_id,
               CASE WHEN o.currency = 'GBP' THEN o.gross_amount ELSE 0 END + o.discount_amt
          FROM stg_orders o;
    """
    seen = _paths(sql, postgres)
    target = "column:fct_revenue.net_amount"
    assert seen[("column:stg_orders.gross_amount", target)] == Transform.CONDITIONAL.value
    assert seen[("column:stg_orders.discount_amt", target)] == Transform.DERIVED.value


def test_identity_survives_d7(postgres: Dictionary) -> None:
    """A bare column is still `identity`. The bottom rung has to keep working."""
    sql = """
        INSERT INTO fct_revenue (cust_id, net_amount)
        SELECT o.cust_id, o.gross_amount FROM stg_orders o;
    """
    seen = _paths(sql, postgres)
    assert seen[("column:stg_orders.cust_id", "column:fct_revenue.cust_id")] == (
        Transform.IDENTITY.value
    )
