"""Def-use analysis over variables (T2.3).

The core of band 1. A value read from one table decides which rows load from another,
three statements later, having passed through two local variables. A SQL parser sees two
unrelated statements; the connection lives in memory that never touches a table.

**The handoff.** To SQLGlot, a PL/SQL variable inside a `WHERE` clause is
indistinguishable from a column reference — it will happily bind `v_cutoff` to the only
table in scope. That is why declarations are collected first: a name is checked against
the declared variables of its unit *before* it is treated as a column. Get this backwards
and you produce a confident, fully corroborated, entirely fictional edge.

**Variables are nodes, not shortcuts.** `ref_policy.window_days -> v_days -> v_cutoff` is
two edges, not one collapsed hop. Collapsing hides where the chain broke, which is the
entire diagnostic value of this band.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from lineage.analysis.band0 import resolve_projection
from lineage.analysis.cfg import Cfg, CfgNode, NodeKind
from lineage.analysis.dynamic import Resolution
from lineage.analysis.predicates import predicate_columns
from lineage.analysis.transforms import (
    AGGREGATE_FUNCTIONS,
    CONDITIONAL_EXPRESSIONS,
    TRANSFORM_RANK,
    VALUE_SELECTING_WINDOW_FUNCTIONS,
    combine as _combine,
    is_aggregate as _is_aggregate,
    is_conditional as _is_conditional,
    transform_of as _transform_of,
)
from lineage.ir.model import (
    Boundary,
    BoundaryKind,
    Flow,
    IREdge,
    Mechanism,
    Node,
    Origin,
    Tier,
    Transform,
)
from lineage.ir.model import (
    NodeKind as IRNodeKind,
)
from lineage.parsing.generated.PlSqlParser import PlSqlParser
from lineage.parsing.plsql import Program, iter_contexts, source_slice
from lineage.parsing.rewrite import strip_unparseable_clauses
from lineage.resolution.dictionary import Dictionary, UnknownObjectError

DIALECT = "oracle"




@dataclass(frozen=True)
class VariableDecl:
    """A declared variable, and where its name is visible."""

    name: str  # bare, upper-cased
    qualified: str  # PACKAGE.NAME for package state, else the bare name
    scope: str  # parameter | local | package
    line: int
    has_default: bool = False  # `v_total NUMBER := 0` is assigned before any statement

    def as_node(self) -> Node:
        return Node(kind=IRNodeKind.VARIABLE, name=self.qualified)


@dataclass(frozen=True)
class RowSource:
    """A cursor or loop record whose fields come from a query's select list.

    `rec.email` in a cursor FOR loop is not a column of anything - it is a field of a
    record whose shape is the cursor's projection. Without this, `SET email = rec.email`
    binds `email` to whichever table is in scope and reports a column as its own source.
    """

    name: str
    query: str  # the SELECT text this row was fetched from
    line: int


@dataclass
class UnitScope:
    """Variables visible inside one program unit."""

    unit: str
    variables: dict[str, VariableDecl] = field(default_factory=dict)
    row_sources: dict[str, RowSource] = field(default_factory=dict)
    cursors: dict[str, str] = field(default_factory=dict)  # cursor name -> SELECT text
    correlations: dict[str, str] = field(
        default_factory=dict,
        metadata={
            "why": (
                "Qualifier -> relation for names that are in scope without appearing in "
                "any FROM clause. A trigger's :NEW and :OLD are the case: they name a row "
                "of the TRIGGERING table, which the statement being analysed does not "
                "mention at all. Without this, `WHERE cust_id = :NEW.cust_id` binds both "
                "operands to the updated table and the trigger's real input vanishes."
            )
        },
    )

    def lookup(self, name: str) -> VariableDecl | None:
        return self.variables.get(name.strip().upper())

    def row_source(self, name: str) -> RowSource | None:
        return self.row_sources.get(name.strip().upper())


# --- declarations --------------------------------------------------------------------


def _nested_unit_contexts() -> list[type]:
    kinds = ["Procedure_bodyContext", "Function_bodyContext"]
    return [c for c in (getattr(PlSqlParser, k, None) for k in kinds) if c is not None]


def _is_inside_nested_unit(ctx: Any, root: Any) -> bool:
    """True if ctx sits inside a procedure/function declared within root."""
    nested = tuple(_nested_unit_contexts())
    parent = ctx.parentCtx
    while parent is not None and parent is not root:
        if isinstance(parent, nested):
            return True
        parent = parent.parentCtx
    return False


def _declarations_of(ctx: Any, package: str | None) -> dict[str, VariableDecl]:
    """Variables declared directly in this unit — not in units nested inside it."""
    found: dict[str, VariableDecl] = {}

    parameter_ctx = getattr(PlSqlParser, "Parameter_nameContext", None)
    if parameter_ctx is not None:
        for node in iter_contexts(ctx, parameter_ctx):
            if _is_inside_nested_unit(node, ctx):
                continue
            name = str(node.getText()).upper()
            found[name] = VariableDecl(name, name, "parameter", node.start.line)

    declaration_ctx = getattr(PlSqlParser, "Variable_declarationContext", None)
    if declaration_ctx is not None:
        for node in iter_contexts(ctx, declaration_ctx):
            if _is_inside_nested_unit(node, ctx):
                continue
            identifier = node.identifier()
            if identifier is None:
                continue
            name = str(identifier.getText()).upper()
            scope = "package" if package else "local"
            qualified = f"{package}.{name}" if package else name
            default = getattr(node, "default_value_part", lambda: None)()
            found[name] = VariableDecl(
                name, qualified, scope, node.start.line, has_default=default is not None
            )

    return found


def collect_scopes(program: Program) -> dict[str, UnitScope]:
    """Variables visible in each program unit.

    Package-level state is visible to every procedure in the package body, which is what
    makes `b1_07` hard: a value written by one call is read by another with no parameter
    passing and nothing in either statement connecting them.
    """
    scopes: dict[str, UnitScope] = {}

    # Package state first, so procedures inside the body inherit it.
    package_state: dict[str, dict[str, VariableDecl]] = {}
    package_ctx = getattr(PlSqlParser, "Create_package_bodyContext", None)
    if package_ctx is not None:
        for body in iter_contexts(program.tree, package_ctx):
            name_node = body.package_name()
            if isinstance(name_node, list):
                name_node = name_node[0] if name_node else None
            if name_node is None:
                continue
            package = str(name_node.getText()).upper()
            package_state[package] = _declarations_of(body, package)

    unit_specs = [
        ("Create_procedure_bodyContext", "procedure_name"),
        ("Create_function_bodyContext", "function_name"),
        ("Procedure_bodyContext", "identifier"),
        ("Function_bodyContext", "identifier"),
    ]

    for context_name, accessor in unit_specs:
        context_class = getattr(PlSqlParser, context_name, None)
        if context_class is None:
            continue
        for ctx in iter_contexts(program.tree, context_class):
            name_node = getattr(ctx, accessor, lambda: None)()
            if isinstance(name_node, list):
                name_node = name_node[0] if name_node else None
            if name_node is None:
                continue
            unit = str(name_node.getText()).upper()

            scope = UnitScope(unit=unit)
            # Enclosing package state, if this unit lives in a package body.
            for package, state in package_state.items():
                if _within_package(ctx, package):
                    scope.variables.update(state)
            scope.variables.update(_declarations_of(ctx, None))
            # CURSORS FIRST (stress finding S4-02). `_collect_row_sources` has to resolve
            # `FOR rec IN c` through the cursor `c` was declared with, so the cursor map
            # must already exist when it runs. These two lines were the other way round.
            _collect_cursors(ctx, scope)
            _collect_row_sources(ctx, scope)
            scopes[unit] = scope

    return scopes


def _collect_row_sources(ctx: Any, scope: UnitScope) -> None:
    """Cursor FOR loop records, and plain FOR loop indices."""
    loop_param = getattr(PlSqlParser, "Cursor_loop_paramContext", None)
    if loop_param is None:
        return

    for param in iter_contexts(ctx, loop_param):
        record = param.record_name() if hasattr(param, "record_name") else None
        select = param.select_statement() if hasattr(param, "select_statement") else None
        if record is not None and select is not None:
            name = str(record.getText()).upper()
            scope.row_sources[name] = RowSource(name, source_slice(select), param.start.line)
            continue

        # `FOR rec IN c` - A DECLARED CURSOR RATHER THAN AN INLINE QUERY (stress finding
        # S4-02). Only the inline form was handled, so this loop registered no row source
        # at all and `rec.field` fell through to the generic binding below `_classify`.
        #
        # THE CONSEQUENCE WAS A WRONG EDGE, NOT A MISSING ONE, which is why it survived.
        # With no row source, `INSERT INTO tgt (amt) VALUES (rec.amt)` bound `rec.amt` to
        # the only relation in scope - THE TARGET - and emitted `TGT.AMT -> TGT.AMT`: a
        # self-edge that reads exactly like a legitimate accumulator, from a table the
        # cursor never mentioned. The s2 silent-failure shape, reached through a record.
        #
        # Both forms are ordinary Oracle and the declared one is the more common in real
        # code, because a named cursor is what you write when the query is long enough to
        # deserve a name - which is exactly when it is also deep enough to matter.
        cursor = param.cursor_name() if hasattr(param, "cursor_name") else None
        if record is not None and cursor is not None:
            query = scope.cursors.get(str(cursor.getText()).upper())
            if query is not None:
                name = str(record.getText()).upper()
                scope.row_sources[name] = RowSource(name, query, param.start.line)
            continue

        index = param.index_name() if hasattr(param, "index_name") else None
        if index is not None:
            # A plain FOR index is an ordinary local: it can appear in predicates and
            # decide which rows a statement reads.
            name = str(index.getText()).upper()
            scope.variables.setdefault(name, VariableDecl(name, name, "local", param.start.line))


def _collect_cursors(ctx: Any, scope: UnitScope) -> None:
    """Declared cursors, so FETCH targets can be bound to their select lists."""
    declaration = getattr(PlSqlParser, "Cursor_declarationContext", None)
    if declaration is None:
        return
    for node in iter_contexts(ctx, declaration):
        identifier = node.identifier()
        select = node.select_statement()
        if identifier is None or select is None:
            continue
        scope.cursors[str(identifier.getText()).upper()] = source_slice(select)


def _within_package(ctx: Any, package: str) -> bool:
    package_ctx = getattr(PlSqlParser, "Create_package_bodyContext", None)
    if package_ctx is None:
        return False
    parent = ctx.parentCtx
    while parent is not None:
        if isinstance(parent, package_ctx):
            name_node = parent.package_name()
            if isinstance(name_node, list):
                name_node = name_node[0] if name_node else None
            return name_node is not None and str(name_node.getText()).upper() == package
        parent = parent.parentCtx
    return False


# --- statement analysis --------------------------------------------------------------


@dataclass
class DefUseResult:
    edges: list[IREdge] = field(default_factory=list)
    # Boundary where this module knows the kind, prose where it does not: a def-use
    # finding about a variable is classified by `procedure._in_unit`, which also knows the
    # unit that a bare line number needs to become an identity. The union is the honest
    # type - claiming every entry is already classified would be a lie mypy would believe.
    unresolved: list[Boundary | str] = field(default_factory=list)


def _value_columns(expression: Any) -> list[Any]:
    """Columns that contribute the VALUE of an expression.

    Columns inside a nested `WHERE` are excluded: in
    `SET is_active = (SELECT MAX(status_code) FROM s WHERE s.cust_id = d.cust_id)`
    the correlation predicate decides WHICH row is read, not what value comes back.
    Counting it as a value source invents an edge saying a customer id determines an
    active flag - plausible-looking, and wrong.
    """
    excluded: set[int] = set()
    for where in expression.find_all(exp.Where):
        excluded.update(id(column) for column in where.find_all(exp.Column))
    return [c for c in expression.find_all(exp.Column) if id(c) not in excluded]


def _subscript_columns(expression: Any, scope: UnitScope) -> set[int]:
    """Columns that only INDEX a collection, by node id (stress finding S3-06).

    `l_batch(i).refund_amount` parses as `Dot(Anonymous(l_batch, [Column(i)]), refund_amount)`,
    so the subscript `i` is the ONLY `exp.Column` in it - the collection name is the
    function name and the field is a bare identifier. Every path that looks for value
    sources therefore found `i` and nothing else, and emitted `i -> l_adjusted`.

    **A subscript chooses WHICH element, exactly as a join key chooses which row.** None of
    the loop counter is in the number that comes out; incrementing it moves you to a
    different element rather than changing any value. This is the collection form of the
    argument D-5 made for join conditions and D-4 made for a window's ordering, and it
    reaches the same answer: no value edge.

    THE SCOPE LOOKUP IS WHAT MAKES IT SAFE. `pkg.f(amt)` is a real function call and its
    arguments really are value sources; only a call on a name that is a DECLARED VARIABLE
    is an index, because PL/SQL has no way to call a variable.

    Returns ids rather than names so a name used both as a subscript and as a value keeps
    its value edge - `l_batch(i).amt + i` is contrived, but excluding by name would quietly
    drop the second half of it.
    """
    found: set[int] = set()
    for call in expression.find_all(exp.Anonymous):
        name = str(call.this or "")
        if not name or scope.lookup(name) is None:
            continue  # a genuine function call: its arguments ARE value sources
        for argument in call.expressions:
            found.update(id(column) for column in argument.find_all(exp.Column))
    return found


def _subscript_only_names(expression_text: str, scope: UnitScope) -> set[str]:
    """Names appearing ONLY as collection subscripts in this expression (S3-06).

    The assignment path scans identifiers out of TEXT rather than an AST, so it needs names
    rather than node ids. Built on `_subscript_columns` rather than repeating the rule: two
    copies of one rule is how S2-08 happened, and this one would drift the same way.
    """
    try:
        parsed = sqlglot.parse_one(expression_text, dialect=DIALECT)
    except Exception:
        # An unparseable right-hand side is the caller's problem; here it just means no
        # subscript can be proven, and proving none is the safe direction.
        return set()

    indexes = _subscript_columns(parsed, scope)
    as_subscript = {c.name.upper() for c in parsed.find_all(exp.Column) if id(c) in indexes}
    elsewhere = {c.name.upper() for c in parsed.find_all(exp.Column) if id(c) not in indexes}
    return as_subscript - elsewhere


def _predicate_columns(expression: Any) -> list[Any]:
    """Columns inside a nested WHERE — filter influence rather than value flow.

    Delegates the per-predicate walk to `analysis.predicates`, so a join condition inside
    a correlated subquery in a SET clause is structural here too (D-5). Getting that wrong
    in one of the several places a predicate can hide is how S2-14 happened.
    """
    found: list[Any] = []
    for where in expression.find_all(exp.Where):
        found.extend(predicate_columns(where))
    return found


def _relations_in(statement: exp.Expression, dictionary: Dictionary) -> dict[str, str]:
    """Alias (and bare name) -> resolved relation name, for everything in scope.

    INTO targets are excluded. SQLGlot models `SELECT x INTO v_days FROM t` with v_days
    as a Table node, and treating it as a relation makes every filter edge point at a
    variable masquerading as a table.
    """
    into = statement.args.get("into") if isinstance(statement, exp.Select) else None
    into_tables = {id(t) for t in into.find_all(exp.Table)} if into is not None else set()

    relations: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        if not table.name or id(table) in into_tables:
            continue
        resolved = dictionary.resolve(table.name)
        real = resolved.name or table.name.upper()
        relations[table.name.upper()] = real
        if table.alias:
            relations[table.alias.upper()] = real
    return relations


def _classify(
    column: exp.Column, scope: UnitScope, relations: dict[str, str], dictionary: Dictionary
) -> tuple[str, str] | None:
    """Resolve a bare identifier to ('variable', qualified) or ('column', TABLE.COLUMN).

    Variables are checked FIRST. To a SQL parser `v_cutoff` looks exactly like a column,
    and binding it to whichever table is in scope is the silent failure this module
    exists to prevent.
    """
    name = column.name.upper()

    # `rec.email` is a record field, not a column. Resolve it through the query the row
    # came from, or the value appears to be its own source.
    if column.table:
        row = scope.row_source(column.table)
        if row is not None:
            resolved = _field_of_row(row, name, dictionary)
            if resolved is not None:
                return resolved
            return None

    if not column.table:
        declaration = scope.lookup(name)
        if declaration is not None:
            return ("variable", declaration.qualified)

    qualifier = column.table.upper() if column.table else None
    candidates = (
        [relations[qualifier]]
        if qualifier and qualifier in relations
        else list(dict.fromkeys(relations.values()))
    )

    for relation in candidates:
        try:
            if name in dictionary.columns_of(relation):
                return ("column", f"{relation}.{name}")
        except UnknownObjectError:
            continue

    return None


def _field_of_row(
    row: RowSource, field_name: str, dictionary: Dictionary
) -> tuple[str, str] | None:
    """Resolve one field of a cursor record back to the column it was selected from.

    Delegates to `band0.resolve_projection` (stress finding S4-02). This function used to
    read the cursor query's OUTERMOST select list and bind each column straight to a base
    relation, which worked only when the cursor selected directly from tables. A cursor
    whose query is a CTE chain - which is most cursors worth naming - resolved nothing, and
    resolved it SILENTLY.

    Band 0 already traverses CTEs, joins, renames and views to answer this exact question.
    Teaching a second copy to do the same is how S2-08 happened.
    """
    resolved = resolve_projection(row.query, field_name, dictionary)
    if resolved is None:
        return None
    relation, column_name, _ = resolved
    return ("column", f"{relation}.{column_name}")


def _projection_of(query: str, dictionary: Dictionary) -> tuple[list[Any] | None, dict[str, str]]:
    """Parse a cursor query and return its select list plus its relations."""
    text = query.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    try:
        parsed = sqlglot.parse_one(text, dialect=DIALECT)
    except Exception:
        return None, {}
    if not isinstance(parsed, exp.Select):
        return None, {}
    return parsed.selects, _relations_in(parsed, dictionary)


def _node_for(kind: str, name: str) -> Node:
    return Node(kind=IRNodeKind.VARIABLE if kind == "variable" else IRNodeKind.COLUMN, name=name)


def _edge(
    source: Node,
    target: Node,
    flow: Flow,
    transform: Transform,
    origin: Origin,
    guard: str | None,
    mechanism: Mechanism,
    band: int = 1,
) -> IREdge:
    return IREdge(
        source=source,
        target=target,
        flow=flow,
        transform=transform,
        band=band,
        mechanism=mechanism,
        tier=Tier.A,
        guard=guard or None,
        origin=origin,
    )


def _band_for(source_kind: str, target_is_variable: bool) -> int:
    """Band 1 if the edge's path touches a variable; band 0 otherwise (ADR-0001 §6)."""
    return 1 if (source_kind == "variable" or target_is_variable) else 0


