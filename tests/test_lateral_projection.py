"""A column projected through a LATERAL alias resolves to its base column (PG-03).

`LEFT JOIN LATERAL (SELECT p.payload ->> 'score' AS score) AS j` is how a warehouse model
names an intermediate expression so several projections can share it. The correlation's
FILTER edges were always correct; the PROJECTION was lost - `j.score` came back as
`unresolved_identifier`, and with it every column downstream of the lateral.

**Cause, since it is not where it looks.** SQLGlot models a LATERAL source's scope with
`scope.expression` set to the `exp.Lateral`, not to the query inside it, and
`Lateral.selects` is an empty list rather than an error. `_projection_named` therefore
found no projection called `score` while the projection sat two nodes away, under
`Lateral -> Subquery -> Select`. An empty list is the dangerous spelling of "I don't know":
it reads as a definite "no such column".

**Most tests here are metamorphic.** A LATERAL that only names an expression computes
exactly what the inline expression computes, so the two spellings must produce identical
lineage - no ground truth needed, and nothing to argue about when they differ.
"""

from __future__ import annotations

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary, ObjectInfo


def _book() -> Dictionary:
    """A captured dictionary, not an inferred one - this is about lineage, not schema."""
    return Dictionary(
        captured_at="2026-09-24T00:00:00Z",
        default_schema="silver",
        dialect="postgres",
        objects={
            name: ObjectInfo(owner="silver", name=name.split(".")[1], object_type="TABLE")
            for name in ("silver.src", "silver.child", "gold.tgt")
        },
        columns={
            "silver.src": ["id", "payload", "display_name", "region"],
            "silver.child": ["pid", "amount", "kind"],
            "gold.tgt": ["a", "b", "c"],
        },
        temporary={"silver.src": False, "silver.child": False, "gold.tgt": False},
    )


def _edges(sql: str) -> set[tuple[str, ...]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, _book(), config, None)
    return {
        (str(e.source), str(e.target), e.flow.value, e.transform.value) for e in result.edges
    }


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, _book(), config, None)


LATERAL_NAMING_AN_EXPRESSION = """
INSERT INTO gold.tgt (a, b)
SELECT j.score, j.name
FROM   silver.src AS p
LEFT   JOIN LATERAL (
           SELECT (p.payload ->> 'score')::numeric AS score,
                  p.display_name                   AS name
       ) AS j ON TRUE
"""

THE_SAME_THING_INLINE = """
INSERT INTO gold.tgt (a, b)
SELECT (p.payload ->> 'score')::numeric, p.display_name
FROM   silver.src AS p
"""


def test_a_lateral_that_names_an_expression_matches_the_inline_spelling() -> None:
    """The load-bearing test. Before the fix this side produced ZERO edges."""
    assert _edges(LATERAL_NAMING_AN_EXPRESSION) == _edges(THE_SAME_THING_INLINE)


def test_the_lateral_spelling_actually_produces_edges() -> None:
    """Stated separately because equality above is also satisfied by BOTH sides producing
    nothing - a regression wearing a passing test as a disguise."""
    result = _result(LATERAL_NAMING_AN_EXPRESSION)

    assert [b.kind.value for b in result.boundaries] == []
    assert _edges(LATERAL_NAMING_AN_EXPRESSION) == {
        ("column:src.payload", "column:tgt.a", "value", "derived"),
        ("column:src.display_name", "column:tgt.b", "value", "identity"),
    }


def test_a_lateral_with_its_own_from_resolves_through_to_the_inner_table() -> None:
    """The other LATERAL shape: a correlated subquery rather than a named expression.

    Its filter edges were already correct - `child.pid` and `src.id` - and only the value
    edge was missing, which is what made the finding easy to under-read as "LATERAL works".
    The transform must be AGGREGATED: the value passed through `sum`.

    **Under D-14 (2026-10-02) the correlation is a VALUE, no longer a filter.** This LATERAL
    aggregates with no GROUP BY, so it returns exactly one row for every `src` row: its
    WHERE cannot remove a result row. It decides which children are summed into `a`.
    """
    edges = _edges(
        """
        INSERT INTO gold.tgt (a)
        SELECT j.total
        FROM   silver.src AS p
        CROSS  JOIN LATERAL (
                   SELECT sum(c.amount) AS total
                   FROM   silver.child c
                   WHERE  c.pid = p.id
               ) AS j
        """
    )
    assert ("column:child.amount", "column:tgt.a", "value", "aggregated") in edges
    assert ("column:child.pid", "column:tgt.a", "value", "aggregated") in edges
    assert ("column:src.id", "column:tgt.a", "value", "aggregated") in edges
    assert not [e for e in edges if e[2] == "filter"], "a one-row LATERAL removes no row (D-14)"


def test_a_lateral_projection_over_an_unknown_column_still_declares() -> None:
    """The guard that keeps the fix honest.

    Resolving MORE must not mean resolving names that do not exist. `p.nonexistent` is not
    a column of `silver.src`, so NO edge is emitted and the statement says so. A fix that
    turned unresolved references into confident edges would be strictly worse than the
    defect it replaced.

    The declaration arrives as a REFUSAL rather than a boundary, and that is the captured
    dictionary talking: it knows `src`'s full column list, so `qualify` cannot bind the name
    at all and declines the statement. Against an INFERRED dictionary - which holds only the
    columns the text mentions and so cannot tell a typo from an uncaptured column - the same
    query yields a per-column boundary instead. Both are honest; which one you get depends
    on how much the schema knows, so this asserts the property that matters (no edge, and
    the statement is not silently counted as clean) rather than the spelling.
    """
    result = _result(
        """
        INSERT INTO gold.tgt (a)
        SELECT j.score
        FROM   silver.src AS p
        LEFT   JOIN LATERAL (SELECT p.nonexistent AS score) AS j ON TRUE
        """
    )
    assert result.edges == []
    assert result.refusals or result.boundaries, "an unresolvable name must be declared"
    assert [r.code.value for r in result.refusals] == ["NAME_NOT_RESOLVED"]


def test_nested_laterals_resolve_through_both_levels() -> None:
    """The shape `another_stress_test.txt` builds 111 times: a lateral reading a lateral."""
    edges = _edges(
        """
        INSERT INTO gold.tgt (a)
        SELECT k.doubled
        FROM   silver.src AS p
        LEFT   JOIN LATERAL (SELECT (p.payload ->> 'score')::numeric AS score) AS j ON TRUE
        LEFT   JOIN LATERAL (SELECT j.score * 2 AS doubled) AS k ON TRUE
        """
    )
    assert edges == {("column:src.payload", "column:tgt.a", "value", "derived")}
