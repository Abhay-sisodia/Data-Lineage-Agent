"""A collection subscript chooses an element. It is not in the value.

Stress finding S3-06, found by stress package 3 on 2026-09-12.

`l_batch(i).refund_amount` parses as `Dot(Anonymous(l_batch, [Column(i)]), refund_amount)`,
so the subscript `i` is the **only** `exp.Column` in the whole expression - the collection
name is the function name, the field is a bare identifier. Every path that looked for value
sources therefore found `i`, and nothing else, and emitted `i -> l_adjusted`.

**A subscript chooses WHICH element, exactly as a join key chooses which row.** None of the
loop counter is in the number that comes out: incrementing it moves you to a different
element rather than changing any value. Same argument D-5 made for join conditions and D-4
made for a window's ordering, and the same answer - no value edge.

WHY THE SCOPE LOOKUP IS THE WHOLE RULE. `pkg.f(amt)` is a genuine function call and its
arguments genuinely are value sources. Only a call on a name that is a DECLARED VARIABLE is
an index, because PL/SQL has no way to call a variable - so the test is "is this name in
scope", not "does this look like a subscript".
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow, NodeKind
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["ID", "AMT"],
    f"{SCHEMA}.TGT": ["ID", "AMT", "N"],
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


# The assignment path: identifiers are scanned out of TEXT here, so `i` arrived as a name.
SUBSCRIPT_IN_ASSIGNMENT = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_tab IS TABLE OF NUMBER;
  l_batch    t_tab;
  l_adjusted NUMBER;
BEGIN
  FOR i IN 1 .. l_batch.COUNT LOOP
    l_adjusted := l_batch(i) * 1.2;
    INSERT INTO tgt (amt) VALUES (l_adjusted);
  END LOOP;
END;
/
"""

# The VALUES path: an AST walk, so `i` arrived as a node. Same rule, different call site.
SUBSCRIPT_IN_VALUES = """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_tab IS TABLE OF NUMBER;
  l_batch t_tab;
BEGIN
  FOR i IN 1 .. l_batch.COUNT LOOP
    INSERT INTO tgt (amt) VALUES (l_batch(i));
  END LOOP;
END;
/
"""

# The control that keeps the rule honest: a REAL function call, whose argument is a real
# value source. If this ever stops emitting an edge, the fix has become a blanket ban on
# call arguments.
REAL_FUNCTION_CALL = """CREATE OR REPLACE PROCEDURE p IS
  v_in  NUMBER;
  v_out NUMBER;
BEGIN
  SELECT amt INTO v_in FROM src WHERE ROWNUM = 1;
  v_out := pkg.f(v_in);
  INSERT INTO tgt (amt) VALUES (v_out);
END;
/
"""


def _variable_sources(source: str, dictionary: Dictionary) -> set[str]:
    result = analyse_source(source, dictionary)
    return {
        edge.source.name
        for edge in result.edges
        if edge.flow is Flow.VALUE and edge.source.kind is NodeKind.VARIABLE
    }


def test_a_loop_index_is_not_a_value_source_in_an_assignment(dictionary: Dictionary) -> None:
    """Without the fix, `I` appears as a value source of L_ADJUSTED and of TGT.AMT."""
    sources = _variable_sources(SUBSCRIPT_IN_ASSIGNMENT, dictionary)

    assert "I" not in sources, "the loop index leaked as a value source"
    assert "L_BATCH" in sources, "the collection itself is the real source and was lost"


def test_a_loop_index_is_not_a_value_source_in_a_values_list(dictionary: Dictionary) -> None:
    """The second call site.

    Tested separately because the two paths find the subscript differently - one scans
    text, the other walks an AST - and a fix applied to one of them would leave the other
    emitting the same wrong edge. Four call sites had to be found for D-5; this is the
    same lesson with two.
    """
    sources = _variable_sources(SUBSCRIPT_IN_VALUES, dictionary)

    assert "I" not in sources, "the loop index leaked from a VALUES list"


def test_a_real_functions_argument_is_still_a_value_source(dictionary: Dictionary) -> None:
    """The rule must not become "call arguments are never values".

    `pkg.f(v_in)` is a genuine call and `v_in` genuinely contributes. What separates it
    from `l_batch(i)` is that `pkg` is not a declared variable - which is why the test is a
    scope lookup rather than a shape match.
    """
    assert "V_IN" in _variable_sources(REAL_FUNCTION_CALL, dictionary), (
        "a real function call's argument was excluded as if it were a subscript"
    )


def test_the_index_still_reaches_nothing_at_all(dictionary: Dictionary) -> None:
    """No flow, not just no value flow.

    An index is not a filter either - it removes no rows - so the honest outcome is that
    `I` appears nowhere in the IR. Checked explicitly, because "moved it to another flow"
    is the failure mode a value-only assertion would pass.
    """
    result = analyse_source(SUBSCRIPT_IN_ASSIGNMENT, dictionary)

    assert not [
        edge
        for edge in result.edges
        if edge.source.kind is NodeKind.VARIABLE and edge.source.name == "I"
    ], "the index was reclassified rather than dropped"
