"""MVP plan F2.1: the lineage explorer's table cards and walk (frontend/explorer.py).

Gate: every fact is drawn exactly once - parallel facts from two statements merge, a
table and its columns share one card, and a table filtered by its own columns says so in
its header instead of drawing loops. Names from SQL cannot inject markup into the page.
Upstream, a filter is shown where it acts but its sources are not walked unless asked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "frontend"))

from explorer import cards_of, collapse, explore, hidable, html, model, table_of


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
    assert 'integrity="sha384-' in page and "LineageExplorer" in page


class _Graph:
    """`_incoming` / `downstream` over a fixed edge list, as GraphTools answers them."""

    def __init__(self, edges: list[dict[str, Any]]) -> None:
        self.edges = edges

    def _incoming(self, frontier: list[str]) -> list[dict[str, Any]]:
        out = []
        for node in frontier:
            table = node.rpartition(".")[0]
            out += [e for e in self.edges if e["target"] in (node, table)]
        return out

    def downstream(self, node: str, depth: int) -> list[dict[str, Any]]:
        return [e for e in self.edges if e["source"] == node]


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