def analyse_statement(
    node: CfgNode,
    cfg: Cfg,
    scope: UnitScope,
    dictionary: Dictionary,
    unit: str,
    dynamic: Resolution | None = None,
) -> DefUseResult:
    """Extract def-use edges from one statement."""
    result = DefUseResult()
    if node.ctx is None:
        return result

    origin = Origin(unit=unit, line=node.line)
    guards = cfg.guards_reaching(node.id)
    guard = " AND ".join(guards) if guards else None
    carriers = dynamic.carriers if dynamic else set()

    # Assignment and FETCH are PL/SQL, not SQL: they come from the parse tree.
    if node.statement_kind == "assignment_statement":
        return _analyse_assignment(node, scope, dictionary, origin, guard, carriers)
    if node.statement_kind == "fetch_statement":
        return _analyse_fetch(node, scope, dictionary, origin, guard)

    # A recovered dynamic statement is analysed exactly like a written one - same parser,
    # same dispatch below. What changes is the TEXT, not the treatment.
    text = source_slice(node.ctx)
    recovered = dynamic is not None and node.line in dynamic.recovered
    if dynamic is not None and recovered:
        text = dynamic.recovered[node.line]
    try:
        statement: Any = sqlglot.parse_one(strip_unparseable_clauses(text), dialect=DIALECT)
    except Exception:
        result.unresolved.append(
            Boundary(
                kind=BoundaryKind.PARSE_FAILURE,
                # Qualified with the unit by `procedure._in_unit`, which knows it.
                subject=str(node.line),
                detail=f"line {node.line}: SQLGlot could not parse the statement",
                line=node.line,
            )
        )
        return result

    # Correlations go LAST so the statement's own relations win when a bare, unqualified
    # name has to be resolved. `WHERE cust_id = :NEW.cust_id` has one operand of each
    # kind, and putting the correlation first binds BOTH to the triggering table - which
    # collapses them into a single self-edge and loses the predicate's other half.
    relations = {**_relations_in(statement, dictionary), **scope.correlations}

    if isinstance(statement, exp.Select) and statement.args.get("into") is not None:
        result = _analyse_select_into(statement, scope, relations, dictionary, origin, guard)
    elif isinstance(statement, exp.Update):
        result = _analyse_update(statement, scope, relations, dictionary, origin, guard)
    elif isinstance(statement, exp.Insert):
        result = _analyse_insert_filter(statement, scope, relations, dictionary, origin, guard)

    # A statement we could only read because constant propagation recovered it is band 2,
    # whatever its own shape (ADR-0001 §6). The band describes the PATH, and this path ran
    # through EXECUTE IMMEDIATE - being able to see through it does not make it easier,
    # it makes it the band's cheap win.
    if recovered:
        result.edges = [edge.model_copy(update={"band": 2}) for edge in result.edges]

    return result


