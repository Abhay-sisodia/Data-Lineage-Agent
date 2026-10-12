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
            label = _column_name(column, card)
            rows.append(
                {
                    "id": ports[column],
                    "label": "columns used" if label == TABLE_ROW else label,
                    "focus": column in focus,
                }
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
                "badge": same[0].get("badge")
                or (BADGE.get(transform, "") if kind == "FEEDS" else ""),
                "tip": (
                    f"{table_of(source)} -> {table_of(target)}: {same[0]['badge']} column(s) "
                    f"used - {same[0].get('breakdown', verb)} ({where})"
                    if same[0].get("count")
                    else f"{verb} {transform} - {source} -> {target}{passed} ({where})"
                ),
                "ids": sorted(e["id"] for e in same),
                "joined": bool(via),
            }
        )
    return {"cards": cards, "edges": lines}


ELK_URL = "https://cdn.jsdelivr.net/npm/elkjs@0.9.3/lib/elk.bundled.js"
# Subresource integrity: the browser refuses a file that does not match this hash.
ELK_SRI = "sha384-KO35c6PFq7tFxAEx6FtATwL9vOBOmCzQYr7GM0iTr3oO22s7Jc9OkclOH4AV7jwK"
ELK_VENDORED = Path(__file__).resolve().parent / "vendor" / "elk.bundled.js"
"""ELK 0.9.3, shipped with the app (EPL-2.0, vendor/ELK-LICENSE.md) - end-to-end gap 10: the
diagram must draw offline and behind a proxy that blocks CDNs."""
RENDERER = Path(__file__).resolve().parent / "explorer_render.js"


def _elk_script() -> str:
    """The `<script>` that loads ELK: the vendored copy inlined when it matches the pinned
    hash, else the pinned CDN file (integrity-checked by the browser). Read once."""
    global _ELK_TAG
    if _ELK_TAG is None:
        import base64
        import hashlib

        tag = f'<script src="{ELK_URL}" integrity="{ELK_SRI}" crossorigin="anonymous"></script>'
        if ELK_VENDORED.exists():
            body = ELK_VENDORED.read_bytes()
            digest = "sha384-" + base64.b64encode(hashlib.sha384(body).digest()).decode()
            if digest == ELK_SRI and b"</script" not in body:
                tag = f"<script>{body.decode('utf-8')}</script>"
        _ELK_TAG = tag
    return _ELK_TAG


