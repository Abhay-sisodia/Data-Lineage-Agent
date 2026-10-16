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

from lineage.dialects import fold
from lineage.parsing.frontend import Frontend, IfShape, LoopShape
from lineage.parsing.plsql import Program

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
    def __init__(self, unit: str, frontend: Frontend) -> None:
        self.cfg = Cfg(unit=unit)
        self._next_id = 0
        # The only thing that knows the grammar. Every question about the tree below goes
        # through it, so this builder is written against SHAPES - a branch has a condition
        # and arms, a loop has a body - and never against how a dialect spells them (A3b).
        self._frontend = frontend

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
        """Build a statement sequence. Returns the exits of the whole sequence.

        Only DIRECT children: nested statements belong to their own construct and are
        walked when that construct is built, not flattened into the parent.
        """
        if seq_ctx is None or depth > MAX_NESTING:
            return incoming

        exits = incoming
        for statement in self._frontend.statements_of(seq_ctx):
            exits = self.build_statement(statement, exits, depth + 1)
        return exits

    def build_statement(self, statement_ctx: Any, incoming: Exits, depth: int) -> Exits:
        shape = self._frontend.shape(statement_ctx)
        if isinstance(shape, IfShape):
            return self._build_if(statement_ctx, shape, incoming, depth)
        if isinstance(shape, LoopShape):
            return self._build_loop(statement_ctx, shape, incoming, depth)

        node = self.add_node(
            NodeKind.STATEMENT,
            _one_line(self._frontend.text(statement_ctx)),
            self._frontend.line(statement_ctx),
            self._frontend.statement_kind(statement_ctx),
            statement_ctx,
        )
        self.connect(incoming, node)
        return [(node, EdgeKind.SEQUENTIAL, None)]

    def _build_if(self, ctx: Any, shape: IfShape, incoming: Exits, depth: int) -> Exits:
        condition = shape.condition
        branch = self.add_node(
            NodeKind.BRANCH, f"IF {condition}", self._frontend.line(ctx), "if_statement", ctx
        )
        self.connect(incoming, branch)

        exits: Exits = []
        negated: list[str] = [_negate(condition)]

        # THEN arm
        exits += self.build_sequence(
            shape.then_sequence, [(branch, EdgeKind.TRUE, condition)], depth
        )

        # ELSIF arms: each guarded by its own condition AND the negation of all before it.
        for part_condition, part_sequence in shape.elsifs:
            combined = " AND ".join([*negated, part_condition])
            exits += self.build_sequence(
                part_sequence, [(branch, EdgeKind.TRUE, combined)], depth
            )
            negated.append(_negate(part_condition))

        # ELSE arm: its condition appears nowhere in the source and must be reconstructed.
        if shape.else_sequence is not None:
            exits += self.build_sequence(
                shape.else_sequence,
                [(branch, EdgeKind.FALSE, " AND ".join(negated))],
                depth,
            )
        else:
            # No ELSE: control can fall through the branch untouched.
            exits.append((branch, EdgeKind.FALSE, " AND ".join(negated)))

        return exits

    def _build_loop(self, ctx: Any, shape: LoopShape, incoming: Exits, depth: int) -> Exits:
        # Only a WHILE contributes a guard. A guard is a condition under which an edge
        # fires or does not; a FOR loop's body always runs, once per iteration, so its
        # iteration spec is not a condition at all. Treating `i IN 1 .. 12` as a guard
        # would say the write is conditional when it is not.
        #
        # The loop variable's real influence - deciding WHICH rows a statement reads - is
        # carried as a filter edge by the def-use analysis, which is where it belongs.
        condition = shape.condition
        exit_is_conditional = condition is not None

        head = self.add_node(
            NodeKind.LOOP,
            f"LOOP {shape.label}".strip(),
            self._frontend.line(ctx),
            "loop_statement",
            ctx,
        )
        self.connect(incoming, head)

        body_exits = self.build_sequence(
            shape.body_sequence, [(head, EdgeKind.LOOP_BODY, condition)], depth
        )
        # The back edge. Without it, a value accumulated across iterations looks like a
        # single assignment and def-use reports a chain one iteration deep.
        for source, _, _ in body_exits:
            self.cfg.edges.append(CfgEdge(source, head, EdgeKind.LOOP_BACK, None))

        exit_condition = _negate(condition) if (condition and exit_is_conditional) else None
        return [(head, EdgeKind.LOOP_EXIT, exit_condition)]


def build_cfg(program: Program, unit_ctx: Any, unit_name: str) -> Cfg:
    """Build the CFG for one program unit."""
    frontend: Frontend = program.frontend
    builder = _Builder(unit_name, frontend)
    cfg = builder.cfg

    body = frontend.body(unit_ctx)
    start_line = frontend.line(unit_ctx)

    cfg.entry = builder.add_node(NodeKind.ENTRY, f"ENTRY {unit_name}", start_line)
    if body is None:
        cfg.exit = builder.add_node(NodeKind.EXIT, f"EXIT {unit_name}", start_line)
        builder.connect([(cfg.entry, EdgeKind.SEQUENTIAL, None)], cfg.exit)
        return cfg

    exits = builder.build_sequence(body.sequence, [(cfg.entry, EdgeKind.SEQUENTIAL, None)])

    cfg.exit = builder.add_node(NodeKind.EXIT, f"EXIT {unit_name}", body.end_line)
    builder.connect(exits, cfg.exit)

    # Exception handlers. Any statement in the protected block can raise, so every
    # statement gets an edge to every handler - which is what makes an error-path write
    # reachable in the analysis at all.
    protected = [n.id for n in cfg.statement_nodes()]
    for handler in body.handlers:
        handler_entry = builder.add_node(
            NodeKind.HANDLER,
            f"WHEN {handler.names}",
            handler.line,
            "exception_handler",
            handler.ctx,
        )
        for statement_id in protected:
            cfg.edges.append(
                CfgEdge(
                    statement_id, handler_entry, EdgeKind.EXCEPTION, f"EXCEPTION {handler.names}"
                )
            )
        handler_exits = builder.build_sequence(
            handler.sequence, [(handler_entry, EdgeKind.SEQUENTIAL, None)]
        )
        builder.connect(handler_exits, cfg.exit)

    return cfg


def build_all(program: Program, dialect: str = "oracle") -> dict[str, Cfg]:
    """A CFG per program unit in the source."""
    graphs: dict[str, Cfg] = {}
    # Unit names are FOLDED with the dialect's rule, not upper-cased. A2 recorded this
    # as residue on the grounds that a unit name is only an Origin label and a
    # comparison key - true for Oracle, where fold IS upper. PostgreSQL proved it
    # false: `collect_scopes` folded to `<script>` while this keyed `<SCRIPT>`, so
    # `scopes.get(unit)` missed and BAND 1 SILENTLY DID NOTHING. Producers fold; the
    # sites that compare two produced names normalise both sides and are unaffected.
    for unit in program.frontend.units(program.tree):
        name = fold(unit.name, dialect)
        graphs[name] = build_cfg(program, unit.ctx, name)

    return graphs


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