def _row_field_edges(
    expression_text: str,
    scope: UnitScope,
    dictionary: Dictionary,
    target: VariableDecl,
    outer: Transform,
    origin: Origin,
    guard: str | None,
) -> list[IREdge]:
    """Cursor record fields read on the right-hand side of an assignment (S4-07).

    `v_gross := rec.gross_total` is the commonest line in a cursor loop and produced NO
    EDGE AT ALL. `_analyse_assignment` scans identifiers out of TEXT and looks each one up
    in the declared variables; `REC` and `GROSS_TOTAL` are neither of them variables, so
    both were discarded and the chain from the cursor's query into the loop's variables
    simply had no first link.

    **The damage was not a missing edge but a chain that appeared to start at a variable.**
    Stress 4's `s4_cursor_ladder` emits eight edges and every one of them begins at a
    local: `V_CUST_ID -> FCT_REVENUE_PART.CUST_ID` is there and
    `STG_ORDERS.CUST_ID -> V_CUST_ID` is not. A variable is a legitimate source in this IR
    (ADR-0001 §3), so the output reads as complete lineage whose origin happens to be
    memory - which is a sentence the analyser is entitled to say and here was not true.

    THE TRANSFORM COMES WITH IT, and that is one fact rather than two (S4-08). Resolving
    `rec.gross_total` means resolving what happened to the value on the way: the field is
    `SUM(j.gross_amount)` two scopes inside the cursor's query, so the edge is `aggregated`
    and not `identity`. Emitting the column while dropping the aggregation would be a
    wrong transform, which ADR-0001 §4 counts as a miss on BOTH sides - so splitting these
    into two commits would have meant shipping a known-wrong answer in between.
    """
    try:
        parsed: Any = sqlglot.parse_one(expression_text, dialect=DIALECT)
    except Exception:
        return []

    edges: list[IREdge] = []
    seen: set[str] = set()
    for column in _value_columns(parsed):
        qualifier = (column.table or "").upper()
        if not qualifier:
            continue
        row = scope.row_source(qualifier)
        if row is None:
            continue
        resolved = resolve_projection(row.query, column.name, dictionary)
        if resolved is None:
            continue
        relation, name, inner = resolved
        source_name = f"{relation}.{name}"
        if source_name in seen:
            continue
        seen.add(source_name)
        edges.append(
            _edge(
                Node(kind=IRNodeKind.COLUMN, name=source_name),
                target.as_node(),
                Flow.VALUE,
                _combine(inner, outer),
                origin,
                guard,
                Mechanism.DEF_USE,
            )
        )
    return edges


