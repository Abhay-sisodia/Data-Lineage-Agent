"""`INSERT ALL` / `INSERT FIRST` — one SELECT fanning into several tables.

Stress finding S2-02, open since 2026-09-09 and closed 2026-09-14.

The statement was refused as *"unsupported statement type MultitableInserts"*, costing seven
labelled edges. **The refusal was accurate and was read as something it did not say.**
"Unsupported" was taken to mean "unparseable" for the whole life of the entry - by me, in
every summary table beside S2-01 - until the parser probe handed SQLGlot the statement and it
parsed cleanly. There was never a parser problem: `MultitableInserts` is a node we are given
and the analyser declined to walk it.

Two existing delegations do the work, which is why this cost no new traversal:

* `resolve_projection` (public for S4-02) binds each `VALUES` item. An arm's values name the
  SOURCE QUERY'S OUTPUT COLUMNS - `VALUES (cust_id, period_month, gross_amount)` are select
  list aliases, not base columns - so each one is exactly the question that function answers.
* `resolve_query_influence` (public for S4-09) gives the source's own predicates and
  grouping, because the source is an ordinary query.

THE WHEN IS A GUARD, NOT A FILTER EDGE. `gross_amount` deciding which of two tables a row
lands in is a condition on the edge, and `guard` is the field for that; emitting it as filter
influence as well would report one fact twice in two mechanisms. The source `WHERE` is the
opposite case - evaluated once, before any `WHEN` - so its edges are unguarded and reach
every target.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.harness.scoring import normalise_guard
from lineage.ir.model import Flow
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.STG_ORDERS": ["CUST_ID", "ORDER_DATE", "GROSS_AMOUNT", "CURRENCY"],
    f"{SCHEMA}.FCT_REVENUE_STAGE": ["CUST_ID", "PERIOD_MONTH", "NET_AMOUNT", "ORDER_COUNT"],
    f"{SCHEMA}.GTT_STAGE": ["CUST_ID", "PERIOD_MONTH", "AMOUNT"],
}


@pytest.fixture
def dictionary() -> Dictionary:
    return Dictionary(
        captured_at="2026-09-14T00:00:00Z",
        default_schema=SCHEMA,
        objects={
            name: {"owner": SCHEMA, "name": name.split(".")[1], "object_type": "TABLE"}
            for name in COLUMNS
        },
        columns=COLUMNS,
        temporary={name: False for name in COLUMNS},
    )


def _procedure(body: str) -> str:
    return f"CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n{body}\nEND;\n/\n"


# The stress-2 unit verbatim in shape: two mutually exclusive WHENs, a derived projection,
# a literal, and a predicate on the source.
INSERT_ALL = _procedure("""
    INSERT ALL
        WHEN gross_amount > 100 THEN
            INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
            VALUES (cust_id, period_month, gross_amount, 1)
        WHEN gross_amount <= 100 THEN
            INTO gtt_stage (cust_id, period_month, amount)
            VALUES (cust_id, period_month, gross_amount)
    SELECT o.cust_id                  AS cust_id,
           TRUNC(o.order_date, 'MM')  AS period_month,
           o.gross_amount             AS gross_amount
      FROM stg_orders o
     WHERE o.currency = 'GBP';""")

INSERT_FIRST = _procedure("""
    INSERT FIRST
        WHEN gross_amount > 100 THEN
            INTO fct_revenue_stage (cust_id) VALUES (cust_id)
        WHEN gross_amount > 10 THEN
            INTO gtt_stage (cust_id) VALUES (cust_id)
    SELECT o.cust_id AS cust_id, o.gross_amount AS gross_amount FROM stg_orders o;""")

WITH_ELSE = _procedure("""
    INSERT ALL
        WHEN gross_amount > 100 THEN
            INTO fct_revenue_stage (cust_id) VALUES (cust_id)
        ELSE
            INTO gtt_stage (cust_id) VALUES (cust_id)
    SELECT o.cust_id AS cust_id, o.gross_amount AS gross_amount FROM stg_orders o;""")

UNCONDITIONAL = _procedure("""
    INSERT ALL
        INTO fct_revenue_stage (cust_id) VALUES (cust_id)
        INTO gtt_stage (cust_id) VALUES (cust_id)
    SELECT o.cust_id AS cust_id FROM stg_orders o;""")

GROUPED_SOURCE = _procedure("""
    INSERT ALL
        WHEN total > 0 THEN
            INTO fct_revenue_stage (cust_id, net_amount) VALUES (cust_id, total)
    SELECT o.cust_id AS cust_id, SUM(o.gross_amount) AS total
      FROM stg_orders o
     GROUP BY o.cust_id;""")


def _values(source: str, dictionary: Dictionary) -> set[tuple[str, str, str, str]]:
    return {
        (e.source.name, e.target.name, e.transform.value, normalise_guard(e.guard) or "")
        for e in analyse_source(source, dictionary).edges
        if e.flow is Flow.VALUE
    }


def _filters(source: str, dictionary: Dictionary) -> set[tuple[str, str, str]]:
    return {
        (e.source.name, e.target.name, normalise_guard(e.guard) or "")
        for e in analyse_source(source, dictionary).edges
        if e.flow is Flow.FILTER
    }


def test_every_arm_gets_its_value_edges(dictionary: Dictionary) -> None:
    """Without the fix the whole statement is refused and produces nothing.

    Each value is bound POSITIONALLY against that arm's own column list, and each name is
    resolved through the source query's select list rather than treated as a base column.
    """
    values = _values(INSERT_ALL, dictionary)

    assert (
        "STG_ORDERS.CUST_ID",
        "FCT_REVENUE_STAGE.CUST_ID",
        "identity",
        normalise_guard("gross_amount > 100"),
    ) in values
    assert (
        "STG_ORDERS.GROSS_AMOUNT",
        "GTT_STAGE.AMOUNT",
        "identity",
        normalise_guard("gross_amount <= 100"),
    ) in values


def test_a_derived_projection_carries_its_transform_into_both_arms(
    dictionary: Dictionary,
) -> None:
    """`TRUNC(o.order_date, 'MM')` is derived in the SOURCE, and the arm copies it through.

    The arm's own item is a bare name, so reading only the arm would score `identity`. This
    is the transform combining `resolve_projection` returns for exactly this reason.
    """
    values = _values(INSERT_ALL, dictionary)

    assert (
        "STG_ORDERS.ORDER_DATE",
        "FCT_REVENUE_STAGE.PERIOD_MONTH",
        "derived",
        normalise_guard("gross_amount > 100"),
    ) in values
    assert (
        "STG_ORDERS.ORDER_DATE",
        "GTT_STAGE.PERIOD_MONTH",
        "derived",
        normalise_guard("gross_amount <= 100"),
    ) in values


def test_the_guard_distinguishes_the_two_targets(dictionary: Dictionary) -> None:
    """The point of the construct, and the thing an unguarded pair of edges would destroy.

    `gross_amount` reaches BOTH tables, under opposite conditions. Drop the guards and the
    IR says it always reaches both - which is not a missing fact but a false one.
    """
    guards = {
        (e.target.name, normalise_guard(e.guard))
        for e in analyse_source(INSERT_ALL, dictionary).edges
        if e.flow is Flow.VALUE and e.source.name == "STG_ORDERS.GROSS_AMOUNT"
    }

    assert guards == {
        ("FCT_REVENUE_STAGE.NET_AMOUNT", normalise_guard("gross_amount > 100")),
        ("GTT_STAGE.AMOUNT", normalise_guard("gross_amount <= 100")),
    }


def test_the_source_predicate_reaches_every_target_unguarded(dictionary: Dictionary) -> None:
    """It is evaluated once, before any WHEN, so all the arms draw from the rows it admits.

    Stress 2's key claimed this edge for the FIRST target only - logged as S2-15, and
    S2-13's shape again: a uniform rule applied to the first instance of it. Unguarded
    because the predicate is not conditional on any WHEN.
    """
    filters = _filters(INSERT_ALL, dictionary)

    assert ("STG_ORDERS.CURRENCY", "FCT_REVENUE_STAGE", "") in filters
    assert ("STG_ORDERS.CURRENCY", "GTT_STAGE", "") in filters


def test_a_literal_produces_no_edge(dictionary: Dictionary) -> None:
    """`VALUES (..., 1)` for ORDER_COUNT has no upstream to name.

    The literal rule, unchanged - and the guard against the easy wrong fix of binding every
    target column to something.
    """
    assert not [
        e for e in _values(INSERT_ALL, dictionary) if e[1] == "FCT_REVENUE_STAGE.ORDER_COUNT"
    ]


def test_insert_first_negates_the_preceding_conditions(dictionary: Dictionary) -> None:
    """`INSERT FIRST` is not `INSERT ALL` with different spelling.

    A row goes to the first matching arm only, so arm two fires on `NOT (c1) AND c2`. Here
    the two conditions OVERLAP - anything over 100 is also over 10 - so under ALL both arms
    would take those rows and under FIRST only the first does. The edges are identical
    either way and only the guard differs, which is exactly the kind of difference that can
    never show up as a false positive: no package contains an `INSERT FIRST`, so nothing
    would have caught it being treated as `ALL`.
    """
    guards = {
        (e.target.name, normalise_guard(e.guard))
        for e in analyse_source(INSERT_FIRST, dictionary).edges
        if e.flow is Flow.VALUE
    }

    assert ("FCT_REVENUE_STAGE.CUST_ID", normalise_guard("gross_amount > 100")) in guards
    assert (
        "GTT_STAGE.CUST_ID",
        normalise_guard("NOT (gross_amount > 100) AND gross_amount > 10"),
    ) in guards


def test_an_else_arm_is_guarded_by_the_negation_of_every_condition(
    dictionary: Dictionary,
) -> None:
    """`ELSE` fires when no `WHEN` matched, which is what its guard has to say."""
    guards = {
        (e.target.name, normalise_guard(e.guard))
        for e in analyse_source(WITH_ELSE, dictionary).edges
        if e.flow is Flow.VALUE
    }

    assert ("GTT_STAGE.CUST_ID", normalise_guard("NOT (gross_amount > 100)")) in guards


def test_an_unconditional_multitable_insert_is_unguarded(dictionary: Dictionary) -> None:
    """`INSERT ALL` with no `WHEN` at all sends every row to every target.

    The control for the guard machinery: a guard invented here would claim a condition the
    statement does not have.
    """
    values = _values(UNCONDITIONAL, dictionary)

    assert ("STG_ORDERS.CUST_ID", "FCT_REVENUE_STAGE.CUST_ID", "identity", "") in values
    assert ("STG_ORDERS.CUST_ID", "GTT_STAGE.CUST_ID", "identity", "") in values


def test_a_grouping_in_the_source_influences_the_column_that_receives_it(
    dictionary: Dictionary,
) -> None:
    """D-2 reaching a fifth statement type.

    The source's `GROUP BY` decides what `total` is, and `total` is written to
    `net_amount`, so the influence lands there. `cust_id` is a grouping key copied through
    and takes none - D-2's exclusion, applied by `_grouping_influence` itself rather than
    re-derived here, which is what S3-08 and S4-03 are on the register about.
    """
    influence = {
        (e.source.name, e.target.name)
        for e in analyse_source(GROUPED_SOURCE, dictionary).edges
        if e.flow is Flow.INFLUENCE
    }

    assert ("STG_ORDERS.CUST_ID", "FCT_REVENUE_STAGE.NET_AMOUNT") in influence
    assert ("STG_ORDERS.CUST_ID", "FCT_REVENUE_STAGE.CUST_ID") not in influence


def test_an_arm_without_a_column_list_is_declared_not_guessed(dictionary: Dictionary) -> None:
    """Positional binding with nothing to bind TO.

    The target's dictionary order would be a guess, and a wrong guess writes a value into
    the wrong column - a plausible, readable, false edge. Declared instead, which is the
    same refusal `defuse` makes for `INSERT ... VALUES` under D-1.
    """
    source = _procedure("""
    INSERT ALL
        WHEN gross_amount > 100 THEN
            INTO gtt_stage VALUES (cust_id, period_month, gross_amount)
    SELECT o.cust_id AS cust_id, o.order_date AS period_month,
           o.gross_amount AS gross_amount
      FROM stg_orders o;""")
    result = analyse_source(source, dictionary)

    assert not [e for e in result.edges if e.flow is Flow.VALUE], (
        "columns were bound positionally against a column order nobody stated"
    )
    assert result.boundaries, "the arm was skipped without declaring anything"


def test_the_statement_is_no_longer_refused(dictionary: Dictionary) -> None:
    """The finding's own terms: a counted, honest refusal that cost seven edges.

    Asserted on the boundary rather than only on the edges, because "analysed" and
    "produced edges" are different claims and parse coverage reads the first one.
    """
    refusals = [
        b
        for b in analyse_source(INSERT_ALL, dictionary).boundaries
        if "MultitableInserts" in (b.detail or "") or "MultitableInserts" in (b.subject or "")
    ]
    assert refusals == []


def test_targets_are_dictionary_resolved(dictionary: Dictionary) -> None:
    """Both arms name unqualified tables; both edges must carry resolved relation names.

    Cheap to assert and the kind of thing that silently produces two node namespaces for one
    table - which is S1-01's family of defect rather than a cosmetic one.
    """
    names = {e.target.name.split(".")[0] for e in analyse_source(INSERT_ALL, dictionary).edges}
    assert names <= {"FCT_REVENUE_STAGE", "GTT_STAGE"}
    assert {e.source.name.split(".")[0] for e in analyse_source(INSERT_ALL, dictionary).edges} == {
        "STG_ORDERS"
    }
