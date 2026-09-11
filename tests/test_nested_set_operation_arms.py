"""An INTERSECT or MINUS arm constrains wherever it is written, not only at the top.

Stress finding S4-04, found by stress package 4 on 2026-09-12.

Stress 2 proposed convention (a): the second arm of an `INTERSECT`/`MINUS` decides which
rows survive and supplies none of the value, so it contributes FILTER influence on the
written relation rather than value edges. **It was implemented for one shape only.**

`_analyse_insert` computes the arms' roles over EXPRESSIONS via `_set_operation_arms`, so a
set operation at the top level of an `INSERT` was handled. Written inside a CTE, the same
`MINUS` is reached through `_trace` instead, and `_flatten_arms` returns the arms having
**thrown away which operator joined them** - so a constraining arm arrived
indistinguishable from a contributing one and every arm was traced as a value source.

WHAT THAT CLAIMED IS THE WORST PART. A value edge from a `MINUS`'s second arm says that arm
supplied a value to the target - **for rows it specifically removed**. `_as_filter_influence`
has said so in its docstring since stress 2; the nested path just never reached it.

`_arm_roles` is `_set_operation_arms`' logic over scopes, deliberately the same shape:
`A UNION B MINUS C` is `Except(Union(A, B), C)`, so A and B supply values and C constrains,
however deep it is nested.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow, NodeKind
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["SID", "AMT"],
    f"{SCHEMA}.OTHER": ["SID", "AMT"],
    f"{SCHEMA}.THIRD": ["SID", "AMT"],
    f"{SCHEMA}.TGT": ["SID", "AMT"],
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


def _procedure(statement: str) -> str:
    return f"CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n{statement}\nEND;\n/\n"


MINUS_IN_A_CTE = _procedure("""
  INSERT INTO tgt (sid, amt)
  WITH kept AS (
    SELECT s.sid AS sid, s.amt AS amt FROM src s
    MINUS
    SELECT o.sid AS sid, o.amt AS amt FROM other o
  )
  SELECT k.sid, k.amt FROM kept k;""")

INTERSECT_IN_A_CTE = _procedure("""
  INSERT INTO tgt (sid, amt)
  WITH both_sides AS (
    SELECT s.sid AS sid, s.amt AS amt FROM src s
    INTERSECT
    SELECT o.sid AS sid, o.amt AS amt FROM other o
  )
  SELECT b.sid, b.amt FROM both_sides b;""")

UNION_IN_A_CTE = _procedure("""
  INSERT INTO tgt (sid, amt)
  WITH combined AS (
    SELECT s.sid AS sid, s.amt AS amt FROM src s
    UNION ALL
    SELECT o.sid AS sid, o.amt AS amt FROM other o
  )
  SELECT c.sid, c.amt FROM combined c;""")

# `A UNION ALL B MINUS C` - two contributing arms and one constraining one, nested.
MIXED_IN_A_CTE = _procedure("""
  INSERT INTO tgt (sid, amt)
  WITH mixed AS (
    SELECT s.sid AS sid, s.amt AS amt FROM src s
    UNION ALL
    SELECT o.sid AS sid, o.amt AS amt FROM other o
    MINUS
    SELECT d.sid AS sid, d.amt AS amt FROM third d
  )
  SELECT m.sid, m.amt FROM mixed m;""")


def _by_flow(source: str, dictionary: Dictionary, flow: Flow) -> set[str]:
    return {
        e.source.name for e in analyse_source(source, dictionary).edges if e.flow is flow
    }


def test_a_nested_minus_arm_supplies_no_value(dictionary: Dictionary) -> None:
    """Without the fix OTHER.AMT arrives as a value source of TGT.AMT.

    That edge says the excluded arm supplied a value to the target, for rows the MINUS
    removed. It is the false-positive half and the one that matters.
    """
    values = _by_flow(MINUS_IN_A_CTE, dictionary, Flow.VALUE)

    assert "SRC.AMT" in values, "the contributing arm lost its value edge"
    assert "OTHER.AMT" not in values, "the excluded arm claimed a value it cannot supply"
    assert "OTHER.SID" not in values


def test_a_nested_minus_arm_is_still_reported_as_filter(dictionary: Dictionary) -> None:
    """Dropping it would be the easy half of the fix and only half an answer.

    The excluded arm IS a real dependency of what ends up in the table - which rows survive
    depends on it entirely. Reporting nothing would trade a wrong edge for a silent one.
    """
    result = analyse_source(MINUS_IN_A_CTE, dictionary)
    filters = {
        e.source.name
        for e in result.edges
        if e.flow is Flow.FILTER and e.target.kind is NodeKind.RELATION
    }

    assert "OTHER.SID" in filters or "OTHER.AMT" in filters, (
        "the excluded arm vanished instead of being restated as filter influence"
    )


def test_a_nested_intersect_behaves_the_same_way(dictionary: Dictionary) -> None:
    """Not MINUS-specific. Stress 2 recorded that INTERSECT and MINUS failed identically."""
    values = _by_flow(INTERSECT_IN_A_CTE, dictionary, Flow.VALUE)

    assert "SRC.AMT" in values
    assert "OTHER.AMT" not in values


def test_a_nested_union_arm_still_supplies_value(dictionary: Dictionary) -> None:
    """The control, and the way this fix could have been worse than the defect.

    A `UNION` arm genuinely contributes: both arms feed the same output positions. Treating
    every second arm as constraining would silently halve the feeds of every UNION in the
    estate - S1-01's "destroyed facts" in a new costume.
    """
    values = _by_flow(UNION_IN_A_CTE, dictionary, Flow.VALUE)

    assert "SRC.AMT" in values
    assert "OTHER.AMT" in values, "a UNION arm was misread as constraining"


def test_the_mixed_chain_assigns_roles_per_arm(dictionary: Dictionary) -> None:
    """`A UNION ALL B MINUS C` parses as Except(Union(A, B), C).

    So A and B contribute and C constrains. This is the case a flat "second arm
    constrains" rule gets wrong, and the reason `_arm_roles` recurses rather than indexing.
    """
    values = _by_flow(MIXED_IN_A_CTE, dictionary, Flow.VALUE)

    assert "SRC.AMT" in values, "first UNION arm lost"
    assert "OTHER.AMT" in values, "second UNION arm lost - it contributes, it does not constrain"
    assert "THIRD.AMT" not in values, "the MINUS arm claimed a value"