def _analyse_assignment(
    node: CfgNode,
    scope: UnitScope,
    dictionary: Dictionary,
    origin: Origin,
    guard: str | None,
    carriers: set[str] = frozenset(),  # type: ignore[assignment]
) -> DefUseResult:
    """`v := expr`. The definition that carries a value between statements.

    Unless the variable carries a STATEMENT rather than a value — see `analysis.dynamic`.
    `v_sql := 'UPDATE ' || p_column` is a true def-use fact and not a lineage fact, and
    emitting it claims a column name determines the column's data.
    """
    result = DefUseResult()
    ctx = node.ctx

    inner = None
    for candidate in iter_contexts(ctx, PlSqlParser.Assignment_statementContext):
        inner = candidate
        break
    if inner is None:
        return result

    target_ctx = inner.general_element()
    if target_ctx is None:
        return result
    target_name = str(target_ctx.getText()).upper()
    if target_name in carriers:
        return result
    target_decl = scope.lookup(target_name)
    if target_decl is None:
        result.unresolved.append(f"line {node.line}: assignment to undeclared {target_name}")
        return result

    expression_text = source_slice(inner.expression()) if inner.expression() else ""
    transform = _assignment_transform(expression_text)

    subscripts = _subscript_only_names(expression_text, scope)

    # A cursor record's fields, which the identifier scan below cannot see: `rec` is not a
    # declared variable and `gross_total` is not a column of anything (S4-07).
    result.edges.extend(
        _row_field_edges(
            expression_text, scope, dictionary, target_decl, transform, origin, guard
        )
    )

    for name in _identifiers_in(expression_text):
        if name in carriers:
            # `v_rows := DBMS_SQL.EXECUTE(v_cursor)` - a cursor handle is API plumbing,
            # not a value. The row count genuinely derives from the statement's effect,
            # which no static edge can express.
            continue
        if name in subscripts:
            # `l_adjusted := l_batch(i).refund_amount * 1.2` - `i` picks the element and
            # contributes none of the value (S3-06).
            continue
        source_decl = scope.lookup(name)
        if source_decl is None:
            continue
        result.edges.append(
            _edge(
                source_decl.as_node(),
                target_decl.as_node(),
                Flow.VALUE,
                transform,
                origin,
                guard,
                Mechanism.DEF_USE,
            )
        )

    return result


