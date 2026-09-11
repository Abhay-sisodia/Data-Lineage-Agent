"""A qualified reference means the relation it names, or nothing.

Stress finding S3-10, found on 2026-09-12 while confirming S3-09 - not by a stress package,
because no package here could see it.

Inside `(SELECT SUM(r.net) FROM rev r WHERE r.rid = s.sid)` the alias `s` belongs to the
ENCLOSING query. `_trace` searched this scope and the scopes BELOW it, never above, so the
correlated reference went unresolved - and the single-source fallback then bound it to
whichever relation the subquery happened to select from:

  * where that relation has no column of that name -> `unresolved_identifier REV.SID`,
    honest but a lost edge;
  * where it HAS one -> an edge from a relation the reference never named.

WHY IT HID, IN TWO LAYERS.

First, real correlations compare columns of the SAME NAME - `r.cust_id = c.cust_id` - so
the wrong binding produced the SAME EDGE as the right one and deduplicated into a
correct-looking answer. Stress 3 contains exactly that shape and scored it as a pass. These
fixtures use `rid` and `sid` rather than a shared `id` so the wrong answer has nowhere to
hide.

Second, and this was measured rather than assumed: an `INSERT ... SELECT` reaches the same
correlation by TWO routes. `_correlated_filter_columns` sees it from the OUTER scope, where
the alias IS in scope, and gets it right; `_filter_edges` sees it again from the inner scope
and mis-binds. The correct edge therefore existed all along, with a spurious
`unresolved_identifier` boundary beside it. A MERGE's synthetic wrapper projects `*`, so
only the broken route ran and the edge was simply absent. **The defect was in shared code
and only one caller exposed it** - which is why the first draft of this file asserted the
INSERT case was equally broken, and was wrong.

**This is the s2 silent-failure shape reached from the other direction.** s2 was a schema
qualifier being discarded so a name bound to a relation the statement never mentioned; this
is a table alias being discarded for the same result. The fix is the same principle: when
the statement says WHICH relation it means, that is not a guess to be overridden.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["SID", "AMT"],
    f"{SCHEMA}.REV": ["RID", "NET"],
    f"{SCHEMA}.TGT": ["SID", "TOTAL"],
    # SHARES A COLUMN NAME WITH SRC, on purpose. This is the only shape in which the
    # defect produces a wrong EDGE rather than a declared boundary - and it is the shape
    # real correlations take, because they compare a key to the same key.
    f"{SCHEMA}.REV_SHARED": ["SID", "NET"],
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


CORRELATED_IN_MERGE = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid,
                (SELECT SUM(r.net) FROM rev r WHERE r.rid = s.sid) AS total
           FROM src s) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.total = x.total;
END;
/
"""

CORRELATED_IN_INSERT = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  INSERT INTO tgt (sid, total)
  SELECT s.sid,
         (SELECT SUM(r.net) FROM rev r WHERE r.rid = s.sid)
    FROM src s;
END;
/
"""

# The qualifier names a relation that exists NOWHERE in the statement. There is no right
# answer to find, so the only acceptable outcome is to declare rather than to guess.
UNREACHABLE_QUALIFIER = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  INSERT INTO tgt (sid, total)
  SELECT s.sid,
         (SELECT SUM(r.net) FROM rev r WHERE r.rid = ghost.sid)
    FROM src s;
END;
/
"""


def _edges(source: str, dictionary: Dictionary) -> list[tuple[str, str, str]]:
    result = analyse_source(source, dictionary)
    return [(e.source.name, e.target.name, e.flow.value) for e in result.edges]


def test_both_operands_of_a_correlation_reach_their_own_relation(
    dictionary: Dictionary,
) -> None:
    """`predicates.py`: "both of its operands stay". One of them could not get there."""
    edges = _edges(CORRELATED_IN_MERGE, dictionary)

    assert ("REV.RID", "TGT", Flow.FILTER.value) in edges, "the inner operand was lost"
    assert ("SRC.SID", "TGT", Flow.FILTER.value) in edges, (
        "the correlated OUTER operand did not resolve to the relation it names"
    )


