"""A construct confined to one CTE refuses that CTE, not the statement (PG-20).

**What the finding was.** The refusal unit was the STATEMENT, and a modern analytical query
is one statement with eighty-five CTEs in it. One `JSON_TABLE` in one CTE therefore
discarded the other eighty-four, which are ordinary joins and aggregates the analyser reads
perfectly well. Measured on the torture variant: **0 edges where 329 were available**, a
ceiling of 334 with no poisoned CTE at all. This was the third finding pointing at refusal
granularity, with PG-06 and PG-16.

**The two halves of the fix, and why the second one is the load-bearing one.** Narrowing the
refusal is easy. Making it safe is not: a construct the CLASSIFIER refuses is not thereby a
construct SQLGLOT declines to interpret, so leaving the body in the tree and merely
declining to trust it would let the refused CTE keep contributing. `CONNECT BY` is the
recorded case - it parses cleanly and emits three `x -> x` self-edges. So the poisoned CTE
is REMOVED from the tree, and what remains is a reference to a relation nobody defines,
which is exactly what it is.

**The tests that matter here are the negative ones.** That the recovered edges appear is one
assertion; that nothing inside the refused CTE contributes, that a construct in the main
query still refuses everything, and that a CTE name which is not in the parsed tree falls
back rather than silently dropping nothing, are the ones that keep the fix honest.
"""

from __future__ import annotations

from typing import Any

import pytest

import lineage.analysis.band0 as band0
from lineage.analysis.procedure import analyse_source
from lineage.analysis.refusal import PoisonedCte, RefusalCode, cte_bodies, cte_scoped_construct
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary, ObjectInfo


def _book() -> Dictionary:
    """A captured dictionary. This is about refusal granularity, not schema inference."""
    names = ("silver.orders", "silver.shipments", "gold.tgt")
    return Dictionary(
        captured_at="2026-09-30T00:00:00Z",
        default_schema="silver",
        dialect="postgres",
        objects={
            name: ObjectInfo(
                owner=name.split(".")[0], name=name.split(".")[1], object_type="TABLE"
            )
            for name in names
        },
        columns={
            "silver.orders": ["id", "amount", "region"],
            "silver.shipments": ["id", "header_attrs"],
            "gold.tgt": ["a", "b", "c"],
        },
        temporary=dict.fromkeys(names, False),
    )


def _result(sql: str) -> Any:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, _book(), config, None)


def _edges(sql: str) -> set[tuple[str, ...]]:
    return {
        (str(e.source), str(e.target), e.flow.value, e.transform.value)
        for e in _result(sql).edges
    }


# One readable CTE, one poisoned CTE, one CTE that aggregates the readable one. The shape
# of every warehouse model that touches a document column anywhere in its eighty-five
# stages.
POISONED = """
INSERT INTO gold.tgt (a, b, c)
WITH clean_orders AS (
    SELECT o.id AS id, o.amount AS amount, o.region AS region
    FROM   silver.orders o
    WHERE  o.region = 'EU'
),
doc_rows AS (
    SELECT jt.pkg_id AS pkg_id, jt.weight AS weight
    FROM   silver.shipments s,
           JSON_TABLE(s.header_attrs, '$.packages[*]'
               COLUMNS (pkg_id text PATH '$.id', weight numeric PATH '$.kg')) AS jt
),
totals AS (
    SELECT c.id AS id, sum(c.amount) AS total
    FROM   clean_orders c
    GROUP  BY c.id
)
SELECT t.id, t.total, d.weight
FROM   totals t
JOIN   doc_rows d ON d.pkg_id = t.id::text
"""

# The same query with the poisoned CTE and everything reading it deleted by hand. This is
# the UPPER BOUND on what narrowing the refusal can recover, and it is written out rather
# than derived so that it cannot drift with the code under test.
HAND_SCOPED = """
INSERT INTO gold.tgt (a, b)
WITH clean_orders AS (
    SELECT o.id AS id, o.amount AS amount, o.region AS region
    FROM   silver.orders o
    WHERE  o.region = 'EU'
),
totals AS (
    SELECT c.id AS id, sum(c.amount) AS total
    FROM   clean_orders c
    GROUP  BY c.id
)
SELECT t.id, t.total
FROM   totals t
"""


def test_the_readable_ctes_yield_exactly_what_they_would_without_the_poisoned_one() -> None:
    """The load-bearing test. Before the fix the left side produced ZERO edges."""
    assert _edges(POISONED) == _edges(HAND_SCOPED)


def test_those_edges_are_not_an_empty_set() -> None:
    """Stated separately because the equality above is also satisfied by both sides
    producing nothing - a regression wearing a passing test as a disguise."""
    assert _edges(POISONED) == {
        ("column:orders.region", "relation:tgt", "filter", "identity"),
        ("column:orders.id", "column:tgt.a", "value", "identity"),
        ("column:orders.id", "column:tgt.b", "influence", "identity"),
        ("column:orders.amount", "column:tgt.b", "value", "aggregated"),
    }


