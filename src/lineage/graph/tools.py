"""Typed, read-only tools over the lineage knowledge graph (plan C3).

These are the only way the agent (C6, C7) and the MCP server (C4) read lineage. Each tool:

* runs **fixed Cypher written here** - nothing generated - in a READ-mode session, which
  Neo4j itself refuses to write through (`neo4j_conn.read_session`);
* takes typed arguments and returns plain dicts that always carry **node and edge ids**,
  so every claim an answer makes can be checked against the graph it came from;
* is deterministic: results are sorted, so the same graph gives the same output.

Lineage is read hop by hop, exactly as the parser stated it (edges are single hops, and a
path is composed at query time - `docs/ir-v0.md`):

* a column's direct sources are the `FEEDS` and `INFLUENCES` into it, and the `FILTERS`
  into its table (a filter decides which rows land, so it bounds every column of them);
* walking further up repeats that from each source column or variable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lineage.graph.neo4j_conn import Neo4jSettings, read_session

LINEAGE = ("FEEDS", "FILTERS", "INFLUENCES")
VALUE_LIKE = ("FEEDS", "INFLUENCES")
_ENDPOINT_LABELS = ("Column", "Table", "Variable", "Boundary", "Procedure", "Literal")
_SNIPPET_LINES = 25

_EDGE_FIELDS = """
    id: r.id, type: type(r), source: a.id, target: b.id,
    transform: r.transform, phase: r.phase, band: r.band, tier: r.tier,
    guard: r.guard, file: r.file, line: r.line, statement: r.statement