def _analyse_fetch(
    node: CfgNode,
    scope: UnitScope,
    dictionary: Dictionary,
    origin: Origin,
    guard: str | None,
) -> DefUseResult:
    """`FETCH cursor INTO v1, v2, v3` — bound POSITIONALLY.

    THE TRAP. The fetch targets bind by position, not by name. In `b1_05` the names
    happen to line up, which is exactly what makes name-matching dangerous: it gets the
    right answer on this code and the wrong answer on code where they do not, with no
    symptom either way.

    SQLGlot cannot parse FETCH, so this reads the parse tree directly - which is the
    correct division of labour rather than a workaround.
    """
    result = DefUseResult()
    ctx = node.ctx

    fetch_ctx = getattr(PlSqlParser, "Fetch_statementContext", None)
    if fetch_ctx is None:
        return result
    inner = next(iter(iter_contexts(ctx, fetch_ctx)), None)
    if inner is None:
        return result

    cursor = inner.cursor_name()
    if cursor is None:
        return result
    query = scope.cursors.get(str(cursor.getText()).upper())
    if query is None:
        result.unresolved.append(f"line {node.line}: FETCH from an undeclared cursor")
        return result

    targets = [str(v.getText()).upper() for v in inner.variable_or_collection()]
    projection, relations = _projection_of(query, dictionary)
    if projection is None:
        result.unresolved.append(f"line {node.line}: cursor query did not parse")
        return result

    if len(targets) != len(projection):
        result.unresolved.append(
            f"line {node.line}: FETCH arity mismatch "
            f"({len(projection)} selected into {len(targets)} targets)"
        )
        return result

    for target_name, item in zip(targets, projection, strict=True):
        declaration = scope.lookup(target_name)
        if declaration is None:
            result.unresolved.append(f"line {node.line}: FETCH into undeclared {target_name}")
            continue
        own = _transform_of(item)
        for column in _value_columns(item):
            table = column.table.upper() if column.table else None
            relation = relations.get(table) if table else next(iter(relations.values()), None)
            if relation is None:
                continue
            try:
                if column.name.upper() not in dictionary.columns_of(relation):
                    continue
            except UnknownObjectError:
                continue
            result.edges.append(
                _edge(
                    Node(kind=IRNodeKind.COLUMN, name=f"{relation}.{column.name.upper()}"),
                    declaration.as_node(),
                    Flow.VALUE,
                    own,
                    origin,
                    guard,
                    Mechanism.DEF_USE,
                )
            )

    return result


