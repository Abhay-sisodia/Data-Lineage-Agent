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
import os
from typing import Any

import explorer
import streamlit as st

MAX_EDGES = 300
FOCUS_KEY = "lg_column"
SCOPE_KEY = "lg_scope"
TABLE_VIEW_FROM = 120
"""A file or the whole graph opens at table level above this many edges (gap 9)."""

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
    with st.sidebar:
        if st.button(
            "Rebuild the final-suite demo graph",
            help="Replace the graph with the 15 final-suite files (stress/pg/final).",
        ):
            with st.spinner("Building the graph from stress/pg/final ..."):
                rebuild_demo(tools)
    snapshot = tools.snapshot()
    if not snapshot:
        st.warning(
            "Neo4j is up but holds no lineage graph. Load SQL from *Parse SQL*, or "
            "rebuild the demo graph from the sidebar."
        )
        return None
    summary = graph_summary(tools)
    st.sidebar.caption(
        f"Graph snapshot `{snapshot}` - {summary['files']} files, "
        f"{summary['lineage']} lineage edges"
    )
    if summary["lineage"] == 0:
        st.warning(
            f"The graph holds {summary['files']} file(s) but **no lineage edges**: "
            f"{', '.join(summary['file_names'][:5])}. Their SQL resolved to nothing - "
            f"{summary['dangling']} dangling references were declared, which usually means "
            "the wrong dictionary was used when loading. Reload from *Parse SQL* with the "
            "right dictionary, or rebuild the demo graph from the sidebar."
        )
    return tools


def graph_summary(tools: Any) -> dict[str, Any]:
    """Files, lineage edges and dangling references in the graph, for the sidebar."""
    row = tools._read(
        """
        OPTIONAL MATCH (f:File) WITH collect(f.id) AS files
        OPTIONAL MATCH ()-[r:FEEDS|FILTERS|INFLUENCES]->() WITH files, count(r) AS lineage
        OPTIONAL MATCH (:File)-[d:DECLARES]->(b:Boundary {kind: 'dangling_reference'})
        RETURN files, lineage, count(d) AS dangling
        """
    )[0]
    return {
        "files": len(row["files"]),
        "file_names": sorted(row["files"]),
        "lineage": row["lineage"],
        "dangling": row["dangling"],
    }


def rebuild_demo(tools: Any) -> None:
    """The final-suite graph (`lineage graph build stress/pg/final ...`)."""
    from pathlib import Path

    from lineage.config import AnalysisConfig
    from lineage.graph.build import analyse_folder, folder_sources, load
    from lineage.resolution.dictionary import Dictionary

    folder = Path(__file__).resolve().parent.parent / "stress" / "pg" / "final"
    if not folder.exists():
        st.error("stress/pg/final is not on this machine.")
        return
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    book = Dictionary.load(folder / "dictionary.json")
    results = analyse_folder(folder, book, config)
    load(
        tools._driver,
        results,
        database=tools._settings.database,
        sources=folder_sources(folder),
        default_schema=book.default_schema,
    )


def best_dictionary(sql: str, choices: dict[str, Any]) -> str:
    """The dictionary that knows the most `schema.table` names the SQL mentions."""
    import json
    import re
    from pathlib import Path

    named = {m.lower() for m in re.findall(r"\b([A-Za-z_]\w*\.[A-Za-z_]\w*)\b", sql)}

    def known(path: Any) -> int:
        tables = json.loads(Path(path).read_text(encoding="utf-8"))["columns"]
        return len(named & {k.lower() for k in tables})

    return max(choices, key=lambda label: (known(choices[label]), -list(choices).index(label)))


