"""A column computed in a derived table still has a source.

Stress finding S4-05, found by stress package 4 on 2026-09-12 and filed as "two-collection
BULK COLLECT resolves only the first". **That description was wrong.** Two collections
straight from a table resolve fine; what fails is a projection that was COMPUTED OR RENAMED
in a derived table, and the second collection was simply the one receiving it.

    SELECT g.sid, g.total BULK COLLECT INTO a, b
      FROM (SELECT s.sid AS sid, SUM(s.amt) AS total FROM src s GROUP BY s.sid) g

`_classify` binds a name through a FLAT alias -> relation map. `total` is a column of no
dictionary relation, so `b` lost its source entirely.

NOTE WHICH HALF WAS SILENT AND WHICH WAS LUCK. `g.sid` resolved - because `SID` HAPPENS to
be a column of the only relation in scope, and the fallback found it. The statement's own
alias was being discarded and the answer was right by coincidence of naming. That is the s2
shape, and it is why fixing only the visibly broken half would have left a guess behind it.

Band 0 traverses derived tables, CTEs, joins and views to answer exactly this question, so
the projection is resolved there - the same delegation S4-02 made for a cursor record, for
the same reason S2-08 is on the register.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow, NodeKind
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["SID", "AMT"],
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


BULK_FROM_DERIVED = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  a t;
  b t;
BEGIN
  SELECT g.sid, g.total BULK COLLECT INTO a, b
    FROM (SELECT s.sid AS sid, SUM(s.amt) AS total FROM src s GROUP BY s.sid) g;
END;
/
"""

BULK_FROM_TABLE = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  a t;
  b t;
BEGIN
  SELECT s.sid, s.amt BULK COLLECT INTO a, b FROM src s;
END;
/
"""

SCALAR_INTO_FROM_DERIVED = """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
BEGIN
  SELECT g.total INTO v
    FROM (SELECT SUM(s.amt) AS total FROM src s) g;
END;
/
"""


def _typed(source: str, dictionary: Dictionary) -> set[tuple[str, str, str]]:
    return {
        (e.source.name, e.target.name, e.transform.value)
        for e in analyse_source(source, dictionary).edges
        if e.flow is Flow.VALUE and e.source.kind is NodeKind.COLUMN
    }


def test_a_column_computed_in_a_derived_table_reaches_its_collection(
    dictionary: Dictionary,
) -> None:
    """Without the fix `b` has no source at all - the aggregate's origin is simply gone."""
    typed = _typed(BULK_FROM_DERIVED, dictionary)

    assert ("SRC.AMT", "B", "aggregated") in typed, (
        "the collection receiving the computed column lost its source"
    )


def test_the_renamed_passthrough_resolves_to_the_same_column(dictionary: Dictionary) -> None:
    """The half that was right by luck must now be right on purpose.

    `g.sid` resolved before this fix because SID happens to exist on the only relation in
    scope. Rename it and the coincidence disappears, which is what this asserts.
    """
    renamed = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  a t;
BEGIN
  SELECT g.renamed BULK COLLECT INTO a
    FROM (SELECT s.sid AS renamed FROM src s) g;
END;
/
"""
    assert ("SRC.SID", "A", "identity") in _typed(renamed, dictionary), (
        "a renamed pass-through in a derived table did not resolve"
    )


def test_the_plain_form_is_unaffected(dictionary: Dictionary) -> None:
    """The control: two collections straight from a table always worked.

    Kept because the finding was FILED as "two collections resolve only the first", and
    this is the assertion that shows that description was wrong.
    """
    typed = _typed(BULK_FROM_TABLE, dictionary)

    assert ("SRC.SID", "A", "identity") in typed
    assert ("SRC.AMT", "B", "identity") in typed


def test_a_scalar_select_into_gets_the_same_treatment(dictionary: Dictionary) -> None:
    """Not BULK-specific. `SELECT ... INTO v` shares the code path and the defect.

    Tested separately so a future failure says whether the bulk or the scalar form broke -
    and because a scalar INTO from a derived aggregate is the commoner of the two in real
    code.
    """
    assert ("SRC.AMT", "V", "aggregated") in _typed(SCALAR_INTO_FROM_DERIVED, dictionary)


def test_nothing_is_invented_when_the_derived_column_has_no_source(
    dictionary: Dictionary,
) -> None:
    """A literal in the derived table has no upstream, and must stay that way.

    The fallback added here runs whenever `_classify` fails, so it must not turn "there is
    nothing to name" into a guess - the same rule that gives `is_active <- 1` no edge.
    """
    literal = """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
BEGIN
  SELECT g.flag INTO v FROM (SELECT 1 AS flag FROM src s) g;
END;
/
"""
    assert not [
        edge for edge in _typed(literal, dictionary) if edge[1] == "V"
    ], "an edge was invented for a column whose derived value is a literal"
