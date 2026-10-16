"""A statement read into a variable still has predicates and a grouping.

Stress finding S4-09, found by stress package 4 on 2026-09-12 and filed as "a declared
cursor's own WHERE produces no filter edge against what the loop writes". **That
description named the wrong construct.** The cursor's `WHERE` is the half still open; what
this closes is wider and has nothing to do with cursors:

    SELECT g.cust_id, g.total BULK COLLECT INTO c1, c2
      FROM (SELECT r.cust_id, SUM(r.net_amount) AS total
              FROM fct_revenue r JOIN dim_customer d ON d.cust_id = r.cust_id
             WHERE d.is_active = 1
             GROUP BY r.cust_id
            HAVING SUM(r.net_amount) > 0) g

`_analyse_select_into` had its own one-level filter walk: ONE top-level `WHERE`, with a
target from `_read_relation`, which returns `None` the moment the `FROM` is a subquery. So
this statement reported no `HAVING`, no `GROUP BY` influence, and not even the inner
`WHERE` - while the IDENTICAL QUERY WRITING A TABLE produced all three. The S2-04 shape
again: a construct that each pass correctly decides is not quite its own.

Delegated to `band0.resolve_query_influence`, which is the third time this handoff has been
needed - S4-02 for a cursor record's field, S4-05 for a projection computed in a derived
table, and now for the clauses that decide which rows are read at all.

WHICH RELATION THE FILTER EDGE TARGETS IS THE PART THIS TEST GUARDS MOST CAREFULLY,
because I implemented it wrong, corrected it to a second wrong answer, and only the stress
run distinguished them. See `test_the_filter_targets_the_filtered_columns_own_relation`.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import FilterPhase, Flow, NodeKind
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["SID", "AMT", "REGION"],
    f"{SCHEMA}.DIM": ["SID", "IS_ACTIVE"],
    f"{SCHEMA}.TGT": ["SID", "AMT"],
}


@pytest.fixture
def dictionary() -> Dictionary:
    return Dictionary(
        captured_at="2026-09-13T00:00:00Z",
        default_schema=SCHEMA,
        objects={
            name: {"owner": SCHEMA, "name": name.split(".")[1], "object_type": "TABLE"}
            for name in COLUMNS
        },
        columns=COLUMNS,
        temporary={name: False for name in COLUMNS},
    )


BULK_GROUPED = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  l_ids t;
  l_amts t;
BEGIN
  SELECT g.sid, g.total BULK COLLECT INTO l_ids, l_amts
    FROM (SELECT s.sid AS sid, SUM(s.amt) AS total
            FROM src s
           WHERE s.region = 'EU'
           GROUP BY s.sid
          HAVING SUM(s.amt) > 0) g;
END;
/
"""

SCALAR_GROUPED = """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
BEGIN
  SELECT g.total INTO v
    FROM (SELECT SUM(s.amt) AS total FROM src s GROUP BY s.sid HAVING SUM(s.amt) > 0) g
   WHERE ROWNUM = 1;
END;
/
"""

# The control: the same query writing a TABLE. Band 0's path, which always worked.
INSERT_GROUPED = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  INSERT INTO tgt (sid, amt)
  SELECT g.sid, g.total
    FROM (SELECT s.sid AS sid, SUM(s.amt) AS total
            FROM src s
           WHERE s.region = 'EU'
           GROUP BY s.sid
          HAVING SUM(s.amt) > 0) g;
