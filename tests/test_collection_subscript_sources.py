"""A collection subscript reads the collection, on every path that can contain one.

Stress finding S4-10, found by stress package 4 on 2026-09-12 and filed as `silent-loss`:
"a subscripted collection in a VALUES list contributes no source". **Measuring it showed
one defect with two directions across five statement paths, and the worse direction was
the one nobody had filed.**

S3-06 established the rule: `l_batch(i)` reads the collection, and `i` only chooses which
element, so the index is not a source - the collection form of D-5's argument about join
keys. It removed `i` from the paths it knew about AND PUT NOTHING IN ANY OF THEM.

The assignment path kept working by accident. It scans identifiers out of TEXT, so
`l_batch` arrives as a name whatever the AST looks like. Every path that walks the AST saw
the collection only as `Anonymous.this` - never an `exp.Column`, so never a candidate:

    VALUES (l_ids(i))              L_IDS lost        (S4-10 as filed)
    VALUES (l_batch(i).sid)        L_BATCH lost
    SET amt = l_amts(i)            L_AMTS lost   AND `I -> TGT.AMT`  value  EMITTED
    WHERE sid = l_ids(i)           L_IDS lost    AND `I -> TGT`      filter EMITTED

THE LAST TWO ARE FALSE POSITIVES, and they are S3-06's own defect still live on paths
S3-06 never reached - the loop counter reported as a value source of a written column and
as a filter source of a read table. No stress package subscripts a collection inside an
`UPDATE` or a `WHERE`, so all four packages scored zero false positives while these stood.

`_subscripted` now returns the exclusion and the replacement as ONE object, so a call site
that drops the index from its walk is holding the collection at the same moment. That is
the whole point: the previous design let a call site take half the rule and look finished.
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
        captured_at="2026-09-13T00:00:00Z",
        default_schema=SCHEMA,
        objects={
            name: {"owner": SCHEMA, "name": name.split(".")[1], "object_type": "TABLE"}
            for name in COLUMNS
        },
        columns=COLUMNS,
        temporary={name: False for name in COLUMNS},
    )


VALUES_SCALAR = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  l_ids t;
  l_amts t;
BEGIN
  SELECT s.sid, s.amt BULK COLLECT INTO l_ids, l_amts FROM src s;
  FOR i IN 1 .. l_ids.COUNT LOOP
    INSERT INTO tgt (sid, amt) VALUES (l_ids(i), l_amts(i));
  END LOOP;
END;
/
"""

VALUES_RECORD_FIELD = """CREATE OR REPLACE PROCEDURE p IS
  TYPE r IS RECORD (sid NUMBER, amt NUMBER);
  TYPE t IS TABLE OF r;
  l_batch t;
BEGIN
  FOR i IN 1 .. l_batch.COUNT LOOP
    INSERT INTO tgt (sid, amt) VALUES (l_batch(i).sid, l_batch(i).amt);
  END LOOP;
END;
/
"""

VALUES_DERIVED = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  l_amts t;
BEGIN
  FOR i IN 1 .. l_amts.COUNT LOOP
    INSERT INTO tgt (amt) VALUES (l_amts(i) * 1.05);
  END LOOP;
END;
/
"""

UPDATE_SET = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  l_amts t;
BEGIN
  FOR i IN 1 .. l_amts.COUNT LOOP
    UPDATE tgt SET amt = l_amts(i) WHERE sid = 1;
  END LOOP;
END;
/
"""

UPDATE_WHERE = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  l_ids t;
BEGIN
  FOR i IN 1 .. l_ids.COUNT LOOP
    UPDATE tgt SET flag = 1 WHERE sid = l_ids(i);
  END LOOP;
END;
/
"""

INSERT_SELECT_WHERE = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  l_ids t;
BEGIN
  FOR i IN 1 .. l_ids.COUNT LOOP
    INSERT INTO tgt (sid, amt) SELECT s.sid, s.amt FROM src s WHERE s.sid = l_ids(i);
  END LOOP;
END;
/
"""


def _typed(source: str, dictionary: Dictionary, flow: Flow) -> set[tuple[str, str, str]]:
    return {
        (e.source.name, e.target.name, e.transform.value)
        for e in analyse_source(source, dictionary).edges
        if e.flow is flow and e.source.kind is NodeKind.VARIABLE
    }


def _variable_sources(source: str, dictionary: Dictionary) -> set[str]:
    return {
        e.source.name
        for e in analyse_source(source, dictionary).edges
        if e.source.kind is NodeKind.VARIABLE
    }


# --- the silent half: the collection was never named -----------------------------------


def test_a_subscripted_collection_in_a_values_list_is_a_value_source(
    dictionary: Dictionary,
) -> None:
    """S4-10 exactly as filed. Without the fix this statement produces no edge at all."""
    typed = _typed(VALUES_SCALAR, dictionary, Flow.VALUE)

    assert ("L_IDS", "TGT.SID", "identity") in typed
    assert ("L_AMTS", "TGT.AMT", "identity") in typed


