"""A MERGE's USING clause groups and windows like any other query.

Stress finding S4-03, found by stress package 4 on 2026-09-12.

S3-03 gave a `MERGE` its FILTER edges and stopped there. `_grouping_influence` and
`_window_influence` were never reached from this statement type at all, so:

    MERGE INTO t USING (SELECT cust_id, SUM(amount) AS total
                          FROM src GROUP BY cust_id) x
    ON (t.cust_id = x.cust_id)
    WHEN MATCHED THEN UPDATE SET t.total = x.total

said nothing about the `GROUP BY` that decides what `x.total` is. D-2 makes a grouping
`influence` onto every aggregated column and D-4 does the same for a window's ordering;
neither reached the statement type a warehouse does its upserts with.

**Reused rather than reimplemented.** Both resolvers walk outward from the setter's
expression through the USING scope, so a `GROUP BY` four CTE levels inside the `USING` is
found exactly as it is under an `INSERT ... SELECT`. A MERGE-shaped copy of either would be
the S2-08 mistake for the third time.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import Flow
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"
COLUMNS = {
    f"{SCHEMA}.SRC": ["SID", "AMT", "REGION", "DT"],
    f"{SCHEMA}.TGT": ["SID", "TOTAL", "RANKING"],
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


GROUPED_USING = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid AS sid, s.region AS region, SUM(s.amt) AS total
           FROM src s
          GROUP BY s.sid, s.region) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.total = x.total;
END;
/
"""

# The grouping is four levels down, to prove the resolver walks rather than peeks.
GROUPED_DEEP_IN_A_CTE_CHAIN = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (
    WITH lvl1 AS (SELECT s.sid AS sid, s.region AS region, s.amt AS amt FROM src s),
         lvl2 AS (SELECT a.sid AS sid, a.region AS region, a.amt AS amt FROM lvl1 a),
         lvl3 AS (SELECT b.sid AS sid, b.region AS region, SUM(b.amt) AS total
                    FROM lvl2 b GROUP BY b.sid, b.region)
    SELECT c.sid AS sid, c.total AS total FROM lvl3 c
  ) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.total = x.total;
END;
/
"""

WINDOWED_USING = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid AS sid,
                RANK() OVER (PARTITION BY s.region ORDER BY s.dt) AS ranking
           FROM src s) x
  ON (t.sid = x.sid)
  WHEN MATCHED THEN UPDATE SET t.ranking = x.ranking;
END;
/
"""

INSERT_ARM_ONLY = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid AS sid, SUM(s.amt) AS total FROM src s GROUP BY s.sid) x
  ON (t.sid = x.sid)
  WHEN NOT MATCHED THEN INSERT (sid, total) VALUES (x.sid, x.total);
END;
/
"""


def _influence(source: str, dictionary: Dictionary, target: str) -> set[str]:
    return {
        e.source.name
        for e in analyse_source(source, dictionary).edges
        if e.flow is Flow.INFLUENCE and e.target.name == target
    }


def test_a_group_by_in_the_using_clause_influences_the_written_column(
    dictionary: Dictionary,
) -> None:
    """Without the fix a MERGE emits no influence edge from anywhere."""
    assert _influence(GROUPED_USING, dictionary, "TGT.TOTAL") == {"SRC.SID", "SRC.REGION"}


def test_the_grouping_is_found_four_levels_inside_the_using_clause(
    dictionary: Dictionary,
) -> None:
    """The reason both resolvers are reused rather than reimplemented.

    A MERGE-shaped version would almost certainly have read the USING subquery's own
    `GROUP BY` and stopped - which is what `_grouping_influence` itself did before
    `sq_02_cte_chain`, and the sentence "a rule that depends on how the CTEs are stacked is
    not a rule" is already in its docstring because of it.
    """
    assert _influence(GROUPED_DEEP_IN_A_CTE_CHAIN, dictionary, "TGT.TOTAL") == {
        "SRC.SID",
        "SRC.REGION",
    }


def test_a_window_in_the_using_clause_influences_the_written_column(
    dictionary: Dictionary,
) -> None:
    """D-4's half. RANK() takes no argument, so influence is ALL it can contribute."""
    assert _influence(WINDOWED_USING, dictionary, "TGT.RANKING") == {"SRC.REGION", "SRC.DT"}


def test_the_not_matched_arm_gets_influence_too(dictionary: Dictionary) -> None:
    """Both arms, not just the one that raised the finding.

    S3-03 added filters to the MERGE and S3-07's lookup to both arms; doing this to the
    UPDATE arm alone would leave an insert-only MERGE - a perfectly ordinary statement -
    silent about its own grouping.
    """
    assert _influence(INSERT_ARM_ONLY, dictionary, "TGT.TOTAL") == {"SRC.SID"}


def test_a_grouping_key_written_through_takes_no_influence(dictionary: Dictionary) -> None:
    """D-2's exclusion has to survive being reached from a new call site.

    `x.sid` is a grouping key copied into `tgt.sid` unchanged, so influence onto it would
    say a column decides its own value. Checked here because this fix opened a path to
    `_grouping_influence` that never existed before, and S3-08 is on the register for
    getting this exclusion wrong by reading it as a name match.
    """
    source = """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  MERGE INTO tgt t
  USING (SELECT s.sid AS sid, SUM(s.amt) AS total FROM src s GROUP BY s.sid) x
  ON (t.sid = x.sid)
  WHEN NOT MATCHED THEN INSERT (sid, total) VALUES (x.sid, x.total);
END;
/
"""
    assert _influence(source, dictionary, "TGT.SID") == set()
