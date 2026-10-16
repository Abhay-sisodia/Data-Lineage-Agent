"""In a MERGE the target is in scope, and the scope object did not know it.

Stress finding S3-07, found by stress package 3 on 2026-09-12.

`_analyse_merge` builds its scope over the USING clause alone - it is the thing whose
projections need tracing - so a reference qualified with the TARGET's alias resolved
against the wrong side entirely:

    WHEN MATCHED THEN UPDATE SET lifetime_value = NVL(t.lifetime_value, 0) + s.amount

`t.lifetime_value` came out as `unresolved_identifier`, and the self-edge it should have
produced was absent.

**That accumulator is how a MERGE adds to a running total**, and the self-edge is a real
one: `trg_recent_audit` does `lifetime_value = NVL(lifetime_value, 0) + 1` and `b2_05` has
labelled exactly that edge since it was written. A column fed by its own prior value is
ordinary lineage, not a curiosity.

THE USING SCOPE IS CONSULTED FIRST, and that ordering is the whole safety of the fix. An
alias that exists in the USING clause belongs to that source whatever it is called;
preferring the target would silently redirect a real source reference at the table being
written, which is a worse defect than the one being fixed.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow, NodeKind
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["SID", "AMT"],
    f"{SCHEMA}.TGT": ["SID", "TOTAL"],
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


ACCUMULATOR = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid, s.amt FROM src s) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.total = NVL(t.total, 0) + x.amt;
END;
/
"""

ARM_WHERE_ON_TARGET = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid, s.amt FROM src s) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.total = x.amt WHERE t.total < 100;
END;
/
"""

# The target is referenced by its NAME rather than an alias - legal, and a different
# lookup.
UNALIASED_TARGET = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt
  USING (SELECT s.sid, s.amt FROM src s) x
  ON (tgt.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET tgt.total = NVL(tgt.total, 0) + x.amt;
END;
/
"""


def _edges(source: str, dictionary: Dictionary) -> set[tuple[str, str, str]]:
    result = analyse_source(source, dictionary)
    return {(e.source.name, e.target.name, e.flow.value) for e in result.edges}


def test_the_accumulator_self_edge_is_emitted(dictionary: Dictionary) -> None:
    """Without the fix this edge is absent and `TOTAL` is declared unresolved instead."""
    edges = _edges(ACCUMULATOR, dictionary)

    assert ("TGT.TOTAL", "TGT.TOTAL", Flow.VALUE.value) in edges, (
        "the accumulator's self-edge was lost - the column's prior value feeds its next one"
    )
    assert ("SRC.AMT", "TGT.TOTAL", Flow.VALUE.value) in edges, "the source half was lost too"


def test_the_target_reference_is_no_longer_declared_unresolved(
    dictionary: Dictionary,
) -> None:
    """The boundary must go, not merely be joined by an edge.

    A boundary promises knowledge stops here. Leaving one beside a now-resolved reference
    would make the coverage statement report a gap that is not there - the same complaint
    S3-10 raised about a spurious `unresolved_identifier`.
    """
    result = analyse_source(ACCUMULATOR, dictionary)

    assert "TOTAL" not in {b.subject for b in result.boundaries}
    assert "TGT.TOTAL" not in {b.subject for b in result.boundaries}


def test_an_arm_where_on_the_target_resolves_too(dictionary: Dictionary) -> None:
    """The second site. S3-03 gave a MERGE its filter edges; they need the same lookup.

    `WHERE t.total < 100` restricts which matched rows are written, and the column it names
    is the target's. Without the fix it would resolve against the USING clause and declare.
    """
    edges = _edges(ARM_WHERE_ON_TARGET, dictionary)

    assert ("TGT.TOTAL", "TGT", Flow.FILTER.value) in edges


def test_an_unaliased_target_resolves_by_name(dictionary: Dictionary) -> None:
    """`MERGE INTO tgt ... SET tgt.total = ...` - legal, and a different lookup path.

    Aliasing a MERGE target is conventional but not required, and a fix keyed only on the
    alias would work on every example anyone wrote while testing and fail on the first
    unaliased statement in an estate.
    """
    assert ("TGT.TOTAL", "TGT.TOTAL", Flow.VALUE.value) in _edges(UNALIASED_TARGET, dictionary)


def test_a_using_alias_is_never_redirected_at_the_target(dictionary: Dictionary) -> None:
    """The ordering guard, and the way this fix could have been worse than the defect.

    The USING scope is consulted first. If the target lookup ran first, an alias that
    genuinely belongs to the USING source would be redirected at the table being written -
    turning a correct source edge into a fabricated self-edge, which is a silent failure
    rather than a missing one.
    """
    edges = _edges(ACCUMULATOR, dictionary)

    assert ("SRC.AMT", "TGT.TOTAL", Flow.VALUE.value) in edges
    assert not [
        edge
        for edge in edges
        if edge[0].startswith("TGT.") and edge[2] == Flow.VALUE.value and edge[1] != "TGT.TOTAL"
    ], "a USING-side reference was resolved against the target"


def test_a_column_the_target_does_not_have_is_still_declared(dictionary: Dictionary) -> None:
    """The fix must not invent a target column to resolve to.

    `t.missing` is not a column of TGT. The honest answer is a boundary and no edge - the
    same treatment `_trace` gives a name that is not a column of the relation it would bind
    to, which is the rule that stops a PL/SQL variable becoming a fictional column.
    """
    source = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid, s.amt FROM src s) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.total = NVL(t.missing, 0) + x.amt;
END;
/
"""
    result = analyse_source(source, dictionary)

    assert not [
        edge
        for edge in result.edges
        if edge.source.kind is NodeKind.COLUMN and edge.source.name == "TGT.MISSING"
    ], "an edge was invented from a column the target does not have"
    assert any("MISSING" in b.subject for b in result.boundaries), "nothing was declared"
