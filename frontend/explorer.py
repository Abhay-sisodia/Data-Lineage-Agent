"""The lineage explorer (MVP plan F2), in Streamlit until the visual design settles.

Two pure pieces, kept apart from the page so the React version can copy the rules:

* `explore` - which edges to show. Upstream, downstream or both, a number of hops, the
  flows wanted. **Filters are shown where they act but not walked further by default**:
  a filter decides which rows a table holds, and walking upstream from every filter column
  pulls in lineage unrelated to the column asked about.
* `model` - what to draw, as data; `explorer_render.js` lays it out with ELK (orthogonal
  edges, crossings minimised) and draws it. One card per table, one row per column; edges run from
  column row to column row. Value edges are solid with a transform badge, influence edges
  dotted, filter edges dashed into the card's header. Parallel facts (two statements
  writing the same column the same way) are drawn once.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BADGE = {"aggregated": "Σ", "derived": "ƒ", "conditional": "?", "identity": ""}
DIRECTIONS = ("upstream", "downstream", "both")


def explore(
    tools: Any,
    node: str,
    direction: str = "upstream",
    depth: int = 2,
    flows: tuple[str, ...] = ("FEEDS", "INFLUENCES", "FILTERS"),
    follow_filters: bool = False,
) -> list[dict[str, Any]]:
    """The edges to draw around `node`, each once, with the hop it was reached at."""
    edges: dict[str, dict[str, Any]] = {}
    if direction in ("upstream", "both"):
        for edge in _walk_up(tools, node, depth, follow_filters):
            edges.setdefault(edge["id"], edge)
    if direction in ("downstream", "both"):
        for edge in tools.downstream(node, depth):
            edges.setdefault(edge["id"], edge)
    return sorted(
        (e for e in edges.values() if e["type"] in flows),
        key=lambda e: (e["depth"], e["type"], e["source"], e["target"], e["id"]),
    )


def _walk_up(tools: Any, node: str, depth: int, follow_filters: bool) -> list[dict[str, Any]]:
    """`upstream`, except that a filter's source is shown and, by default, not walked."""
    seen: dict[str, dict[str, Any]] = {}
    frontier, visited = [node], {node}
    for hop in range(1, max(1, min(int(depth), 15)) + 1):
        nxt: list[str] = []
        for edge in tools._incoming(frontier):
            if edge["id"] in seen:
                continue
            seen[edge["id"]] = {**edge, "depth": hop}
            walk = edge["type"] != "FILTERS" or follow_filters
            if walk and edge["source"] not in visited:
                visited.add(edge["source"])
                nxt.append(edge["source"])
        frontier = nxt
        if not frontier:
            break
    return list(seen.values())


def table_of(node: str) -> str:
    """The card a node belongs on: its table (a column) or itself (a table, a variable)."""
    if node.startswith("<result:"):
        return node.split(">", 1)[0] + ">"
    head, _, _ = node.rpartition(".")
    return head if head.count(".") >= 1 else node


