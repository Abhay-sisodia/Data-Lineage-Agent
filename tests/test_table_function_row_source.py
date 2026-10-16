"""A row source with no relation name must be declared, never crash the analyser.

Found 2026-09-11 by the analyser triage - the pass that asks, of every construct the
parser probe recorded as ACCEPTED, what the analyser then does with it.

`FROM TABLE(f(1))` parses as an `exp.Table` whose `this` is the function call, so
`source.name` is the empty string. `_trace` handed that straight to `Boundary(subject=...)`,
which rejects an empty string, and the `ValidationError` escaped `analyse_source`.

WHY THIS ONE IS TESTED HARDER THAN A MISSING EDGE. Every other failure mode in this
analyser costs one statement. A refusal is declared and counted; a boundary is declared and
counted; even a silent miss leaves the statement counted as analysed and the rest of the
file intact. An exception escaping `analyse_source` costs THE WHOLE FILE - every other
procedure in the package produces nothing, and no refusal, boundary or count records that
it happened. It is the one failure the coverage statement cannot see.

`TABLE(...)` over a pipelined function is ordinary Oracle ETL, and no corpus or stress
package contains one - which is why it survived eighteen findings.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import BoundaryKind
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["ID", "AMT"],
    f"{SCHEMA}.TGT": ["ID", "AMT"],
}


@pytest.fixture
def dictionary() -> Dictionary:
    return Dictionary(
        captured_at="2026-09-11T00:00:00Z",
        default_schema=SCHEMA,
        objects={
            name: {"owner": SCHEMA, "name": name.split(".")[1], "object_type": "TABLE"}
            for name in COLUMNS
        },
        columns=COLUMNS,
        temporary={name: False for name in COLUMNS},
    )


def _procedure(statement: str) -> str:
    return f"CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n  {statement}\nEND;\n/\n"


BARE = _procedure("INSERT INTO tgt (id) SELECT column_value FROM TABLE(f(1));")
ALIASED = _procedure("INSERT INTO tgt (id) SELECT t.column_value FROM TABLE(f(1)) t;")
PACKAGED = _procedure("INSERT INTO tgt (id) SELECT column_value FROM TABLE(pkg.f(1));")
MULTISET = _procedure(
    "INSERT INTO tgt (id) SELECT column_value "
    "FROM TABLE(CAST(MULTISET(SELECT id FROM src) AS id_tab));"
)
JOINED = _procedure(
    "INSERT INTO tgt (id, amt) SELECT s.id, t.column_value FROM src s, TABLE(f(s.id)) t;"
)


@pytest.mark.parametrize(
    "source",
    [BARE, ALIASED, PACKAGED, MULTISET, JOINED],
    ids=["bare", "aliased", "packaged", "multiset", "joined"],
)
def test_a_table_function_row_source_does_not_raise(source: str, dictionary: Dictionary) -> None:
    """Without the fix every one of these raises ValidationError out of analyse_source.

    Five shapes rather than one because the crash was in the row-source handling, not in
    any single statement form - and `SELECT ... INTO FROM TABLE(f(1))` did NOT crash, so
    "table functions crash" would have been the wrong description of the defect.
    """
    analyse_source(source, dictionary)


def test_the_unreadable_row_source_is_declared_rather_than_ignored(
    dictionary: Dictionary,
) -> None:
    """Not crashing is not enough - silence would be the other way to pass the test above.

    A table function's result shape is its return type, which is not in the dictionary, so
    the honest answer is the one an ungranted schema gets: name it and emit nothing.
    """
    result = analyse_source(BARE, dictionary)

    dangling = [
        boundary
        for boundary in result.boundaries
        if boundary.kind is BoundaryKind.DANGLING_REFERENCE
    ]
    assert dangling, "the function row source was neither traced nor declared"
    assert dangling[0].subject == "TABLE(F)"


def test_the_subject_names_the_function_not_its_package(dictionary: Dictionary) -> None:
    """Two functions in one package must not collapse into one boundary.

    A boundary's identity is its kind plus its subject, so a subject of `TABLE(PKG)` -
    which the first version of this fix produced, by walking for the first string it found -
    would make `pkg.load_a` and `pkg.load_b` the same declared fact.
    """
    result = analyse_source(PACKAGED, dictionary)

    subjects = {
        boundary.subject
        for boundary in result.boundaries
        if boundary.kind is BoundaryKind.DANGLING_REFERENCE
    }
    assert subjects == {"TABLE(PKG.F)"}


def test_a_real_table_beside_the_function_still_yields_its_edge(
    dictionary: Dictionary,
) -> None:
    """The function is unreadable; the table next to it is not.

    Refusing the whole statement would be the easy fix and the wrong one - the same
    argument REMOTE_OBJECT already makes for a db-link reference, where the local columns
    are fully provable and only the far side is unknowable.
    """
    result = analyse_source(JOINED, dictionary)

    sources = {edge.source.name for edge in result.edges}
    assert "SRC.ID" in sources, "the locally provable column was lost with the unreadable one"
    assert any(
        boundary.kind is BoundaryKind.DANGLING_REFERENCE
        and boundary.subject.startswith("TABLE(")
        for boundary in result.boundaries
    ), "the unreadable half was dropped without being declared"


def test_no_edge_is_invented_from_the_function(dictionary: Dictionary) -> None:
    """The failure this project exists to prevent, checked explicitly.

    With one relation in scope, `_trace` falls back to the single source - so a guess here
    would bind `column_value` to whatever table happened to be in the FROM and produce a
    confident, entirely fictional edge.
    """
    result = analyse_source(BARE, dictionary)

    assert result.edges == [], f"invented {len(result.edges)} edge(s) from an unreadable row source"