def test_the_refusal_addresses_the_cte_and_not_the_statement() -> None:
    """The span is what makes `violations` mean something here.

    A single refusal covering the first and last poisoned CTE of eighty-five would swallow
    every clean CTE between them, and then every recovered edge would read as a
    contradiction. `doc_rows` occupies lines 8-13 of this statement; the statement itself
    starts at line 2.
    """
    result = _result(POISONED)

    assert [(r.scope, r.line, r.end_line) for r in result.refusals] == [("cte", 8, 13)]
    assert result.refusals[0].code is RefusalCode.SHAPE_NOT_DECIDABLE
    assert "doc_rows" in result.refusals[0].reason


def test_a_partially_refused_statement_counts_as_analysed_and_is_counted_apart() -> None:
    """The arithmetic. `statements_analysed` used to subtract every refusal, which would
    report `analysed 0` beside a non-empty edge list - not conservatism but a count that
    contradicts its own output. The withheld part is `partial_statements` instead."""
    result = _result(POISONED)

    assert result.statements_seen == 1
    assert result.statements_analysed == 1
    assert result.partial_statements == 1
    assert result.parse_coverage == 1.0


def test_no_edge_survives_from_the_refused_cte() -> None:
    """T3.1's cross-check, which is the whole point of recording a refusal as data."""
    assert _result(POISONED).refusal_violations() == []


def test_the_withheld_columns_are_declared_rather_than_dropped_in_silence() -> None:
    """`d.weight` has no traceable source and the reader must be told so, by name."""
    boundaries = _result(POISONED).boundaries
    subjects = {b.subject.upper() for b in boundaries}
    details = " ".join(b.detail for b in boundaries)

    assert "DOC_ROWS" in subjects
    assert "refused" in details
    assert "weight" in details


def test_the_same_construct_is_not_refused_twice_at_two_addresses() -> None:
    """`classify_program`'s source-level pass reports a construct at the line the construct
    sits on, while band 0 reports the line the STATEMENT starts on. Both fired, so one
    `JSON_TABLE` cost two `statements_seen` and reported coverage 0.5 for a statement that
    was analysed - measured on the torture file before the dedup was widened to a span
    test."""
    result = _result(POISONED)

    assert len(result.refusals) == 1
    assert result.statements_seen == 1


# --------------------------------------------------------------------------------------
# The negative half: everything the narrowing must NOT do.
# --------------------------------------------------------------------------------------

CONSTRUCT_IN_THE_MAIN_QUERY = """
INSERT INTO gold.tgt (a, b)
WITH clean_orders AS (
    SELECT o.id AS id, o.amount AS amount FROM silver.orders o
)
SELECT c.id, jt.weight
FROM   clean_orders c,
       JSON_TABLE(c.id, '$.packages[*]' COLUMNS (weight numeric PATH '$.kg')) AS jt
"""


def test_a_construct_in_the_main_query_still_refuses_the_whole_statement() -> None:
    """Nothing about this statement is confined, so nothing about it is narrowed. The old
    behaviour is the right answer here and must be reachable."""
    result = _result(CONSTRUCT_IN_THE_MAIN_QUERY)

    assert result.edges == []
    assert [r.scope for r in result.refusals] == ["statement"]
    assert result.partial_statements == 0
    assert result.statements_analysed == 0


TWO_DIFFERENT_CONSTRUCTS = """
INSERT INTO gold.tgt (a, b)
WITH doc_rows AS (
    SELECT jt.weight AS weight
    FROM   silver.shipments s,
           JSON_TABLE(s.header_attrs, '$' COLUMNS (weight numeric PATH '$.kg')) AS jt
),
hier AS (
    SELECT o.id AS id FROM silver.orders o START WITH o.id IS NULL
),
clean_orders AS (
    SELECT o.id AS id, o.amount AS amount FROM silver.orders o
)
SELECT c.id, c.amount FROM clean_orders c
"""


def test_two_different_constructs_fall_back_to_refusing_the_whole_statement() -> None:
    """A stated limitation rather than an accident: the confinement check classifies the
    FIRST construct, so a statement poisoned by two of them is refused whole. Narrowing
    that further means classifying every construct, which is a separate measurement. The
    cost is one refusal broader than it needs to be - the safe direction."""
    result = _result(TWO_DIFFERENT_CONSTRUCTS)

    assert result.edges == []
    assert [r.scope for r in result.refusals] == ["statement"]


PROVABLE_COLUMN_INSIDE_THE_POISONED_CTE = """
INSERT INTO gold.tgt (a, b)
WITH doc_rows AS (
    SELECT s.id AS ship_id,
           jt.weight AS weight
    FROM   silver.shipments s,
           JSON_TABLE(s.header_attrs, '$' COLUMNS (weight numeric PATH '$.kg')) AS jt
),
clean_orders AS (
    SELECT o.id AS id, o.amount AS amount FROM silver.orders o
)
SELECT c.id, d.ship_id
FROM   clean_orders c JOIN doc_rows d ON d.ship_id = c.id
"""