def model(
    edges: list[dict[str, Any]],
    focus: set[str] | None = None,
    warn: dict[str, str] | None = None,
    tables: set[str] | None = None,
    notes: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """The diagram as data - cards and edges - for `explorer_render.js` to lay out and draw.

    `warn`: card -> why it carries a ⚠ (gaps). `tables` names the nodes that ARE tables
    (filter targets), so a table and its columns share one card instead of the table being
    drawn again on its own. Parallel facts are merged into one edge.
    """
    focus = focus or set()
    warn = warn or {}
    tables = tables or {e["target"] for e in edges if e["type"] == "FILTERS"}

    # A table filtered by its own columns (a load that deletes or upserts by key) would
    # draw loops; it is said in the card's header instead.
    self_filters: dict[str, set[str]] = {}
    for edge in edges:
        if edge["type"] == "FILTERS" and table_of(edge["source"]) == edge["target"]:
            self_filters.setdefault(edge["target"], set()).add(edge["source"])
    edges = [
        e for e in edges if not (e["type"] == "FILTERS" and table_of(e["source"]) == e["target"])
    ]

    members: dict[str, set[str]] = {card: set() for card in [*self_filters, *(notes or {})]}
    for edge in edges:
        for end in (edge["source"], edge["target"]):
            if end in tables:
                members.setdefault(end, set())
            else:
                members.setdefault(table_of(end), set()).add(end)

    ports: dict[str, str] = {}
    cards = []
    for index, (card, columns) in enumerate(sorted(members.items())):
        cid = f"t{index}"
        ports[card] = f"{cid}.h"
        rows = []
        for row, column in enumerate(sorted(columns)):
            ports[column] = f"{cid}.c{row}"
            rows.append(
                {"id": ports[column], "label": _column_name(column, card), "focus": column in focus}
            )
        title, subtitle, colour = _header(card)
        own = sorted(_column_name(c, card) for c in self_filters.get(card, ()))
        cards.append(
            {
                "id": cid,
                "key": card,
                "title": title,
                "subtitle": subtitle,
                "colour": colour,
                "warn": warn.get(card),
                "notes": ([f"rows filtered by its own {', '.join(own)}"] if own else [])
                + (notes or {}).get(card, []),
                "rows": rows,
            }
        )

    drawn: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for edge in edges:
        drawn.setdefault((edge["type"], edge["source"], edge["target"]), []).append(edge)
    lines = []
    for index, ((kind, source, target), same) in enumerate(sorted(drawn.items())):
        where = "; ".join(
            sorted({w for e in same for w in e.get("where", [f"{e['file']}:{e['line']}"])})
        )
        via = sorted({v for e in same for v in e.get("via", [])})
        transform = max(
            (e["transform"] or "identity" for e in same),
            key=lambda t: LADDER.index(t) if t in LADDER else 0,
        )
        verb = {"FEEDS": "value", "INFLUENCES": "influence", "FILTERS": "filter"}[kind]
        passed = f" via {', '.join(via)}" if via else ""
        lines.append(
            {
                "id": f"e{index}",
                "kind": kind,
                "from": ports[source],
                "to": ports[target],
                "badge": BADGE.get(transform, "") if kind == "FEEDS" else "",
                "tip": f"{verb} {transform} - {source} -> {target}{passed} ({where})",
                "ids": sorted(e["id"] for e in same),
                "joined": bool(via),
            }
        )
    return {"cards": cards, "edges": lines}


ELK_URL = "https://cdn.jsdelivr.net/npm/elkjs@0.9.3/lib/elk.bundled.js"
# Subresource integrity: the browser refuses a file that does not match this hash.
ELK_SRI = "sha384-KO35c6PFq7tFxAEx6FtATwL9vOBOmCzQYr7GM0iTr3oO22s7Jc9OkclOH4AV7jwK"
RENDERER = Path(__file__).resolve().parent / "explorer_render.js"

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>
  body { margin: 0; font-family: Helvetica, Arial, sans-serif; background: transparent; }
  #view { width: 100%; height: __HEIGHT__px; overflow: hidden; cursor: grab;
          border: 1px solid #e2e8f0; border-radius: 8px; background: #f8fafc; }
  #view svg { width: 100%; height: 100%; }
  #tools { position: absolute; right: 14px; top: 10px; }
  #tools button { border: 1px solid #cbd5e1; background: #fff; border-radius: 6px;
                  padding: 2px 9px; margin-left: 4px; cursor: pointer; font-size: 14px; }
  #msg { padding: 16px; color: #b91c1c; }
