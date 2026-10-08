"""The lineage graph over MCP: the C3 typed tools, plus the semantics document (plan C4).

Every tool is a thin wrapper over `GraphTools`, so a result over MCP is byte-for-byte the
CLI's result - the C4 gate. All tools are read-only (and hinted so): they run fixed Cypher
in a READ-mode session. The semantics document is served as the resource
`lineage://semantics` and the prompt `lineage_semantics`, so any client reads lineage with
the same vocabulary the agent does.

Run over stdio (what Claude Code launches):  `python -m lineage.mcp.server`
"""

from __future__ import annotations

from functools import cache
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from lineage.graph.neo4j_conn import Neo4jSettings, connect
from lineage.graph.tools import GraphTools
from lineage.semantics import load as load_semantics

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)

server = MCPServer(
    name="lineage-graph",
    instructions=(
        "Column-level SQL lineage from a deterministic parser, in a Neo4j graph. Read the "
        "resource lineage://semantics before answering. Resolve names with `find` first; "
        "cite edge ids; report every boundary and refusal as an unknown."
    ),
)


@cache
def _tools() -> GraphTools:
    settings = Neo4jSettings.from_env()
    return GraphTools(connect(settings), settings)


@server.tool(annotations=READ_ONLY)
def find(name: str, limit: int = 10) -> list[dict[str, Any]]:
    """Resolve a (partial) table or column name to graph node ids. Several results = ambiguous."""
    return _tools().find(name, limit)


@server.tool(annotations=READ_ONLY)
def upstream(node: str, depth: int = 1) -> list[dict[str, Any]]:
    """Lineage edges into a column (value, influence, and filters on its table), `depth` hops up."""
    return _tools().upstream(node, depth)


@server.tool(annotations=READ_ONLY)
def downstream(node: str, depth: int = 1) -> list[dict[str, Any]]:
    """Impact: lineage edges out of a column, `depth` hops down."""
    return _tools().downstream(node, depth)


@server.tool(annotations=READ_ONLY)
def path(source: str, target: str, max_hops: int = 8) -> list[list[dict[str, Any]]]:
    """Every shortest lineage path from `source` to `target`, each a list of edges."""
    return _tools().path(source, target, max_hops)


@server.tool(annotations=READ_ONLY)
def explain_edge(edge: str) -> dict[str, Any] | None:
    """One lineage edge by id, with the SQL of the statement that produced it."""
    return _tools().explain_edge(edge)


@server.tool(annotations=READ_ONLY)
def gaps(node: str | None = None, file: str | None = None) -> dict[str, Any]:
    """Boundaries and refusals: on a node's lineage, or declared by a file."""
    return _tools().gaps(node=node, file=file)


@server.tool(annotations=READ_ONLY)
def coverage(table: str) -> dict[str, Any]:
    """How much of a table's lineage is proven: columns, writers, filters, gaps."""
    return _tools().coverage(table)


@server.resource("lineage://semantics", name="lineage-semantics", mime_type="text/markdown")
def semantics() -> str:
    """How to read the parser output and the lineage graph."""
    return load_semantics()


@server.prompt(name="lineage_semantics")
def semantics_prompt() -> str:
    """The lineage semantics document, as a prompt to prepend."""
    return load_semantics()


if __name__ == "__main__":
    server.run("stdio")