"""


class GraphTools:
    """The typed tools, bound to one Neo4j driver."""

    def __init__(self, driver: Any, settings: Neo4jSettings | None = None) -> None:
        self._driver = driver
        self._settings = settings or Neo4jSettings.from_env()

    # --- the tools -------------------------------------------------------------------

    def find(self, name: str, limit: int = 10) -> list[dict[str, Any]]:
        """Graph nodes matching a name: an exact id first, then ids ending or containing it.

        Matching is case-insensitive. Used to turn what a user typed into node ids before
        any other tool runs; more than one result means the name is ambiguous.
        """
        wanted = name.strip().lower()
        rows = self._read(
            """
            MATCH (n) WHERE (n:Column OR n:Table OR n:Variable) AND toLower(n.id) CONTAINS $q
            WITH n, toLower(n.id) AS lid,
                 CASE WHEN toLower(n.id) = $q THEN 0
                      WHEN toLower(n.id) ENDS WITH '.' + $q THEN 1 ELSE 2 END AS rank
            RETURN n.id AS id, labels(n)[0] AS label, rank
            ORDER BY rank, size(lid), id LIMIT $limit
            """,
            q=wanted,
            limit=limit,
        )
        best = [row for row in rows if row["rank"] == rows[0]["rank"]] if rows else []
        return [{"id": row["id"], "label": row["label"]} for row in best]

    def upstream(self, node: str, depth: int = 1) -> list[dict[str, Any]]:
        """Where a column comes from: every lineage edge into it, `depth` hops up.

        Each edge is returned once, with the hop (`depth`) at which it was first reached.
        """
        return self._walk(node, depth, self._incoming)

    def downstream(self, node: str, depth: int = 1) -> list[dict[str, Any]]:
        """What a column affects: every lineage edge out of it, `depth` hops down.

        A `FILTERS` edge ends at a table - every column of it is affected - and the walk
        stops there rather than fanning out to the whole table.
        """
        return self._walk(node, depth, self._outgoing)

    def path(self, source: str, target: str, max_hops: int = 8) -> list[list[dict[str, Any]]]:
        """Every shortest lineage path from `source` to `target`, as lists of edges."""
        hops = max(1, min(int(max_hops), 15))
        rows = self._read(
            f"""
            MATCH (s {{id: $source}}), (t {{id: $target}})
            MATCH p = allShortestPaths((s)-[:{"|".join(LINEAGE)}*..{hops}]->(t))
            RETURN [r IN relationships(p) |
                    {{id: r.id, type: type(r), source: startNode(r).id, target: endNode(r).id,
                      transform: r.transform, phase: r.phase, band: r.band, tier: r.tier,
                      guard: r.guard, file: r.file, line: r.line,
                      statement: r.statement}}] AS edges
            """,
            source=source,
            target=target,
        )
        paths = [row["edges"] for row in rows]
        return sorted(paths, key=lambda edges: [edge["id"] for edge in edges])

    def explain_edge(self, edge: str) -> dict[str, Any] | None:
        """One lineage edge with the SQL of the statement that produced it."""
        rows = self._read(
            f"""
            MATCH (a)-[r]->(b) WHERE r.id = $edge AND type(r) IN $types
            OPTIONAL MATCH (f:File {{id: r.file}})
            RETURN {{{_EDGE_FIELDS}}} AS edge, f.text AS text
            """,
            edge=edge,
            types=list(LINEAGE),
        )
        if not rows:
            return None
        found = dict(rows[0]["edge"])
        found["sql"] = _snippet(rows[0]["text"], found["line"])
        return found

    def gaps(self, node: str | None = None, file: str | None = None) -> dict[str, Any]:
        """Where knowledge stops: boundaries and refusals.

        * `file`: everything that file declared - each boundary and each refused statement.
        * `node`: the boundaries on the statements that produce the node's lineage (all hops
          up), the boundaries attached to it, and the refusals in the files it comes from.
          A refused statement has no edges, so it cannot be tied to one column: it is
          reported per file, and said to be so.
        """
        if file is not None:
            return {
                "scope": f"file {file}",
                "boundaries": self._read(
                    """
                    MATCH (:File {id: $file})-[d:DECLARES]->(b:Boundary)
                    RETURN DISTINCT b.id AS id, b.kind AS kind, b.subject AS subject,
                           d.detail AS detail, d.line AS line ORDER BY id, line
                    """,
                    file=file,
                ),
                "refusals": self._refusals([file]),
            }
        if node is None:
            raise ValueError("gaps needs a node or a file")
        edges = self.upstream(node, depth=15)
        statements = sorted({edge["statement"] for edge in edges})
        files = sorted({edge["file"] for edge in edges})
        nodes = sorted({node} | {edge["source"] for edge in edges})
        tables = sorted(
            {
                _table_id(n)
                for n in {node, *(e["source"] for e in edges), *(e["target"] for e in edges)}
            }
        )
        boundaries = self._read(
            """
            MATCH (s:Statement)-[:BLOCKED_BY]->(b:Boundary) WHERE s.id IN $statements
            RETURN DISTINCT b.id AS id, b.kind AS kind, b.subject AS subject,
                   s.id AS statement
            UNION
            MATCH (b:Boundary)-[:BOUNDS]->(n) WHERE n.id IN $nodes
            RETURN DISTINCT b.id AS id, b.kind AS kind, b.subject AS subject,
                   null AS statement
            """,
            statements=statements,
            nodes=nodes,
        )
        boundaries.sort(key=lambda row: (row["id"], row["statement"] or ""))
        return {
            "scope": f"lineage of {node}",
            "boundaries": boundaries,
            "refusals": self._refusals(files, tables),
            "refusals_note": "a refused statement has no edges: it is listed when its SQL names "
            "a table on this lineage, or when it names no table at all (then per file)",
        }

    def coverage(self, table: str) -> dict[str, Any]:
        """How much of a table's lineage is proven, and what is in the way."""
        rows = self._read(
            """
            MATCH (t:Table {id: $table})
            OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:Column)
            WITH t, collect(c) AS columns
            WITH t, columns,
                 [c IN columns WHERE EXISTS { (c)<-[:FEEDS|INFLUENCES]-() }] AS fed
            OPTIONAL MATCH (s:Statement)-[:WRITES]->(t)
            WITH t, columns, fed, collect(DISTINCT s) AS writers
            RETURN t.id AS id,
                   size(columns) AS columns,
                   size(fed) AS columns_with_lineage,
                   [c IN columns WHERE NOT c IN fed | c.id] AS columns_without_lineage,
                   [s IN writers | s.id] AS written_by,
                   [s IN writers | s.file] AS files
            """,
            table=table,
        )
        if not rows:
            return {"id": table, "found": False}
        found = dict(rows[0])
        found["found"] = True
        files = sorted(set(found.pop("files")))
        found["written_by"] = sorted(found["written_by"])
        found["columns_without_lineage"] = sorted(found["columns_without_lineage"])
        found["filters"] = self._read(
            f"""
            MATCH (a)-[r:FILTERS]->(b:Table {{id: $table}})
            RETURN {{{_EDGE_FIELDS}}} AS edge ORDER BY r.id
            """,
            table=table,
        )
        found["filters"] = [row["edge"] for row in found["filters"]]
        found["gaps"] = {
            "boundaries": self._read(
                """
                MATCH (s:Statement)-[:BLOCKED_BY]->(b:Boundary) WHERE s.id IN $statements
                RETURN DISTINCT b.id AS id, b.kind AS kind, b.subject AS subject
                ORDER BY id
                """,
                statements=found["written_by"],
            ),
            "refusals": self._refusals(files, [table]),
        }
        return found

    def graph_query(self, cypher: str) -> dict[str, Any]:
        """The fallback for questions no typed tool fits: model-written, validated Cypher.

        Rejected unless it passes every check in `agent.cypher_guard`, then Neo4j's
        `EXPLAIN`; it runs read-only with a row limit. Returns rows, or why it was refused.
        """
        from lineage.agent.cypher_guard import check

        checked = check(cypher)
        if not checked.ok:
            return {"rejected": True, "reason": checked.reason}
        try:
            self._read(f"EXPLAIN {checked.query}")
            rows = self._read(checked.query)
        except Exception as error:  # the database's own refusal, reported to the model
            return {"rejected": True, "reason": f"Neo4j: {str(error)[:300]}"}
        return {"rejected": False, "query": checked.query, "rows": rows}

    def snapshot(self) -> str:
        """The id of the build the graph holds ('' when it holds none)."""
        rows = self._read("MATCH (s:Snapshot) RETURN s.id AS id ORDER BY id")
        return ",".join(row["id"] for row in rows)

    def resolve_edges(self, ids: list[str]) -> dict[str, tuple[str, str, str]]:
        """Lineage edge id -> (type, source, target). Ids not in the graph are absent."""
        rows = self._read(
            """
            MATCH (a)-[r]->(b) WHERE r.id IN $ids AND type(r) IN $types
            RETURN r.id AS id, type(r) AS type, a.id AS source, b.id AS target
            """,
            ids=list(ids),
            types=list(LINEAGE),
        )
        return {row["id"]: (row["type"], row["source"], row["target"]) for row in rows}

    # --- internals ---------------------------------------------------------------------

    def _walk(
        self,
        node: str,
        depth: int,
        step: Callable[[list[str]], list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        frontier = [node]
        visited = {node}
        for hop in range(1, max(1, min(int(depth), 15)) + 1):
            edges = step(frontier)
            frontier = []
            for edge in edges:
                if edge["id"] in seen:
                    continue
                seen[edge["id"]] = {**edge, "depth": hop}
                upward = step == self._incoming
                nxt = edge["source"] if upward else edge["target"]
                # Downstream, a FILTERS edge ends at a whole table: reported, not walked.
                if (upward or edge["type"] in VALUE_LIKE) and nxt not in visited:
                    visited.add(nxt)
                    frontier.append(nxt)
            if not frontier:
                break
        return sorted(
            seen.values(),
            key=lambda e: (e["depth"], e["type"], e["source"], e["target"], e["id"]),
        )

    def _incoming(self, frontier: list[str]) -> list[dict[str, Any]]:
        rows = self._read(
            f"""
            UNWIND $ids AS nid
            MATCH (b {{id: nid}}) WHERE b:Column OR b:Variable OR b:Table
            CALL (b) {{
                MATCH (a)-[r:FEEDS|INFLUENCES]->(b) RETURN a, r
                UNION
                MATCH (b)<-[:HAS_COLUMN]-(t:Table)<-[r:FILTERS]-(a) RETURN a, r
            }}
            WITH a, r, b
            MATCH (a)-[r]->(target)
            RETURN DISTINCT {{{_EDGE_FIELDS.replace("b.id", "target.id")}}} AS edge
            """,
            ids=frontier,
        )
        return [row["edge"] for row in rows]

    def _outgoing(self, frontier: list[str]) -> list[dict[str, Any]]:
        rows = self._read(
            f"""
            UNWIND $ids AS nid
            MATCH (a {{id: nid}})-[r:FEEDS|INFLUENCES|FILTERS]->(b)
            WHERE a:Column OR a:Variable
            RETURN DISTINCT {{{_EDGE_FIELDS}}} AS edge
            """,
            ids=frontier,
        )
        return [row["edge"] for row in rows]

    def _refusals(self, files: list[str], tables: list[str] | None = None) -> list[dict[str, Any]]:
        """Refusals in `files`. With `tables`: only those whose SQL names one of them, or
        names no table at all (unlinkable, so kept - a refusal is never silently dropped)."""
        return self._read(
            """
            MATCH (x:Refusal) WHERE x.file IN $files
              AND ($tables IS NULL
                   OR EXISTS { (x)-[:MENTIONS]->(t:Table) WHERE t.id IN $tables }
                   OR NOT EXISTS { (x)-[:MENTIONS]->(:Table) })
            OPTIONAL MATCH (x)-[:MENTIONS]->(m:Table)
            WITH x, collect(m.id) AS mentions
            RETURN x.id AS id, x.code AS code, x.file AS file, x.line AS line,
                   x.statement_kind AS statement_kind, x.reason AS reason,
                   mentions AS tables
            ORDER BY file, line
            """,
            files=files,
            tables=tables,
        )

    def _read(self, query: str, **parameters: Any) -> list[dict[str, Any]]:
        with read_session(self._driver, self._settings) as session:
            rows: list[dict[str, Any]] = session.execute_read(
                lambda tx: [record.data() for record in tx.run(query, **parameters)]
            )
            return rows


def _table_id(node: str) -> str:
    """The table a node id belongs to: a column's table, or the id itself."""
    if node.startswith("<result:"):
        return node.split(">", 1)[0] + ">"
    head, _, _ = node.rpartition(".")
    return head if head.count(".") >= 1 else node


def _snippet(text: str | None, line: int | None) -> str | None:
    """The statement starting at `line`: up to its terminating `;`, at most 25 lines."""
    if not text or not line:
        return None
    lines = text.splitlines()[line - 1 : line - 1 + _SNIPPET_LINES]
    taken = []
    for current in lines:
        taken.append(current)
        if current.rstrip().endswith(";"):
            break
    return "\n".join(taken)
