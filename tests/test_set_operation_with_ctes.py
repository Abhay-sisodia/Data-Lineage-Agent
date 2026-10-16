"""The CTEs belong to the set operation, not to either arm.

Stress finding S4-01, found by stress package 4 on 2026-09-12.

    INSERT INTO t
    WITH w AS (...)
    SELECT ... FROM w
    UNION ALL
    SELECT ... FROM base

SQLGlot hangs the `WITH` on the `Union`, not on either arm. `_analyse_insert` then hands
each arm to `_analyse_select_into` on its own - detached from it - so `FROM w` resolved to
a bare `Table` that is in no dictionary, and **the whole statement produced nothing**: no
refusal, no explanatory boundary, and parse coverage counting it as analysed.

S1-02 fixed the top-level set operation under `INSERT` and this is the half it could not
see. Its case selects straight from base tables, so the arms needed no CTE to be visible.

**A set operation over aggregated CTEs is the normal way to write a reconciliation** - "what
we sold minus what came back", each side grouped to its own grain - which is why stress 4
hit this on its first try and three earlier packages never did.

WHY THIS RANKED ABOVE THE OTHER STRESS-4 FINDINGS BUT ONE: twelve edges vanished with
nothing anywhere saying so. A refusal loses the same edges and says it did; this was the
category the product exists to prevent.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["SID", "AMT", "CAT"],
    f"{SCHEMA}.OTHER": ["SID", "AMT"],
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


# One arm reads a CTE, the other a base table, so a failure says which side broke.
UNION_WITH_CTE = _procedure("""
  INSERT INTO tgt (sid, amt)
  WITH grouped AS (
    SELECT s.sid AS sid, SUM(s.amt) AS total FROM src s GROUP BY s.sid
  )
  SELECT m.sid, m.total FROM grouped m
  UNION ALL
  SELECT o.sid, o.amt FROM other o;""")

# The S1-02 shape, which worked all along: no CTE anywhere.
UNION_WITHOUT_CTE = _procedure("""
  INSERT INTO tgt (sid, amt)
  SELECT s.sid, s.amt FROM src s
  UNION ALL
  SELECT o.sid, o.amt FROM other o;""")

# MINUS, where the second arm contributes filter rather than value.
MINUS_WITH_CTE = _procedure("""
  INSERT INTO tgt (sid, amt)
  WITH kept AS (SELECT s.sid AS sid, s.amt AS amt FROM src s),
       dropped AS (SELECT o.sid AS sid, o.amt AS amt FROM other o)
  SELECT a.sid, a.amt FROM kept a
  MINUS
  SELECT b.sid, b.amt FROM dropped b;""")


def _edges(source: str, dictionary: Dictionary) -> set[tuple[str, str, str]]:
    return {
        (e.source.name, e.target.name, e.flow.value)
        for e in analyse_source(source, dictionary).edges
    }


def test_both_arms_are_read_when_one_of_them_is_a_cte(dictionary: Dictionary) -> None:
    """Without the fix this statement yields ZERO edges - both arms, not just the CTE one."""
    edges = _edges(UNION_WITH_CTE, dictionary)

    assert edges, "the whole statement produced nothing"
    assert ("SRC.SID", "TGT.SID", Flow.VALUE.value) in edges, "the CTE arm was lost"
    assert ("SRC.AMT", "TGT.AMT", Flow.VALUE.value) in edges
    assert ("OTHER.SID", "TGT.SID", Flow.VALUE.value) in edges, "the base-table arm was lost"


def test_the_cte_arms_aggregation_survives(dictionary: Dictionary) -> None:
    """Resolving the CTE is only useful if what happened inside it comes with the edge.

    `SUM(s.amt)` is inside the CTE. An arm that resolved the relation but flattened the
    transform would score as recall while reporting a copy where there is an aggregate -
    a wrong transform, which ADR-0001 §4 counts as a miss on both sides.
    """
    aggregated = {
        (e.source.name, e.target.name)
        for e in analyse_source(UNION_WITH_CTE, dictionary).edges
        if e.transform.value == "aggregated"
    }
    assert ("SRC.AMT", "TGT.AMT") in aggregated


def test_the_form_without_a_cte_is_unaffected(dictionary: Dictionary) -> None:
    """The S1-02 shape is the control: it worked before this fix and must still work.

    Its own test case is why S1-02 looked complete - the arms select from base tables, so
    nothing needed to be inherited. Kept here so a future failure says whether the CTE
    handling or the set-operation handling regressed.
    """
    edges = _edges(UNION_WITHOUT_CTE, dictionary)

    assert ("SRC.SID", "TGT.SID", Flow.VALUE.value) in edges
    assert ("OTHER.SID", "TGT.SID", Flow.VALUE.value) in edges


def test_a_minus_over_ctes_keeps_its_arms_distinct(dictionary: Dictionary) -> None:
    """Inheriting the WITH must not flatten the difference between the arms.

    A `MINUS`'s second arm decides which rows survive and supplies none of the value, so it
    contributes filter influence on the relation. If attaching the shared `WITH` made both
    arms look alike, the excluded arm would start claiming value edges - which for a MINUS
    means claiming a value for rows that were specifically removed.
    """
    result = analyse_source(MINUS_WITH_CTE, dictionary)
    value_sources = {e.source.name for e in result.edges if e.flow is Flow.VALUE}

    assert "SRC.AMT" in value_sources, "the kept arm lost its value edge"
    assert "OTHER.AMT" not in value_sources, (
        "the excluded arm claimed a value edge - a value for rows that were removed"
    )