_ELK_TAG: str | None = None

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
__ELK__
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
    "Could not load the layout engine (ELK): the vendored copy is missing and the CDN is blocked.";
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
    dragging pans. ELK is inlined from the vendored copy, so no network is needed; the page
    says so if it cannot be loaded at all."""
    # Names come from user SQL: no `<`, `>` or `&` may reach the page unescaped.
    payload = (
        json.dumps(diagram).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    page = (
        _PAGE.replace("__HEIGHT__", str(height - 8))
        .replace("__RENDERER__", RENDERER.read_text(encoding="utf-8"))
        .replace("__MODEL__", payload)
    )
    return page.replace("__ELK__", _elk_script())  # last: nothing is substituted inside ELK


def _header(card: str) -> tuple[str, str, str]:
    """(title, subtitle, header colour) for a card."""
    if card.startswith("<result:"):
        return "query result", card[len("<result:") : -1], "#0f766e"
    if card.startswith("pg_temp."):
        return card.split(".", 1)[1], "temp table", "#64748b"
    schema, _, name = card.rpartition(".")
    if not schema:
        return card, "variable", "#475569"
    if schema == "cte":
        return name, "step inside the query (CTE or subquery)", "#6d28d9"
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


# --- step through the query's CTEs (end-to-end gap 8) --------------------------------------
#
# The parser folds a statement's CTEs into one hop - base column -> result column - and the
# answer keys are written that way. For READING a long query that hides the "how": the
# step view walks one result column back through each CTE with SQLGlot's own column
# lineage, for display only. Facts, transforms in the edge table, Ask and the impact report
# all stay the parser's.


def cte_steps(
    statement: str, column: str, result: str, default_schema: str, schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """Edges, in the explorer's format, from base columns through each CTE to `result`.

    `statement` is the SQL of the statement that produces `result` (whose last part is the
    column name `column` in its select list); `schema` is SQLGlot's {schema: {table:
    {column: type}}}. Returns [] when the walk is not possible (e.g. a statement with no
    SELECT); never raises.
    """
    import sqlglot
    from sqlglot import exp
    from sqlglot.lineage import lineage

    try:
        parsed = sqlglot.parse_one(statement, read="postgres")
        query = parsed if isinstance(parsed, exp.Query) else parsed.find(exp.Query)
        if query is None:
            return []
        name = _select_name(parsed, query, column)
        # The graph knows only the columns that carry lineage, so the schema is partial:
        # qualify tolerantly rather than refuse, as the parser does (end-to-end gap 6).
        root = lineage(
            name,
            query,
            schema=schema,
            dialect="postgres",
            validate_qualify_columns=False,
            allow_partial_qualification=True,
            infer_schema=True,
        )
    except Exception:
        return []

    edges: dict[tuple[str, str], dict[str, Any]] = {}

    def node_id(node: Any) -> str | None:
        if node.name.isdigit():  # GROUP BY 1: a position, not a column
            return None
        part = node.name.split(".")[-1]
        if isinstance(node.source, exp.Table):
            table = node.source.name.lower()
            owners = [db for db, tables in schema.items() if table in tables]
            db = node.source.db or (owners[0] if len(owners) == 1 else default_schema)
            return f"{db}.{table}.{part}".lower()
        if node.reference_node_name:
            return f"cte.{node.reference_node_name}.{part}".lower()
        return None

    def walk(node: Any, target: str, seen: set[int]) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        for child in node.downstream:
            source = node_id(child)
            if source is None:  # pass through a positional node to what it names
                walk(child, target, seen)
                continue
            key = (source, target)
            if key not in edges:
                edges[key] = {
                    "id": f"step:{len(edges)}",
                    "type": "FEEDS",
                    "source": source,
                    "target": target,
                    "transform": _step_transform(_expression_of(child, node)),
                    "file": "",
                    "line": 0,
                    "depth": 1,
                }
            walk(child, source, seen)

    walk(root, result, set())
    return list(edges.values())


def _select_name(parsed: Any, query: Any, column: str) -> str:
    """The select-list name that produces `column`: itself, or - for INSERT (cols) SELECT -
    the projection in the same position as `column` in the insert's column list."""
    from sqlglot import exp

    if isinstance(parsed, exp.Insert) and isinstance(parsed.this, exp.Schema):
        names = [c.name.lower() for c in parsed.this.expressions]
        if column.lower() in names:
            selects = query.selects if hasattr(query, "selects") else []
            index = names.index(column.lower())
            if index < len(selects):
                return str(selects[index].alias_or_name)
    return column


def _expression_of(child: Any, parent: Any) -> Any:
    """The expression that computes the PARENT's column from the child: its select item."""
    return parent.expression


def _step_transform(expression: Any) -> str:
    """identity / derived / conditional / aggregated, read off one select item."""
    from sqlglot import exp

    inner = expression.this if isinstance(expression, exp.Alias) else expression
    if inner is None or isinstance(inner, exp.Column):
        return "identity"
    if inner.find(exp.AggFunc) is not None:
        return "aggregated"
    if inner.find(exp.Case, exp.Coalesce, exp.If, exp.Greatest, exp.Least) is not None:
        return "conditional"
    return "derived"


