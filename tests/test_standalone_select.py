"""PG-09: a standalone SELECT is counted and refused, never silent.

It parses, every relation and predicate in it is readable, and it has NO TARGET - the
result leaves the database rather than landing in a column, so an edge has no far end.
Until 2026-09-24 that made it silent: 0 edges, 0 refusals, 0 boundaries, and
`statements_seen = 0`, so a file of nothing but `SELECT`s returned a clean run at 100%
parse coverage with no output and nothing recording that anything had been seen.

**The danger is the shape of the failure, not its size.** Read-only analytical SQL is a
large share of what a lineage tool gets pointed at - view bodies, BI queries, report
definitions, dbt models - and the user of a silent analyser cannot tell "nothing flows
here" from "I did not look".

A refusal costs one statement of parse coverage and converts the silence into a statement.
Naming a target for a projection is a model decision that interacts with S1-05's identity
work; it is deliberately NOT made here.

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
    SELECT o.cust_id, SUM(o.gross_amount)
      FROM stg_orders o
      JOIN stg_customer c ON c.cust_id = o.cust_id
     WHERE o.currency = 'GBP'
     GROUP BY o.cust_id;
"""


def test_a_standalone_select_is_counted_and_refused(postgres: Dictionary) -> None:
    result = run(BARE, postgres)

    assert result.statements_seen == 1, "counted is the whole point - silence is the defect"
    assert len(result.refusals) == 1
    assert result.refusals[0].code.value == "UNSUPPORTED_CONSTRUCT"
    assert not result.edges, "no target exists, so no edge may be claimed for one"


def test_the_refusal_reaches_parse_coverage(postgres: Dictionary) -> None:
    """A refusal is never free, and that is what makes it honest.

    A classifier that abstained from everything would report 0% coverage rather than 100%
    precision. Here the single statement is seen and not analysed, so coverage is 0% - and
    0% is a true statement about a file the analyser cannot do anything with, where 100%
    was a false one.
    """
    result = run(BARE, postgres)
    assert result.statements_analysed == 0
    assert result.parse_coverage == 0.0


def test_a_select_beside_an_insert_leaves_the_insert_alone(postgres: Dictionary) -> None:
    """Containment: the refusal must not touch the statement next to it."""
    result = run(
        "SELECT a.cust_id FROM stg_orders a;\n"
        "INSERT INTO fct_revenue (cust_id) SELECT o.cust_id FROM stg_orders o;",
        postgres,
    )
    assert result.statements_seen == 2
    assert result.statements_analysed == 1
    assert len(result.refusals) == 1
    assert len(result.edges) == 1


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
