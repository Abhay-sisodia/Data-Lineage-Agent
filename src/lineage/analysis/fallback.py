"""Rungs 3 and 4 of the fallback ladder: what a REFUSED statement still tells us (D-60).

A refused statement has no edges - correctly, since the column-lineage analysis could not
run. But a refusal in the middle of a load must not make the load's tables look unrelated:
PostgreSQL's own parse tree (`pg_referee`) still says which columns and tables it touches.

* **Rung 3 - `mention`.** For a refused INSERT, UPDATE, MERGE, upsert or CREATE TABLE AS:
  each written column, from the columns its expression NAMES. No claim about how they are
  used (value, filter, key) - only that they are there. A column the statement names
  anywhere else (a WHERE, a join) is a mention of the written RELATION, as a filter is.
* **Rung 4 - `opaque`.** Every relation the refused statement reads, to every relation it
  writes - for a refused routine, whose body is not read column by column, and for any
  read relation no mention already connects.

Both are kept in `AnalysisResult.fallback`, apart from the scored edges, as join keys are,
and a name is only ever resolved through the statement's own aliases and the dictionary:
an alias used twice for different relations, or an unqualified name two relations share,
resolves to nothing rather than to a guess - rung 4 still connects its tables.
"""

from __future__ import annotations

from typing import Any

from pglast import ast

from lineage.ir.model import Flow, IREdge, Mechanism, Node, NodeKind, Origin
from lineage.parsing.pg_referee import Verdict, _slots
from lineage.parsing.sqlscript import SCRIPT_UNIT
from lineage.resolution.dictionary import Dictionary

_AMBIGUOUS = "<ambiguous>"


def _name(rel: Any, book: Dictionary) -> str:
    resolved = book.resolve(f"{rel.schemaname}.{rel.relname}" if rel.schemaname else rel.relname)
    if resolved.qualified is not None:
        return resolved.qualified.lower()
    schema = rel.schemaname or book.default_schema
    return f"{schema}.{rel.relname}".lower()


def _nodes(node: Any, kind: type) -> list[Any]:
    """Every `kind` node under `node`, in the order the tree holds them."""
    found: list[Any] = []

    def walk(item: Any) -> None:
        if isinstance(item, (list, tuple)):
            for child in item:
                walk(child)
        elif isinstance(item, ast.Node):
            if isinstance(item, kind):
                found.append(item)
            for slot in _slots(type(item)):
                walk(getattr(item, slot, None))

    walk(node)
    return found


class _Scope:
    """The statement's relations by alias, and how its column names resolve against them."""

    def __init__(self, statement: Any, book: Dictionary) -> None:
        self.book = book
        self.aliases: dict[str, str] = {}
        ctes = {str(cte.ctename).lower() for cte in _nodes(statement, ast.CommonTableExpr)}
        for rel in _nodes(statement, ast.RangeVar):
            if not rel.schemaname and str(rel.relname).lower() in ctes:
                continue
            table = _name(rel, book)
            for alias in {str(rel.relname).lower(), (rel.alias.aliasname if rel.alias else "")}:
                if alias:
                    known = self.aliases.get(alias.lower())
                    self.aliases[alias.lower()] = table if known in (None, table) else _AMBIGUOUS
        self.tables = sorted({t for t in self.aliases.values() if t != _AMBIGUOUS})

    def column(self, ref: Any) -> str | None:
        """`schema.table.column` for a ColumnRef, or None when it cannot be pinned down."""
        parts = [str(f.sval).lower() for f in ref.fields or () if isinstance(f, ast.String)]
        if len(parts) != len(ref.fields or ()) or not parts:
            return None  # `t.*`, `(row).field` ...
        if len(parts) >= 2:
            table = self.aliases.get(parts[-2])
            return None if table in (None, _AMBIGUOUS) else f"{table}.{parts[-1]}"
        owners = [t for t in self.tables if parts[0] in self.book.columns.get(t, [])]
        return f"{owners[0]}.{parts[0]}" if len(owners) == 1 else None

    def columns(self, node: Any) -> list[str]:
        return [c for c in (self.column(r) for r in _nodes(node, ast.ColumnRef)) if c]


