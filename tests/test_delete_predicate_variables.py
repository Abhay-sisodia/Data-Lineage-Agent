"""A `DELETE`'s predicate sees variables, exactly as an `UPDATE`'s does (finding P-02).

    DELETE FROM tgt WHERE sid = v_cutoff;            -- V_CUTOFF lost
    UPDATE tgt SET flag = 1 WHERE sid = v_cutoff;    -- V_CUTOFF reported

Band 0 owns the DELETE and has no unit scope, so it cannot tell a variable from a column;
def-use never claimed the statement, so nothing looked. The S2-04 shape: two passes each
correctly deciding the statement was not theirs.

**P-02 carried a condition, and the subscript tests below are it.** The DELETE form of the
S4-10 probe showed no `I -> TGT` false positive only because band 0 could not resolve `i`
to anything - correct for the wrong reason, and bound to stop being correct the moment this
gap closed. So closing it had to bring S4-10's rule with it, in the same change.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow, NodeKind
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["SID", "AMT"],
    f"{SCHEMA}.TGT": ["SID", "AMT", "FLAG"],
}


@pytest.fixture
def dictionary() -> Dictionary:
    return Dictionary(
        captured_at="2026-10-01T00:00:00Z",
        default_schema=SCHEMA,
        objects={
            name: {"owner": SCHEMA, "name": name.split(".")[1], "object_type": "TABLE"}
            for name in COLUMNS
        },
        columns=COLUMNS,
        temporary={name: False for name in COLUMNS},
    )


def _procedure(statement: str) -> str:
    return f"""CREATE OR REPLACE PROCEDURE p (v_cutoff NUMBER) IS
BEGIN
  {statement}
END;
/
"""


def _filters(source: str, dictionary: Dictionary) -> set[tuple[str, str, str]]:
    return {
        (e.source.kind.value, e.source.name, e.target.name)
        for e in analyse_source(source, dictionary).edges
        if e.flow is Flow.FILTER
    }


def test_a_variable_in_a_delete_predicate_is_a_filter_source(dictionary: Dictionary) -> None:
    """P-02 exactly as filed. Without the fix this statement names no variable at all."""
    filters = _filters(_procedure("DELETE FROM tgt WHERE sid = v_cutoff;"), dictionary)

    assert ("variable", "V_CUTOFF", "TGT") in filters


def test_delete_and_update_report_the_same_variable(dictionary: Dictionary) -> None:
    """The metamorphic form: one predicate, two statements, one answer about the variable."""

    def variables(statement: str) -> set[tuple[str, str]]:
        return {
            (name, target)
            for kind, name, target in _filters(_procedure(statement), dictionary)
            if kind == "variable"
        }

    deleted = variables("DELETE FROM tgt WHERE sid = v_cutoff;")
    updated = variables("UPDATE tgt SET flag = 1 WHERE sid = v_cutoff;")
    assert deleted and deleted == updated


def test_the_column_side_is_unchanged_and_not_doubled(dictionary: Dictionary) -> None:
    """Band 0 still owns the column side. Def-use emitting it too would put two edges for
    one fact into the IR, so the column filter must appear exactly once."""
    edges = [
        e
        for e in analyse_source(
            _procedure("DELETE FROM tgt WHERE sid = v_cutoff;"), dictionary
        ).edges
        if e.flow is Flow.FILTER and e.source.kind is NodeKind.COLUMN
    ]
    assert [(e.source.name, e.target.name) for e in edges] == [("TGT.SID", "TGT")]


def test_a_delete_with_no_variable_gains_nothing(dictionary: Dictionary) -> None:
    """The control: a column-only predicate must come out exactly as it did before."""
    filters = _filters(
        _procedure("DELETE FROM tgt WHERE sid IN (SELECT s.sid FROM src s WHERE s.amt > 0);"),
        dictionary,
    )
    assert {kind for kind, _, _ in filters} == {"column"}


SUBSCRIPTED = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  l_ids t;
BEGIN
  FOR i IN 1 .. l_ids.COUNT LOOP
    DELETE FROM tgt WHERE sid = l_ids(i);
  END LOOP;
END;
/
"""


def test_a_subscripted_collection_is_a_filter_source_of_a_delete(
    dictionary: Dictionary,
) -> None:
    """S4-10's rule, on the path P-02 opens: which rows go depends on what is IN `l_ids`."""
    assert ("variable", "L_IDS", "TGT") in _filters(SUBSCRIPTED, dictionary)


def test_the_loop_index_is_not_a_filter_source_of_a_delete(dictionary: Dictionary) -> None:
    """The false positive P-02 warned would appear the moment variables became visible."""
    names = {name for kind, name, _ in _filters(SUBSCRIPTED, dictionary) if kind == "variable"}
    assert "I" not in names


def test_a_cursor_record_field_in_a_delete_predicate_resolves(dictionary: Dictionary) -> None:
    """`rec.sid` names a row source, not a table - band 0 cannot bind it either. It resolves
    through the cursor's query to the column it was selected from."""
    source = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  FOR rec IN (SELECT s.sid FROM src s WHERE s.amt < 0) LOOP
    DELETE FROM tgt WHERE sid = rec.sid;
  END LOOP;
END;
/
"""
    assert ("column", "SRC.SID", "TGT") in _filters(source, dictionary)
