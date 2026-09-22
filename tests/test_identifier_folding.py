"""No identifier reaches a node name unfolded.

A2's problem is that `fold` is `upper` for Oracle, so **every folding edit is invisible on
the only dialect there is**. A site left as `.upper()` behaves identically to a folded one,
and the mistake surfaces for the first time on a PostgreSQL corpus that does not exist yet.
That is precisely the silent-failure shape this project exists to avoid, so it needs a check
that can fail today.

**The check is a dialect that folds the other way.** `_LowerFoldingOracle` keeps Oracle's
name for SQLGlot - so the SQL is parsed by exactly the same grammar, and nothing about the
analysis changes - and folds identifiers DOWN. Run against a dictionary whose keys are
lower-cased, every identifier that went through `fold` comes out lower-case, and every
identifier that is still `.upper()` arrives SHOUTING in the middle of a node name.

This is not a PostgreSQL test. It is a test that the folding seam is complete, which is a
different and more useful thing: it will keep failing for any future dialect that does not
fold upward, whichever one that turns out to be.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.dialects import SUPPORTED, register
from lineage.ir.model import NodeKind
from lineage.resolution.dictionary import Dictionary

DIALECT = "redshift"


class _LowerFolding:
    """A dialect that folds DOWN, used to make A2's edits visible.

    **`name` and `fold` are not independent, and the first version of this class proved it.**
    It used `name="oracle"` with `fold=lower`, reasoning that holding the grammar fixed would
    isolate folding from every other difference. That dialect cannot exist: SQLGlot's
    `normalize_identifiers` is keyed on the dialect NAME, so `qualify` normalised every
    identifier UP to `CUST_ID` while the dictionary was keyed lower - and the analysis
    produced zero edges. The "nothing escaped folding" assertion passed perfectly over a
    total loss, which is why that test has a control pinned beside it.

    So the pair has to be coherent. `redshift` is used rather than `postgres` because
    PostgreSQL is now a REAL registered dialect with its own front end, and registering an
    instrument over it would both collide with it and tear it out of the registry on
    teardown. Redshift normalises down in SQLGlot exactly as PostgreSQL does, and is not
    otherwise implemented - so this stays a test of FOLDING rather than of any dialect.
    """

    @property
    def name(self) -> str:
        return "redshift"

    @property
    def frontend(self):  # type: ignore[no-untyped-def]
        """Oracle's front end, on purpose.

        The source below is PL/SQL, and holding the parser fixed is what isolates folding
        from every other difference between dialects - which is the whole point of the
        instrument.
        """
        from lineage.parsing.plsql import ORACLE_FRONTEND

        return ORACLE_FRONTEND

    def fold(self, identifier: str) -> str:
        return identifier.lower()


SCHEMA = "lineage"
COLUMNS = {
    f"{SCHEMA}.stg_orders": ["order_id", "cust_id", "order_date", "gross_amount", "currency"],
    f"{SCHEMA}.dim_customer": ["cust_id", "region", "is_active"],
    f"{SCHEMA}.ref_policy": ["region", "window_days"],
    f"{SCHEMA}.fct_revenue": ["cust_id", "period_month", "net_amount", "order_count"],
}


@pytest.fixture
def lower_dialect() -> None:
    """Registered under its SQLGlot name, because the registry now insists on it.

    `register` refuses a key that is not the dialect's own name, and refuses a `fold` that
    disagrees with SQLGlot's normalisation for it - both learned from this test failing.
    """
    register(_LowerFolding())  # type: ignore[arg-type]
    yield
    SUPPORTED.pop(DIALECT, None)


@pytest.fixture
def dictionary() -> Dictionary:
    """A snapshot keyed the way a lower-folding database would store it."""
    return Dictionary(
        captured_at="2026-09-14T00:00:00Z",
        default_schema=SCHEMA,
        dialect=DIALECT,
        objects={
            name: {"owner": SCHEMA, "name": name.split(".")[1], "object_type": "TABLE"}
            for name in COLUMNS
        },
        columns=COLUMNS,
        temporary={name: False for name in COLUMNS},
    )


# Deliberately wide: a join, a filter on each side, a grouping, a HAVING, a variable
# governing rows, a cursor loop with a record field, a row-level write and an UPDATE. Each
# one reaches a different family of folding sites.
SOURCE = """CREATE OR REPLACE PROCEDURE p_report(p_region VARCHAR2) IS
  v_days   NUMBER;
  v_cutoff DATE;
  v_total  NUMBER;
  CURSOR c_cust IS
    SELECT o.cust_id AS cust_id, SUM(o.gross_amount) AS gross_total
      FROM stg_orders o
      JOIN dim_customer d
        ON d.cust_id = o.cust_id
     WHERE d.is_active = 1
       AND o.currency = 'GBP'
     GROUP BY o.cust_id
    HAVING SUM(o.gross_amount) > 0;
