"""What does not supply a value at one scope does not supply a value at two.

Stress finding S3-02, found by stress package 3 on 2026-09-12.

`_value_columns` encodes two exclusions that the whole IR rests on:

  * a window's PARTITION BY / ORDER BY supply no value - they decide which value the
    column receives (S2-12, decision D-4);
  * a correlated predicate's columns supply no value - they decide which row (D-5).

Both were applied to the projection the caller could SEE, and `_trace`'s recursion into a
subquery then walked `projection.find_all(exp.Column)` - every column, exclusions gone.
So the rules held for a window written in the statement that writes, and failed for the
same window moved one level down into a CTE.

**A rule that depends on how the CTEs are stacked is not a rule.** That sentence is
already in `_grouping_influence`, which learned it from `sq_02_cte_chain`; this is the
same lesson in the function next door.

WHY IT IS THE DANGEROUS DIRECTION. The leak produces FALSE VALUE EDGES: it says
`cust_id` contributed to a number when it only decided the row ordering. A missing edge
is a gap someone can see in the coverage statement. A confident wrong edge is the silent
failure this project exists to prevent, and it is indistinguishable from a real one.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["ID", "AMT", "DT", "CAT"],
    f"{SCHEMA}.TGT": ["ID", "AMT", "N"],
    f"{SCHEMA}.LOOKUP": ["ID", "VAL"],
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


# The window is INSIDE the CTE and its result is selected by the outer query. Written
# flat - window and INSERT in one statement - this already worked; the nesting is the
# whole test.
WINDOW_IN_CTE = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  INSERT INTO tgt (id, amt)
  WITH ranked AS (
    SELECT s.id,
           LAG(s.amt) OVER (PARTITION BY s.cat ORDER BY s.dt) AS prev_amt
      FROM src s
  )
  SELECT r.id, r.prev_amt FROM ranked r;
END;
/
"""

# The same window written flat, as the control. If this ever breaks, the defect is in
# D-4 itself rather than in the recursion.
WINDOW_FLAT = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  INSERT INTO tgt (id, amt)
  SELECT s.id, LAG(s.amt) OVER (PARTITION BY s.cat ORDER BY s.dt)
    FROM src s;
END;
/
"""

# A correlated scalar aggregate inside a subquery that the outer query selects from.
# Same recursion, the other exclusion.
CORRELATED_IN_SUBQUERY = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  INSERT INTO tgt (id, amt)
  SELECT u.id, u.total
    FROM (SELECT s.id,
                 (SELECT SUM(l.val) FROM lookup l WHERE l.id = s.id) AS total
            FROM src s) u;
END;
/
"""


def _value_sources(source: str, dictionary: Dictionary, target: str) -> set[str]:
    result = analyse_source(source, dictionary)
    return {
        edge.source.name
        for edge in result.edges
        if edge.flow is Flow.VALUE and edge.target.name == target
    }


def test_a_windows_partition_does_not_become_a_value_source_through_a_cte(
    dictionary: Dictionary,
) -> None:
    """Without the fix, SRC.CAT and SRC.DT both arrive as value sources of TGT.AMT."""
    sources = _value_sources(WINDOW_IN_CTE, dictionary, "TGT.AMT")

    assert "SRC.AMT" in sources, "the window's ARGUMENT is a real value source and was lost"
    assert "SRC.CAT" not in sources, "PARTITION BY leaked as a value source through the CTE"
    assert "SRC.DT" not in sources, "ORDER BY leaked as a value source through the CTE"


def test_the_flat_window_behaves_identically(dictionary: Dictionary) -> None:
    """The control. Nesting must not change the answer - that is the entire finding.

    Kept separate from the test above so a future failure says WHICH of the two broke: a
    regression in D-4 itself, or a regression in the recursion that reaches it.
    """
    assert _value_sources(WINDOW_FLAT, dictionary, "TGT.AMT") == _value_sources(
        WINDOW_IN_CTE, dictionary, "TGT.AMT"
    )


def test_the_partition_is_still_DECLARED_as_influence_through_a_cte(
    dictionary: Dictionary,
) -> None:
    """Not leaking as `value` is half the fix; the dependency is real and must be stated.

    The leak and the loss were one defect with two faces - the partition columns came out
    as `value` because the recursion dropped the exclusion, and did not come out as
    `influence` because the search never descended. **Silence is the worse half to leave**,
    and it is the half a precision-only test would have declared fixed.
    """
    result = analyse_source(WINDOW_IN_CTE, dictionary)
    influence = {
        edge.source.name
        for edge in result.edges
        if edge.flow is Flow.INFLUENCE and edge.target.name == "TGT.AMT"
    }

    assert influence == {"SRC.CAT", "SRC.DT"}, (
        "PARTITION BY / ORDER BY inside a CTE produced no influence edge - the dependency "
        "is gone from the IR rather than merely misclassified"
    )


def test_nesting_changes_nothing_about_influence_either(dictionary: Dictionary) -> None:
    """The flat control, for influence as well as value.

    Two controls rather than one because the two halves can regress independently, and
    "which half broke" is the first question anyone will ask.
    """

    def influence_of(source: str) -> set[str]:
        return {
            edge.source.name
            for edge in analyse_source(source, dictionary).edges
            if edge.flow is Flow.INFLUENCE and edge.target.name == "TGT.AMT"
        }

    assert influence_of(WINDOW_FLAT) == influence_of(WINDOW_IN_CTE)


def test_a_correlated_predicate_does_not_become_a_value_source_through_a_subquery(
    dictionary: Dictionary,
) -> None:
    """The second exclusion, same recursion.

    Recorded because it was found by accident: the fix for the window case removed this
    too, and stress 3's register entry had it diagnosed as a separate defect in a
    different module. One `find_all`, two findings.
    """
    sources = _value_sources(CORRELATED_IN_SUBQUERY, dictionary, "TGT.AMT")

    assert "LOOKUP.VAL" in sources, "the aggregate's argument is a real value source"
    assert "LOOKUP.ID" not in sources, "the correlation's inner column leaked as a value"
    assert "SRC.ID" not in sources, "the correlation's outer column leaked as a value"
