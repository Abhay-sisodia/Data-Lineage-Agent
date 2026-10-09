"""TEMPORARY observability views over the lineage knowledge graph (plan C9).

Two views, picked in the app's sidebar:

* **Ask the lineage** - a question through the agent harness (`lineage ask`): the answer,
  its status, the cited edges as a table and as a graph, the unknowns, and the full trace.
* **Lineage graph** - one column's upstream lineage or downstream impact, drawn to a chosen
  depth, coloured by flow, grouped by table, with the gaps on it.

Like the rest of `frontend/`, this only imports and renders the product; deleting the
folder removes it. Graphs are DOT rendered by Streamlit's built-in graphviz support.
"""

from __future__ import annotations

import asyncio
from typing import Any

import streamlit as st

FLOW_STYLE = {
    "FEEDS": 'color="#2563eb" penwidth=1.6',
    "FILTERS": 'color="#dc2626" style=dashed',
    "INFLUENCES": 'color="#7c3aed" style=dotted penwidth=1.6',
}
MAX_EDGES = 300

EXAMPLES = [
    "Which columns feed the value of mart.shipment_fact.gross_margin_usd?",
    "Which columns filter the rows that land in mart.po_supplier_summary?",
    "What is directly affected if mart.fact_po_line.supplier_id changes?",
    "If gold.fx_rate.conversion_rate is wrong, which columns in mart tables are affected, "
    "directly or through intermediate tables? List the affected mart columns.",
    "What could not be analysed in hard/h4_nightly_load.sql?",
]


@st.cache_resource
def _tools() -> Any:
    from lineage.graph.neo4j_conn import Neo4jSettings, connect
    from lineage.graph.tools import GraphTools

    settings = Neo4jSettings.from_env()
    driver = connect(settings)
    driver.verify_connectivity()
    return GraphTools(driver, settings)


def _connected() -> Any | None:
    try:
        tools = _tools()
    except Exception as error:
        st.error(
            "No lineage graph to read. Start Neo4j (`docker compose up -d neo4j`), install "
            "`requirements-kg.txt`, and build the graph (`lineage graph build <folder>`).\n\n"
            f"`{error}`"
        )
        return None
    snapshot = tools.snapshot()
    if not snapshot:
        st.warning("Neo4j is up but holds no lineage graph. Run `lineage graph build <folder>`.")
        return None
    st.sidebar.caption(f"Graph snapshot `{snapshot}`")
    return tools


def lineage_dot(edges: list[dict[str, Any]], focus: set[str] | None = None) -> str:
    """DOT for lineage edges: columns grouped into their tables, edges styled by flow."""
    focus = focus or set()
    tables: dict[str, set[str]] = {}
    standalone: set[str] = set()
    for edge in edges:
        for node in (edge["source"], edge["target"]):
            table = _table_of(node)
            if table == node:  # a table (filter target) or a variable
                standalone.add(node)
            else:
                tables.setdefault(table, set()).add(node)
    lines = [
        "digraph lineage {",
        "  rankdir=LR; bgcolor=transparent; compound=true;",
        '  node [shape=box style="rounded,filled" fillcolor="#f8fafc" color="#94a3b8" '
        'fontname="Helvetica" fontsize=11];',
        '  edge [fontname="Helvetica" fontsize=9 fontcolor="#475569"];',
    ]
    for index, (table, columns) in enumerate(sorted(tables.items())):
        lines.append(f'  subgraph "cluster_{index}" {{')
        lines.append(
            f'    label={_q(table)}; style="rounded,filled"; color="#cbd5e1"; '
            'fillcolor="#eef2ff"; fontname="Helvetica"; fontsize=10;'
        )
        for column in sorted(columns):
            lines.append(
                f"    {_q(column)} [label={_q(column.rsplit('.', 1)[-1])}{_hl(column, focus)}];"
            )
        lines.append("  }")
    for node in sorted(standalone):
        shape = 'shape=cylinder fillcolor="#fee2e2"' if _is_table(node) else ""
        lines.append(f"  {_q(node)} [label={_q(node)} {shape}{_hl(node, focus)}];")
    for edge in edges:
        label = edge["transform"] if edge["type"] == "FEEDS" else edge["type"].lower()
        where = f"{edge['file']}:{edge['line']}"
        lines.append(
            f"  {_q(edge['source'])} -> {_q(edge['target'])} "
            f"[{FLOW_STYLE.get(edge['type'], '')} label={_q(label)} tooltip={_q(where)}];"
        )
    lines.append("}")
    return "\n".join(lines)


def edge_rows(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "flow": {"FEEDS": "value", "FILTERS": "filter", "INFLUENCES": "influence"}.get(
                e["type"], e["type"]
            ),
            "source": e["source"],
            "target": e["target"],
            "transform": e["transform"],
            "phase": e["phase"],
            "where": f"{e['file']}:{e['line']}",
            "hop": e.get("depth", ""),
            "id": e["id"],
        }
        for e in edges
    ]


def legend() -> None:
    st.caption(
        "Blue solid = **value** (data flows in, labelled with its transform) · red dashed = "
        "**filter** (decides which rows of the table land) · purple dotted = **influence** "
        "(GROUP BY / window ordering) · red cylinder = a table a filter applies to."
    )


# --- the views ---------------------------------------------------------------------------