END;
/
"""


def _filters(source: str, dictionary: Dictionary) -> set[tuple[str, str, str]]:
    return {
        (e.source.name, e.target.name, e.phase.value)
        for e in analyse_source(source, dictionary).edges
        if e.flow is Flow.FILTER and e.source.kind is NodeKind.COLUMN
    }


def _influence(source: str, dictionary: Dictionary) -> set[tuple[str, str]]:
    return {
        (e.source.name, e.target.name)
        for e in analyse_source(source, dictionary).edges
        if e.flow is Flow.INFLUENCE
    }


def test_a_having_inside_a_bulk_collect_is_a_post_aggregation_filter(
    dictionary: Dictionary,
) -> None:
    """Without the fix this statement emits no filter edge at all."""
    assert ("SRC.AMT", "SRC", FilterPhase.POST_AGGREGATION.value) in _filters(
        BULK_GROUPED, dictionary
    )


def test_a_where_one_level_down_is_a_pre_aggregation_filter(dictionary: Dictionary) -> None:
    """Not only the clause that was missing - the clause that was already handled, nested.

    `_read_relation` returns None as soon as the FROM is a subquery, so the local walk lost
    the `WHERE` too. D-2's whole point is that these two clauses are distinguishable, and a
    fix that found the HAVING while still missing a nested WHERE would satisfy the finding
    as filed and leave the commoner case broken.
    """
    assert ("SRC.REGION", "SRC", FilterPhase.PRE_AGGREGATION.value) in _filters(
        BULK_GROUPED, dictionary
    )


def test_a_group_by_influences_the_variable_that_receives_the_aggregate(
    dictionary: Dictionary,
) -> None:
    """D-2's influence half, onto a variable rather than a column.

    The variable stands exactly where D-2 puts the aggregated column: change the grouping
    and `l_amts` holds different numbers, while no part of `sid` is in any of them.
    """
    assert ("SRC.SID", "L_AMTS") in _influence(BULK_GROUPED, dictionary)


def test_a_grouping_key_read_into_a_variable_takes_no_influence(
    dictionary: Dictionary,
) -> None:
    """D-2's exclusion has to survive being reached from a new call site.

    `l_ids` receives the grouping key itself, copied through unchanged, so influence onto it
    would say a column decides its own value. S3-08 is on the register for getting this
    exclusion wrong once, and S4-03 had to check it again for the MERGE path.
    """
    assert ("SRC.SID", "L_IDS") not in _influence(BULK_GROUPED, dictionary)


def test_a_scalar_select_into_gets_the_same_treatment(dictionary: Dictionary) -> None:
    """Not BULK-specific: both forms share the code path, so both share the defect."""
    assert ("SRC.AMT", "SRC", FilterPhase.POST_AGGREGATION.value) in _filters(
        SCALAR_GROUPED, dictionary
    )
    assert ("SRC.SID", "V") in _influence(SCALAR_GROUPED, dictionary)


def test_the_filter_targets_the_filtered_columns_own_relation(dictionary: Dictionary) -> None:
    """The convention, and the assertion this whole file exists to pin.

    A filter edge targets the relation the statement WRITES; when it writes only variables,
    the relation READ - which is the relation the filtered column belongs to.

    I GOT THIS WRONG TWICE. First as written, then "corrected" to the relations the
    statement's VALUES come from, on the strength of band-0 claims where
    `DIM_CUSTOMER.IS_ACTIVE` targets `FCT_REVENUE`, `GTT_STAGE` and `TMP_RECENT` - but those
    statements WRITE a relation, so they are the convention's first clause and say nothing
    about this one.

    This is the shape that separates the two readings: values come from SRC, the predicate
    is on DIM, and the answer is DIM alone. Ten band-1 claims across three packages agree,
    and `fn_stress_net` in stress 1 is the live instance - it reads values from two
    relations through a join and its key claims exactly one filter edge. The
    value-relations reading scored a true positive and a false one off a single predicate.
    """
    source = """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
BEGIN
  SELECT SUM(s.amt) INTO v
    FROM src s
    JOIN dim d ON d.sid = s.sid
   WHERE d.is_active = 1;
END;
/
"""
    filters = _filters(source, dictionary)

    assert ("DIM.IS_ACTIVE", "DIM", FilterPhase.PRE_AGGREGATION.value) in filters
    assert not [f for f in filters if f[0] == "DIM.IS_ACTIVE" and f[1] == "SRC"], (
        "the filter edge was pointed at the relation the VALUES come from"
    )


def test_a_join_condition_is_still_structural(dictionary: Dictionary) -> None:
    """D-5, reached through a new call site.

    `ON d.sid = s.sid` connects the tables so the predicate can be evaluated; it decides no
    rows. The delegation reuses `predicates.py`, so this holds by construction rather than
    by a second implementation - but S2-14 and S3-09 are both on the register for this rule
    going wrong at a call site that looked unrelated.
    """
    source = """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
BEGIN
  SELECT SUM(s.amt) INTO v FROM src s JOIN dim d ON d.sid = s.sid;
END;
/
"""
    assert _filters(source, dictionary) == set()


def test_a_variable_in_the_top_level_predicate_still_produces_its_edge(
    dictionary: Dictionary,
) -> None:
    """The control for the half that was NOT replaced.

    `WHERE sid = v_cutoff` is the reason band 1 exists, and only this module can tell that
    `v_cutoff` is a variable rather than a column. The local walk stays for that; if it had
    been replaced by the delegation, band 0 would have bound `v_cutoff` to whichever table
    was in scope - the fictional edge this module's docstring is about.
    """
    source = """CREATE OR REPLACE PROCEDURE p IS
  v_cutoff NUMBER := 1;
  v NUMBER;
BEGIN
  SELECT s.amt INTO v FROM src s WHERE s.sid = v_cutoff;
END;
/
"""
    variables = {
        e.source.name
        for e in analyse_source(source, dictionary).edges
        if e.flow is Flow.FILTER and e.source.kind is NodeKind.VARIABLE
    }
    assert "V_CUTOFF" in variables


def test_the_table_writing_form_is_unchanged(dictionary: Dictionary) -> None:
    """Band 0's answer to the same query is the specification this fix was measured against.

    Kept as an executable statement of that: the two paths now agree about the clauses, and
    the only difference is the target - a table where the other has a variable.
    """
    filters = _filters(INSERT_GROUPED, dictionary)

    assert ("SRC.AMT", "TGT", FilterPhase.POST_AGGREGATION.value) in filters
    assert ("SRC.REGION", "TGT", FilterPhase.PRE_AGGREGATION.value) in filters
    assert ("SRC.SID", "TGT.AMT") in _influence(INSERT_GROUPED, dictionary)
