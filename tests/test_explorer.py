"""MVP plan F2.1: the lineage explorer's table cards and walk (frontend/explorer.py).

Gate: every fact is drawn exactly once - parallel facts from two statements merge, a
table and its columns share one card, and a table filtered by its own columns says so in
its header instead of drawing loops. Names from SQL cannot inject markup into the page.
Upstream, a filter is shown where it acts but its sources are not walked unless asked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "frontend"))

from explorer import (
    cards_of,
    collapse,
    context_html,
    cte_steps,
    edge_context,
    explore,
    hidable,
    html,
    model,
    table_of,
)


def _edge(kind: str, source: str, target: str, transform: str = "identity", n: int = 0) -> dict:
    return {
        "id": f"{kind}:{source}>{target}:{n}",
        "type": kind,
        "source": source,
        "target": target,
        "transform": transform,
        "file": "a.sql",
        "line": 1 + n,
        "depth": 1,
    }


EDGES = [
    _edge("FEEDS", "s.orders.amount", "m.sales.total", "aggregated"),
    _edge("FEEDS", "s.orders.amount", "m.sales.total", "aggregated", n=1),  # 2nd statement
    _edge("INFLUENCES", "s.orders.customer_id", "m.sales.total"),
    _edge("FILTERS", "s.orders.status", "m.sales"),
    _edge("FILTERS", "m.sales.total", "m.sales"),  # the table filtered by its own column
]


def test_each_fact_is_drawn_once() -> None:
    drawn = model(EDGES, {"m.sales.total"})["edges"]
    assert len(drawn) == 3  # amount (merged), customer_id, status - not the self-filter
    merged = next(e for e in drawn if e["kind"] == "FEEDS")
    assert len(merged["ids"]) == 2 and "a.sql:1; a.sql:2" in merged["tip"]
    assert merged["badge"] == "Σ"
    status = next(e for e in drawn if e["kind"] == "FILTERS")
    assert status["to"].endswith(".h")  # a filter meets the card's header


def test_a_table_and_its_columns_share_one_card() -> None:
    cards = model(EDGES, {"m.sales.total"})["cards"]
    assert len(cards) == 2 == len(cards_of(EDGES))
    sales = next(c for c in cards if c["key"] == "m.sales")
    assert sales["notes"] == ["rows filtered by its own total"]
    assert [(r["label"], r["focus"]) for r in sales["rows"]] == [("total", True)]


def test_gaps_and_result_cards() -> None:
    edges = [_edge("FEEDS", "s.t.x", "<result:q.sql>.y")]
    cards = model(edges, warn={"<result:q.sql>": "a boundary"})["cards"]
    result = next(c for c in cards if c["key"] == "<result:q.sql>")
    assert (result["title"], result["subtitle"], result["warn"]) == (
        "query result",
        "q.sql",
        "a boundary",
    )


def test_the_page_cannot_be_injected_through_a_name() -> None:
    edges = [_edge("FEEDS", "s.t.x", "<result:</script><img src=x onerror=alert(1)>.sql>.y")]
    page = html(model(edges))
    assert "</script><img" not in page and "onerror=alert(1)>" not in page
    assert "LineageExplorer" in page
    assert "cdn.jsdelivr.net" not in page.split("LineageExplorer")[0], "ELK is inlined (gap 10)"


class _Graph:
    """`_incoming` / `downstream` over a fixed edge list, as GraphTools answers them."""

    def __init__(self, edges: list[dict[str, Any]]) -> None:
        self.edges = edges

    def _incoming(
        self, frontier: list[str], join_keys: bool = False, fallback: bool = False
    ) -> list[dict[str, Any]]:
        out = []
        for node in frontier:
            table = node.rpartition(".")[0]
            out += [e for e in self.edges if e["target"] in (node, table)]
        return [e for e in out if _wanted(e, join_keys, fallback)]

    def downstream(
        self, node: str, depth: int, join_keys: bool = False, fallback: bool = False
    ) -> list[dict[str, Any]]:
        out = [e for e in self.edges if e["source"] == node]
        return [e for e in out if _wanted(e, join_keys, fallback)]


def _wanted(edge: dict[str, Any], join_keys: bool, fallback: bool) -> bool:
    if edge["type"] == "KEYS":
        return join_keys
    if edge["type"] in ("MENTIONS_COL", "OPAQUE"):
        return fallback
    return True


def test_filters_are_shown_but_not_walked_unless_asked() -> None:
    graph = _Graph(
        [
            _edge("FEEDS", "s.orders.amount", "m.sales.total"),
            _edge("FILTERS", "s.flags.active", "m.sales"),
            _edge("FEEDS", "raw.flags.is_on", "s.flags.active"),  # the filter's own source
        ]
    )
    shown = {e["source"] for e in explore(graph, "m.sales.total", "upstream", 3)}
    assert shown == {"s.orders.amount", "s.flags.active"}
    followed = {
        e["source"] for e in explore(graph, "m.sales.total", "upstream", 3, follow_filters=True)
    }
    assert "raw.flags.is_on" in followed


def test_both_directions_and_flow_choice() -> None:
    graph = _Graph(
        [
            _edge("FEEDS", "s.orders.amount", "m.sales.total"),
            _edge("FEEDS", "m.sales.total", "r.report.value"),
            _edge("INFLUENCES", "s.orders.region", "m.sales.total"),
        ]
    )
    both = explore(graph, "m.sales.total", "both", 1)
    assert {(e["source"], e["target"]) for e in both} == {
        ("s.orders.amount", "m.sales.total"),
        ("m.sales.total", "r.report.value"),
        ("s.orders.region", "m.sales.total"),
    }
    values_only = explore(graph, "m.sales.total", "both", 1, flows=("FEEDS",))
    assert all(e["type"] == "FEEDS" for e in values_only) and len(values_only) == 2


def _joined(edges: list[dict], hide: set[str]) -> dict[tuple[str, str], tuple[str, str]]:
    drawn, _, _ = collapse(edges, hide)
    assert not any(
        table_of(end) in hide or end in hide for e in drawn for end in (e["source"], e["target"])
    ), "a hidden table is still on the diagram"
    return {(e["source"], e["target"]): (e["type"], e["transform"]) for e in drawn}


def test_value_through_a_hidden_table_keeps_the_hardest_transform() -> None:
    edges = [
        _edge("FEEDS", "s.a.x", "tmp.t.y", "derived"),
        _edge("FEEDS", "tmp.t.y", "m.r.z", "aggregated"),
    ]
    assert _joined(edges, {"tmp.t"}) == {("s.a.x", "m.r.z"): ("FEEDS", "aggregated")}


def test_mixed_paths_become_influence_or_filter() -> None:
    edges = [
        _edge("FEEDS", "s.a.v", "tmp.t.k"),
        _edge("INFLUENCES", "tmp.t.k", "m.r.z"),  # value then influence -> influence
        _edge("FEEDS", "tmp.t.k", "m.r.w"),
        _edge("INFLUENCES", "s.a.g", "tmp.t.k"),  # influence then value -> influence
        _edge("FEEDS", "s.a.f", "tmp.t.flag"),
        _edge("FILTERS", "tmp.t.flag", "m.r"),  # value then filter -> filter on m.r
        _edge("FILTERS", "s.a.d", "tmp.t"),  # filter into the hidden table: its rows
    ]
    joined = _joined(edges, {"tmp.t"})
    assert joined[("s.a.v", "m.r.z")] == ("INFLUENCES", "identity")
    assert joined[("s.a.v", "m.r.w")] == ("FEEDS", "identity")
    assert joined[("s.a.g", "m.r.w")] == ("INFLUENCES", "identity")
    assert joined[("s.a.f", "m.r")] == ("FILTERS", "identity")
    assert joined[("s.a.d", "m.r")] == ("FILTERS", "identity")


def test_join_keys_are_walked_only_when_chosen_and_survive_a_hidden_table() -> None:
    graph = _Graph(
        [
            _edge("FEEDS", "ref.map.label", "m.r.status"),
            _edge("KEYS", "s.o.code", "m.r.status"),  # D-57: picked the map's row
        ]
    )
    default = explore(graph, "m.r.status", "upstream", 2)
    assert {e["type"] for e in default} == {"FEEDS"}
    chosen = explore(graph, "m.r.status", "upstream", 2, flows=("FEEDS", "KEYS"))
    assert {(e["type"], e["source"]) for e in chosen} == {
        ("FEEDS", "ref.map.label"),
        ("KEYS", "s.o.code"),
    }
    edges = [_edge("FEEDS", "s.a.code", "tmp.t.code"), _edge("KEYS", "tmp.t.code", "m.r.z")]
    assert _joined(edges, {"tmp.t"}) == {("s.a.code", "m.r.z"): ("KEYS", "identity")}
    assert '"kind": "KEYS"' in html(model(edges))


def test_a_chain_of_hidden_tables_joins_end_to_end() -> None:
    edges = [
        _edge("FEEDS", "s.a.x", "tmp.one.y", "identity"),
        _edge("FEEDS", "tmp.one.y", "tmp.two.y", "conditional"),
        _edge("FEEDS", "tmp.two.y", "m.r.z", "derived"),
    ]
    drawn, hidden, _ = collapse(edges, {"tmp.one", "tmp.two"})
    assert hidden == ["tmp.one", "tmp.two"]
    assert [(e["source"], e["target"], e["transform"], e["via"]) for e in drawn] == [
        ("s.a.x", "m.r.z", "conditional", ["tmp.one.y", "tmp.two.y"])
    ]
    assert "via tmp.one.y, tmp.two.y" in model(drawn)["edges"][0]["tip"]


def test_an_edge_no_visible_path_reaches_becomes_a_note() -> None:
    edges = [
        _edge("FEEDS", "s.a.x", "tmp.t.y"),
        _edge("FEEDS", "tmp.t.y", "m.r.z"),
        _edge("FILTERS", "tmp.t.flag", "m.r"),  # flag's own sources were not walked
    ]
    _, _, notes = collapse(edges, {"tmp.t"})
    assert notes == {"m.r": ["rows also filtered by hidden t: flag"]}
    card = next(
        c for c in model(collapse(edges, {"tmp.t"})[0], notes=notes)["cards"] if c["key"] == "m.r"
    )
    assert card["notes"] == ["rows also filtered by hidden t: flag"]


def test_the_focus_table_is_never_offered_for_hiding() -> None:
    assert hidable(EDGES, "m.sales.total") == ["s.orders"]


SCHEMA = {"s": {"orders": {"amt": "numeric", "k": "int", "st": "text"}}}


def test_steps_walk_a_column_back_through_each_cte() -> None:
    sql = (
        "WITH a AS (SELECT o.amt AS amt, o.k FROM s.orders o), "
        "b AS (SELECT a.k, SUM(a.amt) AS total FROM a GROUP BY 1) "
        "SELECT b.total * 2 AS doubled FROM b"
    )
    steps = cte_steps(sql, "doubled", "<result:q.sql>.doubled", "s", SCHEMA)
    chain = {(e["source"], e["target"], e["transform"]) for e in steps}
    assert chain == {
        ("s.orders.amt", "cte.a.amt", "identity"),
        ("cte.a.amt", "cte.b.total", "aggregated"),
        ("cte.b.total", "<result:q.sql>.doubled", "derived"),
    }
    cards = {c["key"]: c for c in model(steps)["cards"]}
    assert cards["cte.b"]["title"] == "b" and "step inside the query" in cards["cte.b"]["subtitle"]


def test_steps_map_an_insert_column_to_its_select_item() -> None:
    sql = "INSERT INTO m.t (total) WITH a AS (SELECT o.amt FROM s.orders o) SELECT a.amt FROM a"
    steps = cte_steps(sql, "total", "m.t.total", "s", SCHEMA)
    assert ("cte.a.amt", "m.t.total") in {(e["source"], e["target"]) for e in steps}


def test_steps_never_raise() -> None:
    assert cte_steps("not sql at all ((", "x", "r.x", "s", SCHEMA) == []
    assert cte_steps("DELETE FROM s.orders", "x", "r.x", "s", SCHEMA) == []


def test_a_tampered_vendored_elk_falls_back_to_the_pinned_cdn(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Gap 10: the vendored ELK is inlined only when it matches the pinned hash."""
    import explorer

    bad = tmp_path / "elk.bundled.js"
    bad.write_text("window.ELK = function () {};", encoding="utf-8")
    monkeypatch.setattr(explorer, "ELK_VENDORED", bad)  # type: ignore[attr-defined]
    monkeypatch.setattr(explorer, "_ELK_TAG", None)  # type: ignore[attr-defined]
    tag = explorer._elk_script()
    assert explorer.ELK_URL in tag and f'integrity="{explorer.ELK_SRI}"' in tag
    monkeypatch.setattr(explorer, "_ELK_TAG", None)  # type: ignore[attr-defined]


