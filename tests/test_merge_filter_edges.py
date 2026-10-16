"""A MERGE filters in two places, and used to report neither.

Stress finding S3-03, found by stress package 3 on 2026-09-12.

`_analyse_merge` built value edges only. So this, in the statement type a warehouse does
its upserts with:

    USING (SELECT ... FROM stg_customer c WHERE c.signup_date < SYSDATE) s

produced nothing at all - a predicate deciding which customers the load touches, absent
from the IR. **That is the finding filter lineage exists for.** ADR-0001 2 makes the case on
a policy table silently governing which rows load, and a MERGE's USING clause is where a
warehouse writes exactly that.

The second place is the arm: `WHEN MATCHED THEN UPDATE SET ... WHERE s.total > 0`. It sits
on the `Update` rather than on the `WHEN`, and it decides which matched rows are actually
written.

THE ON CLAUSE IS NOT ONE OF THE TWO, and that is deliberate. It is a join condition,
structural wherever it is written (D-5), and reading it as a filter is precisely what
S2-14 was.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow, NodeKind
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["SID", "AMT", "DT", "REGION"],
    f"{SCHEMA}.TGT": ["SID", "AMT", "REGION"],
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


USING_WHERE = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid, s.amt, s.region FROM src s WHERE s.dt < SYSDATE) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.amt = x.amt
  WHEN NOT MATCHED THEN INSERT (sid, amt) VALUES (x.sid, x.amt);
END;
/
"""

ARM_WHERE = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid, s.amt, s.region FROM src s) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.amt = x.amt WHERE x.region = 'EU';
END;
/
"""


def _filter_sources(source: str, dictionary: Dictionary) -> set[str]:
    result = analyse_source(source, dictionary)
    return {
        edge.source.name
        for edge in result.edges
        if edge.flow is Flow.FILTER and edge.target.kind is NodeKind.RELATION
    }


def test_the_using_clauses_where_is_a_filter(dictionary: Dictionary) -> None:
    """Without the fix a MERGE emits no filter edge from anywhere."""
    assert "SRC.DT" in _filter_sources(USING_WHERE, dictionary)


def test_the_arms_where_is_a_filter(dictionary: Dictionary) -> None:
    """The second site, tested separately because it lives on the Update, not the WHEN.

    A fix that only walked the USING scope would pass the test above and leave this one
    failing - and an arm-level WHERE is how a MERGE expresses "only restate the rows that
    actually changed", which is not a rare thing to write.
    """
    assert "SRC.REGION" in _filter_sources(ARM_WHERE, dictionary)


def test_the_on_clause_is_still_not_a_filter(dictionary: Dictionary) -> None:
    """The guard in the other direction, and the reason this fix could have gone wrong.

    `ON (t.sid = x.sid)` is a join condition. D-5 made those structural wherever they are
    written, and S2-14 is on the register because the same clause got two answers depending
    on where it sat. Emitting filter edges for a MERGE's ON clause would have scored as
    recall while being exactly the defect D-5 closed.
    """
    sources = _filter_sources(USING_WHERE, dictionary)

    assert "SRC.SID" not in sources, "the ON clause was read as a filter"
    assert "TGT.SID" not in sources, "the ON clause was read as a filter"


def test_a_merge_with_no_predicate_emits_no_filter_edge(dictionary: Dictionary) -> None:
    """Nothing invented when there is nothing to report.

    The cheap way to pass the tests above is to sweep every column in the statement into a
    filter edge; this is what says the fix reads predicates rather than everything.
    """
    no_predicate = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid, s.amt FROM src s) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.amt = x.amt;
END;
/
"""
    assert _filter_sources(no_predicate, dictionary) == set()
