"""PG-09: a standalone SELECT produces lineage into its own projections.

It parses, every relation and predicate in it is readable, and it has no target table -
the result is the output of the query itself. Until 2026-09-24 that made it SILENT: 0
edges, 0 refusals, 0 boundaries and `statements_seen = 0`, so a file of nothing but
`SELECT`s returned a clean run at 100% parse coverage with no output and nothing anywhere
recording that anything had been seen.

**The danger is the shape of the failure, not its size.** Read-only analytical SQL is a
large share of what a lineage tool gets pointed at - view bodies, BI queries, report
definitions, dbt and Databricks models - and the user of a silent analyser cannot tell
"nothing flows here" from "I did not look".

It was refused for part of that day, which was the honest interim while naming the far end
of the edge was still an open model decision. The decision is now taken and has two halves:

* nothing supplied - the projections land on `<result>`, so any pasted query yields
  lineage with no configuration. Enough to read ONE query, not enough to tell two apart.
* a `result_target` supplied - they land on that relation instead, exactly as an `INSERT`
  into it would, which is what lets a directory of model files chain together.

**The Oracle half of this file is the load-bearing half.** Both frontends call a query
`query_block`, so kind alone cannot separate PostgreSQL's bare SELECT from Oracle's
`SELECT ... INTO` - and refusing the latter would refuse a construct def-use handles and
that the signed phase-0 measurement depends on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def postgres() -> Dictionary:
    return Dictionary.load(ROOT / "stress" / "dictionary_postgres.json")


@pytest.fixture(scope="module")
def oracle() -> Dictionary:
    return Dictionary.load(ROOT / "corpus" / "dictionary.json")


def run(sql: str, book: Dictionary):
    config = AnalysisConfig.load().model_copy(update={"dialect": book.dialect})
    return analyse_source(sql, book, config, None)


BARE = """
    SELECT o.cust_id, SUM(o.gross_amount) AS total
      FROM stg_orders o
      JOIN stg_customer c ON c.cust_id = o.cust_id
     WHERE o.currency = 'GBP'
     GROUP BY o.cust_id;
"""


def test_a_standalone_select_is_counted_and_analysed(postgres: Dictionary) -> None:
    result = run(BARE, postgres)

    assert result.statements_seen == 1, "counted is the floor - silence is the defect"
    assert result.refusals == []
    assert result.edges, "a query's own projections are a real place for lineage to land"


def test_its_projections_become_the_target_columns(postgres: Dictionary) -> None:
    """The output columns are named by the query, so the edges are readable without a table.

    `SUM(o.gross_amount)` is aliased `total`, so the edge lands on `<result>.total` - and
    `gross_amount` reaching it through a SUM must still be `aggregated`, which is the whole
    delegation to the INSERT path working.
    """
    result = run(BARE, postgres)
    edges = {
        (str(e.source), str(e.target), e.flow.value, e.transform.value) for e in result.edges
    }
    assert (
        "column:stg_orders.gross_amount",
        "column:<result>.total",
        "value",
        "aggregated",
    ) in edges
    assert (
        "column:stg_orders.cust_id",
        "column:<result>.cust_id",
        "value",
        "identity",
    ) in edges


def test_a_named_target_replaces_the_synthetic_one(postgres: Dictionary) -> None:
    """What makes model-to-model lineage possible.

    With `<result>` every file writes into the same relation and two of them cannot be told
    apart. Given the model's own table name, the edges land there - so the columns this
    file produces are named the same way the NEXT file's FROM clause will name them, and
    the lineage crosses the file boundary.
    """
    config = AnalysisConfig.load().model_copy(update={"dialect": postgres.dialect})
    result = analyse_source(
        BARE, postgres, config, None, result_target="public.fct_revenue"
    )
    targets = {str(e.target) for e in result.edges}
    assert "column:fct_revenue.total" in targets
    assert not any("<result>" in t for t in targets), "the synthetic name leaked through"


def test_a_select_beside_an_insert_leaves_the_insert_alone(postgres: Dictionary) -> None:
    """Containment: each statement gets its own target, and neither disturbs the other."""
    result = run(
        "SELECT a.cust_id FROM stg_orders a;\n"
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id FROM stg_orders o;",
        postgres,
    )
    assert result.statements_seen == 2
    assert result.statements_analysed == 2
    assert result.refusals == []
    # One edge into the INSERT's table, one into the standalone query's own result.
    targets = {str(e.target) for e in result.edges}
    assert "column:fct_revenue.cust_id" in targets
    assert "column:<result>.cust_id" in targets


def test_a_select_inside_an_insert_is_not_refused(postgres: Dictionary) -> None:
    """The obvious over-reach, checked rather than assumed.

    `INSERT ... SELECT` contains a query block and is the commonest statement in both
    corpora. A rule that matched on the query rather than on its position would refuse
    every load in the project.
    """
    result = run(
        "INSERT INTO fct_revenue (cust_id, net_amount)"
        " SELECT o.cust_id, SUM(o.gross_amount) FROM stg_orders o GROUP BY o.cust_id;",
        postgres,
    )
    assert result.refusals == []
    assert result.edges


def test_oracle_select_into_is_untouched(oracle: Dictionary) -> None:
    """THE LOAD-BEARING TEST. Oracle's `SELECT ... INTO` shares the kind `query_block`.

    It is analysed by def-use, it carries band-1 edges the gate is computed from, and
    refusing it would move a signed measurement. The refusal is scoped by POSITION - only
    the script frontend produces the synthetic `<script>` unit - so an Oracle program
    cannot satisfy it by construction rather than by a name coincidence. This test is what
    makes that claim checkable.
    """
    result = run(
        """
        CREATE OR REPLACE PROCEDURE p_select_into IS
            v_total NUMBER;
        BEGIN
            SELECT SUM(o.gross_amount) INTO v_total
              FROM stg_orders o
             WHERE o.currency = 'GBP';
        END p_select_into;
        /
        """,
        oracle,
    )
    assert result.refusals == [], f"Oracle SELECT INTO wrongly refused: {result.refusals}"
    assert result.edges, "the def-use edges from SELECT ... INTO disappeared"
