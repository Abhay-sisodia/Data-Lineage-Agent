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