def edge_rows(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "flow": explorer.VERB.get(e["type"], e["type"]),
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
        "Scroll to zoom, drag to pan. Blue solid = **value** (Σ aggregated · ƒ derived · "
        "? conditional · no badge = copied) "
        "· red dashed into a card's header = **filter** (decides which rows of that table "
        "exist) · purple dotted = **influence** (GROUP BY, window ordering) · teal dash-dot = "
        "**join key** (picked a lookup's row) · grey dashed = **unverified**: what a "
        "refused statement still names (a column, or table to table) · yellow row = the "
        "column you asked about · ⚠ = a gap or an effect: a boundary, a refused statement, "
        "or a TRUNCATE / DROP / ALTER touches that table (hover it)."
    )


def load_sql(sql: str, name: str, book: Any, config: Any, replace: bool) -> Any:
    """Analyse one pasted script and load it into the graph, as the file `name`.

    The same path `lineage graph build` takes - analyse, export to lineage-result/1 and
    back, load - so the graph holds exactly what the parser said. `replace` empties the
    graph first; otherwise the script is merged in beside what is there. Either way the
    snapshot changes, so no cached answer from the previous graph is served for it.
    """
    import json

    from lineage.analysis.procedure import analyse_source
    from lineage.export import from_document, to_document
    from lineage.graph.build import load

    document = to_document(
        analyse_source(sql, book, config),
        source_path=name,
        source_text=sql,
        dialect=config.dialect,
        config_fingerprint=config.fingerprint(),
        dictionary_fingerprint=book.fingerprint(),
    )
    result = from_document(json.loads(json.dumps(document)))
    tools = _tools()
    return load(
        tools._driver,  # the build is the one writer; tools only ever read
        [result],
        database=tools._settings.database,
        replace=replace,
        sources={result.source.path: sql},
        default_schema=book.default_schema,
    )


def load_panel(sql: str, book: Any, config: Any) -> None:
    """The Parse SQL view's bridge to the graph views."""
    with st.expander("Load this SQL into the lineage graph - then ask questions about it"):
        if config.dialect != "postgres":
            st.info("The lineage graph is PostgreSQL-only for now.")
            return
        name = st.text_input("File name in the graph", value="pasted/query.sql")
        mode = st.radio(
            "How",
            ["replace the graph with this SQL", "add this SQL to the graph"],
            help="Replace: the graph holds only this script. Add: it joins the files already "
            "there, and tables they share link up.",
        )
        if not st.button("Load into graph"):
            return
        try:
            report = load_sql(
                sql,
                name.strip() or "pasted/query.sql",
                book,
                config,
                replace=mode.startswith("replace"),
            )
        except Exception as error:
            st.error(f"Could not load the graph: {error}")
            return
        lineage = sum(report.counts.get(t, 0) for t in ("FEEDS", "FILTERS", "INFLUENCES"))
        if lineage == 0:
            dangling = report.counts.get("DECLARES", 0)
            st.warning(
                f"Loaded `{name}`, but it produced **no lineage edges** ({dangling} boundaries "
                "declared). Most often the dictionary does not know these tables - check the "
                "PostgreSQL dictionary in the sidebar (or use auto-detect) and load again."
            )
            return
        st.success(
            f"Loaded `{name}`: {lineage} lineage edges, snapshot `{report.snapshot}`. "
            "Switch the sidebar **View** to *Ask the lineage* or *Lineage graph*."
        )


# --- the views ---------------------------------------------------------------------------


