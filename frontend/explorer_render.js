// Lineage explorer renderer (MVP plan F2): ELK layered layout + an SVG of table cards.
//
// Input: the model from frontend/explorer.py `model()` - cards (one per table, a row per
// column) and edges (column row -> column row, or -> a card's header for a filter).
// Output: an SVG string. Orthogonal edges only (horizontal and vertical segments).
//
// Two ELK passes, for fewer crossings:
//   1. ports free on their side: ELK orders them to minimise crossings;
//   2. rows re-ordered inside each card by where pass 1 put their ports, then laid out
//      again with every port fixed to its row - so an edge always meets its row.
// Shared by the Streamlit page (in the browser) and by Node (for static images); the
// React explorer is meant to reuse it unchanged.

(function (root) {
  const HEADER_H = 40, NOTE_H = 20, ROW_H = 24, PAD = 12;
  const COLOURS = {
    FEEDS: "#2563eb", INFLUENCES: "#7c3aed", FILTERS: "#ef4444",
  };

  function textWidth(text, size) { return text.length * size * 0.6; }

  function geometry(card) {
    const widths = [textWidth(card.title, 13) + 34, textWidth(card.subtitle, 10) + 24];
    if (card.note) widths.push(textWidth(card.note, 10) + 20);
    for (const row of card.rows) widths.push(textWidth(row.label, 12) + 28);
    const width = Math.max(150, Math.ceil(Math.max(...widths)));
    const top = HEADER_H + (card.note ? NOTE_H : 0);
    return { width, top, height: top + card.rows.length * ROW_H + 4 };
  }

  function elkGraph(model, fixed) {
    const children = model.cards.map((card) => {
      const g = geometry(card);
      card._g = g;
      const ports = [{
        id: card.id + ".h", width: 0, height: 0,
        layoutOptions: { "elk.port.side": "WEST" },
        ...(fixed ? { x: 0, y: HEADER_H / 2 } : {}),
      }];
      card.rows.forEach((row, i) => {
        const y = g.top + i * ROW_H + ROW_H / 2;
        for (const side of ["WEST", "EAST"]) {
          ports.push({
            id: row.id + (side === "WEST" ? ".w" : ".e"), width: 0, height: 0,
            layoutOptions: { "elk.port.side": side },
            ...(fixed ? { x: side === "WEST" ? 0 : g.width, y } : {}),
          });
        }
      });
      return {
        id: card.id, width: g.width, height: g.height, ports,
        layoutOptions: { "elk.portConstraints": fixed ? "FIXED_POS" : "FIXED_SIDE" },
      };
    });
    const edges = model.edges.map((e) => ({
      id: e.id,
      sources: [e.from + ".e"],
      targets: [e.to.endsWith(".h") ? e.to : e.to + ".w"],
    }));
    return {
      id: "root",
      layoutOptions: {
        "elk.algorithm": "layered",
        "elk.direction": "RIGHT",
        "elk.edgeRouting": "ORTHOGONAL",
        "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
        "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
        "elk.layered.spacing.nodeNodeBetweenLayers": "110",
        "elk.spacing.nodeNode": "36",
        "elk.spacing.edgeEdge": "10",
        "elk.layered.spacing.edgeEdgeBetweenLayers": "10",
        "elk.layered.spacing.edgeNodeBetweenLayers": "24",
        "elk.padding": "[top=20,left=20,bottom=20,right=20]",
      },
      children, edges,
    };
  }

  function reorderRows(model, laid) {
    const portY = {};
    for (const node of laid.children) for (const p of node.ports || []) portY[p.id] = p.y;
    const linked = new Set();
    for (const e of model.edges) { linked.add(e.from + ".e"); linked.add(e.to + ".w"); }
    for (const card of model.cards) {
      const key = (row) => {
        const ys = [row.id + ".w", row.id + ".e"].filter((id) => linked.has(id)).map((id) => portY[id]);
        return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 1e9;
      };
      card.rows.sort((a, b) => key(a) - key(b) || a.label.localeCompare(b.label));
    }
  }

  function esc(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function svg(model, laid) {
    const out = [];
    const W = Math.ceil(laid.width), H = Math.ceil(laid.height);
    out.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" font-family="Helvetica, Arial, sans-serif">`);
    out.push("<defs>");
    for (const [kind, colour] of Object.entries(COLOURS)) {
      out.push(`<marker id="arrow-${kind}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="${colour}"/></marker>`);
    }
    out.push("</defs>");
    const where = {};
    for (const node of laid.children) where[node.id] = node;

    // Edges first, so the cards sit on top of any line that passes behind them.
    const byId = {}, into = {};
    for (const e of model.edges) { byId[e.id] = e; into[e.to] = (into[e.to] || 0) + 1; }
    for (const edge of laid.edges) {
      const e = byId[edge.id];
      for (const s of edge.sections || []) {
        const pts = [s.startPoint, ...(s.bendPoints || []), s.endPoint];
        const d = pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
        const dash = e.kind === "FILTERS" ? ' stroke-dasharray="6 4"' : e.kind === "INFLUENCES" ? ' stroke-dasharray="2 3"' : "";
        out.push(`<g class="edge"><title>${esc(e.tip)}</title>`);
        out.push(`<path d="${d}" fill="none" stroke="transparent" stroke-width="10"/>`);
        out.push(`<path d="${d}" fill="none" stroke="${COLOURS[e.kind]}" stroke-width="${e.kind === "FEEDS" ? 1.6 : 1.3}"${dash} marker-end="url(#arrow-${e.kind})" opacity="${e.kind === "FILTERS" ? 0.75 : 1}"/>`);
        if (e.badge) {
          // At the arrow head, unless several edges share it - then by each edge's source,
          // so the badges do not stack on one point.
          const shared = into[e.to] > 1;
          const bx = shared ? s.startPoint.x + 18 : s.endPoint.x - 20;
          const by = shared ? s.startPoint.y : s.endPoint.y;
          out.push(`<circle cx="${bx}" cy="${by}" r="8" fill="#ffffff" stroke="${COLOURS.FEEDS}" stroke-width="1.2"/>`);
          out.push(`<text x="${bx}" y="${by + 4}" text-anchor="middle" font-size="11" font-weight="bold" fill="${COLOURS.FEEDS}">${esc(e.badge)}</text>`);
        }
        out.push("</g>");
      }
    }

    for (const card of model.cards) {
      const n = where[card.id], g = card._g, x = n.x, y = n.y;
      out.push(`<g class="card">`);
      out.push(`<rect x="${x}" y="${y}" width="${g.width}" height="${g.height}" rx="8" fill="#ffffff" stroke="#94a3b8" stroke-width="1"/>`);
      out.push(`<path d="M${x},${y + 8} a8,8 0 0 1 8,-8 h${g.width - 16} a8,8 0 0 1 8,8 v${HEADER_H - 8} h-${g.width} z" fill="${card.colour}"/>`);
      out.push(`<text x="${x + PAD}" y="${y + 18}" font-size="13" font-weight="bold" fill="#ffffff">${esc(card.title)}</text>`);
      out.push(`<text x="${x + PAD}" y="${y + 32}" font-size="10" fill="#e2e8f0">${esc(card.subtitle)}</text>`);
      if (card.warn) {
        out.push(`<g><title>${esc(card.warn)}</title><text x="${x + g.width - 20}" y="${y + 24}" font-size="15" fill="#fbbf24">⚠</text></g>`);
      }
      if (card.note) {
        out.push(`<rect x="${x + 1}" y="${y + HEADER_H}" width="${g.width - 2}" height="${NOTE_H}" fill="#fef2f2"/>`);
        out.push(`<text x="${x + PAD}" y="${y + HEADER_H + 14}" font-size="10" fill="#b91c1c">${esc(card.note)}</text>`);
      }
      card.rows.forEach((row, i) => {
        const ry = y + g.top + i * ROW_H;
        if (i) out.push(`<line x1="${x + 1}" y1="${ry}" x2="${x + g.width - 1}" y2="${ry}" stroke="#f1f5f9"/>`);
        if (row.focus) out.push(`<rect x="${x + 1}" y="${ry}" width="${g.width - 2}" height="${ROW_H}" fill="#fde68a"/>`);
        out.push(`<text x="${x + PAD}" y="${ry + 16}" font-size="12" fill="${row.focus ? "#78350f" : "#0f172a"}"${row.focus ? ' font-weight="bold"' : ""}>${esc(row.label)}</text>`);
      });
      out.push("</g>");
    }
    out.push("</svg>");
    return out.join("\n");
  }

  async function render(model, ELK) {
    const elk = new ELK();
    const first = await elk.layout(elkGraph(model, false));
    reorderRows(model, first);
    const laid = await elk.layout(elkGraph(model, true));
    return svg(model, laid);
  }

  const api = { render };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.LineageExplorer = api;
})(typeof window !== "undefined" ? window : globalThis);