def test_no_edge_is_invented_from_the_wrong_relation(dictionary: Dictionary) -> None:
    """The half that matters: `s.sid` must never arrive as a column of REV.

    Written as an explicit negative rather than relying on the positive above, because the
    defect's signature was an edge from the WRONG relation - and with same-named operands
    that is indistinguishable from the right one. This fixture names them differently so
    the wrong answer has nowhere to hide.
    """
    sources = {source for source, _, _ in _edges(CORRELATED_IN_MERGE, dictionary)}

    assert "REV.SID" not in sources, "the outer alias was discarded and bound to REV"


# In MERGE form, because that is the caller where only the broken route runs. In an
# INSERT the outer scope resolves the correlation by a second path and the wrong edge never
# appears - so an INSERT fixture here would pass with or without the fix and prove nothing.
SHARED_NAME_CORRELATION = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid,
                (SELECT SUM(r.net) FROM rev_shared r WHERE r.sid = s.sid) AS total
           FROM src s) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.total = x.total;
END;
/
"""


def test_the_outer_operand_is_not_bound_to_the_inner_relation(dictionary: Dictionary) -> None:
    """The defect's real signature: a WRONG EDGE, not a missing one.

    `r.sid = s.sid` over a relation that HAS a `sid` column. Before the fix, `s.sid` fell
    back to the subquery's single source and resolved to `REV_SHARED.SID` - a confident
    edge from a relation the reference never named, and one that is indistinguishable from
    the correct `REV_SHARED.SID` edge the inner operand produces.

    **That indistinguishability is why this survived.** In stress 3 the two edges merged
    into one and the package scored a pass. Here the assertion requires `SRC.SID` as a
    FILTER source, which the wrong binding can never produce.
    """
    edges = _edges(SHARED_NAME_CORRELATION, dictionary)
    # The FLOW matters. `s.sid` is also the first projected column, so it reaches SRC.SID
    # by a value edge no matter what the predicate does - asserting on the source set alone
    # passes without the fix, for a reason that has nothing to do with the finding.
    filters = {source for source, _, flow in edges if flow == Flow.FILTER.value}

    assert "SRC.SID" in filters, (
        "the outer operand never reached SRC - it was bound to the inner relation instead, "
        "and with a shared column name that produces a wrong edge rather than a boundary"
    )
    assert "REV_SHARED.SID" in filters, "the inner operand's own filter edge was lost"


def test_the_insert_form_loses_its_spurious_boundary(dictionary: Dictionary) -> None:
    """The INSERT form was NOT equally broken, and this records what it actually was.

    Measured, not assumed: an `INSERT ... SELECT` reaches this correlation twice.
    `_correlated_filter_columns` reads it from the outer scope, where `s` IS in scope, and
    produces the right edge; `_filter_edges` reads it again from the inner scope and
    mis-binds `s.sid` to REV. Since REV has no SID, the second route declared
    `unresolved_identifier REV.SID` rather than inventing an edge - so the visible symptom
    was a boundary claiming something unresolvable that the statement had in fact resolved.

    A boundary is a promise that knowledge stops here. One raised for a name another code
    path resolved without difficulty makes the coverage statement report a gap that is not
    there, and this is the assertion that keeps it gone.
    """
    result = analyse_source(CORRELATED_IN_INSERT, dictionary)

    assert ("SRC.SID", "TGT", Flow.FILTER.value) in [
        (e.source.name, e.target.name, e.flow.value) for e in result.edges
    ]
    assert "REV.SID" not in {b.subject for b in result.boundaries}, (
        "a boundary was declared for a reference the outer scope resolves perfectly well"
    )


def test_an_unreachable_qualifier_is_declared_not_guessed(dictionary: Dictionary) -> None:
    """When there is no right answer, silence with a boundary beats a confident wrong one.

    `ghost.sid` names nothing in the statement. The fallback this fix narrowed would have
    bound it to REV; what should happen is a declared boundary and no edge, which is the
    same treatment an ungranted schema gets.
    """
    result = analyse_source(UNREACHABLE_QUALIFIER, dictionary)

    assert "REV.SID" not in {edge.source.name for edge in result.edges}
    assert result.boundaries, "nothing was declared for a qualifier that resolves to nothing"