def test_an_ordinary_projection_inside_a_refused_cte_contributes_nothing() -> None:
    """The safety property, and it costs something real - which is recorded, not hidden.

    `s.id AS ship_id` is an ordinary column that owes nothing to the document, so
    `shipments.id -> tgt.b` is a TRUE edge this fix declines to make. Declining is
    deliberate: the refusal withdraws the CTE's columns, and a construct the classifier
    refuses may still be one SQLGlot interprets confidently enough to emit a guessed edge
    from - `CONNECT BY`'s `x -> x` self-edges are the recorded case. Trusting part of a
    body whose shape is not decidable would re-create exactly the guess the classifier
    exists to prevent.

    Narrowing further, to the COLUMNS of a poisoned CTE rather than the CTE, is the next
    step and is registered as PG-22 rather than taken here.
    """
    edges = _edges(PROVABLE_COLUMN_INSIDE_THE_POISONED_CTE)

    assert not [e for e in edges if e[0] == "column:shipments.id"]
    assert ("column:orders.id", "column:tgt.a", "value", "identity") in edges
    assert _result(PROVABLE_COLUMN_INSIDE_THE_POISONED_CTE).refusal_violations() == []


def test_a_cte_name_that_is_not_in_the_parsed_tree_refuses_the_whole_statement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard against the defect that nearly shipped inside this very fix.

    The first version of `_drop_ctes` read `parsed.args.get("with")`, which is None twice
    over - an INSERT's `WITH` hangs off the inner `Select`, and the key there is spelled
    `with_`. It found nothing, dropped nothing, and the reduction still produced the right
    four edges, because `JSON_TABLE` happens to dangle on its own. A silent no-op wearing a
    passing result, and the third time in this project that an argument key returned an
    empty answer where "not found" was meant.

    So the caller now compares what was dropped against what it asked for. This forces the
    mismatch by naming a CTE that does not exist, and the statement must be refused whole
    rather than analysed with the construct still in it.
    """
    real = band0.cte_scoped_construct

    def lying(text: str, dialect: str = "oracle") -> Any:
        answer = real(text, dialect)
        if answer is None:
            return None
        construct, _ = answer
        return construct, [PoisonedCte(name="no_such_cte", line=8, end_line=13)]

    monkeypatch.setattr(band0, "cte_scoped_construct", lying)
    result = _result(POISONED)

    assert result.edges == []
    assert result.statements_analysed == 0
    assert any("not CTEs of the parsed statement" in r.reason for r in result.refusals)


# --------------------------------------------------------------------------------------
# The scanner. Text-based on purpose: this module classifies constructs the SQL parser may
# not be able to read at all, so locating the CTEs with SQLGlot would make the refusal
# depend on the parse whose failure is the thing being refused.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "sql", "expected"),
    [
        ("no WITH clause", "INSERT INTO t (a) SELECT 1 FROM s", []),
        (
            "WITH ORDINALITY is not a CTE list",
            "INSERT INTO t (a) SELECT v FROM unnest(ARRAY[1]) WITH ORDINALITY AS u(v, n)",
            [],
        ),
        (
            "an explicit column list",
            "WITH a (x, y) AS (SELECT 1, 2), b AS (SELECT 3) SELECT * FROM a",
            ["a", "b"],
        ),
        (
            "PostgreSQL materialisation hints",
            "WITH a AS MATERIALIZED (SELECT 1), b AS NOT MATERIALIZED (SELECT 2) SELECT 1",
            ["a", "b"],
        ),
        (
            "a quoted name that is a reserved word",
            'WITH "select" AS (SELECT 1), b AS (SELECT 2) SELECT 1',
            ["select", "b"],
        ),
        ("RECURSIVE", "WITH RECURSIVE a AS (SELECT 1), b AS (SELECT 2) SELECT 1", ["a", "b"]),
    ],
)
def test_the_scanner_finds_the_cte_list(label: str, sql: str, expected: list[str]) -> None:
    assert [name for name, _, _ in cte_bodies(sql)] == expected, label


def test_the_scanner_counts_parentheses_rather_than_matching_them() -> None:
    """A CTE body is full of `count(*)`, `coalesce(a, b)` and nested subqueries, and a
    literal may contain an unbalanced one. Regex cannot do this, and getting it wrong in
    the silent direction was the shape of two separate defects in the rewrite rules."""
    sql = (
        "WITH bad AS (SELECT coalesce(count(*), 0) AS n FROM s "
        "WHERE s.k IN (SELECT k FROM other WHERE f(k, '(') = 1)), "
        "good AS (SELECT 1) SELECT 1"
    )
    bodies = cte_bodies(sql)

    assert [name for name, _, _ in bodies] == ["bad", "good"]
    assert sql[bodies[0][1] : bodies[0][2]].endswith("= 1)")


def test_a_construct_mentioned_only_in_a_comment_is_not_confined_because_it_is_not_there() -> (
    None
):
    """The classifier strips comments and string literals before looking, so there is
    nothing to confine - and `cte_scoped_construct` says None rather than inventing a
    poisoned CTE out of prose."""
    sql = """
    WITH good AS (SELECT 1)  -- JSON_TABLE is discussed here and not used
    SELECT 1 FROM good
    """
    assert cte_scoped_construct(sql, "postgres") is None