def graph_page() -> None:
    st.title("Lineage graph")
    st.caption(
        "A column's upstream lineage or downstream impact, one file's lineage, or the whole "
        "graph - read from the graph."
    )
    tools = _connected()
    if tools is None:
        return
    with st.sidebar:
        scope = st.radio(
            "Show",
            list(explorer.SCOPES),
            key=SCOPE_KEY,
            horizontal=True,
            help="One column: its lineage, hop by hop. A file: everything one SQL file "
            "produces. Entire graph: every lineage edge loaded.",
        )
    all_flows = ["FEEDS", "FILTERS", "INFLUENCES", "KEYS", "MENTIONS_COL", "OPAQUE"]
    flow_help = (
        "KEYS (teal, dash-dot): the join key that picked a lookup's row - e.g. a status code "
        "mapped through a reference table. Not a value, filter or influence (D-5, D-57). "
        "MENTIONS_COL / OPAQUE (grey dashed): a REFUSED statement names that column, or read "
        "that table and wrote this one - unverified, but never left out (D-60)."
    )
    node: str | None = None
    detail = "Columns"
    upward = False
    if scope == "One column":
        with st.sidebar:
            # Keyed, so the focus buttons and the jump picker below can set it (F2.3).
            st.session_state.setdefault(FOCUS_KEY, "gross_margin_usd")
            name = st.text_input("Column (partial names allowed)", key=FOCUS_KEY)
            direction = st.radio(
                "Direction",
                list(explorer.DIRECTIONS),
                horizontal=True,
                help="Upstream: where it comes from. Downstream: what it affects.",
            )
            depth = st.slider("Hops", 1, 8, 2)
            flows = st.multiselect("Flows", all_flows, default=all_flows, help=flow_help)
            follow_filters = st.checkbox(
                "Follow filters further upstream",
                value=False,
                help="Off: a filter is shown where it acts, but its own sources are not walked "
                "- they decide which rows exist, not this column's value.",
            )
            steps = st.checkbox(
                "Step through the query (CTEs)",
                value=False,
                help="Walk this column back through each CTE and subquery of the statement "
                "that produces it - how the value is computed, step by step.",
            )
        matches = tools.find(name, limit=50) if name.strip() else []
        if not matches:
            st.info(f"No column or table in the current graph matches '{name}'.")
            examples = tools._read(
                "MATCH (c:Column)<-[:FEEDS]-() RETURN DISTINCT c.id AS id ORDER BY id LIMIT 12"
            )
            if examples:
                st.caption("Columns with lineage in this graph, to try:")
                st.code("\n".join(row["id"] for row in examples), language="text")
            return
        node = matches[0]["id"]
        if len(matches) > 1:
            node = st.selectbox(
                f"'{name}' matches {len(matches)} nodes", [m["id"] for m in matches]
            )
        upward = direction != "downstream"
        if steps:
            _steps_view(tools, node)
            return
        edges = explorer.explore(tools, node, direction, depth, tuple(flows), follow_filters)
        limit = MAX_EDGES
    else:
        with st.sidebar:
            file = None
            if scope == "A file":
                file = st.selectbox("File", explorer.files(tools))
            flows = st.multiselect("Flows", all_flows, default=all_flows, help=flow_help)
            limit = st.slider(
                "Most edges to draw",
                100,
                2000,
                600,
                step=100,
                help="Large graphs take a few seconds to lay out; the table lists every edge.",
            )
        edges = explorer.all_edges(tools, file, tuple(flows))
        with st.sidebar:
            detail = st.radio(
                "Detail",
                ["Tables", "Columns"],
                index=0 if len(edges) > TABLE_VIEW_FROM else 1,
                horizontal=True,
                help="Tables: one card per table and one line per pair of tables, its badge "
                "the number of columns behind it. Columns: every column and every edge.",
            )
        direction = "whole graph" if scope == "Entire graph" else f"file {file}"
    candidates = explorer.hidable(edges, node or "")
    temps = [c for c in candidates if c.startswith("pg_temp.")]
    with st.sidebar:
        st.markdown("**Simplify**")
        hide_temps = st.checkbox(
            "Hide temp tables",
            value=True,
            help="Lines through a hidden table are joined into one, keeping the hardest "
            "transform; hover a line to see what it passed through.",
            disabled=not temps,
        )
        extra = st.multiselect(
            "Also hide",
            [c for c in candidates if c not in temps],
            help="Any other intermediate table, e.g. a staging table.",
        )
    hide = set(temps if hide_temps else []) | set(extra)

    top = st.columns(6)
    top[5].metric("Unverified", sum(e["type"] in explorer.FALLBACK for e in edges))
    top[0].metric("Edges", len(edges))
    top[1].metric("Value", sum(e["type"] == "FEEDS" for e in edges))
    top[2].metric("Filter", sum(e["type"] == "FILTERS" for e in edges))
    top[3].metric("Influence", sum(e["type"] == "INFLUENCES" for e in edges))
    top[4].metric("Join key", sum(e["type"] == "KEYS" for e in edges))
    if not edges:
        subject = f" for `{node}`" if node else ""
        st.info(f"No {direction} lineage{subject} with these flows.")
    else:
        shown = edges[:limit]
        if len(edges) > limit:
            hint = "lower the hops" if node else "raise the limit, or show one file"
            st.warning(f"Showing the first {limit} of {len(edges)} edges - {hint}.")
        drawn, hidden, notes = explorer.collapse(shown, hide)
        marks = explorer.gap_marks(tools, explorer.cards_of(drawn))
        if detail == "Tables":
            drawn = explorer.table_level(drawn)
        focus = {node} if node else set()
        page = explorer.html(explorer.model(drawn, focus, marks, notes=notes), height=760)
        st.iframe(page, height=760)
        if hidden:
            st.caption(
                f"Hidden: {', '.join(f'`{t}`' for t in hidden)} - lines through them are "
                "joined; hover a line to see what it passed through. The table below lists "
                "every edge, hidden ones included."
            )
        legend()
        _edge_panel(tools, edges, scope)

    gaps = tools.gaps(node=node) if node and upward else None
    if gaps and (gaps["boundaries"] or gaps["refusals"]):
        st.subheader("Where knowledge stops on this lineage")
        for b in gaps["boundaries"]:
            st.warning(f"**{b['kind']}** - `{b['subject']}`")
        for r in gaps["refusals"]:
            st.error(f"Refused statement **{r['file']}:{r['line']}** ({r['code']}): {r['reason']}")