def graph_page() -> None:
    st.title("Lineage graph")
    st.caption("A column's upstream lineage or downstream impact, read from the graph.")
    tools = _connected()
    if tools is None:
        return
    with st.sidebar:
        name = st.text_input("Column (partial names allowed)", value="gross_margin_usd")
        direction = st.radio("Direction", ["upstream (lineage)", "downstream (impact)"])
        depth = st.slider("Hops", 1, 8, 2)
        flows = st.multiselect(
            "Flows", ["FEEDS", "FILTERS", "INFLUENCES"], default=["FEEDS", "FILTERS", "INFLUENCES"]
        )
    matches = tools.find(name, limit=50) if name.strip() else []
    if not matches:
        st.info(f"No column or table matches '{name}'.")
        return
    node = matches[0]["id"]
    if len(matches) > 1:
        node = st.selectbox(f"'{name}' matches {len(matches)} nodes", [m["id"] for m in matches])
    upward = direction.startswith("upstream")
    edges = tools.upstream(node, depth) if upward else tools.downstream(node, depth)
    edges = [e for e in edges if e["type"] in flows]

    top = st.columns(4)
    top[0].metric("Edges", len(edges))
    top[1].metric("Value", sum(e["type"] == "FEEDS" for e in edges))
    top[2].metric("Filter", sum(e["type"] == "FILTERS" for e in edges))
    top[3].metric("Influence", sum(e["type"] == "INFLUENCES" for e in edges))
    if not edges:
        st.info(f"No {'lineage' if upward else 'impact'} for `{node}` with these flows.")
    else:
        shown = edges[:MAX_EDGES]
        if len(edges) > MAX_EDGES:
            st.warning(f"Showing the first {MAX_EDGES} of {len(edges)} edges - lower the hops.")
        st.graphviz_chart(lineage_dot(shown, {node}), use_container_width=True)
        legend()
        st.dataframe(edge_rows(edges), use_container_width=True, hide_index=True)

    gaps = tools.gaps(node=node) if upward else None
    if gaps and (gaps["boundaries"] or gaps["refusals"]):
        st.subheader("Where knowledge stops on this lineage")
        for b in gaps["boundaries"]:
            st.warning(f"**{b['kind']}** - `{b['subject']}`")
        for r in gaps["refusals"]:
            st.error(f"Refused statement **{r['file']}:{r['line']}** ({r['code']}): {r['reason']}")


def ask_page() -> None:
    st.title("Ask the lineage")
    st.caption(
        "A question goes through the agent harness: write guard, name resolver, a Claude agent "
        "limited to the read-only graph tools, grounding check, coverage gate. Answers cite "
        "graph ids; a repeated question on the same graph is served from the cache."
    )
    tools = _connected()
    if tools is None:
        return
    with st.sidebar:
        use_cache = st.checkbox(
            "Use cached answers",
            value=True,
            help="Off = ask the model again (uses your Claude plan).",
        )
        example = st.selectbox("Example questions", ["(type your own)", *EXAMPLES])
    question = st.text_area(
        "Question", value="" if example == "(type your own)" else example, height=90
    )
    if not st.button("Ask", type="primary", disabled=not question.strip()):
        return

    from lineage.agent.harness import MODEL, Harness
    from lineage.agent.store import TraceStore

    with st.spinner(f"Asking {MODEL} through the harness..."):
        try:
            run = asyncio.run(
                Harness(tools, store=TraceStore(), read_cache=use_cache).ask(question)
            )
        except Exception as error:
            st.error(f"The harness raised: {error}")
            return
    answer = run.answer

    badge = {
        "answered": "🟢 answered",
        "clarify": "🟡 needs clarification",
        "refused": "🔴 refused",
    }
    top = st.columns(4)
    top[0].metric("Status", badge.get(answer.status, answer.status))
    top[1].metric("Stage", run.stage)
    top[2].metric("Tool calls", len(run.tool_calls))
    top[3].metric("Cost (USD)", f"{run.cost_usd:.3f}")
    st.markdown(answer.text)

    if answer.candidates:
        st.subheader("Which one did you mean?")
        st.write(answer.candidates)
    cited = tools.describe_edges(answer.cited_edges) if answer.cited_edges else []
    if cited:
        st.subheader(f"Evidence: {len(cited)} cited edges")
        st.graphviz_chart(lineage_dot(cited[:MAX_EDGES]), use_container_width=True)
        legend()
        st.dataframe(edge_rows(cited), use_container_width=True, hide_index=True)
    if answer.cited_nodes:
        st.subheader("Listed nodes")
        st.write(answer.cited_nodes)
    if answer.unknowns:
        st.subheader("Where knowledge stops")
        for unknown in answer.unknowns:
            st.warning(unknown)
    with st.expander(
        f"Trace - {len(run.tool_calls)} tool calls; {'; '.join(run.notes) or 'no notes'}"
    ):
        if run.trace_path:
            st.caption(f"Saved at `{run.trace_path}` - replay with `lineage replay <path>`.")
        for call in run.tool_calls:
            st.markdown(f"**{call['tool']}** `{call['args']}`")
            st.json(call["result"], expanded=False)


# --- helpers -----------------------------------------------------------------------------


def _table_of(node: str) -> str:
    if node.startswith("<result:"):
        return node.split(">", 1)[0] + ">" if "." in node.split(">", 1)[-1] else node
    head, _, _ = node.rpartition(".")
    return head if head.count(".") >= 1 else node


def _is_table(node: str) -> bool:
    return node.startswith("<result:") or node.count(".") == 1


def _q(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _hl(node: str, focus: set[str]) -> str:
    return ' fillcolor="#fde68a" color="#d97706" penwidth=2' if node in focus else ""