def _assignment_transform(text: str) -> Transform:
    """Classify an assignment's right-hand side.

    Text-based because an assignment RHS is PL/SQL, not SQL, and handing it to a SQL
    parser would be the same category error this module warns about.
    """
    upper = text.upper()
    if re.search(r"\bCASE\b", upper):
        return Transform.CONDITIONAL
    if re.search(r"\b(SUM|COUNT|AVG|MIN|MAX)\s*\(", upper):
        return Transform.AGGREGATED
    if re.fullmatch(r"[A-Z0-9_$#.]+", upper.strip()):
        return Transform.IDENTITY
    return Transform.DERIVED


IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_$#]*")

# Words that look like identifiers but are PL/SQL syntax or built-ins.
NOT_IDENTIFIERS = {
    "SYSDATE",
    "SYSTIMESTAMP",
    "NULL",
    "TRUE",
    "FALSE",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "AND",
    "OR",
    "NOT",
    "NVL",
    "TRUNC",
    "ROUND",
    "TO_DATE",
    "TO_CHAR",
    "TO_NUMBER",
    "SELECT",
    "FROM",
    "WHERE",
    "MOD",
    "ABS",
}


def _identifiers_in(text: str) -> list[str]:
    return [
        match.group(0).upper()
        for match in IDENTIFIER.finditer(text)
        if match.group(0).upper() not in NOT_IDENTIFIERS
    ]


def _analyse_select_into(
    statement: Any,
    scope: UnitScope,
    relations: dict[str, str],
    dictionary: Dictionary,
    origin: Origin,
    guard: str | None,
) -> DefUseResult:
    """`SELECT ... INTO v`. Binds POSITIONALLY, like every other INTO in PL/SQL."""
    result = DefUseResult()

    into = statement.args["into"]
    targets = [t.name.upper() for t in into.find_all(exp.Identifier)] or [into.this.name.upper()]
    projections = statement.selects

    if len(targets) != len(projections):
        result.unresolved.append(
            f"line {origin.line}: INTO arity mismatch "
            f"({len(projections)} selected into {len(targets)} targets)"
        )
        return result

    for target_name, projection in zip(targets, projections, strict=True):
        declaration = scope.lookup(target_name)
        if declaration is None:
            result.unresolved.append(f"line {origin.line}: INTO undeclared {target_name}")
            continue
        own = _transform_of(projection)
        unresolved_here: list[Any] = []
        for column in _value_columns(projection):
            classified = _classify(column, scope, relations, dictionary)
            if classified is None:
                unresolved_here.append(column)
                continue
            kind, name = classified
            result.edges.append(
                _edge(
                    _node_for(kind, name),
                    declaration.as_node(),
                    Flow.VALUE,
                    own,
                    origin,
                    guard,
                    Mechanism.DEF_USE if kind == "variable" else Mechanism.AST,
                    band=1,  # the target is a variable, so the path is band 1
                )
            )

        # A COLUMN COMPUTED OR RENAMED IN A DERIVED TABLE (stress finding S4-05).
        #
        # `_classify` binds a name through a FLAT alias -> relation map, so
        # `SELECT g.total BULK COLLECT INTO b FROM (SELECT SUM(s.amt) AS total ...) g`
        # had nothing to bind `total` to - it is a column of no dictionary relation - and
        # the collection lost its source entirely.
        #
        # Note which half was silent and which was luck: `g.sid` resolved, because SID
        # HAPPENS to be a column of the only relation in scope and the fallback found it.
        # The statement's own alias was being discarded and the answer was right by
        # coincidence of naming - the s2 shape again, and it is why fixing the visibly
        # broken half alone would have left a guess behind it.
        #
        # Band 0 traverses derived tables, CTEs, joins and views to answer exactly this, so
        # the projection is resolved there rather than teaching this module a second
        # traversal - the same delegation S4-02 made for a cursor record, for the same
        # reason S2-08 is on the register.
        if unresolved_here:
            probe = statement.copy()
            probe.set("into", None)
            resolved = resolve_projection(
                probe.sql(dialect=DIALECT), projection.alias_or_name, dictionary
            )
            if resolved is None:
                for column in unresolved_here:
                    result.unresolved.append(f"line {origin.line}: unresolved {column.name}")
            else:
                relation, column_name, inner = resolved
                result.edges.append(
                    _edge(
                        Node(kind=IRNodeKind.COLUMN, name=f"{relation}.{column_name}"),
                        declaration.as_node(),
                        Flow.VALUE,
                        _combine(inner, own),
                        origin,
                        guard,
                        Mechanism.AST,
                        band=1,
                    )
                )

    # A predicate on the read relation decides which row the variable receives.
    result.edges += _filter_edges_for(
        statement.args.get("where"),
        scope,
        relations,
        dictionary,
        _read_relation(statement, relations),
        origin,
        guard,
        result,
    )
    return result


def _read_relation(statement: Any, relations: dict[str, str]) -> str | None:
    # sqlglot stores the FROM clause under "from_", not "from".
    source = statement.args.get("from_") or statement.args.get("from")
    if source is None:
        return next(iter(relations.values()), None)
    table = source.this if isinstance(source, exp.From) else source
    if isinstance(table, exp.Table) and table.name:
        return relations.get(table.name.upper(), table.name.upper())
    return next(iter(relations.values()), None)


