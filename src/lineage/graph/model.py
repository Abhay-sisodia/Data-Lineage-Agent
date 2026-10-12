"""Parser output -> knowledge-graph rows (plan C2). Pure: no database, no AI.

One `ExportedResult` per SQL file goes in; nodes and relationships come out as plain rows
that `build.load` writes to Neo4j. Keeping the mapping pure is what lets the C2 gate
compare the graph with the parser exactly: every count below is derived here, and
`build` only writes what this produces.

**Nodes** (label: id)
- `Table` - a relation, `schema.table`; `Column` - `schema.table.column`;
  `Variable` - a procedural variable. Ids are the parser's own node names (D-25), so a table
  written by one file and read by another is ONE node, which is what links the files.
- `File` - relative path; `Statement` - `file#unit:line`;
  `Boundary` - `KIND:SUBJECT` (its identity, `Boundary.identity`); `Refusal` - `file#unit:line`.

**Relationships**
- `FEEDS` / `FILTERS` / `INFLUENCES` - one per parser edge (value / filter / influence),
  carrying transform, phase, band, tier, mechanism, guard and origin.
- `HAS_COLUMN` (Table -> Column); `CONTAINS` (File -> Statement);
  `READS` / `WRITES` (Statement -> Table), from the edges' endpoints;
  `DECLARES` (File -> Boundary), one per declared boundary; `BLOCKED_BY`
  (Statement -> Boundary) when the boundary has a line; `BOUNDS` (Boundary -> what it bounds);
  `REFUSED` (Statement -> Refusal).

**A bare SELECT's `<result>` is per file.** Every targetless query writes `<result>`; read
as one node, unrelated reports would share columns. It becomes `<result:FILE>`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from lineage.export import ExportedResult
from lineage.ir.model import Flow, IREdge, Node, NodeKind

SCHEMA_VERSION = "lineage-graph/1"

RESULT = "<result>"

_LABELS = {
    NodeKind.COLUMN: "Column",
    NodeKind.RELATION: "Table",
    NodeKind.VARIABLE: "Variable",
    NodeKind.BOUNDARY: "Boundary",
    NodeKind.STATEMENT: "Statement",
    NodeKind.PROCEDURE: "Procedure",
    NodeKind.LITERAL: "Literal",
}

FLOW_TYPES = {Flow.VALUE: "FEEDS", Flow.FILTER: "FILTERS", Flow.INFLUENCE: "INFLUENCES"}
"""Lineage relationship type per parser flow."""

NODE_LABELS = (*sorted(set(_LABELS.values())), "File", "Refusal")
"""Every node label the build writes (besides `Snapshot`)."""

STRUCTURAL_TYPES = (
    "HAS_COLUMN",
    "CONTAINS",
    "READS",
    "WRITES",
    "DECLARES",
    "BLOCKED_BY",
    "BOUNDS",
    "REFUSED",
    "MENTIONS",
)
"""Every non-lineage relationship type the build writes."""


@dataclass
class GraphBatch:
    """Everything one build writes: nodes by label, relationships by type."""

    nodes: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    relationships: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)

    def node(self, label: str, node_id: str, **properties: Any) -> None:
        bucket = self.nodes.setdefault(label, {})
        bucket.setdefault(node_id, {"id": node_id}).update(properties)

    def relationship(
        self,
        rel_type: str,
        rel_id: str,
        start: tuple[str, str],
        end: tuple[str, str],
        **properties: Any,
    ) -> None:
        self.relationships.setdefault(rel_type, {})[rel_id] = {
            "id": rel_id,
            "start_label": start[0],
            "start": start[1],
            "end_label": end[0],
            "end": end[1],
            "props": properties,
        }

    def counts(self) -> dict[str, int]:
        found = {label: len(rows) for label, rows in self.nodes.items()}
        found.update({rel: len(rows) for rel, rows in self.relationships.items()})
        return dict(sorted(found.items()))


def build_batch(results: list[ExportedResult]) -> GraphBatch:
    """All files' results -> one batch. Order-independent: ids decide identity."""
    batch = GraphBatch()
    for result in results:
        _add_file(batch, result)
    return batch