def _focus_on(column: str) -> None:
    """Callback: show `column` in the One column scope."""
    st.session_state[FOCUS_KEY] = column
    st.session_state[SCOPE_KEY] = "One column"


def _edge_panel(tools: Any, edges: list[dict[str, Any]], scope: str) -> None:
    """F2.3: the edge table, selectable - and for the selected edge, why it exists.

    The panel shows the fact in words and only the lines of its statement that name the
    source (yellow) or the target (pink), narrowed to the CTEs on the target's own path.
    Buttons re-focus the view on either end; a picker jumps to any column on the diagram.
    """
    columns = sorted(
        {
            end
            for e in edges
            for end in (e["source"], e["target"])
            if "." in end.split(">")[-1] or (end.startswith("<result:") and ">." in end)
        }
    )
    st.selectbox(
        "Jump to a column on this diagram",
        ["", *columns],
        key="lg_jump",
        on_change=lambda: (
            _focus_on(st.session_state["lg_jump"]) if st.session_state.get("lg_jump") else None
        ),
        help="Re-focus the One column view on it.",
    )
    st.caption("Select a row to see why that edge exists - the SQL behind it.")
    rows = edge_rows(edges)
    event = st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"lg_edges_{scope}",
    )
    picked = event.selection.rows if event and event.selection else []
    if not picked:
        return
    row = rows[picked[0]]
    context = explorer.edge_context(tools, row["id"])
    if context is None:
        st.warning("That edge is no longer in the graph - it was rebuilt; select it again.")
        return
    edge = context["edge"]
    verb = {
        "FEEDS": "feeds the value of",
        "INFLUENCES": "influences",
        "FILTERS": "filters",
        "KEYS": "is the join key that picks the lookup row of",
        "MENTIONS_COL": "is named, in refused SQL, by",
        "OPAQUE": "is read, by refused SQL, into",
    }
    st.subheader("Why this edge exists")
    st.markdown(
        f"`{edge['source']}` **{verb.get(edge['type'], edge['type'])}** `{edge['target']}`"
        + (f" - transform **{edge['transform']}**" if edge["type"] == "FEEDS" else "")
        + (f", phase {edge['phase']}" if edge.get("phase") else "")
        + f" - statement at `{edge['file']}`:{edge['line']}."
    )
    left, right = st.columns(2)
    left.button(
        "Focus on the source",
        on_click=_focus_on,
        args=(edge["source"],),
        disabled=edge["source"].startswith("<") and ">." not in edge["source"],
    )
    right.button(
        "Focus on the target",
        on_click=_focus_on,
        args=(edge["target"],),
        disabled=edge["type"] == "FILTERS",
    )
    if context["lines"]:
        where = "the CTEs on this column's own path" if context.get("narrowed") else "the statement"
        st.caption(
            f"Lines of {where} that name the **source** (yellow) or the **target** (pink); "
            f"green names both. {context['statement_lines']} lines in the statement; the rest "
            "are folded."
        )
        st.html(explorer.context_html(context["lines"]))
    else:
        st.caption("The statement's SQL is not stored in the graph for this file.")


