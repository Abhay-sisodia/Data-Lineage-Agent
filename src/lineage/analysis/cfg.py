"""Control flow graph construction (T2.2).

A procedure is a program, not a query. Before any value can be traced through a variable
we need to know what can execute after what — which is the difference between analysing
SQL and analysing code, and the reason this spike exists.

Three decisions worth stating:

* **Exception handlers are real edges, not an afterthought.** `b1_09` writes the same
  regulated column from a completely different source on its `NO_DATA_FOUND` path. On a
  failure day that IS the lineage, and it appears nowhere in the happy path. An exception
  can be raised by any statement, so every statement in a protected block gets an edge to
  every handler.
* **Loops produce a back edge.** Without it, def-use sees one definition reaching a use
  and reports an accumulation exactly one iteration deep — quietly wrong rather than
  loudly broken.
* **Branch conditions are carried on the edges.** T2.4 needs the guard for each path, and
  the `ELSE` arm's condition exists nowhere in the source: it has to be reconstructed as
  the negation of every preceding arm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from lineage.parsing.generated.PlSqlParser import PlSqlParser
from lineage.parsing.plsql import (
    Program,
    iter_contexts,
    most_specific,
    rule_name,
    source_slice,
)

# Guard against pathological nesting rather than recursing forever.
MAX_NESTING = 64


class NodeKind(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    STATEMENT = "statement"
    BRANCH = "branch"
    LOOP = "loop"
    HANDLER = "handler"


class EdgeKind(StrEnum):
    SEQUENTIAL = "sequential"
    TRUE = "true"
    FALSE = "false"
    LOOP_BODY = "loop_body"
    LOOP_BACK = "loop_back"
    LOOP_EXIT = "loop_exit"
    EXCEPTION = "exception"


@dataclass(frozen=True)
class CfgNode:
    id: int
    kind: NodeKind
    label: str
    line: int
    statement_kind: str | None = None
    ctx: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class CfgEdge:
    source: int
    target: int
    kind: EdgeKind
    condition: str | None = None

    @property
    def is_exceptional(self) -> bool:
        return self.kind is EdgeKind.EXCEPTION


@dataclass
class Cfg:
    """The control flow graph of one program unit."""

    unit: str
    nodes: dict[int, CfgNode] = field(default_factory=dict)
    edges: list[CfgEdge] = field(default_factory=list)
    entry: int = 0
    exit: int = 0

    def successors(self, node_id: int) -> list[CfgEdge]:
        return [edge for edge in self.edges if edge.source == node_id]

    def predecessors(self, node_id: int) -> list[CfgEdge]:
        return [edge for edge in self.edges if edge.target == node_id]

    def statement_nodes(self) -> list[CfgNode]:
        return [n for n in self.nodes.values() if n.kind is NodeKind.STATEMENT]

    @property
    def exceptional_edges(self) -> list[CfgEdge]:
        return [edge for edge in self.edges if edge.is_exceptional]

    def guards_reaching(self, node_id: int, _stack: frozenset[int] | None = None) -> list[str]:
        """Conditions that must hold for control to reach this node, outermost first.

        Computed as the **common prefix over every incoming path**, which is what makes
        merge points correct. The `COMMIT` after an `IF/ELSIF/ELSE` is reached whichever
        arm ran, so it carries no guard from that branch - taking the first predecessor
        instead would claim the statement only runs for EU customers, which is exactly
        the kind of confident wrong answer this project exists to avoid.

        Nested branches still conjoin: the APAC arm of `b1_02` is governed by
        `NOT (EU) AND APAC AND strict`, and reporting only the outer condition would
        claim it fires for every APAC run.

        Loop back edges are excluded - they carry no new condition and would recurse
        forever. Handler bodies fall back to their exception edge, so an error-path
        write is guarded by the exception that reached it rather than appearing
        unconditional.
        """
        if node_id == self.entry:
            return []

        stack = _stack or frozenset()
        if node_id in stack:
            return []

        incoming = [
            edge
            for edge in self.predecessors(node_id)
            if edge.kind is not EdgeKind.LOOP_BACK and not edge.is_exceptional
        ]

        if not incoming:
            exceptional = [e for e in self.predecessors(node_id) if e.is_exceptional]
            if exceptional and exceptional[0].condition:
                return [exceptional[0].condition]
            return []

        deeper = stack | {node_id}
        paths: list[list[str]] = []
        for edge in incoming:
            upstream = self.guards_reaching(edge.source, deeper)
            paths.append([*upstream, edge.condition] if edge.condition else upstream)

        return _common_prefix(paths)


# Exit points of a fragment: the node to continue from, plus the edge kind and condition
# that should label the edge leaving it.
Exits = list[tuple[int, EdgeKind, str | None]]


class _Builder:
    def __init__(self, unit: str) -> None:
        self.cfg = Cfg(unit=unit)
        self._next_id = 0

    def add_node(
        self,
        kind: NodeKind,
        label: str,
        line: int,
        statement_kind: str | None = None,
        ctx: Any = None,
    ) -> int:
        node_id = self._next_id
        self._next_id += 1
        self.cfg.nodes[node_id] = CfgNode(node_id, kind, label, line, statement_kind, ctx)
        return node_id

    def connect(self, exits: Exits, target: int) -> None:
        for source, kind, condition in exits:
            self.cfg.edges.append(CfgEdge(source, target, kind, condition))

    # ---- statement dispatch --------------------------------------------------------

    def build_sequence(self, seq_ctx: Any, incoming: Exits, depth: int = 0) -> Exits:
        """Build a seq_of_statements. Returns the exits of the whole sequence."""
        if seq_ctx is None or depth > MAX_NESTING:
            return incoming

        exits = incoming
        for statement in self._statements_of(seq_ctx):
            exits = self.build_statement(statement, exits, depth + 1)
        return exits

    def _statements_of(self, seq_ctx: Any) -> list[Any]:
        """Direct child statements of a sequence, in source order.

        Only DIRECT children: nested statements belong to their own construct and are
        walked when that construct is built, not flattened into the parent.
        """
        found = []
        for index in range(seq_ctx.getChildCount()):
            child = seq_ctx.getChild(index)
            if isinstance(child, PlSqlParser.StatementContext):
                found.append(child)
        return found

    def build_statement(self, statement_ctx: Any, incoming: Exits, depth: int) -> Exits:
        inner = most_specific(statement_ctx)
        kind = rule_name(inner)

        if isinstance(inner, PlSqlParser.If_statementContext):
            return self._build_if(inner, incoming, depth)
        if isinstance(inner, PlSqlParser.Loop_statementContext):
            return self._build_loop(inner, incoming, depth)

        node = self.add_node(
            NodeKind.STATEMENT,
            _one_line(source_slice(statement_ctx)),
            statement_ctx.start.line,
            kind,
            statement_ctx,
        )
        self.connect(incoming, node)
        return [(node, EdgeKind.SEQUENTIAL, None)]

    def _build_if(self, ctx: Any, incoming: Exits, depth: int) -> Exits:
        condition = _text(ctx.condition())
        branch = self.add_node(
            NodeKind.BRANCH, f"IF {condition}", ctx.start.line, "if_statement", ctx
        )
        self.connect(incoming, branch)

        exits: Exits = []
        negated: list[str] = [_negate(condition)]

        # THEN arm
        exits += self.build_sequence(
            ctx.seq_of_statements(), [(branch, EdgeKind.TRUE, condition)], depth
        )

        # ELSIF arms: each guarded by its own condition AND the negation of all before it.
        for part in ctx.elsif_part() or []:
            part_condition = _text(part.condition())
            combined = " AND ".join([*negated, part_condition])
            exits += self.build_sequence(
                part.seq_of_statements(), [(branch, EdgeKind.TRUE, combined)], depth
            )
            negated.append(_negate(part_condition))

        # ELSE arm: its condition appears nowhere in the source and must be reconstructed.
        else_part = ctx.else_part()
        if else_part is not None:
            exits += self.build_sequence(
                else_part.seq_of_statements(),
                [(branch, EdgeKind.FALSE, " AND ".join(negated))],
                depth,
            )
        else:
            # No ELSE: control can fall through the branch untouched.
            exits.append((branch, EdgeKind.FALSE, " AND ".join(negated)))

        return exits

    def _build_loop(self, ctx: Any, incoming: Exits, depth: int) -> Exits:
        condition = None
        # A WHILE condition is a genuine boolean guard: its negation governs what runs
        # after the loop. A FOR iteration spec is not - a FOR loop always completes, so
        # negating it would attach a meaningless condition to every following statement.
        exit_is_conditional = False

        if ctx.condition() is not None:  # WHILE
            condition = _text(ctx.condition())
            exit_is_conditional = True
        elif ctx.cursor_loop_param() is not None:  # FOR
            condition = _text(ctx.cursor_loop_param())

        head = self.add_node(
            NodeKind.LOOP,
            f"LOOP {condition or ''}".strip(),
            ctx.start.line,
            "loop_statement",
            ctx,
        )
        self.connect(incoming, head)

        body_exits = self.build_sequence(
            ctx.seq_of_statements(), [(head, EdgeKind.LOOP_BODY, condition)], depth
        )
        # The back edge. Without it, a value accumulated across iterations looks like a
        # single assignment and def-use reports a chain one iteration deep.
        for source, _, _ in body_exits:
            self.cfg.edges.append(CfgEdge(source, head, EdgeKind.LOOP_BACK, None))

        exit_condition = _negate(condition) if (condition and exit_is_conditional) else None
        return [(head, EdgeKind.LOOP_EXIT, exit_condition)]


def build_cfg(program: Program, unit_ctx: Any, unit_name: str) -> Cfg:
    """Build the CFG for one program unit."""
    builder = _Builder(unit_name)
    cfg = builder.cfg

    body = unit_ctx.body() if hasattr(unit_ctx, "body") else None
    start_line = unit_ctx.start.line

    cfg.entry = builder.add_node(NodeKind.ENTRY, f"ENTRY {unit_name}", start_line)
    if body is None:
        cfg.exit = builder.add_node(NodeKind.EXIT, f"EXIT {unit_name}", start_line)
        builder.connect([(cfg.entry, EdgeKind.SEQUENTIAL, None)], cfg.exit)
        return cfg

    exits = builder.build_sequence(
        body.seq_of_statements(), [(cfg.entry, EdgeKind.SEQUENTIAL, None)]
    )

    cfg.exit = builder.add_node(NodeKind.EXIT, f"EXIT {unit_name}", body.stop.line)
    builder.connect(exits, cfg.exit)

    # Exception handlers. Any statement in the protected block can raise, so every
    # statement gets an edge to every handler - which is what makes an error-path write
    # reachable in the analysis at all.
    protected = [n.id for n in cfg.statement_nodes()]
    for handler in body.exception_handler() or []:
        names = " OR ".join(_text(n) for n in handler.exception_name())
        handler_entry = builder.add_node(
            NodeKind.HANDLER, f"WHEN {names}", handler.start.line, "exception_handler", handler
        )
        for statement_id in protected:
            cfg.edges.append(
                CfgEdge(statement_id, handler_entry, EdgeKind.EXCEPTION, f"EXCEPTION {names}")
            )
        handler_exits = builder.build_sequence(
            handler.seq_of_statements(), [(handler_entry, EdgeKind.SEQUENTIAL, None)]
        )
        builder.connect(handler_exits, cfg.exit)

    return cfg


def build_all(program: Program) -> dict[str, Cfg]:
    """A CFG per program unit in the source."""
    graphs: dict[str, Cfg] = {}
    unit_types = [
        ("Create_procedure_bodyContext", "procedure_name"),
        ("Create_function_bodyContext", "function_name"),
        ("Procedure_bodyContext", "identifier"),
        ("Function_bodyContext", "identifier"),
    ]

    for context_name, accessor in unit_types:
        context_class = getattr(PlSqlParser, context_name, None)
        if context_class is None:
            continue
        for ctx in iter_contexts(program.tree, context_class):
            name_node = getattr(ctx, accessor, lambda: None)()
            if isinstance(name_node, list):
                name_node = name_node[0] if name_node else None
            if name_node is None:
                continue
            name = str(name_node.getText()).upper()
            graphs[name] = build_cfg(program, ctx, name)

    return graphs


def _text(ctx: Any) -> str:
    return _one_line(source_slice(ctx)) if ctx is not None else ""


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _negate(condition: str | None) -> str:
    return f"NOT ({condition})" if condition else ""


def _common_prefix(paths: list[list[str]]) -> list[str]:
    """Conditions shared by every path into a node.

    A merge point is governed only by what holds on ALL routes to it - anything else
    would be true on one arm and false on another.
    """
    if not paths:
        return []
    prefix: list[str] = []
    for index in range(min(len(path) for path in paths)):
        candidate = paths[0][index]
        if all(path[index] == candidate for path in paths):
            prefix.append(candidate)
        else:
            break
    return prefix