def add_refusal_mentions(
    batch: GraphBatch,
    results: list[ExportedResult],
    sources: dict[str, str],
    default_schema: str | None = None,
) -> None:
    """`MENTIONS` (Refusal -> Table) for each table a refused statement's own SQL names.

    A refused statement has no edges, so nothing ties it to the lineage it might affect.
    Its text still names its tables - `TRUNCATE mart.load_audit` - and that is what lets a
    question about `mart.shipment_fact` leave out a refusal that touches only
    `mart.load_audit`, instead of reporting every refusal in the same file. A refusal whose
    text names no table stays linked to its file only, and is still reported per file.
    `default_schema` qualifies the unqualified names, as the dictionary does.
    """
    for result in results:
        text = sources.get(result.source.path)
        if text is None:
            continue
        lines = text.splitlines()
        for refusal in result.refusals:
            span = lines[refusal.line - 1 : (refusal.end_line or refusal.line)]
            statement = "\n".join(span)
            refusal_id = f"{result.source.path}#{refusal.unit}:{refusal.line}:{refusal.scope}"
            for table in sorted(statement_tables(statement, default_schema)):
                _table(batch, table)
                batch.relationship(
                    "MENTIONS",
                    _hash("MENTIONS", refusal_id, table),
                    ("Refusal", refusal_id),
                    ("Table", table),
                )


def statement_tables(statement: str, default_schema: str | None = None) -> set[str]:
    """`schema.table` names a statement's SQL refers to, lower-cased (PostgreSQL folding).

    Parsed with SQLGlot when it can. An unqualified table is in `default_schema` (none:
    left out); a CTE's name is not a table. Only a statement SQLGlot cannot read - often why
    it was refused - or reads as an opaque command falls back to the qualified names in its
    text: a parsed query's `alias.column` references must never pass for tables.
    """
    import re

    import sqlglot
    from sqlglot import exp

    try:
        parsed = sqlglot.parse_one(statement, read="postgres")
        tables = [t for t in parsed.find_all(exp.Table) if t.name]
        if tables and not isinstance(parsed, exp.Command):
            ctes = {cte.alias_or_name.lower() for cte in parsed.find_all(exp.CTE)}
            found = set()
            for table in tables:
                if table.db:
                    found.add(f"{table.db}.{table.name}".lower())
                elif default_schema and table.name.lower() not in ctes:
                    found.add(f"{default_schema}.{table.name}".lower())
            return found
    except Exception:  # unreadable SQL: use the text
        pass
    return {
        match.lower()
        for match in re.findall(r"\b([A-Za-z_]\w*\.[A-Za-z_]\w*)\b", statement)
        if not re.match(r"^\d", match)
    }


def snapshot_id(results: list[ExportedResult]) -> str:
    """Same inputs, same id: the SQL hashes, the settings, the analyser and the schema."""
    material = {
        "schema": SCHEMA_VERSION,
        "files": sorted((r.source.path, r.source.sha256, r.source.dialect) for r in results),
        "provenance": sorted({json.dumps(r.provenance, sort_keys=True) for r in results}),
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:16]


def edge_id(file: str, edge: IREdge) -> str:
    """Stable id of one parser edge in one file (its ledger identity, plus the file)."""
    return _hash(file, *edge.identity())