def _analyse_update(
    statement: Any,
    scope: UnitScope,
    relations: dict[str, str],
    dictionary: Dictionary,
    origin: Origin,
    guard: str | None,
) -> DefUseResult:
    """`UPDATE t SET col = expr WHERE ...`.

    Not handled by the band-0 analyser, which only claims INSERT and MERGE. Both the SET
    expressions and the predicate matter: one decides the value, the other decides which
    rows receive it.
    """
    result = DefUseResult()

    target = statement.this
    target_name = None
    if isinstance(target, exp.Table) and target.name:
        target_name = relations.get(target.name.upper(), target.name.upper())
    if target_name is None:
        result.unresolved.append(f"line {origin.line}: UPDATE target not resolved")
        return result

    for setter in statement.args.get("expressions") or []:
        if not isinstance(setter, exp.EQ):
            continue
        column_name = setter.this.name.upper()
        own = _transform_of(setter.expression)
        target_node = Node(kind=IRNodeKind.COLUMN, name=f"{target_name}.{column_name}")

        # A correlation predicate inside the assigned expression selects rows; it does
        # not supply the value. Its columns become filter edges on the updated relation.
        for column in _predicate_columns(setter.expression):
            classified = _classify(column, scope, relations, dictionary)
            if classified is None:
                continue
            kind, name = classified
            result.edges.append(
                _edge(
                    _node_for(kind, name),
                    Node(kind=IRNodeKind.RELATION, name=target_name),
                    Flow.FILTER,
                    Transform.IDENTITY,
                    origin,
                    guard,
                    Mechanism.DEF_USE if kind == "variable" else Mechanism.AST,
                    band=_band_for(kind, target_is_variable=False),
                )
            )

        for column in _value_columns(setter.expression):
            classified = _classify(column, scope, relations, dictionary)
            if classified is None:
                result.unresolved.append(f"line {origin.line}: unresolved {column.name}")
                continue
            kind, name = classified
            result.edges.append(
                _edge(
                    _node_for(kind, name),
                    target_node,
                    Flow.VALUE,
                    own,
                    origin,
                    guard,
                    Mechanism.DEF_USE if kind == "variable" else Mechanism.AST,
                    band=_band_for(kind, target_is_variable=False),
                )
            )

    result.edges += _filter_edges_for(
        statement.args.get("where"),
        scope,
        relations,
        dictionary,
        target_name,
        origin,
        guard,
        result,
    )
    return result


def _analyse_insert_filter(
    statement: Any,
    scope: UnitScope,
    relations: dict[str, str],
    dictionary: Dictionary,
    origin: Origin,
    guard: str | None,
) -> DefUseResult:
    """Variable predicates on an INSERT ... SELECT, and the whole of an INSERT ... VALUES.

    For `INSERT ... SELECT` the column-to-column lineage is the band-0 analyser's job.
    What it cannot do is recognise `v_cutoff` — a variable governing which rows load.

    For `INSERT ... VALUES` there is no select list to trace and band 0 has nothing to
    work with, because an unqualified name in a VALUES list is a PL/SQL variable far more
    often than a column and only this module can tell the two apart. So the whole
    statement is handled here (stress finding S1-04, decision D-1).
    """
    result = DefUseResult()

    target = statement.this
    if isinstance(target, exp.Schema):
        target = target.this
    if not isinstance(target, exp.Table) or not target.name:
        return result
    target_name = relations.get(target.name.upper(), target.name.upper())

    values = statement.expression
    if isinstance(values, exp.Values):
        return _analyse_insert_values(
            statement, values, target_name, scope, relations, dictionary, origin, guard
        )

    select = statement.expression
    if not isinstance(select, exp.Select):
        return result

    # find_all includes the select itself, so listing it separately would double-count.
    for scoped in select.find_all(exp.Select):
        where = scoped.args.get("where")
        if where is None:
            continue
        for column in predicate_columns(where):
            classified = _classify(column, scope, relations, dictionary)
            if classified is None or classified[0] != "variable":
                continue  # column-side filters belong to the band-0 analyser
            result.edges.append(
                _edge(
                    _node_for(*classified),
                    Node(kind=IRNodeKind.RELATION, name=target_name),
                    Flow.FILTER,
                    Transform.IDENTITY,
                    origin,
                    guard,
                    Mechanism.DEF_USE,
                )
            )
    return result