</style></head><body>
<div id="tools"><button onclick="zoom(1/1.25)">+</button><button onclick="zoom(1.25)">-</button>
<button onclick="fit()">fit</button></div>
<div id="view"><div id="msg">Laying out the lineage ...</div></div>
<script src="__ELK__" integrity="__SRI__" crossorigin="anonymous"></script>
<script>__RENDERER__</script>
<script>
const model = __MODEL__;
let box = null, base = null;
function apply() { const s = document.querySelector("#view svg");
  if (s) s.setAttribute("viewBox", box.join(" ")); }
function fit() { box = base.slice(); apply(); }
function zoom(f, cx, cy) { const [x, y, w, h] = box;
  cx = cx ?? x + w / 2; cy = cy ?? y + h / 2;
  box = [cx - (cx - x) * f, cy - (cy - y) * f, w * f, h * f]; apply(); }
if (typeof ELK === "undefined") {
  document.getElementById("msg").textContent =
    "Could not load the layout engine (ELK) from cdn.jsdelivr.net - check the network.";
} else {
  LineageExplorer.render(model, ELK).then((svg) => {
    const view = document.getElementById("view");
    view.innerHTML = svg;
    const s = view.querySelector("svg");
    base = s.getAttribute("viewBox").split(" ").map(Number);
    s.removeAttribute("width"); s.removeAttribute("height");
    fit();
    view.addEventListener("wheel", (ev) => { ev.preventDefault();
      const r = s.getBoundingClientRect(), [x, y, w, h] = box;
      const scale = Math.max(w / r.width, h / r.height);
      const cx = x + (ev.clientX - r.left - (r.width - w / scale) / 2) * scale;
      const cy = y + (ev.clientY - r.top - (r.height - h / scale) / 2) * scale;
      zoom(ev.deltaY > 0 ? 1.15 : 1 / 1.15, cx, cy); }, { passive: false });
    let drag = null;
    view.addEventListener("mousedown", (ev) => { drag = [ev.clientX, ev.clientY, box.slice()];
      view.style.cursor = "grabbing"; });
    window.addEventListener("mouseup", () => { drag = null; view.style.cursor = "grab"; });
    window.addEventListener("mousemove", (ev) => { if (!drag) return;
      const r = s.getBoundingClientRect(), [x, y, w, h] = drag[2];
      const scale = Math.max(w / r.width, h / r.height);
      box = [x - (ev.clientX - drag[0]) * scale, y - (ev.clientY - drag[1]) * scale, w, h];
      apply(); });
  }).catch((err) => { document.getElementById("msg").textContent = "Layout failed: " + err; });
}
</script></body></html>"""


def html(diagram: dict[str, Any], height: int = 640) -> str:
    """A self-contained page: ELK lays the model out, the SVG is drawn, the wheel zooms and
    dragging pans. ELK comes from jsDelivr (pinned, integrity-checked); if it is blocked,
    the page says so."""
    # Names come from user SQL: no `<`, `>` or `&` may reach the page unescaped.
    payload = (
        json.dumps(diagram).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    return (
        _PAGE.replace("__HEIGHT__", str(height - 8))
        .replace("__ELK__", ELK_URL)
        .replace("__SRI__", ELK_SRI)
        .replace("__RENDERER__", RENDERER.read_text(encoding="utf-8"))
        .replace("__MODEL__", payload)
    )


def _header(card: str) -> tuple[str, str, str]:
    """(title, subtitle, header colour) for a card."""
    if card.startswith("<result:"):
        return "query result", card[len("<result:") : -1], "#0f766e"
    if card.startswith("pg_temp."):
        return card.split(".", 1)[1], "temp table", "#64748b"
    schema, _, name = card.rpartition(".")
    if not schema:
        return card, "variable", "#475569"
    return name, schema, "#1e3a8a"


def _column_name(column: str, card: str) -> str:
    return column[len(card) + 1 :] if column.startswith(card + ".") else column


def gap_marks(tools: Any, cards: set[str]) -> dict[str, str]:
    """Card -> why it carries a ⚠: a statement writing it hit a boundary, or a refused
    statement names it - its lineage may be incomplete."""
    rows = tools._read(
        """
        UNWIND $ids AS tid
        MATCH (t:Table {id: tid})
        OPTIONAL MATCH (s:Statement)-[:WRITES]->(t)
          WHERE EXISTS { (s)-[:BLOCKED_BY]->(:Boundary) }
        OPTIONAL MATCH (x:Refusal)-[:MENTIONS]->(t)
        WITH t, count(DISTINCT s) AS blocked,
             collect(DISTINCT x.file + ':' + toString(x.line)) AS refused
        WHERE blocked > 0 OR size(refused) > 0
        RETURN t.id AS id, blocked, refused
        """,
        ids=sorted(cards),
    )
    marks: dict[str, str] = {}
    for row in rows:
        parts = []
        if row["blocked"]:
            parts.append(f"{row['blocked']} statement(s) writing it hit a boundary")
        if row["refused"]:
            parts.append("named by refused statement(s) " + ", ".join(sorted(row["refused"])))
        marks[row["id"]] = "; ".join(parts)
    return marks


def cards_of(edges: list[dict[str, Any]]) -> set[str]:
    """Every card the edges put on the diagram."""
    tables = {e["target"] for e in edges if e["type"] == "FILTERS"}
    return {
        end if end in tables else table_of(end) for e in edges for end in (e["source"], e["target"])
    }


LADDER = ("identity", "derived", "conditional", "aggregated")
"""Transforms, weakest to strongest: a joined edge carries the hardest on its path (D-7)."""


def hidable(edges: list[dict[str, Any]], focus: str) -> list[str]:
    """Cards that may be hidden: every card except the focus's own."""
    return sorted(cards_of(edges) - {table_of(focus), focus})