class _Statement:
    """describe_edges / _read over one file holding one statement (F2.3)."""

    SQL = (
        "WITH unrelated AS (\n"  # 1
        "  SELECT o.amt AS other FROM s.orders o\n"  # 2  names amt, off the path
        "),\n"  # 3
        "a AS (\n"  # 4
        "  SELECT o.amt AS amt FROM s.orders o\n"  # 5  on the path
        "),\n"  # 6
        "b AS (\n"  # 7
        "  SELECT SUM(a.amt) AS total FROM a\n"  # 8
        ")\n"  # 9
        "SELECT b.total FROM b;\n"  # 10
    )
    EDGE: ClassVar[dict[str, Any]] = {
        "id": "e1",
        "type": "FEEDS",
        "source": "s.orders.amt",
        "target": "<result:q.sql>.total",
        "transform": "aggregated",
        "phase": "pre-aggregation",
        "file": "q.sql",
        "line": 1,
    }

    def describe_edges(self, ids: list[str]) -> list[dict[str, Any]]:
        return [self.EDGE] if ids == ["e1"] else []

    def _read(self, query: str, **_: Any) -> list[dict[str, Any]]:
        if "HAS_COLUMN" in query:
            return [{"t": "s.orders", "cols": ["s.orders.amt", "s.orders.k"]}]
        if "r.line AS line" in query:
            return [{"text": self.SQL, "line": 1}]
        return [{"text": self.SQL}]


