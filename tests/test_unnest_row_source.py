"""A column of `UNNEST(arr) AS t(x)` traces to the array it came from (PG-05).

The only known column-level silence in the PostgreSQL path. SQLGlot gives an unnest a
scope whose `selects` are the ALIAS IDENTIFIERS, not expressions, so `t.tag` traced to a
bare identifier with no column in it: no edge, no boundary, a clean-looking run.
`generate_series` in the same position declared `dangling_reference`, so the miss was
visible only by comparison.

**Most tests here are metamorphic.** `SELECT unnest(c.tags)` already produced the right
edge, and moving the unnest into `FROM` computes the same rows - so every `FROM` spelling
must produce exactly the select-list spelling's lineage.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary, ObjectInfo


def _book() -> Dictionary:
    return Dictionary(
        captured_at="2026-10-01T00:00:00Z",
        default_schema="public",
        dialect="postgres",
        objects={
            name: ObjectInfo(owner="public", name=name.split(".")[1], object_type="TABLE")
            for name in ("public.c", "public.a")
        },
        columns={
            "public.c": ["id", "tags", "codes", "csv"],
            "public.a": ["id", "x", "y"],
        },
        temporary={"public.c": False, "public.a": False},
    )


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, _book(), config, None)


def _edges(sql: str) -> set[tuple[str, ...]]:
    return {
        (str(e.source), str(e.target), e.flow.value, e.transform.value)
        for e in _result(sql).edges
    }


def _declared(sql: str) -> list[str]:
    return [
        b.kind.value
        for b in _result(sql).boundaries
        if b.kind.value != "context_dependent_binding"
    ]


IN_THE_SELECT_LIST = "INSERT INTO a (id, x) SELECT c.id, unnest(c.tags) FROM c"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO a (id, x) SELECT c.id, t.tag FROM c, unnest(c.tags) AS t(tag)",
        "INSERT INTO a (id, x) SELECT c.id, t.tag "
        "FROM c CROSS JOIN LATERAL unnest(c.tags) AS t(tag)",
        "INSERT INTO a (id, x) SELECT c.id, t.tag "
        "FROM c JOIN LATERAL unnest(c.tags) AS t(tag) ON TRUE",
        "INSERT INTO a (id, x) SELECT c.id, t.tag "
        "FROM c, unnest(c.tags) WITH ORDINALITY AS t(tag, n)",
    ],
    ids=["comma", "cross-lateral", "join-lateral", "with-ordinality"],
)
def test_every_from_spelling_matches_the_select_list_spelling(sql: str) -> None:
    assert _edges(sql) == _edges(IN_THE_SELECT_LIST)


def test_the_edge_actually_exists() -> None:
    """Stated separately: equality above also holds if both sides lose the column."""
    sql = "INSERT INTO a (id, x) SELECT c.id, t.tag FROM c, unnest(c.tags) AS t(tag)"
    assert ("column:public.c.tags", "column:public.a.x", "value", "derived") in _edges(sql)
    assert _declared(sql) == []


def test_multiple_arrays_bind_by_position() -> None:
    """`unnest(a, b) AS t(x, y)` zips: `t.y` is an element of the SECOND array. Binding
    both to the first would be a confident wrong edge, the worst outcome available."""
    edges = _edges(
        "INSERT INTO a (x, y) SELECT t.p, t.q FROM c, unnest(c.tags, c.codes) AS t(p, q)"
    )
    assert edges == {
        ("column:public.c.tags", "column:public.a.x", "value", "derived"),
        ("column:public.c.codes", "column:public.a.y", "value", "derived"),
    }


def test_the_array_expression_is_traced_through() -> None:
    edges = _edges(
        "INSERT INTO a (x) SELECT t.v FROM c, unnest(string_to_array(c.csv, ',')) AS t(v)"
    )
    assert {e[:3] for e in edges} == {("column:public.c.csv", "column:public.a.x", "value")}


def test_the_ordinality_column_has_no_value_source() -> None:
    """A position counter, like ROW_NUMBER(): no column's value is in it. Neither an edge
    nor a boundary - nothing was lost."""
    sql = "INSERT INTO a (y) SELECT t.n FROM c, unnest(c.tags) WITH ORDINALITY AS t(tag, n)"
    assert _edges(sql) == set()
    assert _declared(sql) == []


def test_a_filter_on_the_element_is_a_filter_on_the_array() -> None:
    edges = _edges(
        "INSERT INTO a (id) SELECT c.id FROM c, unnest(c.tags) AS t(tag) WHERE t.tag = 'vip'"
    )
    assert any(e[0] == "column:public.c.tags" and e[2] == "filter" for e in edges)


def test_an_alias_list_that_does_not_map_by_position_is_declared() -> None:
    """Two names over one array: an array of records expanded into its fields. Which
    field is which is in the element type, not the dictionary - declare, never guess."""
    sql = "INSERT INTO a (x) SELECT t.p FROM c, unnest(c.tags) AS t(p, q)"
    assert _edges(sql) == set()
    assert _declared(sql) == ["unresolved_identifier"]


def test_a_bare_table_alias_is_declared_not_silent() -> None:
    sql = "INSERT INTO a (x) SELECT t FROM c, unnest(c.tags) AS t"
    assert _edges(sql) == set()
    assert _declared(sql) == ["unresolved_identifier"]