def _analyse_insert_values(
    statement: Any,
    values: Any,
    target_name: str,
    scope: UnitScope,
    relations: dict[str, str],
    dictionary: Dictionary,
    origin: Origin,
    guard: str | None,
) -> DefUseResult:
    """`INSERT INTO t (a, b) VALUES (v_cust_id, SYSDATE)` — stress finding S1-04, D-1.

    This used to be refused outright, with the reason *"INSERT ... VALUES carries no
    column lineage from a relation"*. That is true of relations and false of the statement:
    ADR-0001 §3 makes variables first-class nodes, so `v_cust_id -> tmp_recent.cust_id` is
    ordinary lineage and writing a temp table row-by-row from cursor variables is ordinary
    PL/SQL. The refusal was the one false abstention in the signed phase-0 measurement.

    Binding is POSITIONAL against the target column list, the same rule a UNION arm and a
    `FETCH INTO` already follow. `SYSDATE`, `USER` and literals supply values from nowhere
    and produce no edge — the literal rule, unchanged.

    A VALUES list has no `WHERE`, so there are no filter edges to find here. A row-level
    write also carries no *relation* source: nothing decides which rows are read, because
    none are.
    """
    result = DefUseResult()

    schema = statement.this
    if not isinstance(schema, exp.Schema) or not schema.expressions:
        # Without an explicit column list, positional binding has nothing to bind TO. The
        # target's dictionary order would be a guess, and a wrong guess here writes a
        # value into the wrong column - a plausible, readable, false edge.
        result.unresolved.append(
            f"line {origin.line}: INSERT ... VALUES without a column list - "
            f"positional binding needs the target's column order stated"
        )
        return result

    columns = [c.name.upper() for c in schema.expressions]

    rows = list(values.expressions)
    if not rows:
        return result

    for row in rows:
        items = list(row.expressions)
        for index, column in enumerate(columns):
            if index >= len(items):
                break
            item = items[index]
            target = Node(kind=IRNodeKind.COLUMN, name=f"{target_name}.{column}")
            indexes = _subscript_columns(item, scope)
            for reference in item.find_all(exp.Column):
                if id(reference) in indexes:
                    # `VALUES (l_batch(i).product_id, ...)` - the subscript is not a source
                    # of the column it indexes into (S3-06).
                    continue
                classified = _classify(reference, scope, relations, dictionary)
                if classified is None:
                    result.unresolved.append(
                        f"line {origin.line}: unresolved {reference.name} in VALUES"
                    )
                    continue
                kind, name = classified
                result.edges.append(
                    _edge(
                        _node_for(kind, name),
                        target,
                        Flow.VALUE,
                        _transform_of(item),
                        origin,
                        guard,
                        Mechanism.DEF_USE,
                        band=_band_for(kind, target_is_variable=False),
                    )
                )
    return result


def _filter_edges_for(
    where: Any,
    scope: UnitScope,
    relations: dict[str, str],
    dictionary: Dictionary,
    target_relation: str | None,
    origin: Origin,
    guard: str | None,
    result: DefUseResult,
) -> list[IREdge]:
    """Every operand of a predicate influences which rows are selected."""
    if where is None or target_relation is None:
        return []

    edges: list[IREdge] = []
    target = Node(kind=IRNodeKind.RELATION, name=target_relation)

    for column in predicate_columns(where):
        classified = _classify(column, scope, relations, dictionary)
        if classified is None:
            result.unresolved.append(f"line {origin.line}: unresolved {column.name}")
            continue
        kind, name = classified
        edges.append(
            _edge(
                _node_for(kind, name),
                target,
                Flow.FILTER,
                Transform.IDENTITY,
                origin,
                guard,
                Mechanism.DEF_USE if kind == "variable" else Mechanism.AST,
                band=_band_for(kind, target_is_variable=False),
            )
        )
    return edges


def definitions_and_uses(node: CfgNode, scope: UnitScope) -> tuple[set[str], set[str]]:
    """Variables this statement defines, and variables it reads.

    Names only — the edges themselves are built elsewhere. This feeds reaching-definitions
    analysis, which asks a different question: not *what* flows, but *whether anything
    was assigned before the read*.
    """
    defined: set[str] = set()
    used: set[str] = set()
    if node.ctx is None:
        return defined, used

    text = source_slice(node.ctx)

    if node.statement_kind == "assignment_statement":
        inner = next(iter(iter_contexts(node.ctx, PlSqlParser.Assignment_statementContext)), None)
        if inner is not None:
            target = inner.general_element()
            if target is not None and scope.lookup(str(target.getText())) is not None:
                defined.add(str(target.getText()).upper())
            expression = inner.expression()
            if expression is not None:
                for name in _identifiers_in(source_slice(expression)):
                    if scope.lookup(name) is not None:
                        used.add(name)
        return defined, used

    if node.statement_kind == "fetch_statement":
        inner = next(iter(iter_contexts(node.ctx, PlSqlParser.Fetch_statementContext)), None)
        if inner is not None:
            for target in inner.variable_or_collection():
                name = str(target.getText()).upper()
                if scope.lookup(name) is not None:
                    defined.add(name)
        return defined, used

    # SQL statements: an INTO target is a definition, everything else a read.
    try:
        statement: Any = sqlglot.parse_one(strip_unparseable_clauses(text), dialect=DIALECT)
    except Exception:
        return defined, used

    into = statement.args.get("into") if isinstance(statement, exp.Select) else None
    into_names: set[str] = set()
    if into is not None:
        into_names = {i.name.upper() for i in into.find_all(exp.Identifier)}
        for name in into_names:
            if scope.lookup(name) is not None:
                defined.add(name)

    for column in statement.find_all(exp.Column):
        if column.table:
            continue
        name = column.name.upper()
        if name in into_names:
            continue
        if scope.lookup(name) is not None:
            used.add(name)

    return defined, used


def analyse_unit(
    cfg: Cfg,
    scope: UnitScope,
    dictionary: Dictionary,
    dynamic: Resolution | None = None,
) -> DefUseResult:
    """Def-use edges for one program unit.

    ``dynamic`` carries the dynamic-SQL resolution for the whole source (T3.2): which
    variables are statement carriers rather than data, and what text each decidable site
    resolved to. Passing None analyses the unit as if no dynamic SQL existed, which is
    what the pre-T3.2 behaviour was.
    """
    combined = DefUseResult()
    seen: set[tuple[Any, ...]] = set()

    for node in cfg.nodes.values():
        if node.kind is not NodeKind.STATEMENT:
            continue
        result = analyse_statement(node, cfg, scope, dictionary, cfg.unit, dynamic)
        for edge in result.edges:
            # Deduplicate on ledger identity, not the match key: two facts that differ
            # by guard or origin are genuinely different and both belong.
            if edge.identity() in seen:
                continue
            seen.add(edge.identity())
            combined.edges.append(edge)
        combined.unresolved.extend(result.unresolved)

    combined.unresolved = list(dict.fromkeys(combined.unresolved))
    return combined
