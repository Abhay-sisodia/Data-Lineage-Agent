"""Every form of GROUP BY is a GROUP BY.

Stress finding S3-01, found by stress package 3 on 2026-09-12.

D-2 makes a `GROUP BY` `influence` onto every aggregated output column: the grouping
decides the value of `SUM(amount)` while supplying none of it. `_grouping_influence`
implemented that by reading `group.expressions` - and SQLGlot files the extended forms
under their own args:

    GROUP BY a, b                  -> expressions=[a, b]
    GROUP BY ROLLUP (a, b)         -> rollup=[...],        expressions=[]
    GROUP BY CUBE (a, b)           -> cube=[...],          expressions=[]
    GROUP BY GROUPING SETS (...)   -> grouping_sets=[...], expressions=[]

So D-2 held for a plain `GROUP BY` - including one two CTE scopes down, which stress 3
confirmed - and produced NOTHING for the three forms a reporting warehouse actually uses.

THE MIXED FORM IS WHY THE FIX WALKS THE NODE. `GROUP BY a, ROLLUP (b, c)` fills both
`expressions` and `rollup`, so an arg-by-arg version that missed one would emit a PARTIAL
grouping. A partial answer is worse than none here, because it looks complete - and S2-13
is already on the register for exactly that: a uniform rule applied to the first instance
only.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["ID", "AMT", "CAT", "REGION"],
    f"{SCHEMA}.TGT": ["ID", "AMT", "N"],
}


@pytest.fixture
def dictionary() -> Dictionary:
    return Dictionary(
        captured_at="2026-09-12T00:00:00Z",
        default_schema=SCHEMA,
        objects={
            name: {"owner": SCHEMA, "name": name.split(".")[1], "object_type": "TABLE"}
            for name in COLUMNS
        },
        columns=COLUMNS,
        temporary={name: False for name in COLUMNS},
    )


def _procedure(group_by: str) -> str:
    return (
        "CREATE OR REPLACE PROCEDURE p IS\n"
        "BEGIN\n"
        "  INSERT INTO tgt (id, amt)\n"
        "  SELECT s.cat, SUM(s.amt) FROM src s\n"
        f"  {group_by};\n"
        "END;\n/\n"
    )


def _influence_on_the_aggregate(source: str, dictionary: Dictionary) -> set[str]:
    result = analyse_source(source, dictionary)
    return {
        edge.source.name
        for edge in result.edges
        if edge.flow is Flow.INFLUENCE and edge.target.name == "TGT.AMT"
    }


@pytest.mark.parametrize(
    "group_by",
    [
        "GROUP BY s.cat, s.region",
        "GROUP BY ROLLUP (s.cat, s.region)",
        "GROUP BY CUBE (s.cat, s.region)",
        "GROUP BY GROUPING SETS ((s.cat), (s.region))",
        "GROUP BY s.cat, ROLLUP (s.region)",
    ],
    ids=["plain", "rollup", "cube", "grouping_sets", "mixed"],
)
def test_every_grouping_form_influences_the_aggregate(
    group_by: str, dictionary: Dictionary
) -> None:
    """All five forms group by the same two columns, so all five must say so.

    The plain case is parametrised alongside the others rather than left implicit: it is
    the control that says a failure here is about the FORM and not about D-2.
    """
    assert _influence_on_the_aggregate(_procedure(group_by), dictionary) == {
        "SRC.CAT",
        "SRC.REGION",
    }


def test_the_mixed_form_is_not_partially_grouped(dictionary: Dictionary) -> None:
    """The case that decided the shape of the fix.

    `GROUP BY s.cat, ROLLUP (s.region)` fills `expressions` AND `rollup`. Before the fix
    this emitted SRC.CAT alone - one of two grouping columns, with nothing anywhere saying
    the answer was half of one. A partial grouping claims `SUM(amt)` is governed by less
    than it is, and reads as a complete answer.
    """
    mixed = _influence_on_the_aggregate(_procedure("GROUP BY s.cat, ROLLUP (s.region)"), dictionary)

    assert "SRC.CAT" in mixed
    assert "SRC.REGION" in mixed, "the ROLLUP half was dropped and the answer looked whole"


def test_a_grouping_key_copied_through_still_takes_no_influence(
    dictionary: Dictionary,
) -> None:
    """D-2's exclusion must survive the wider walk - the fix must not over-emit.

    `s.cat` is a grouping key projected into `tgt.id` unchanged, so influence onto it
    would say a column decides its own value. Tested under ROLLUP specifically, because
    that is the path this fix opened.
    """
    result = analyse_source(_procedure("GROUP BY ROLLUP (s.cat, s.region)"), dictionary)

    onto_the_copied_key = {
        edge.source.name
        for edge in result.edges
        if edge.flow is Flow.INFLUENCE and edge.target.name == "TGT.ID"
    }
    assert onto_the_copied_key == set(), (
        f"influence emitted onto a grouping key copied through unchanged: {onto_the_copied_key}"
    )