def collapse(
    edges: list[dict[str, Any]], hide: set[str]
) -> tuple[list[dict[str, Any]], list[str], dict[str, list[str]]]:
    """The edges with the `hide` tables taken out and every path through them joined.

    A path through hidden columns becomes one edge from its first visible node to its last:
    value then value stays a value with the hardest transform; anything then a filter, or a
    filter into a hidden table then anything, is a filter on the final table (the hidden
    table's rows decide those rows); every other mix is an influence. Each joined edge keeps
    what it passed through (`via`) and every statement on the way.

    An edge out of a hidden column that no visible path reaches (its own sources were not
    walked) cannot be joined; it is not dropped silently but becomes a note on the card it
    lands on. Returns (edges, the hidden tables that were on the diagram, notes per card).
    """

    def hidden(node: str) -> bool:
        return node in hide or table_of(node) in hide

    present = sorted(t for t in hide if t in cards_of(edges))
    if not present:
        return edges, [], {}
    out_of: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        out_of.setdefault(edge["source"], []).append(edge)
    columns_of: dict[str, set[str]] = {}
    for edge in edges:
        for end in (edge["source"], edge["target"]):
            columns_of.setdefault(table_of(end), set()).add(end)

    def onward(node: str) -> list[dict[str, Any]]:
        """Edges leaving a hidden node; from a hidden TABLE, every edge of its columns."""
        if node in hide:  # a filter reached the table itself: its rows go wherever it feeds
            return [e for c in sorted(columns_of.get(node, ())) for e in out_of.get(c, [])]
        return out_of.get(node, [])

    joined: dict[tuple[str, str, str], dict[str, Any]] = {}
    kept = [e for e in edges if not hidden(e["source"]) and not hidden(e["target"])]
    reached: set[str] = set()
    for start in edges:
        if hidden(start["source"]) or not hidden(start["target"]):
            continue
        stack = [(start, start["type"], start["transform"] or "identity", [start])]
        while stack:
            edge, kind, transform, trail = stack.pop()
            for nxt in onward(edge["target"]):
                if nxt in trail:
                    continue
                if edge["target"] not in hide:  # reached the column itself, not via its table
                    reached.add(nxt["id"])
                k, t = _join(kind, transform, nxt["type"], nxt["transform"] or "identity")
                if hidden(nxt["target"]):
                    stack.append((nxt, k, t, [*trail, nxt]))
                    continue
                target = nxt["target"]
                if k == "FILTERS" and nxt["type"] != "FILTERS":
                    target = table_of(target)  # rows of the table it lands in
                path = [*trail, nxt]
                key = (k, start["source"], target)
                found = joined.setdefault(
                    key,
                    {
                        "id": "via:" + "|".join(e["id"] for e in path),
                        "type": k,
                        "source": start["source"],
                        "target": target,
                        "transform": t if k == "FEEDS" else "identity",
                        "file": start["file"],
                        "line": start["line"],
                        "depth": start.get("depth", 1),
                        "via": [],
                        "where": [],
                    },
                )
                if k == "FEEDS" and LADDER.index(t) > LADDER.index(found["transform"]):
                    found["transform"] = t
                for e in path[:-1]:
                    if e["target"] not in found["via"]:
                        found["via"].append(e["target"])
                for e in path:
                    where = f"{e['file']}:{e['line']}"
                    if where not in found["where"]:
                        found["where"].append(where)
    unjoined: dict[tuple[str, str], set[str]] = {}
    for edge in edges:
        if hidden(edge["source"]) and not hidden(edge["target"]) and edge["id"] not in reached:
            card = edge["target"] if edge["type"] == "FILTERS" else table_of(edge["target"])
            unjoined.setdefault((card, edge["type"]), set()).add(edge["source"])
    words = {
        "FILTERS": "rows also filtered by",
        "FEEDS": "values also from",
        "INFLUENCES": "also influenced by",
    }
    notes: dict[str, list[str]] = {}
    for (card, kind), sources in sorted(unjoined.items()):
        by_table: dict[str, list[str]] = {}
        for source in sorted(sources):
            by_table.setdefault(table_of(source), []).append(_column_name(source, table_of(source)))
        for table, names in by_table.items():
            short = _header(table)[0]
            notes.setdefault(card, []).append(f"{words[kind]} hidden {short}: {', '.join(names)}")
    return kept + sorted(joined.values(), key=lambda e: e["id"]), present, notes


