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

from explorer import cards_of, explore, html, model


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
    assert sales["note"] == "rows filtered by its own total"
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