BEGIN
  SELECT r.window_days INTO v_days FROM ref_policy r WHERE r.region = p_region;
  v_cutoff := SYSDATE - v_days;

  INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
  SELECT o.cust_id, TRUNC(o.order_date, 'MM'), SUM(o.gross_amount), COUNT(*)
    FROM stg_orders o
   WHERE o.order_date >= v_cutoff
   GROUP BY o.cust_id, TRUNC(o.order_date, 'MM');

  FOR rec IN c_cust LOOP
    v_total := rec.gross_total;
    INSERT INTO fct_revenue (cust_id, net_amount) VALUES (rec.cust_id, v_total);
  END LOOP;

  UPDATE fct_revenue SET net_amount = v_total WHERE cust_id = v_days;
END p_report;
/
"""


def _shouting(text: str) -> bool:
    """Any upper-case letter at all. Node names here should be entirely lower-cased."""
    return any(character.isupper() for character in text)


def test_no_column_or_relation_node_escapes_folding(
    lower_dialect: None, dictionary: Dictionary
) -> None:
    """The whole point of A2, in one assertion.

    Every COLUMN and RELATION node name is a database identifier, so under a lower-folding
    dialect every one of them must be lower-case. A single `.upper()` left on a path that
    builds a node name shows up here as an upper-case fragment - `fct_revenue.NET_AMOUNT`,
    or `STG_ORDERS.cust_id` - which is a name no catalogue contains.
    """
    result = analyse_source(SOURCE, dictionary, AnalysisConfig(dialect=DIALECT))

    offenders = sorted(
        {
            f"{edge.origin.unit}: {edge.source.name} -> {edge.target.name}"
            for edge in result.edges
            for node in (edge.source, edge.target)
            if node.kind in (NodeKind.COLUMN, NodeKind.RELATION) and _shouting(node.name)
        }
    )
    assert offenders == [], "identifiers reached a node name without being folded"


def test_the_analysis_still_produces_edges_under_the_other_folding(
    lower_dialect: None, dictionary: Dictionary
) -> None:
    """The control, and it is not a formality.

    An empty edge list would satisfy the assertion above perfectly. If folding were wired up
    wrongly - names folded on the way in but not in the dictionary keys, say - every name
    would fail to resolve and the test above would pass over a total loss. So the count is
    pinned too: this source produces column-to-column lineage, and it must still do so.
    """
    result = analyse_source(SOURCE, dictionary, AnalysisConfig(dialect=DIALECT))

    columns = [
        edge
        for edge in result.edges
        if edge.source.kind is NodeKind.COLUMN and edge.target.kind is NodeKind.COLUMN
    ]
    assert len(columns) >= 6, f"only {len(columns)} column edges - names are failing to bind"

    names = {edge.source.name for edge in columns} | {edge.target.name for edge in columns}
    assert "stg_orders.gross_amount" in names
    assert "fct_revenue.net_amount" in names


def test_the_same_source_under_oracle_is_upper_cased(dictionary: Dictionary) -> None:
    """The other half of the control: folding really is what makes the difference.

    Same source, same analyser, an Oracle dictionary - and every name comes out upper. If
    this failed, the test above would be measuring something other than the fold.
    """
    oracle_dictionary = Dictionary(
        captured_at="2026-09-14T00:00:00Z",
        default_schema="LINEAGE",
        objects={
            name.upper(): {
                "owner": "LINEAGE",
                "name": name.split(".")[1].upper(),
                "object_type": "TABLE",
            }
            for name in COLUMNS
        },
        columns={name.upper(): [c.upper() for c in cols] for name, cols in COLUMNS.items()},
        temporary={name.upper(): False for name in COLUMNS},
    )
    result = analyse_source(SOURCE, oracle_dictionary, AnalysisConfig())

    names = {
        node.name
        for edge in result.edges
        for node in (edge.source, edge.target)
        if node.kind is NodeKind.COLUMN
    }
    assert names, "the Oracle control produced no column edges at all"
    assert all(name == name.upper() for name in names)