def test_a_record_collections_field_names_the_collection(dictionary: Dictionary) -> None:
    """`l_batch(i).sid` reports L_BATCH, not a field-qualified name.

    A locally declared RECORD type has no query behind it to resolve a field against - the
    delegation S4-02 and S4-05 make for a CURSOR record has nothing to delegate to here.
    Stress 3's key has named the collection itself since 2026-09-12, and an
    `L_BATCH.SID` node would give a relation-shaped name to something that is not a
    relation.
    """
    assert ("L_BATCH", "TGT.SID", "identity") in _typed(
        VALUES_RECORD_FIELD, dictionary, Flow.VALUE
    )
    assert ("L_BATCH", "TGT.AMT", "identity") in _typed(
        VALUES_RECORD_FIELD, dictionary, Flow.VALUE
    )


def test_a_subscripted_collection_is_a_value_source_of_an_update(
    dictionary: Dictionary,
) -> None:
    assert ("L_AMTS", "TGT.AMT", "identity") in _typed(UPDATE_SET, dictionary, Flow.VALUE)


def test_a_subscripted_collection_in_a_predicate_is_a_filter_source(
    dictionary: Dictionary,
) -> None:
    """Which rows are updated depends entirely on what is IN the collection."""
    assert ("L_IDS", "TGT", "identity") in _typed(UPDATE_WHERE, dictionary, Flow.FILTER)


def test_the_same_holds_in_an_insert_select_predicate(dictionary: Dictionary) -> None:
    """The fourth predicate walk in the module. D-5 needed four call sites too."""
    assert ("L_IDS", "TGT", "identity") in _typed(
        INSERT_SELECT_WHERE, dictionary, Flow.FILTER
    )


# --- the false-positive half: the index was claimed as a source ------------------------


def test_the_loop_index_is_not_a_value_source_of_an_update(dictionary: Dictionary) -> None:
    """Without the fix: `I -> TGT.AMT` value derived.

    The more serious direction, and the one that was not filed. A missing edge is a recall
    number; this one asserts that a loop counter determines a monetary column, which is
    exactly the kind of confident fiction the module docstring is about.
    """
    assert "I" not in _variable_sources(UPDATE_SET, dictionary)


def test_the_loop_index_is_not_a_filter_source(dictionary: Dictionary) -> None:
    """Without the fix: `I -> TGT` filter, on both predicate paths."""
    assert "I" not in _variable_sources(UPDATE_WHERE, dictionary)
    assert "I" not in _variable_sources(INSERT_SELECT_WHERE, dictionary)


# --- the transform, and the controls --------------------------------------------------


def test_a_subscript_is_not_itself_a_transformation(dictionary: Dictionary) -> None:
    """`l_amts(i)` is the element, unchanged - identity, not derived.

    To the transform ladder a subscript parses as a function application, so every value
    written from a collection scored `derived`. ADR-0001 §4 makes a wrong transform a MISS
    ON BOTH SIDES, so that is a false positive AND a miss per edge, not a cosmetic slip -
    which is why `identity` is asserted above rather than the source name alone.
    """
    assert ("L_AMTS", "TGT.AMT", "identity") in _typed(VALUES_SCALAR, dictionary, Flow.VALUE)


def test_the_expression_around_the_subscript_still_decides_the_transform(
    dictionary: Dictionary,
) -> None:
    """The control for the line above, and the way that fix could have been worse.

    Reading a subscript as identity must not flatten the expression CONTAINING it.
    `l_amts(i) * 1.05` is derived; the ladder is asked the ordinary question about a
    rewritten tree rather than a second ladder being written here.
    """
    assert ("L_AMTS", "TGT.AMT", "derived") in _typed(VALUES_DERIVED, dictionary, Flow.VALUE)


def test_a_genuine_function_call_keeps_its_argument_as_a_source(
    dictionary: Dictionary,
) -> None:
    """The control that keeps the whole rule honest, carried from S3-06.

    `pkg.f(v)` is a real call and `v` really is a value source. The scope lookup is the
    only thing separating the two cases, in both directions: a real call's name is not a
    variable to report, and its arguments are not indexes to drop. If this ever fails, the
    fix has become a blanket ban on call arguments.
    """
    source = """CREATE OR REPLACE PROCEDURE p IS
  v_in NUMBER;
BEGIN
  SELECT s.amt INTO v_in FROM src s WHERE ROWNUM = 1;
  INSERT INTO tgt (amt) VALUES (pkg.f(v_in));
END;
/
"""
    assert "V_IN" in _variable_sources(source, dictionary)


def test_a_name_used_as_both_index_and_value_keeps_its_value_edge(
    dictionary: Dictionary,
) -> None:
    """Why the exclusion is keyed on node id rather than on name.

    `VALUES (i, l_amts(i))` writes the counter into one column and indexes with it in the
    next. Contrived, but excluding by name would silently drop the first item - and the
    same shape is not contrived at all once a sequence number is written alongside the row
    it numbers.
    """
    source = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t IS TABLE OF NUMBER;
  l_amts t;
BEGIN
  FOR i IN 1 .. l_amts.COUNT LOOP
    INSERT INTO tgt (sid, amt) VALUES (i, l_amts(i));
  END LOOP;
END;
/
"""
    typed = _typed(source, dictionary, Flow.VALUE)

    assert ("I", "TGT.SID", "identity") in typed, "the index as a real value was dropped"
    assert ("L_AMTS", "TGT.AMT", "identity") in typed
    assert ("I", "TGT.AMT", "identity") not in typed, "the index leaked into the column it indexes"