def _writes(
    statement: Any, scope: _Scope, book: Dictionary
) -> tuple[str | None, list[tuple[str, Any]]]:
    """The written relation, and `(column, expression)` for each column it writes."""
    if isinstance(statement, ast.InsertStmt):
        target = _name(statement.relation, book)
        names = [str(t.name).lower() for t in statement.cols or ()]
        if not names:
            names = [c.lower() for c in book.columns.get(target, [])]
        pairs: list[tuple[str, Any]] = []
        select = statement.selectStmt
        if isinstance(select, ast.SelectStmt) and select.valuesLists:
            for row in select.valuesLists:
                pairs.extend(zip(names, row, strict=False))
        elif isinstance(select, ast.SelectStmt) and select.targetList:
            pairs.extend(zip(names, (t.val for t in select.targetList), strict=False))
        clause = statement.onConflictClause
        if clause is not None:
            pairs.extend((str(t.name).lower(), t.val) for t in clause.targetList or ())
        return target, pairs
    if isinstance(statement, ast.UpdateStmt):
        target = _name(statement.relation, book)
        return target, [(str(t.name).lower(), t.val) for t in statement.targetList or ()]
    if isinstance(statement, ast.MergeStmt):
        target = _name(statement.relation, book)
        pairs = []
        for when in statement.mergeWhenClauses or ():
            if when.values:
                names = [str(t.name).lower() for t in when.targetList or ()]
                pairs.extend(zip(names, when.values, strict=False))
            else:
                pairs.extend((str(t.name).lower(), t.val) for t in when.targetList or ())
        return target, pairs
    into = getattr(statement, "into", None) or getattr(statement, "intoClause", None)
    query = statement.query if isinstance(statement, ast.CreateTableAsStmt) else statement
    if into is not None and isinstance(query, ast.SelectStmt):
        target = _name(into.rel, book)
        pairs = []
        for item in query.targetList or ():
            name = item.name or (
                item.val.fields[-1].sval
                if isinstance(item.val, ast.ColumnRef) and item.val.fields
                else None
            )
            if name:
                pairs.append((str(name).lower(), item.val))
        return target, pairs
    return None, []


def _edge(source: Node, target: Node, flow: Flow, line: int) -> IREdge:
    return IREdge(
        source=source,
        target=target,
        flow=flow,
        band=0,
        mechanism=Mechanism.AST,
        origin=Origin(unit=SCRIPT_UNIT, line=line),
    )


def for_statement(verdict: Verdict, book: Dictionary) -> list[IREdge]:
    """The mentions and opaque edges PostgreSQL's tree gives for one refused statement."""
    statement = verdict.tree
    out: list[IREdge] = []
    connected: set[str] = set()
    if statement is not None and not isinstance(statement, (ast.CreateFunctionStmt, ast.DoStmt)):
        scope = _Scope(statement, book)
        target, pairs = _writes(statement, scope, book)
        if target is not None:
            valued: set[str] = set()
            for column, expression in pairs:
                for source in scope.columns(expression):
                    valued.add(source)
                    out.append(
                        _edge(
                            Node(kind=NodeKind.COLUMN, name=source),
                            Node(kind=NodeKind.COLUMN, name=f"{target}.{column}"),
                            Flow.MENTION,
                            verdict.line,
                        )
                    )
            for source in sorted(set(scope.columns(statement)) - valued):
                out.append(
                    _edge(
                        Node(kind=NodeKind.COLUMN, name=source),
                        Node(kind=NodeKind.RELATION, name=target),
                        Flow.MENTION,
                        verdict.line,
                    )
                )
            connected = {str(e.source.name).rpartition(".")[0] for e in out}
    for read in verdict.reads:
        table = book.resolve(read).qualified or read
        if table.lower() in connected:
            continue
        for write in verdict.writes:
            written = book.resolve(write).qualified or write
            if written.lower() != table.lower():
                out.append(
                    _edge(
                        Node(kind=NodeKind.RELATION, name=table.lower()),
                        Node(kind=NodeKind.RELATION, name=written.lower()),
                        Flow.OPAQUE,
                        verdict.line,
                    )
                )
    unique: dict[tuple[str, ...], IREdge] = {}
    for edge in out:
        unique.setdefault(edge.identity(), edge)
    return list(unique.values())


def build(verdicts: list[Verdict], result: Any, book: Dictionary) -> list[IREdge]:
    """Fallback edges for every statement refused WHOLE that produced no edge of its own."""
    found: list[IREdge] = []
    for verdict in verdicts:
        if not verdict.valid:
            continue
        span = range(verdict.line, verdict.end_line + 1)
        refused = any(r.scope == "statement" and r.line in span for r in result.refusals)
        produced = any(e.origin.line in span for e in [*result.edges, *result.join_keys])
        if refused and not produced:
            found.extend(for_statement(verdict, book))
    return found
