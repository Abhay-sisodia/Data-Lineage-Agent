"""Interprocedural summaries (T2.7).

A scalar UDF inside a `SELECT` looks like a column expression and contains a query.
Without summarising the callee, `fct_revenue.net_amount` appears to come from nowhere —
a function call with no visible source. That is not a wrong edge; it is a regulated
column with no traceable origin, which is worse.

**The failure this replaces is a plausible one.** Treating the call's *arguments* as value
sources produces `stg_orders.order_id -> fct_revenue.net_amount`. It type-checks, it
reads sensibly, and it is false: an order id does not determine a net amount, it selects
which row the callee reads. The argument is filter influence; the value comes from
whatever columns the callee's return expression depends on.

Each callee is summarised once and the summary inlined at every call site. Depth is
capped from configuration, and beyond the cap a boundary node is emitted and counted —
never a guessed edge. Recursion terminates by memoising on the unit name and refusing to
re-enter a unit already on the stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lineage.analysis.cfg import build_all
from lineage.analysis.defuse import UnitScope, analyse_unit, collect_scopes
from lineage.ir.model import Flow, NodeKind, Transform
from lineage.parsing.generated.PlSqlParser import PlSqlParser
from lineage.parsing.plsql import Program, iter_contexts, source_slice
from lineage.resolution.dictionary import Dictionary

TRANSFORM_RANK = {
    Transform.IDENTITY: 0,
    Transform.DERIVED: 1,
    Transform.CONDITIONAL: 2,
    Transform.AGGREGATED: 3,
}


def _combine(first: Transform, second: Transform) -> Transform:
    return first if TRANSFORM_RANK[first] >= TRANSFORM_RANK[second] else second


@dataclass(frozen=True)
class ReturnSource:
    """A column the callee's return value depends on."""

    column: str  # TABLE.COLUMN
    transform: Transform


@dataclass
class Summary:
    """What a callable does, from the caller's point of view."""

    unit: str
    returns_from: list[ReturnSource] = field(default_factory=list)
    reads: set[str] = field(default_factory=set)  # relations the callee filters
    truncated: bool = False  # depth cap or recursion stopped the analysis

    def consolidated(self) -> list[ReturnSource]:
        """One entry per column, keeping the WEAKEST transform found.

        Two different combination rules, and confusing them produces a wrong answer:

        * Along **one path**, the strongest transform wins - an aggregation over a
          derived expression is an aggregation.
        * Across **different paths** to the same column, the weakest wins. In
          `fn_net_amount`, `gross_amount` reaches the result twice: unconditionally
          through `v_gross`, and conditionally through the rate's CASE. Reporting it as
          conditional would claim it only sometimes contributes, which is false.

        `discount_amt` has only the conditional path, so it stays conditional - it
        genuinely does not contribute when `gross_amount = 0`.
        """
        weakest: dict[str, Transform] = {}
        for item in self.returns_from:
            current = weakest.get(item.column)
            if current is None or TRANSFORM_RANK[item.transform] < TRANSFORM_RANK[current]:
                weakest[item.column] = item.transform
        return [ReturnSource(column, transform) for column, transform in sorted(weakest.items())]

    def boundary(self) -> str | None:
        if not self.truncated:
            return None
        return (
            f"call to {self.unit} not fully summarised - interprocedural depth cap "
            f"reached, so any lineage below it is out of coverage rather than absent"
        )


def _return_expressions(unit_ctx: Any) -> list[str]:
    """Source text of every RETURN expression in a unit."""
    return_ctx = getattr(PlSqlParser, "Return_statementContext", None)
    if return_ctx is None:
        return []
    texts = []
    for node in iter_contexts(unit_ctx, return_ctx):
        expression = node.expression() if hasattr(node, "expression") else None
        if expression is not None:
            texts.append(source_slice(expression))
    return texts


def _unit_contexts(program: Program) -> dict[str, Any]:
    """Every callable unit in the source, by name."""
    units: dict[str, Any] = {}
    for context_name, accessor in [
        ("Create_function_bodyContext", "function_name"),
        ("Create_procedure_bodyContext", "procedure_name"),
        ("Function_bodyContext", "identifier"),
        ("Procedure_bodyContext", "identifier"),
    ]:
        context_class = getattr(PlSqlParser, context_name, None)
        if context_class is None:
            continue
        for ctx in iter_contexts(program.tree, context_class):
            name_node = getattr(ctx, accessor, lambda: None)()
            if isinstance(name_node, list):
                name_node = name_node[0] if name_node else None
            if name_node is None:
                continue
            units[str(name_node.getText()).upper()] = ctx
    return units


