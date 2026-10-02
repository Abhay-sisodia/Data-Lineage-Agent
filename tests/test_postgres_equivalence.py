"""Metamorphic tests: the same logic written two ways must produce the same lineage.

**The cheapest instrument in this project, and the only one with no author judgement in
it.** Every other test rests on a ground-truth key someone wrote by hand, and a key is
exactly as good as the care that went into it. PG-12 and PG-13 are what that costs: a
461-line package found that the KEY degrades before the analyser does, twice, by forgetting
at depth eleven a rule it applied correctly at depth two.

A metamorphic pair has no key to degrade. It cannot say whether an answer is RIGHT - only a
key does that - but it says, with no room for argument, that two answers which MUST agree do
not. One of the two is wrong, and no labelling was needed to prove it.

It earned its place immediately: PG-14, a silent defect, was the eighth pair written.

WHAT MAKES A LEGITIMATE PAIR. Both spellings must be semantically identical AND must not
cross a convention boundary this project has deliberately drawn. `JOIN ... ON` against a
comma join is NOT a valid pair: D-5 says an `ON` clause is structural and a `WHERE` equality
contributes both operands, so the two forms are MEANT to differ. Pairs like that test the
convention, not the implementation, and belong in a key where the intended answer is
written down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

STRESS = Path(__file__).resolve().parent.parent / "stress"


@pytest.fixture(scope="module")
def book() -> Dictionary:
    return Dictionary.load(STRESS / "dictionary_postgres.json")


@pytest.fixture(scope="module")
def config() -> AnalysisConfig:
    return AnalysisConfig.load().model_copy(update={"dialect": "postgres"})


def edges_of(sql: str, book: Dictionary, config: AnalysisConfig) -> set[tuple[str, ...]]:
    result = analyse_source(sql, book, config, None)
    return {
        (str(e.source), str(e.target), e.flow.value, e.transform.value, e.phase.value)
        for e in result.edges
    }


# name -> (form A, form B). Both spellings compute the identical result.
EQUIVALENT: dict[str, tuple[str, str]] = {
    "alias renaming": (
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, SUM(o.gross_amount)"
        " FROM stg_orders o WHERE o.currency='GBP' GROUP BY o.cust_id;",
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT zz.cust_id, SUM(zz.gross_amount)"
        " FROM stg_orders zz WHERE zz.currency='GBP' GROUP BY zz.cust_id;",
    ),
    "join reordering": (
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, o.gross_amount"
        " FROM stg_orders o JOIN stg_customer c ON c.cust_id=o.cust_id WHERE c.region='EU';",
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, o.gross_amount"
        " FROM stg_customer c JOIN stg_orders o ON o.cust_id=c.cust_id WHERE c.region='EU';",
    ),
    "schema qualification": (
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, o.gross_amount"
        " FROM stg_orders o WHERE o.currency='GBP';",
        "INSERT INTO public.fct_revenue (cust_id, net_amount) SELECT o.cust_id, o.gross_amount"
        " FROM public.stg_orders o WHERE o.currency='GBP';",
    ),
    # SELECT * expands from the dictionary, so this pair also asserts the expansion is
    # ordered and complete - a wrong order would bind values to the wrong columns silently.
    "SELECT * expansion": (
        "INSERT INTO gtt_stage (cust_id, period_month, amount, note)"
        " SELECT s.cust_id, s.period_month, s.amount, s.note FROM gtt_stage s WHERE s.cust_id > 0;",
        "INSERT INTO gtt_stage (cust_id, period_month, amount, note)"
        " SELECT s.* FROM gtt_stage s WHERE s.cust_id > 0;",
    ),
    "BETWEEN vs two comparisons": (
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id FROM stg_orders o"
        " WHERE o.gross_amount BETWEEN 1 AND 9;",
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id FROM stg_orders o"
        " WHERE o.gross_amount >= 1 AND o.gross_amount <= 9;",
    ),
    "NOT BETWEEN vs negated pair": (
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id FROM stg_orders o"
        " WHERE o.gross_amount NOT BETWEEN 1 AND 9;",
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id FROM stg_orders o"
        " WHERE o.gross_amount < 1 OR o.gross_amount > 9;",
    ),
    "IN list vs OR chain": (
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id FROM stg_orders o"
        " WHERE o.currency IN ('GBP','EUR');",
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id FROM stg_orders o"
        " WHERE o.currency='GBP' OR o.currency='EUR';",
    ),
    # The structural pair that matters most: a CTE and an inline derived table are the same
    # thing written two ways, and S4-01/S4-04/S4-05 are all defects that appeared only in
    # the CTE form.
    "CTE vs inline derived table": (
        """INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
           WITH scoped AS (
               SELECT o.cust_id AS cust_id,
                      date_trunc('month', o.order_date) AS period_month,
                      o.gross_amount - COALESCE(o.discount_amt, 0) AS net
                 FROM stg_orders o WHERE o.currency = 'GBP'),
                agg AS (
               SELECT s.cust_id AS cust_id, s.period_month AS period_month,
                      SUM(s.net) AS net_total, COUNT(*) AS order_count
                 FROM scoped s GROUP BY s.cust_id, s.period_month HAVING SUM(s.net) > 0)
           SELECT a.cust_id, a.period_month, a.net_total, a.order_count
             FROM agg a JOIN stg_customer c ON c.cust_id = a.cust_id
            WHERE c.status_code = 'A';""",
        """INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
           SELECT a.cust_id, a.period_month, a.net_total, a.order_count
             FROM (SELECT s.cust_id AS cust_id, s.period_month AS period_month,
                          SUM(s.net) AS net_total, COUNT(*) AS order_count
                     FROM (SELECT o.cust_id AS cust_id,
                                  date_trunc('month', o.order_date) AS period_month,
                                  o.gross_amount - COALESCE(o.discount_amt, 0) AS net
                             FROM stg_orders o WHERE o.currency = 'GBP') s
                    GROUP BY s.cust_id, s.period_month HAVING SUM(s.net) > 0) a
             JOIN stg_customer c ON c.cust_id = a.cust_id
            WHERE c.status_code = 'A';""",
    ),
    "CTE chain vs flattened": (
        """INSERT INTO fct_revenue (cust_id, net_amount)
           WITH a AS (SELECT o.cust_id AS cust_id, o.gross_amount AS amt
                        FROM stg_orders o WHERE o.currency='GBP'),
                b AS (SELECT a.cust_id AS cust_id, a.amt * 2 AS amt2 FROM a)
           SELECT b.cust_id, b.amt2 FROM b;""",
        "INSERT INTO fct_revenue (cust_id, net_amount)"
        " SELECT o.cust_id, o.gross_amount * 2 FROM stg_orders o WHERE o.currency='GBP';",
    ),
    "GROUP BY ordinal vs expression": (
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, SUM(o.gross_amount)"
        " FROM stg_orders o GROUP BY o.cust_id;",
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, SUM(o.gross_amount)"
        " FROM stg_orders o GROUP BY 1;",
    ),
    "GROUP BY output alias": (
        "INSERT INTO fct_revenue (period_month, net_amount)"
        " SELECT date_trunc('month', o.order_date), SUM(o.gross_amount) FROM stg_orders o"
        " GROUP BY date_trunc('month', o.order_date);",
        "INSERT INTO fct_revenue (period_month, net_amount)"
        " SELECT date_trunc('month', o.order_date) AS pm, SUM(o.gross_amount) FROM stg_orders o"
        " GROUP BY pm;",
    ),
    "HAVING on output alias": (
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, SUM(o.gross_amount)"
        " FROM stg_orders o GROUP BY o.cust_id HAVING SUM(o.gross_amount) > 0;",
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, SUM(o.gross_amount) AS tot"
        " FROM stg_orders o GROUP BY o.cust_id HAVING tot > 0;",
    ),
    "unqualified column references": (
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, o.gross_amount"
        " FROM stg_orders o WHERE o.currency='GBP';",
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT cust_id, gross_amount"
        " FROM stg_orders WHERE currency='GBP';",
    ),
    # Lower-case quoted identifiers only. A MIXED-case quoted identifier is a DIFFERENT
    # object in PostgreSQL and the analyser does not yet distinguish them - recorded as
    # GL-002 and in docs/postgres_scope.md, and deliberately not asserted here.
    "quoted identifiers": (
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, o.gross_amount"
        " FROM stg_orders o WHERE o.currency='GBP';",
        'INSERT INTO "fct_revenue" (cust_id, net_amount) SELECT o."cust_id", o."gross_amount"'
        ' FROM "stg_orders" o WHERE o."currency"=\'GBP\';',
    ),
    "CAST vs :: operator": (
        "INSERT INTO fct_revenue (cust_id) SELECT CAST(o.cust_id AS int) FROM stg_orders o;",
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id::int FROM stg_orders o;",
    ),
    "UNION vs UNION ALL": (
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id FROM stg_orders o"
        " UNION ALL SELECT r.cust_id FROM stg_returns r;",
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id FROM stg_orders o"
        " UNION SELECT r.cust_id FROM stg_returns r;",
    ),
    "redundant subquery nesting": (
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT o.cust_id, o.gross_amount"
        " FROM stg_orders o WHERE o.currency='GBP';",
        "INSERT INTO fct_revenue (cust_id, net_amount) SELECT t.cust_id, t.amt FROM"
        " (SELECT * FROM (SELECT o.cust_id AS cust_id, o.gross_amount AS amt"
        " FROM stg_orders o WHERE o.currency='GBP') u) t;",
    ),
}


@pytest.mark.parametrize(
    ("name", "pair"),
    sorted(EQUIVALENT.items()),
    ids=[name for name, _ in sorted(EQUIVALENT.items())],
)
def test_equivalent_spellings_produce_identical_lineage(
    name: str, pair: tuple[str, str], book: Dictionary, config: AnalysisConfig
) -> None:
    first, second = (edges_of(sql, book, config) for sql in pair)
    # A pair that produced nothing on both sides would pass by agreeing about silence.
    assert first, f"{name}: form A produced no edges at all - the pair proves nothing"
    assert first == second, (
        f"{name}: two spellings of one query disagree.\n"
        f"  only in form A: {sorted(first - second)}\n"
        f"  only in form B: {sorted(second - first)}\n"
        "One of the two readings is wrong; no ground-truth key is needed to know that."
    )


INLINE_WINDOW = """INSERT INTO fct_cohort (region, running_revenue)
    SELECT c.region, SUM(c.cust_id) OVER (PARTITION BY c.region ORDER BY c.signup_date)
      FROM stg_customer c;"""

NAMED_WINDOW = """INSERT INTO fct_cohort (region, running_revenue)
    SELECT c.region, SUM(c.cust_id) OVER w
      FROM stg_customer c WINDOW w AS (PARTITION BY c.region ORDER BY c.signup_date);"""

SHARED_WINDOW = """INSERT INTO fct_cohort (region, rank_in_region, running_revenue)
    SELECT c.region, RANK() OVER w, SUM(c.cust_id) OVER w
      FROM stg_customer c WINDOW w AS (PARTITION BY c.region ORDER BY c.signup_date);"""


def test_a_named_window_keeps_its_partition_and_order(
    book: Dictionary, config: AnalysisConfig
) -> None:
    """PG-14, fixed 2026-09-23 by `band0._resolve_named_windows`.

    `OVER (PARTITION BY ...)` and `OVER w ... WINDOW w AS (PARTITION BY ...)` are two
    spellings of one query. Before the fix the named form lost BOTH influence edges with no
    refusal and no boundary, at 100% parse coverage.
    """
    assert edges_of(INLINE_WINDOW, book, config) == edges_of(NAMED_WINDOW, book, config)


def test_a_shared_named_window_reaches_every_function_that_uses_it(
    book: Dictionary, config: AnalysisConfig
) -> None:
    """The form the construct exists FOR, and the one the defect hurt most.

    A named window is written to be SHARED. Before the fix `RANK() OVER w` produced no edge
    of any kind - no value (correct, RANK takes no argument) and no influence (wrong) - so
    `rank_in_region` left the lineage entirely, while `SUM(x) OVER w` kept its value edge
    beside it and made the output look populated.

    Asserted per target column rather than as a total, so a regression names which of the
    two functions lost its window.
    """
    edges = edges_of(SHARED_WINDOW, book, config)

    for target in ("public.fct_cohort.rank_in_region", "public.fct_cohort.running_revenue"):
        influence = {
            e[0] for e in edges if e[1] == f"column:{target}" and e[2] == "influence"
        }
        assert influence == {
            "column:public.stg_customer.region",
            "column:public.stg_customer.signup_date",
        }, f"{target} lost its window's PARTITION BY / ORDER BY: {sorted(influence)}"

    # RANK() takes no argument, so it must have influence and NO value - the distinction
    # the whole of D-4 rests on, and the one an over-eager fix would collapse.
    assert not [
        e for e in edges if e[1] == "column:public.fct_cohort.rank_in_region" and e[2] == "value"
    ], "RANK() takes no argument; a value edge into rank_in_region is invented"


def test_a_named_window_is_resolved_through_a_derived_table(
    book: Dictionary, config: AnalysisConfig
) -> None:
    """A window name is local to its own select, and resolution walks outwards from there.

    This is the case that would pass vacuously if the fix had simply scanned the whole
    statement for any `WINDOW` clause: the definition sits in an inner select, and the use
    site must find it there rather than by luck.
    """
    nested = """INSERT INTO fct_cohort (region, rank_in_region)
        SELECT t.region, t.r FROM (
            SELECT c.region AS region, RANK() OVER w AS r
              FROM stg_customer c WINDOW w AS (PARTITION BY c.signup_date)) t;"""

    assert (
        "column:public.stg_customer.signup_date",
        "column:public.fct_cohort.rank_in_region",
        "influence",
        "identity",
        "pre-aggregation",
    ) in edges_of(nested, book, config)


def test_filter_clause_and_case_inside_an_aggregate_agree(
    book: Dictionary, config: AnalysisConfig
) -> None:
    """PG-15, decided as D-10 on 2026-10-01 and implemented: FILTER reads as CASE.

    `SUM(x) FILTER (WHERE c)` and `SUM(CASE WHEN c THEN x END)` compute the identical
    number. The FILTER spelling used to make `c` a filter on the RELATION while the CASE
    spelling made it a value of the COLUMN - a model answering a question about syntax.
    D-10 took the column-targeted reading: a relation-targeted filter cannot express that
    the restriction applies to one aggregate in the select list and not to its neighbour.

    This was a conditional xfail while the decision was open. It is now a plain assertion,
    so the two spellings cannot drift apart again unnoticed.
    """
    with_filter = (
        "INSERT INTO fct_revenue (cust_id, net_amount)"
        " SELECT o.cust_id, SUM(o.gross_amount) FILTER (WHERE o.currency='GBP')"
        " FROM stg_orders o GROUP BY o.cust_id;"
    )
    with_case = (
        "INSERT INTO fct_revenue (cust_id, net_amount)"
        " SELECT o.cust_id, SUM(CASE WHEN o.currency='GBP' THEN o.gross_amount END)"
        " FROM stg_orders o GROUP BY o.cust_id;"
    )
    assert edges_of(with_filter, book, config) == edges_of(with_case, book, config)
    assert any(
        "currency" in str(edge) and "net_amount" in str(edge)
        for edge in edges_of(with_filter, book, config)
    ), "equal by both losing the FILTER column is not agreement"