def _steps_view(tools: Any, node: str) -> None:
    """End-to-end gap 8: one column, walked back through the CTEs of its statement."""
    inputs = explorer.step_inputs(tools, node)
    if inputs is None:
        st.info(f"Nothing in the graph writes `{node}`, so it has no steps to show.")
        return
    statement, schema = inputs
    edges = explorer.cte_steps(statement, node.rsplit(".", 1)[-1], node, "public", schema)
    if not edges:
        st.info(
            f"`{node}` is computed in one step - or its statement could not be walked. "
            "Untick the option to see its lineage."
        )
        return
    ctes = sorted({e["source"].split(".")[1] for e in edges if e["source"].startswith("cte.")})
    top = st.columns(2)
    top[0].metric("Steps (CTEs and subqueries)", len(ctes))
    top[1].metric("Lines", len(edges))
    st.iframe(explorer.html(explorer.model(edges, {node}), height=660), height=660)
    st.caption(
        "Purple cards are steps inside the query - its CTEs and subqueries - in the order the "
        "value passes through them. **A reading aid:** it follows how the VALUE is computed "
        "(SQLGlot's column lineage); filters and grouping are in the normal view, and the "
        "lineage facts, Ask and the impact report are always the parser's."
    )


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
    # In the review bundle there is no Claude Code login to fall back on: the agent needs a key.
    if os.environ.get("LINEAGE_REQUIRE_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        st.info(
            "**Asking needs a Claude API key.** Put `ANTHROPIC_API_KEY=...` in the `.env` file "
            "next to `docker-compose.review.yml`, then restart: "
            "`docker compose -f docker-compose.review.yml up -d`. "
            "The Parse SQL and Lineage graph views work without one."
        )
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
        evidence = cited[:MAX_EDGES]
        marks = explorer.gap_marks(tools, explorer.cards_of(evidence))
        st.iframe(explorer.html(explorer.model(evidence, warn=marks)), height=560)
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


def not_analysed_page() -> None:
    """Plan step 7: every statement that is not plain lineage, with why and its SQL."""
    st.title("Not analysed")
    st.caption(
        "Every statement the lineage does not fully cover: refused, partly refused, kept only "
        "as names or tables (unverified), an effect (TRUNCATE, DROP, ALTER), or not valid "
        "PostgreSQL - each with its reason, the tables it touches, and its SQL. Nothing the "
        "parser could not read is left out of the graph."
    )
    tools = _connected()
    if tools is None:
        return
    rows = explorer.not_analysed(tools)
    statuses = sorted({row["status"] for row in rows})
    with st.sidebar:
        wanted = st.multiselect("Status", statuses, default=statuses)
    shown = [row for row in rows if row["status"] in wanted]
    st.metric("Statements", len(shown))
    st.dataframe(
        [{k: row[k] for k in ("file", "line", "kind", "status", "reason")} for row in shown],
        use_container_width=True,
        hide_index=True,
    )
    for row in shown:
        title = f"{row['file']}:{row['line']} - {row['status']} - {row['kind']}"
        with st.expander(title):
            st.markdown(row["reason"])
            if row["tables"]:
                st.markdown("Tables: " + ", ".join(f"`{t}`" for t in row["tables"]))
            st.code(row["sql"], language="sql")