def step_inputs(tools: Any, node: str) -> tuple[str, dict[str, Any]] | None:
    """(the SQL of the statement that writes `node`, SQLGlot's schema from the graph).

    None when nothing in the graph writes `node` (a base column has no steps to show).
    """
    from lineage.parsing.sqlscript import split_statements

    rows = tools._read(
        """
        MATCH ()-[r:FEEDS|INFLUENCES]->(c {id: $id}) WITH r LIMIT 1
        MATCH (f:File {id: r.file}) RETURN f.text AS text, r.line AS line
        """,
        id=node,
    )
    if not rows or not rows[0]["text"]:
        return None
    statement = next(
        (text for line, text in split_statements(rows[0]["text"]) if line == rows[0]["line"]),
        None,
    )
    if statement is None:
        return None
    schema: dict[str, Any] = {}
    for row in tools._read(
        "MATCH (t:Table)-[:HAS_COLUMN]->(c:Column) RETURN t.id AS t, collect(c.id) AS cols"
    ):
        parts = row["t"].split(".")
        if len(parts) != 2 or row["t"].startswith("<"):
            continue
        schema.setdefault(parts[0], {})[parts[1]] = {
            col.rsplit(".", 1)[-1]: "text" for col in row["cols"]
        }
    return statement, schema


# --- table-level summary (end-to-end gap 9) ------------------------------------------------
#
# A wide report - 49 result columns fed by 15 tables - drew 268 lines into one 49-row card:
# correct and unreadable. The table view keeps one compact card per table and ONE line per
# pair of tables, its badge the number of the source's columns behind it. The detail is a
# switch away, and the edge table under the diagram always lists every fact.

TABLE_ROW = "⟨columns⟩"
"""The single row a card has in the table view; never a real column name."""


