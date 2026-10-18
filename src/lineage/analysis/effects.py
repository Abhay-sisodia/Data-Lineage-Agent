"""Effects: what a statement does to a whole relation (plan step 6, D-62).

TRUNCATE, a DELETE with no WHERE, DROP, a rename, a dropped or retyped column, an attached
or detached partition - none of them moves a column's value, so none is a lineage edge, and
until step 6 each was refused or passed over. But they decide what a table holds: a
TRUNCATE before a reload means the table contains ONLY what the reload wrote. So each is an
`Operation` fact on the relation, at its file:line, from PostgreSQL's own parse - top-level
statements and the statements inside routines and DO blocks alike.
"""

from __future__ import annotations

from typing import Any

from pglast import ast

from lineage.ir.model import Operation, OperationKind
from lineage.parsing.pg_referee import Verdict
from lineage.parsing.sqlscript import SCRIPT_UNIT
from lineage.resolution.dictionary import Dictionary

_DROPPED = {"OBJECT_TABLE", "OBJECT_VIEW", "OBJECT_MATVIEW", "OBJECT_FOREIGN_TABLE"}
_TABLE_CHANGES = {
    "AT_DropColumn": "drops column {name}",
    "AT_AlterColumnType": "rewrites column {name}'s values",
    "AT_AttachPartition": "attaches partition {partition}",
    "AT_DetachPartition": "detaches partition {partition}",
}


def _relation(rel: Any, book: Dictionary) -> str:
    written = f"{rel.schemaname}.{rel.relname}" if rel.schemaname else str(rel.relname)
    return (book.resolve(written).qualified or written).lower()


def _effects(statement: Any, book: Dictionary) -> list[tuple[OperationKind, str, str]]:
    """`(kind, relation, detail)` for each effect one parsed statement has."""
    found: list[tuple[OperationKind, str, str]] = []
    if isinstance(statement, ast.TruncateStmt):
        for rel in statement.relations or ():
            found.append((OperationKind.RESETS, _relation(rel, book), "emptied (TRUNCATE)"))
    elif isinstance(statement, ast.DeleteStmt) and statement.whereClause is None:
        if statement.usingClause is None:
            target = _relation(statement.relation, book)
            found.append((OperationKind.RESETS, target, "every row deleted (DELETE, no WHERE)"))
    elif isinstance(statement, ast.DropStmt):
        kind = statement.removeType.name if statement.removeType is not None else ""
        if kind in _DROPPED:
            for name in statement.objects or ():
                parts = [str(part.sval) for part in name if isinstance(part, ast.String)]
                relation = ".".join(parts)
                resolved = (book.resolve(relation).qualified or relation).lower()
                found.append((OperationKind.DROPS, resolved, f"dropped ({kind[7:]})"))
    elif isinstance(statement, ast.RenameStmt) and statement.relation is not None:
        target = _relation(statement.relation, book)
        if statement.subname:
            detail = f"renames column {statement.subname} to {statement.newname}"
        else:
            detail = f"renamed to {statement.newname}"
        found.append((OperationKind.ALTERS, target, detail))
    elif isinstance(statement, ast.AlterObjectSchemaStmt) and statement.relation is not None:
        target = _relation(statement.relation, book)
        found.append((OperationKind.ALTERS, target, f"moved to schema {statement.newschema}"))
    elif isinstance(statement, ast.AlterTableStmt) and statement.relation is not None:
        target = _relation(statement.relation, book)
        for command in statement.cmds or ():
            subtype = command.subtype.name if command.subtype is not None else ""
            if subtype not in _TABLE_CHANGES:
                continue
            partition = getattr(getattr(command.def_, "name", None), "relname", "") or ""
            detail = _TABLE_CHANGES[subtype].format(name=command.name or "", partition=partition)
            found.append((OperationKind.ALTERS, target, detail))
    return found


def rename_edges(verdicts: list[Verdict], book: Dictionary, after: Dictionary | None) -> list[Any]:
    """A rename carries the data (holdout5): identity edges from each old name to its new one.

    `RENAME COLUMN a TO b` after a load leaves the lineage on `t.a` and every later reader on
    `t.b` - nothing linked them, so the values written before the rename reached no one. The
    same for `RENAME TO u` and `SET SCHEMA s`, column by column. The statement stays an
    effect in the ledger; the edges are only the continuity of the column's contents.
    """
    from lineage.harness.scoring import PredictedEdge
    from lineage.ir.model import (
        FilterPhase,
        Flow,
        Mechanism,
        Node,
        NodeKind,
        Origin,
        Tier,
        Transform,
    )

    def edge(old: str, new: str, line: int) -> Any:
        return PredictedEdge(
            source=Node(kind=NodeKind.COLUMN, name=old),
            target=Node(kind=NodeKind.COLUMN, name=new),
            flow=Flow.VALUE,
            transform=Transform.IDENTITY,
            phase=FilterPhase.PRE_AGGREGATION,
            band=0,
            mechanism=Mechanism.AST,
            tier=Tier.A,
            origin=Origin(unit=SCRIPT_UNIT, line=line),
        )

    def columns(relation: str) -> list[str]:
        for source in (after, book):
            if source is None:
                continue
            try:
                return [c.lower() for c in source.columns_of(relation)]
            except Exception:
                continue
        return []

    edges: list[Any] = []
    for verdict in verdicts:
        statement = verdict.tree if verdict.valid else None
        if isinstance(statement, ast.RenameStmt) and statement.relation is not None:
            old = _relation(statement.relation, book)
            schema = old.rpartition(".")[0]
            kind = statement.renameType.name if statement.renameType is not None else ""
            if statement.subname and kind == "OBJECT_COLUMN":
                new_column = str(statement.newname).lower()
                edges.append(edge(f"{old}.{statement.subname.lower()}", f"{old}.{new_column}",
                                  verdict.line))  # fmt: skip
            elif kind in ("OBJECT_TABLE", "OBJECT_VIEW", "OBJECT_MATVIEW"):
                new = f"{schema}.{statement.newname}".lower() if schema else str(statement.newname)
                edges += [edge(f"{old}.{c}", f"{new}.{c}", verdict.line) for c in columns(new)]
        elif isinstance(statement, ast.AlterObjectSchemaStmt) and statement.relation is not None:
            old = _relation(statement.relation, book)
            new = f"{statement.newschema}.{old.rpartition('.')[2]}".lower()
            edges += [edge(f"{old}.{c}", f"{new}.{c}", verdict.line) for c in columns(new)]
    return edges


def build(verdicts: list[Verdict], book: Dictionary) -> list[Operation]:
    """Every effect in the script: each statement's own, and each routine body's."""
    operations: list[Operation] = []
    for verdict in verdicts:
        if not verdict.valid:
            continue
        located = [(verdict.line, verdict.tree), *verdict.body]
        for line, statement in located:
            for kind, relation, detail in _effects(statement, book):
                operations.append(
                    Operation(
                        kind=kind, relation=relation, detail=detail, unit=SCRIPT_UNIT, line=line
                    )
                )
    return operations
