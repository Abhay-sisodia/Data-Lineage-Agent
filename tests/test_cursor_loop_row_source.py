"""A FOR loop over a DECLARED cursor has a row source too.

Stress finding S4-02, found by stress package 4 on 2026-09-12.

`_collect_row_sources` registered a record's row source only for the INLINE form:

    FOR rec IN (SELECT s.sid, s.amt FROM src s) LOOP   -- registered
    FOR rec IN c LOOP                                  -- registered NOTHING

So in the declared-cursor form `rec.field` was not a record field to anything, and fell
through to the generic column binding.

THE CONSEQUENCE WAS A WRONG EDGE, NOT A MISSING ONE, which is why it survived four stress
packages. With no row source, `INSERT INTO tgt (amt) VALUES (rec.amt)` bound `rec.amt` to
the only relation in scope - **the target** - and emitted `TGT.AMT -> TGT.AMT`: a self-edge
that reads exactly like a legitimate accumulator, sourced from a table the cursor never
mentioned. `RowSource`'s own docstring names this failure - *"binds `email` to whichever
table is in scope and reports a column as its own source"* - and it was happening in the
one loop form the collector did not cover.

**Both forms are ordinary Oracle, and the declared one is the more common in real code**,
because a named cursor is what you write when the query is long enough to deserve a name -
which is exactly when it is also deep enough to matter.

The two collectors also had to be reordered: `_collect_row_sources` now runs AFTER
`_collect_cursors`, because it resolves the loop through the cursor map.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["SID", "AMT", "CAT"],
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


DECLARED_CURSOR = """CREATE OR REPLACE PROCEDURE p IS
  CURSOR c IS SELECT s.sid, s.amt FROM src s;
BEGIN
  FOR rec IN c LOOP
    INSERT INTO tgt (sid, amt) VALUES (rec.sid, rec.amt);
  END LOOP;
END;
/
"""

INLINE_CURSOR = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  FOR rec IN (SELECT s.sid, s.amt FROM src s) LOOP
    INSERT INTO tgt (sid, amt) VALUES (rec.sid, rec.amt);
  END LOOP;
END;
/
"""


def _edges(source: str, dictionary: Dictionary) -> set[tuple[str, str, str]]:
    return {
        (e.source.name, e.target.name, e.flow.value)
        for e in analyse_source(source, dictionary).edges
    }


def test_a_declared_cursors_record_resolves_to_the_base_column(
    dictionary: Dictionary,
) -> None:
    """Without the fix these edges are absent and TGT.SID -> TGT.SID is there instead."""
    edges = _edges(DECLARED_CURSOR, dictionary)

    assert ("SRC.SID", "TGT.SID", Flow.VALUE.value) in edges
    assert ("SRC.AMT", "TGT.AMT", Flow.VALUE.value) in edges


def test_no_self_edge_is_invented_on_the_target(dictionary: Dictionary) -> None:
    """The half that matters, asserted as an explicit negative.

    `TGT.AMT -> TGT.AMT` is not a missing edge; it is a CONFIDENT WRONG ONE, and it is
    indistinguishable in the IR from the accumulator self-edges S3-07 exists to produce.
    A test that only checked for the correct edges would pass while this one still shipped.
    """
    edges = _edges(DECLARED_CURSOR, dictionary)

    assert ("TGT.SID", "TGT.SID", Flow.VALUE.value) not in edges, (
        "the record field was bound to the TARGET table and emitted as a self-edge"
    )
    assert ("TGT.AMT", "TGT.AMT", Flow.VALUE.value) not in edges


def test_the_declared_form_now_agrees_with_the_inline_form(dictionary: Dictionary) -> None:
    """The two spellings mean the same thing, so they must produce the same edges.

    Kept as its own test because the inline form worked all along: a failure here says
    which of the two regressed, and that is the first question anyone will ask.
    """
    assert _edges(DECLARED_CURSOR, dictionary) == _edges(INLINE_CURSOR, dictionary)


def test_a_plain_for_index_is_still_not_a_row_source(dictionary: Dictionary) -> None:
    """The reorder must not turn a numeric FOR index into a record.

    `FOR i IN 1 .. 10` has an index_name and no record, and the collector's third branch
    treats it as an ordinary local. Guarded because this fix added a branch between the
    record cases and that one.
    """
    source = """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
BEGIN
  FOR i IN 1 .. 10 LOOP
    v := i;
    INSERT INTO tgt (amt) VALUES (v);
  END LOOP;
END;
/
"""
    edges = _edges(source, dictionary)

    assert not [e for e in edges if e[0].startswith("SRC.")], (
        "a numeric loop index was resolved as if it were a cursor record"
    )

CURSOR_OVER_A_CTE_CHAIN = """CREATE OR REPLACE PROCEDURE p IS
  CURSOR c IS
    WITH first_level AS (
      SELECT s.sid AS sid, s.amt AS amt FROM src s WHERE s.cat <> 'X'
    ),
    second_level AS (
      SELECT f.sid AS sid, f.amt AS amt FROM first_level f
    )
    SELECT m.sid, m.amt FROM second_level m;
BEGIN
  FOR rec IN c LOOP
    INSERT INTO tgt (sid, amt) VALUES (rec.sid, rec.amt);
  END LOOP;
END;
/
"""