def table_level(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ONE edge per (source table, target table), whatever mix of flows it carries.

    Drawn in its strongest flow - value, else influence, else filter (a filter-only pair
    meets the target's header) - with the number of the source table's columns behind it as
    the badge, and the breakdown by flow in the tooltip.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for edge in edges:
        source = table_of(edge["source"])
        target = edge["target"] if edge["type"] == "FILTERS" else table_of(edge["target"])
        if source == target:
            continue
        groups.setdefault((source, target), []).append(edge)
    words = {"FEEDS": "as values", "INFLUENCES": "as grouping or ordering", "FILTERS": "as filters"}
    out = []
    for (source, target), same in sorted(groups.items()):
        kinds = {e["type"] for e in same}
        kind = next(k for k in ("FEEDS", "INFLUENCES", "FILTERS") if k in kinds)
        used = {e["source"] for e in same}
        values = [e for e in same if e["type"] == "FEEDS"]
        hardest = max(
            (e["transform"] or "identity" for e in values),
            key=lambda t: LADDER.index(t) if t in LADDER else 0,
            default="identity",
        )
        parts = [
            f"{len({e['source'] for e in same if e['type'] == k})} {words[k]}"
            for k in ("FEEDS", "INFLUENCES", "FILTERS")
            if k in kinds
        ]
        out.append(
            {
                "id": "table:" + "|".join(sorted(e["id"] for e in same))[:200],
                "type": kind,
                "source": f"{source}.{TABLE_ROW}",
                "target": target if kind == "FILTERS" else f"{target}.{TABLE_ROW}",
                "transform": hardest,
                "badge": str(len(used)),
                "file": same[0]["file"],
                "line": same[0]["line"],
                "where": sorted({f"{e['file']}:{e['line']}" for e in same}),
                "depth": 1,
                "count": len(same),
                "breakdown": ", ".join(parts),
            }
        )
    return out


# --- why does this edge exist? (F2.3) --------------------------------------------------------


def edge_context(tools: Any, edge_id: str, context: int = 2) -> dict[str, Any] | None:
    """One edge, and the lines of its statement that explain it.

    Only the lines that name the source column or the target column, with `context` lines
    around each; the stretches between are folded. A 900-line statement shows a dozen lines
    instead of its first 200. Lines are file line numbers.
    """
    import re

    from lineage.parsing.sqlscript import split_statements

    found = tools.describe_edges([edge_id])
    if not found:
        return None
    edge = found[0]
    rows = tools._read("MATCH (f:File {id: $id}) RETURN f.text AS text", id=edge["file"])
    text = rows[0]["text"] if rows else None
    statement = None
    if text:
        statement = next(
            (sql for line, sql in split_statements(text) if line == edge["line"]), None
        )
    if statement is None:
        return {"edge": edge, "lines": []}

    def word(node: str) -> re.Pattern[str] | None:
        if node.startswith("<result:") and ">." not in node:
            return None  # a whole result table, e.g. a filter target
        name = node.rsplit(".", 1)[-1]
        if not name or name.startswith("<"):
            return None
        return re.compile(rf'(?<![\w$])"?{re.escape(name)}"?(?![\w$])', re.I)

    source, target = word(edge["source"]), word(edge["target"])
    numbered = list(enumerate(statement.splitlines(), start=edge["line"]))

    # The source's lines by what each reference BINDS to, not by its name: `unit_cost` is a
    # column of several tables, and only `c.unit_cost` is components'. None: not parsable.
    bound = _source_lines(statement, edge["source"])
    first = edge["line"] - 1

    def marked(within: set[int] | None) -> dict[int, str]:
        found: dict[int, str] = {}
        for number, line in numbered:
            if within is not None and number not in within:
                continue
            code = line.split("--", 1)[0]
            if bound is not None:
                hit_source = (number - first) in bound
            else:
                hit_source = bool(source and source.search(code))
            hit_target = bool(target and target.search(code))
            if hit_source or hit_target:
                found[number] = (
                    "both" if hit_source and hit_target else ("source" if hit_source else "target")
                )
        return found

    # Narrowed to the CTEs on the target's own path when they are known; the whole
    # statement when that would hide the source altogether.
    on_path = _path_lines(tools, edge, statement)
    marks = marked(on_path)
    narrowed = on_path is not None and any(m in ("source", "both") for m in marks.values())
    if not narrowed:
        marks = marked(None)
    keep = {n for marked in marks for n in range(marked - context, marked + context + 1)} or {
        n for n, _ in numbered[: 2 * context + 1]
    }
    lines: list[dict[str, Any]] = []
    previous = None
    for number, line in numbered:
        if number not in keep:
            continue
        if previous is not None and number != previous + 1:
            lines.append(
                {"number": None, "text": f"... {number - previous - 1} lines ...", "mark": "gap"}
            )
        lines.append({"number": number, "text": line, "mark": marks.get(number)})
        previous = number
    return {
        "edge": edge,
        "lines": lines,
        "statement_lines": len(numbered),
        "narrowed": narrowed,
    }


def _source_lines(statement: str, source: str) -> set[int] | None:
    """Statement-relative lines where a reference BINDS to the source column.

    `alias.col` counts when the alias names the source's table in its own SELECT or an
    enclosing one (a correlated subquery); a bare `col` counts when that table is the only
    relation in its SELECT's FROM. None when the SQL cannot be parsed - the caller then
    falls back to matching the name.
    """
    import sqlglot
    from sqlglot import exp

    table_id, _, column = source.rpartition(".")
    schema, _, table = table_id.rpartition(".")
    if not table or not column or source.startswith("<"):
        return None
    try:
        parsed = sqlglot.parse_one(statement, read="postgres")
    except Exception:
        return None

    def is_source(node: Any) -> bool:
        return (
            isinstance(node, exp.Table)
            and node.name.lower() == table.lower()
            and (not node.db or not schema or node.db.lower() == schema.lower())
        )

    def owning(node: Any) -> Any:
        parent = node.parent
        while parent is not None and not isinstance(parent, exp.Select):
            parent = parent.parent
        return parent

    def relations(select: Any) -> list[Any]:
        from_ = select.args.get("from_") or select.args.get("from")
        found = [from_.this] if from_ is not None else []
        return found + [join.this for join in select.args.get("joins") or []]

    lines: set[int] = set()
    for ref in parsed.find_all(exp.Column):
        if ref.name.lower() != column.lower():
            continue
        line = ref.this.meta.get("line") if isinstance(ref.this, exp.Identifier) else None
        if not line:
            continue
        select = owning(ref)
        if ref.table:
            wanted = ref.table.lower()
            while select is not None:
                named = [r for r in relations(select) if (r.alias_or_name or "").lower() == wanted]
                if named:
                    if is_source(named[0]):
                        lines.add(int(line))
                    break
                select = owning(select)
        elif select is not None:
            sources = relations(select)
            if len(sources) == 1 and is_source(sources[0]):
                lines.add(int(line))
    return lines


def _path_lines(tools: Any, edge: dict[str, Any], statement: str) -> set[int] | None:
    """File lines of the CTEs on the target column's value path, plus the final query.

    A source column is often used all over a long statement - `order_date` in a dozen CTEs -
    while only a few of those CTEs lead to THIS target. The step walker names them; SQLGlot
    gives each CTE's first line. None (no narrowing) when the walk is not possible.
    """
    import sqlglot
    from sqlglot import exp

    if edge["type"] == "FILTERS":
        return None
    inputs = step_inputs(tools, edge["target"])
    if inputs is None:
        return None
    column = edge["target"].rsplit(".", 1)[-1]
    steps = cte_steps(statement, column, edge["target"], "public", inputs[1])
    names = {e["source"].split(".")[1] for e in steps if e["source"].startswith("cte.")}
    if not names:
        return None
    try:
        parsed = sqlglot.parse_one(statement, read="postgres")
    except Exception:
        return None
    starts = []
    for cte in parsed.find_all(exp.CTE):
        alias = cte.args.get("alias")
        line = alias.this.meta.get("line") if alias is not None and alias.this else None
        if line:
            starts.append((int(line), cte.alias_or_name.lower()))
    if not starts:
        return None
    starts.sort()
    total = len(statement.splitlines())
    query = parsed if isinstance(parsed, exp.Query) else parsed.find(exp.Query)
    main = None
    if query is not None and getattr(query, "selects", None):
        ident = query.selects[0].find(exp.Identifier)
        main = ident.meta.get("line") if ident is not None else None
    keep: set[int] = set()
    for index, (line, name) in enumerate(starts):
        if index + 1 < len(starts):
            end = starts[index + 1][0] - 1
        else:
            end = int(main) - 1 if main else total
        if name in names:
            keep.update(range(line, end + 1))
    if main:
        keep.update(range(int(main) - 1, total + 1))
    return {edge["line"] - 1 + n for n in keep}


def context_html(lines: list[dict[str, Any]]) -> str:
    """The context lines as a small, escaped HTML listing with the marks coloured.

    Every colour is explicit - background AND text - so the listing reads the same in
    Streamlit's light and dark themes (inheriting the theme's text colour once put white
    text on a white background).
    """
    # Marked lines: a tinted dark background and a light text of the same hue.
    marks = {
        "source": ("#3d3510", "#fde68a"),  # yellow - names the source column
        "target": ("#4a1d3c", "#fbcfe8"),  # pink - names the target column
        "both": ("#123d24", "#bbf7d0"),  # green - names both
    }
    out = [
        '<div style="font-family: Consolas, Menlo, monospace; font-size: 12.5px; '
        "border: 1px solid #334155; border-radius: 6px; overflow-x: auto; "
        'background: #0f172a; color: #e2e8f0; padding: 4px 0">'
    ]
    for line in lines:
        text = _escape(line["text"]) or "&nbsp;"
        if line["mark"] == "gap":
            out.append(
                '<div style="color:#64748b; padding: 1px 8px; background:#111c33; '
                f'font-style: italic">{text}</div>'
            )
            continue
        background, colour = marks.get(line["mark"] or "", ("transparent", "#cbd5e1"))
        out.append(
            f'<div style="white-space: pre; padding: 0 8px; background:{background}; '
            f'color:{colour}"><span style="color:#64748b; display:inline-block; width:4.5em">'
            f"{line['number']}</span>{text}</div>"
        )
    out.append("</div>")
    return "".join(out)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
