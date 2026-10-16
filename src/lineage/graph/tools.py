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

A lookup-join key (`KEYS`, D-57) is walked only when asked for (`join_keys=True`): it is
not one of the parser's scored edges, and the default walk is the one the keys check.

Plan step 7 - unknowns are part of the answer, not a side list:

* the fallback hops of refused statements (D-60) - `MENTIONS_COL`, a column a refused
  statement's expression names, and `OPAQUE`, a table it read to a table it wrote - are
  walked by default and come back flagged `unverified: true` (`fallback=False` leaves them
  out), so a refused statement in the middle of a lineage cannot make it look complete;
* `gaps` and `coverage` also name the effects on the tables involved (D-62: `RESETS`,
  `DROPS`, `ALTERS` - "emptied by TRUNCATE at load.sql:88") and every statement whose
  ledger status is not plain lineage (plan step 3: refused, partial, effect ...).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lineage.graph.neo4j_conn import Neo4jSettings, read_session

LINEAGE = ("FEEDS", "FILTERS", "INFLUENCES", "KEYS")
VALUE_LIKE = ("FEEDS", "INFLUENCES", "KEYS")
FALLBACK = ("MENTIONS_COL", "OPAQUE")
"""What a refused statement still says (D-60): walked by default, always flagged."""
EFFECTS = ("RESETS", "DROPS", "ALTERS")
"""What a statement does to a whole table (D-62)."""
_CITABLE = (*LINEAGE, *FALLBACK)
_QUIET_STATUSES = ("column_level", "no_lineage")
_ENDPOINT_LABELS = ("Column", "Table", "Variable", "Boundary", "Procedure", "Literal")
_SNIPPET_LINES = 200

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

    def upstream(
        self, node: str, depth: int = 1, join_keys: bool = False, fallback: bool = True
    ) -> list[dict[str, Any]]:
        """Where a column comes from: every lineage edge into it, `depth` hops up.

        Each edge is returned once, with the hop (`depth`) at which it was first reached.
        `join_keys` also walks the `KEYS` edges: the columns that chose a lookup's row.
        `fallback` (on by default) walks a refused statement's `MENTIONS_COL` / `OPAQUE` hops,
        each flagged `unverified` - the lineage passes through SQL that was not analysed.
        """
        return self._walk(node, depth, lambda ids: self._incoming(ids, join_keys, fallback))

    def downstream(
        self, node: str, depth: int = 1, join_keys: bool = False, fallback: bool = True
    ) -> list[dict[str, Any]]:
        """What a column affects: every lineage edge out of it, `depth` hops down.

        A `FILTERS` edge ends at a table - every column of it is affected - and the walk
        stops there rather than fanning out to the whole table. `fallback` as in `upstream`:
        a table read by a refused statement reaches what it wrote (`OPAQUE`, unverified).
        """
        return self._walk(
            node, depth, lambda ids: self._outgoing(ids, join_keys, fallback), upward=False
        )

    def effects(self, tables: list[str]) -> list[dict[str, Any]]:
        """What statements do to these whole tables (D-62): emptied, dropped, altered."""
        return self._read(
            """
            MATCH (s:Statement)-[r:RESETS|DROPS|ALTERS]->(t:Table) WHERE t.id IN $tables
            RETURN r.id AS id, type(r) AS type, t.id AS table, r.detail AS detail,
                   r.file AS file, r.line AS line, s.id AS statement
            ORDER BY file, line, table, type
            """,
            tables=sorted(set(tables)),
        )

    def statements(self, files: list[str], tables: list[str] | None = None) -> list[dict[str, Any]]:
        """Statements in `files` whose ledger status is not plain lineage (plan step 3).

        Refused, partial, unclassified, table-level, effect, declared, not analysed, not
        valid: each with its status and reason. With `tables`, only those that write or
        read one of them, or name none (kept, never dropped in silence).
        """
        return self._read(
            """
            MATCH (s:Statement) WHERE s.file IN $files AND s.status IS NOT NULL
              AND NOT s.status IN $quiet
              AND ($tables IS NULL
                   OR EXISTS { (s)-[:WRITES|READS|RESETS|DROPS|ALTERS]->(t:Table)
                               WHERE t.id IN $tables }
                   OR EXISTS { (s)-[:REFUSED]->(:Refusal)-[:MENTIONS]->(t:Table)
                               WHERE t.id IN $tables }
                   OR NOT EXISTS { (s)-[:WRITES|READS|RESETS|DROPS|ALTERS]->(:Table) })
            RETURN s.id AS id, s.file AS file, s.line AS line, s.end_line AS end_line,
                   s.kind AS kind, s.status AS status, s.reason AS reason
            ORDER BY file, line
            """,
            files=sorted(set(files)),
            tables=tables,
            quiet=list(_QUIET_STATUSES),
        )

    def impact(self, node: str, change: str = "drop", depth: int = 15) -> dict[str, Any]:
        """What breaks if a column or table is dropped, renamed or retyped (MVP F3).

        Affected downstream nodes (as `downstream`), the statements and lines to edit -
        including SQL that names the column with no lineage edge, like a join key - and the
        unknowns. See `lineage.graph.impact`.
        """
        from lineage.graph.impact import impact

        return impact(self, node, change, depth)

    def path(self, source: str, target: str, max_hops: int = 8) -> list[list[dict[str, Any]]]:
        """Every shortest lineage path from `source` to `target`, as lists of edges."""
        hops = max(1, min(int(max_hops), 15))
        rows = self._read(
            f"""
            MATCH (s {{id: $source}}), (t {{id: $target}})
            MATCH p = allShortestPaths((s)-[:{"|".join(_CITABLE)}*..{hops}]->(t))
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
            types=list(_CITABLE),
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
                "statements": self.statements([file]),
                "effects": self._read(
                    """
                    MATCH (s:Statement)-[r:RESETS|DROPS|ALTERS]->(t:Table) WHERE r.file = $file
                    RETURN r.id AS id, type(r) AS type, t.id AS table, r.detail AS detail,
                           r.file AS file, r.line AS line, s.id AS statement
                    ORDER BY line, table, type
                    """,
                    file=file,
                ),
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
            "unverified": [e for e in edges if e.get("unverified")],
            "effects": self.effects(tables),
            "statements": self.statements(files, tables),
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
        found["lineage_note"] = (
            "with/without lineage counts what FEEDS or INFLUENCES each column (incoming). "
            "A source table's columns are usually all 'without lineage' and still feed other "
            "tables: use `downstream` for what a column feeds"
        )
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
            "statements": self.statements(files, [table]),
        }
        found["effects"] = self.effects([table])
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

    def describe_edges(self, ids: list[str]) -> list[dict[str, Any]]:
        """Full records of the given lineage edges (unknown ids are skipped)."""
        rows = self._read(
            f"""
            MATCH (a)-[r]->(b) WHERE r.id IN $ids AND type(r) IN $types
            RETURN {{{_EDGE_FIELDS}}} AS edge ORDER BY r.id
            """,
            ids=list(ids),
            types=list(_CITABLE),
        )
        return [row["edge"] for row in rows]

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
            types=list(_CITABLE),
        )
        return {row["id"]: (row["type"], row["source"], row["target"]) for row in rows}

    # --- internals ---------------------------------------------------------------------

    def _walk(
        self,
        node: str,
        depth: int,
        step: Callable[[list[str]], list[dict[str, Any]]],
        upward: bool = True,
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
                nxt = edge["source"] if upward else edge["target"]
                # Downstream, a FILTERS edge ends at a whole table: reported, not walked.
                # An OPAQUE hop's table IS walked on: table-level is all a refusal leaves.
                walk_on = upward or edge["type"] in (*VALUE_LIKE, "MENTIONS_COL", "OPAQUE")
                if walk_on and nxt not in visited:
                    visited.add(nxt)
                    frontier.append(nxt)
            if not frontier:
                break
        return sorted(
            seen.values(),
            key=lambda e: (e["depth"], e["type"], e["source"], e["target"], e["id"]),
        )

    def _incoming(
        self, frontier: list[str], join_keys: bool = False, fallback: bool = False
    ) -> list[dict[str, Any]]:
        into = "FEEDS|INFLUENCES|KEYS" if join_keys else "FEEDS|INFLUENCES"
        into_table = "FILTERS|MENTIONS_COL|OPAQUE" if fallback else "FILTERS"
        into_column = f"{into}|MENTIONS_COL" if fallback else into
        rows = self._read(
            f"""
            UNWIND $ids AS nid
            MATCH (b {{id: nid}}) WHERE b:Column OR b:Variable OR b:Table
            CALL (b) {{
                MATCH (a)-[r:{into_column}]->(b) RETURN a, r
                UNION
                MATCH (b)<-[:HAS_COLUMN]-(t:Table)<-[r:{into_table}]-(a) RETURN a, r
                UNION
                MATCH (b:Table)<-[r:{"OPAQUE" if fallback else "NONE"}]-(a) RETURN a, r
            }}
            WITH a, r, b
            MATCH (a)-[r]->(target)
            RETURN DISTINCT {{{_EDGE_FIELDS.replace("b.id", "target.id")}}} AS edge
            """,
            ids=frontier,
        )
        return [_flagged(row["edge"]) for row in rows]

    def _outgoing(
        self, frontier: list[str], join_keys: bool = False, fallback: bool = False
    ) -> list[dict[str, Any]]:
        out = "FEEDS|INFLUENCES|FILTERS|KEYS" if join_keys else "FEEDS|INFLUENCES|FILTERS"
        if fallback:
            out += "|MENTIONS_COL"
        rows = self._read(
            f"""
            UNWIND $ids AS nid
            MATCH (a {{id: nid}})-[r:{out}]->(b) WHERE a:Column OR a:Variable
            RETURN DISTINCT {{{_EDGE_FIELDS}}} AS edge
            """,
            ids=frontier,
        )
        if fallback:
            # A table a refused statement read reaches what it wrote (OPAQUE) - from the
            # table itself, or from any of its columns, even one no analysed statement read
            # (then it has no node of its own: its table is all the graph knows of it).
            tables = sorted({nid if nid.count(".") < 2 else _table_id(nid) for nid in frontier})
            rows += self._read(
                f"""
                MATCH (a:Table)-[r:OPAQUE]->(b) WHERE a.id IN $tables
                RETURN DISTINCT {{{_EDGE_FIELDS}}} AS edge
                """,
                tables=tables,
            )
        return [_flagged(row["edge"]) for row in rows]

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


def _flagged(edge: dict[str, Any]) -> dict[str, Any]:
    """A fallback hop carries `unverified: true` - it passes through SQL not analysed."""
    if edge["type"] in FALLBACK:
        return {**edge, "unverified": True}
    return edge


def _table_id(node: str) -> str:
    """The table a node id belongs to: a column's table, or the id itself."""
    if node.startswith("<result:"):
        return node.split(">", 1)[0] + ">"
    head, _, _ = node.rpartition(".")
    return head if head.count(".") >= 1 else node


def _snippet(text: str | None, line: int | None) -> str | None:
    """The statement starting at `line`: up to its terminating `;`, at most 200 lines.

    The whole statement, so a CTE body or join far below its first line stays visible; a
    longer one is cut and says how many lines were left out.
    """
    if not text or not line:
        return None
    taken = []
    for current in text.splitlines()[line - 1 :]:
        taken.append(current)
        if current.rstrip().endswith(";"):
            break
    if len(taken) > _SNIPPET_LINES:
        cut = len(taken) - _SNIPPET_LINES
        taken = [*taken[:_SNIPPET_LINES], f"-- ... {cut} more lines of this statement"]
    return "\n".join(taken)