def _join(kind: str, transform: str, nxt_kind: str, nxt_transform: str) -> tuple[str, str]:
    if kind == "FILTERS" or nxt_kind == "FILTERS":
        return "FILTERS", "identity"
    if kind == "FEEDS" and nxt_kind == "FEEDS":
        hardest = max(transform, nxt_transform, key=lambda t: LADDER.index(t) if t in LADDER else 0)
        return "FEEDS", hardest
    return "INFLUENCES", "identity"


SCOPES = ("One column", "A file", "Entire graph")


def all_edges(
    tools: Any, file: str | None = None, flows: tuple[str, ...] = ("FEEDS", "INFLUENCES", "FILTERS")
) -> list[dict[str, Any]]:
    """Every lineage edge in the graph, or in one file - ordered by file and line, so a
    cut-off keeps whole statements together."""
    from lineage.graph.tools import _EDGE_FIELDS

    rows = tools._read(
        f"""
        MATCH (a)-[r]->(b) WHERE type(r) IN $types AND ($file IS NULL OR r.file = $file)
        RETURN {{{_EDGE_FIELDS}}} AS edge ORDER BY r.file, r.line, r.id
        """,
        types=list(flows),
        file=file,
    )
    return [{**row["edge"], "depth": 1} for row in rows]


def files(tools: Any) -> list[str]:
    """The files in the graph that produced at least one lineage edge."""
    rows = tools._read(
        "MATCH ()-[r:FEEDS|FILTERS|INFLUENCES]->() RETURN DISTINCT r.file AS file ORDER BY file"
    )
    return [row["file"] for row in rows]
