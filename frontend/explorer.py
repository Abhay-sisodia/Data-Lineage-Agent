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

    members: dict[str, set[str]] = {card: set() for card in self_filters}
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
                "note": f"rows filtered by its own {', '.join(own)}" if own else None,
                "rows": rows,
            }
        )

    drawn: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for edge in edges:
        drawn.setdefault((edge["type"], edge["source"], edge["target"]), []).append(edge)
    lines = []
    for index, ((kind, source, target), same) in enumerate(sorted(drawn.items())):
        where = "; ".join(sorted({f"{e['file']}:{e['line']}" for e in same}))
        verb = {"FEEDS": "value", "INFLUENCES": "influence", "FILTERS": "filter"}[kind]
        lines.append(
            {
                "id": f"e{index}",
                "kind": kind,
                "from": ports[source],
                "to": ports[target],
                "badge": BADGE.get(same[0]["transform"] or "", "") if kind == "FEEDS" else "",
                "tip": f"{verb} {same[0]['transform'] or ''} - {source} -> {target} ({where})",
                "ids": sorted(e["id"] for e in same),
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