def test_why_an_edge_exists_shows_only_the_lines_on_its_path() -> None:
    context = edge_context(_Statement(), "e1", context=0)
    assert context is not None and context["narrowed"] is True
    marked = {line["number"]: line["mark"] for line in context["lines"] if line["number"]}
    assert marked[5] == "source"  # amt in the CTE on total's path
    assert 2 not in marked  # amt in an unrelated CTE is folded away
    # a.amt is the CTE's column, not s.orders' - only the alias that binds to the table counts
    assert marked[8] == "target"
    assert marked[10] == "target"
    assert edge_context(_Statement(), "missing") is None


def test_the_context_listing_escapes_sql() -> None:
    page = context_html([{"number": 3, "text": "SELECT '<b>' AS x", "mark": "source"}])
    assert "&lt;b&gt;" in page and "<b>" not in page


def test_source_lines_follow_what_a_reference_binds_to() -> None:
    """F2.3: `unit_cost` is a column of two tables; only references bound to the edge's
    source table are its lines - by alias, through a correlated subquery, or unqualified
    when that table is alone in its FROM."""
    from explorer import _source_lines

    sql = (
        "SELECT c.unit_cost,\n"  # 1  components
        "       od.unit_cost,\n"  # 2  order_details
        "       (SELECT MAX(x.unit_cost) FROM s.order_details x\n"  # 3  order_details
        "         WHERE x.k = c.k AND c.unit_cost > 0)\n"  # 4  correlated: components
        "FROM s.components c JOIN s.order_details od ON od.k = c.k\n"  # 5
        "UNION ALL SELECT unit_cost, 1, 2 FROM s.components\n"  # 6  unqualified, alone
    )
    assert _source_lines(sql, "s.components.unit_cost") == {1, 4, 6}
    assert _source_lines(sql, "s.order_details.unit_cost") == {2, 3}
    assert _source_lines("not sql ((", "s.t.c") is None