def _add_file(batch: GraphBatch, result: ExportedResult) -> None:
    file = result.source.path
    batch.node(
        "File",
        file,
        sha256=result.source.sha256,
        dialect=result.source.dialect,
        statements_seen=result.statements_seen,
        statements_analysed=result.statements_analysed,
    )

    for edge in result.edges:
        source = _endpoint(batch, edge.source, file)
        target = _endpoint(batch, edge.target, file)
        statement = _statement(batch, file, edge.origin.unit, edge.origin.line)
        batch.relationship(
            FLOW_TYPES[edge.flow],
            edge_id(file, edge),
            source,
            target,
            transform=edge.transform.value,
            phase=edge.phase.value if edge.phase else None,
            band=edge.band,
            tier=edge.tier.value,
            mechanism=edge.mechanism.value,
            guard=edge.guard,
            file=file,
            unit=edge.origin.unit,
            line=edge.origin.line,
            statement=statement,
        )
        for table, verb in ((_table_of(source), "READS"), (_table_of(target), "WRITES")):
            if table is not None:
                batch.relationship(
                    verb, _hash(verb, statement, table), ("Statement", statement), ("Table", table)
                )

    for index, boundary in enumerate(result.boundaries):
        node = boundary.as_node()
        batch.node("Boundary", node.name, kind=boundary.kind.value, subject=boundary.subject)
        batch.relationship(
            "DECLARES",
            _hash("DECLARES", file, index, *boundary.identity()),
            ("File", file),
            ("Boundary", node.name),
            detail=boundary.detail,
            unit=boundary.unit,
            line=boundary.line,
        )
        if boundary.unit and boundary.line:
            statement = _statement(batch, file, boundary.unit, boundary.line)
            batch.relationship(
                "BLOCKED_BY",
                _hash("BLOCKED_BY", statement, node.name),
                ("Statement", statement),
                ("Boundary", node.name),
            )
        if boundary.attaches_to is not None:
            bounded = _endpoint(batch, boundary.attaches_to, file)
            batch.relationship(
                "BOUNDS", _hash("BOUNDS", node.name, *bounded), ("Boundary", node.name), bounded
            )

    for refusal in result.refusals:
        statement = _statement(batch, file, refusal.unit, refusal.line)
        refusal_id = f"{file}#{refusal.unit}:{refusal.line}:{refusal.scope}"
        batch.node(
            "Refusal",
            refusal_id,
            code=refusal.code.value,
            statement_kind=refusal.kind,
            reason=refusal.reason,
            excerpt=refusal.excerpt,
            scope=refusal.scope,
            line=refusal.line,
            end_line=refusal.end_line,
            file=file,
        )
        batch.relationship(
            "REFUSED",
            _hash("REFUSED", statement, refusal_id),
            ("Statement", statement),
            ("Refusal", refusal_id),
        )


def _endpoint(batch: GraphBatch, node: Node, file: str) -> tuple[str, str]:
    """Add a parser node (and its table, for a column); return (label, id)."""
    label = _LABELS[node.kind]
    name = _local_result(node.name, file)
    if node.kind is NodeKind.COLUMN:
        table, _, column = name.rpartition(".")
        batch.node("Column", name, table=table, name=column)
        if table:
            _table(batch, table)
            batch.relationship(
                "HAS_COLUMN", _hash("HAS_COLUMN", table, name), ("Table", table), ("Column", name)
            )
    elif node.kind is NodeKind.RELATION:
        _table(batch, name)
    else:
        batch.node(label, name)
    return label, name


def _table(batch: GraphBatch, table: str) -> None:
    if table.startswith("<result:"):
        batch.node("Table", table, schema=None, name=table)
        return
    schema, _, name = table.rpartition(".")
    batch.node("Table", table, schema=schema or None, name=name)


def _table_of(endpoint: tuple[str, str]) -> str | None:
    label, node_id = endpoint
    if label == "Table":
        return node_id
    if label == "Column":
        return node_id.rpartition(".")[0] or None
    return None


def _statement(batch: GraphBatch, file: str, unit: str, line: int) -> str:
    statement = f"{file}#{unit}:{line}"
    batch.node("Statement", statement, file=file, unit=unit, line=line)
    batch.relationship(
        "CONTAINS", _hash("CONTAINS", file, statement), ("File", file), ("Statement", statement)
    )
    return statement


def _local_result(name: str, file: str) -> str:
    if name == RESULT or name.startswith(RESULT + "."):
        return f"<result:{file}>" + name[len(RESULT) :]
    return name


def _hash(*parts: Any) -> str:
    text = json.dumps([str(part) for part in parts])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