class SummaryBuilder:
    """Builds and caches one summary per callable."""

    def __init__(self, program: Program, dictionary: Dictionary, depth_cap: int) -> None:
        self._program = program
        self._dictionary = dictionary
        self._depth_cap = depth_cap
        self._units = _unit_contexts(program)
        self._scopes: dict[str, UnitScope] = collect_scopes(program)
        self._graphs = build_all(program)
        self._cache: dict[str, Summary] = {}

    @property
    def callables(self) -> set[str]:
        return set(self._units)

    def summary_of(self, unit: str, stack: frozenset[str] | None = None) -> Summary:
        """Summarise one callable, following nested calls up to the depth cap."""
        name = unit.upper()
        active = stack or frozenset()

        if name in self._cache and not active:
            return self._cache[name]
        if name not in self._units:
            return Summary(unit=name, truncated=True)
        if name in active:
            # Recursion. Stop and declare rather than unrolling forever.
            return Summary(unit=name, truncated=True)
        if len(active) >= self._depth_cap:
            return Summary(unit=name, truncated=True)

        summary = self._build(name, active | {name})
        if not active:
            self._cache[name] = summary
        return summary

    def _build(self, name: str, stack: frozenset[str]) -> Summary:
        summary = Summary(unit=name)

        cfg = self._graphs.get(name)
        scope = self._scopes.get(name)
        if cfg is None or scope is None:
            summary.truncated = True
            return summary

        edges = analyse_unit(cfg, scope, self._dictionary).edges

        # Within the callee: which columns feed which variables, and which relations its
        # parameters filter.
        feeds: dict[str, list[tuple[str, str, Transform]]] = {}
        for edge in edges:
            if edge.flow is Flow.VALUE and edge.target.kind is NodeKind.VARIABLE:
                feeds.setdefault(edge.target.name, []).append(
                    (edge.source.kind.value, edge.source.name, edge.transform)
                )
            if edge.flow is Flow.FILTER and edge.target.kind is NodeKind.RELATION:
                summary.reads.add(edge.target.name)

        for text in _return_expressions(self._units[name]):
            summary.returns_from.extend(self._resolve_return(text, feeds, scope, stack))

        # Nested calls inside the return expression contribute their own reads.
        for text in _return_expressions(self._units[name]):
            for callee in self._calls_in(text):
                nested = self.summary_of(callee, stack)
                summary.reads |= nested.reads
                summary.truncated = summary.truncated or nested.truncated

        return summary

    def _calls_in(self, text: str) -> list[str]:
        """Names of known callables invoked in an expression."""
        upper = text.upper()
        return [c for c in self._units if f"{c}(" in upper.replace(" ", "")]

    def _resolve_return(
        self,
        text: str,
        feeds: dict[str, list[tuple[str, str, Transform]]],
        scope: UnitScope,
        stack: frozenset[str],
    ) -> list[ReturnSource]:
        """Columns the return expression ultimately depends on."""
        found: list[ReturnSource] = []
        upper = text.upper()

        # A RETURN that is anything other than a bare variable transforms the value.
        outer = Transform.IDENTITY if upper.strip().isidentifier() else Transform.DERIVED

        for variable in scope.variables:
            if variable in _identifiers(upper):
                for kind, source, transform in _walk_back(variable, feeds):
                    if kind == "column":
                        found.append(ReturnSource(source, _combine(outer, transform)))

        for callee in self._calls_in(text):
            nested = self.summary_of(callee, stack)
            for item in nested.returns_from:
                found.append(ReturnSource(item.column, _combine(outer, item.transform)))

        return found


def _identifiers(text: str) -> set[str]:
    import re

    return set(re.findall(r"[A-Z][A-Z0-9_$#]*", text))


def _walk_back(
    variable: str,
    feeds: dict[str, list[tuple[str, str, Transform]]],
    seen: frozenset[str] | None = None,
) -> list[tuple[str, str, Transform]]:
    """Follow a variable back to the columns that fed it."""
    visited = seen or frozenset()
    if variable in visited:
        return []

    resolved: list[tuple[str, str, Transform]] = []
    for kind, source, transform in feeds.get(variable, []):
        if kind == "column":
            resolved.append((kind, source, transform))
        else:
            for deeper in _walk_back(source, feeds, visited | {variable}):
                resolved.append((deeper[0], deeper[1], _combine(transform, deeper[2])))
    return resolved


def build_summaries(program: Program, dictionary: Dictionary, depth_cap: int) -> dict[str, Summary]:
    """Summarise every callable in the source."""
    builder = SummaryBuilder(program, dictionary, depth_cap)
    return {name: builder.summary_of(name) for name in builder.callables}