def test_a_cursor_whose_query_is_a_cte_chain_resolves(dictionary: Dictionary) -> None:
    """The second half of S4-02, and the reason `_field_of_row` now delegates to band 0.

    That function used to read the cursor query's OUTERMOST select list and bind each
    column straight to a base relation. `SELECT m.sid FROM second_level m` binds `m` to a
    CTE, which is not a dictionary relation, so it resolved NOTHING - silently.

    **A cursor whose query is a CTE chain is not an edge case; it is what a named cursor is
    for.** A query long enough to deserve a name is usually long enough to need one.
    """
    edges = _edges(CURSOR_OVER_A_CTE_CHAIN, dictionary)

    assert ("SRC.SID", "TGT.SID", Flow.VALUE.value) in edges
    assert ("SRC.AMT", "TGT.AMT", Flow.VALUE.value) in edges


def test_the_cte_chain_resolves_without_inventing_a_relation(dictionary: Dictionary) -> None:
    """The negative half: no edge may name a CTE or the target.

    `SELECT m.sid FROM second_level m` used to resolve to nothing at all. The failure to
    avoid now is the OTHER one - naming `SECOND_LEVEL` as though it were a table, or
    falling back to the target the way the declared-cursor form did before this fix.

    WHAT THIS DELIBERATELY DOES NOT ASSERT: the cursor query's own `WHERE s.cat <> 'X'`
    producing a filter edge against the loop's target. `resolve_projection` answers "which
    base column does this output column come from" and nothing else; predicates inside a
    declared cursor's query reaching the loop's writes is a separate capability that was
    never built. An earlier draft of this file asserted it, failed, and was wrong to ask -
    the gap is recorded in FINDINGS rather than hidden in a test that hopes for it.
    """
    sources = {source for source, _, _ in _edges(CURSOR_OVER_A_CTE_CHAIN, dictionary)}

    assert not [name for name in sources if name.startswith(("FIRST_LEVEL", "SECOND_LEVEL"))], (
        "a CTE was named as a source relation"
    )
    assert "TGT.SID" not in sources, "the record field fell back to the target"


# --- S4-07 and S4-08: the record field read into a VARIABLE ---------------------------

ASSIGNMENT_FROM_RECORD = """CREATE OR REPLACE PROCEDURE p IS
  CURSOR c IS
    WITH grouped AS (
      SELECT s.sid AS sid, SUM(s.amt) AS total FROM src s GROUP BY s.sid
    )
    SELECT m.sid, m.total FROM grouped m;
  v_id    NUMBER;
  v_total NUMBER;
  v_net   NUMBER;
BEGIN
  FOR rec IN c LOOP
    v_id    := rec.sid;
    v_total := rec.total;
    v_net   := v_total * 2;
    INSERT INTO tgt (sid, amt) VALUES (v_id, v_net);
  END LOOP;
END;
/
"""


def _typed(source: str, dictionary: Dictionary) -> set[tuple[str, str, str]]:
    return {
        (e.source.name, e.target.name, e.transform.value)
        for e in analyse_source(source, dictionary).edges
    }


def test_a_record_field_assigned_to_a_variable_reaches_the_base_column(
    dictionary: Dictionary,
) -> None:
    """S4-07. `v := rec.field` is the commonest line in a cursor loop and produced nothing.

    The identifier scan looks each name up in the declared variables; `REC` and `TOTAL` are
    neither, so both were discarded and the chain had no first link.
    """
    edges = _edges(ASSIGNMENT_FROM_RECORD, dictionary)

    assert ("SRC.SID", "V_ID", Flow.VALUE.value) in edges
    assert ("SRC.AMT", "V_TOTAL", Flow.VALUE.value) in edges


def test_the_chain_no_longer_appears_to_begin_at_a_variable(dictionary: Dictionary) -> None:
    """The damage was not a missing edge - it was a chain that looked complete.

    A variable IS a legitimate source in this IR (ADR-0001 §3), so eight edges all
    beginning at locals read as lineage whose origin happens to be memory. That is a
    sentence the analyser is entitled to say, and here it was false. This asserts every
    variable that receives a record field has something upstream of it.
    """
    edges = _edges(ASSIGNMENT_FROM_RECORD, dictionary)
    targets_of_columns = {target for source, target, _ in edges if "." in source}

    assert "V_ID" in targets_of_columns, "V_ID still has no upstream column"
    assert "V_TOTAL" in targets_of_columns, "V_TOTAL still has no upstream column"


def test_the_transform_inside_the_cursor_query_reaches_the_edge(
    dictionary: Dictionary,
) -> None:
    """S4-08, fixed with S4-07 because they are one fact.

    `rec.total` is `SUM(s.amt)` two scopes inside the cursor's query. Emitting the column
    while dropping the aggregation is a WRONG transform, which ADR-0001 §4 counts as a miss
    on both sides - so the edge has to carry what happened to the value on the way.
    """
    typed = _typed(ASSIGNMENT_FROM_RECORD, dictionary)

    assert ("SRC.AMT", "V_TOTAL", "aggregated") in typed, (
        "the SUM inside the cursor query did not reach the edge"
    )
    assert ("SRC.SID", "V_ID", "identity") in typed, "an aggregation was applied where none exists"


def test_the_transform_still_combines_with_the_assignment(dictionary: Dictionary) -> None:
    """The ladder must span both sides of the record boundary.

    `v_net := v_total * 2` is derived, and `v_total` is aggregated from inside the cursor.
    The edge into V_NET comes from V_TOTAL rather than from SRC.AMT, so this checks the
    hop is present and typed rather than collapsed.
    """
    typed = _typed(ASSIGNMENT_FROM_RECORD, dictionary)

    assert ("V_TOTAL", "V_NET", "derived") in typed