def impact_page() -> None:
    """MVP plan F3: what breaks if a column or table changes - deterministic, no LLM."""
    from lineage.graph.impact import CHANGES, to_csv, to_markdown

    st.title("Impact report")
    st.caption(
        "What breaks if a column or table is dropped, renamed or retyped: the downstream "
        "columns it reaches, the statements and lines to edit, and what cannot be known. "
        "Read from the graph - no model call."
    )
    tools = _connected()
    if tools is None:
        return
    with st.sidebar:
        name = st.text_input("Column or table (partial names allowed)", value="amount_cancelled")
        change = st.radio("Change", list(CHANGES), horizontal=True)
        depth = st.slider("Hops downstream", 1, 15, 15)
    matches = tools.find(name, limit=50) if name.strip() else []
    node = name.strip()
    if len(matches) > 1:
        node = st.selectbox(f"'{name}' matches {len(matches)} nodes", [m["id"] for m in matches])
    elif matches:
        node = matches[0]["id"]
    if not node:
        return
    report = tools.impact(node, change, depth)
    if not report["found"]:
        st.info(f"`{name}` is not in the lineage graph. Give `schema.table.column`.")
        return

    s = report["summary"]
    st.markdown(f"**{report['change'].title()} `{report['subject']}`** - {report['consequence']}")
    top = st.columns(4)
    top[0].metric("Affected", s["affected"], help="downstream columns or tables")
    top[1].metric("Statements to edit", s["statements_to_edit"])
    top[2].metric("Files to edit", s["files_to_edit"])
    top[3].metric("Unknowns", s["unknowns"])

    st.subheader("Statements to edit")
    if report["edit"]:
        st.dataframe(
            [
                {
                    "file": e["file"],
                    "statement line": e["line"],
                    "lines naming it": ", ".join(str(n) for n in e["lines"]),
                    "why": e["reason"],
                }
                for e in report["edit"]
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Nothing in the analysed SQL names it.")

    st.subheader("Affected downstream")
    if report["affected"]:
        st.dataframe(
            [
                {
                    "hops": a["hops"],
                    "column or table": a["node"],
                    "how": a["flow"],
                    "transform": a["transform"],
                    "from": a["from"],
                    "where": f"{a['file']}:{a['line']}",
                }
                for a in report["affected"]
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Nothing downstream.")

    st.subheader("Unknowns")
    unknowns = report["unknowns"]
    for b in unknowns["boundaries"]:
        st.warning(f"Boundary `{b['kind']}` on `{b['subject']}` in `{b['statement']}`")
    for x in unknowns["refusals"]:
        st.warning(
            f"Refused statement `{x['file']}`:{x['line']} ({x['code']}) names "
            f"{', '.join(x['tables'])}: its effect is unknown."
        )
    if not s["unknowns"]:
        st.caption("None: the analysed SQL is fully covered on this path.")
    for note in report["notes"]:
        st.caption(f"- {note}")

    stem = report["subject"].replace("<", "").replace(">", "").replace(":", "_").replace("/", "_")
    left, right = st.columns(2)
    left.download_button(
        "Download Markdown", to_markdown(report), f"impact_{stem}.md", mime="text/markdown"
    )
    right.download_button("Download CSV", to_csv(report), f"impact_{stem}.csv", mime="text/csv")
