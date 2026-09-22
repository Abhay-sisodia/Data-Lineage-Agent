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

from lineage.analysis.band0 import resolve_projection, resolve_query_influence
from lineage.analysis.cfg import Cfg, CfgNode, NodeKind
from lineage.analysis.dynamic import Resolution
from lineage.analysis.predicates import predicate_columns
from lineage.dialects import fold, for_name
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
    FilterPhase,
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
from lineage.parsing.frontend import Frontend
from lineage.parsing.plsql import Program
from lineage.parsing.rewrite import strip_unparseable_clauses
from lineage.resolution.dictionary import Dictionary, UnknownObjectError

# The dialect is not a constant here any more. It travels on the captured dictionary,
# because a dictionary belongs to one database and already reaches every resolver in this
# module - see `lineage.dialects.base` for what the seam holds and what it deliberately
# does not. `AnalysisConfig.dialect` stays the declared authority and the entry points
# check the two agree.




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
    dialect: str = "oracle"
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
        return self.variables.get(fold(name.strip(), self.dialect))

    def row_source(self, name: str) -> RowSource | None:
        return self.row_sources.get(fold(name.strip(), self.dialect))


# --- declarations --------------------------------------------------------------------
#
# Everything in this section used to reach into the generated Oracle grammar by class
# name - fourteen context classes across five modules, with "what counts as a unit" copied
# into four of them. It now asks `program.frontend`, which answers the same questions for
# whichever dialect parsed the program (A3). No behaviour changed; the measurement says so.


def _declarations_of(
    frontend: Frontend, ctx: Any, package: str | None, dialect: str
) -> dict[str, VariableDecl]:
    """Variables declared directly in this unit — not in units nested inside it."""
    found: dict[str, VariableDecl] = {}

    for parameter in frontend.parameters(ctx):
        name = fold(parameter.name, dialect)
        found[name] = VariableDecl(name, name, "parameter", parameter.line)

    for variable in frontend.variables(ctx):
        name = fold(variable.name, dialect)
        scope = "package" if package else "local"
        qualified = f"{package}.{name}" if package else name
        found[name] = VariableDecl(
            name, qualified, scope, variable.line, has_default=variable.has_default
        )

    return found


def collect_scopes(program: Program, dialect: str = "oracle") -> dict[str, UnitScope]:
    """Variables visible in each program unit.

    Package-level state is visible to every procedure in the package body, which is what
    makes `b1_07` hard: a value written by one call is read by another with no parameter
    passing and nothing in either statement connecting them.
    """
    frontend = _frontend_of(program, dialect)
    scopes: dict[str, UnitScope] = {}

    # Package state first, so procedures inside the body inherit it.
    package_state: dict[str, dict[str, VariableDecl]] = {}
    for package_name, body in frontend.package_bodies(program.tree):
        package = fold(package_name, dialect)
        package_state[package] = _declarations_of(frontend, body, package, dialect)

    for unit_ref in frontend.units(program.tree):
        unit = fold(unit_ref.name, dialect)
        ctx = unit_ref.ctx

        scope = UnitScope(unit=unit, dialect=dialect)
        # Enclosing package state, if this unit lives in a package body.
        enclosing = frontend.enclosing_package(ctx)
        if enclosing is not None:
            state = package_state.get(fold(enclosing, dialect))
            if state:
                scope.variables.update(state)
        scope.variables.update(_declarations_of(frontend, ctx, None, dialect))
        # CURSORS FIRST (stress finding S4-02). `_collect_row_sources` has to resolve
        # `FOR rec IN c` through the cursor `c` was declared with, so the cursor map
        # must already exist when it runs. These two lines were the other way round.
        _collect_cursors(frontend, ctx, scope)
        _collect_row_sources(frontend, ctx, scope)
        scopes[unit] = scope

    return scopes


def _frontend_of(program: Program, dialect: str) -> Frontend:
    """The front end that parsed this program - or, for a `Program` built before the field
    existed, the one the dialect names."""
    frontend = getattr(program, "frontend", None)
    if frontend is not None:
        return frontend  # type: ignore[no-any-return]
    return for_name(dialect).frontend


def _collect_row_sources(frontend: Frontend, ctx: Any, scope: UnitScope) -> None:
    """Cursor FOR loop records, and plain FOR loop indices."""
    for param in frontend.loop_params(ctx):
        if param.record is not None and param.query is not None:
            name = fold(param.record, scope.dialect)
            scope.row_sources[name] = RowSource(name, param.query, param.line)
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
        if param.record is not None and param.cursor is not None:
            query = scope.cursors.get(fold(param.cursor, scope.dialect))
            if query is not None:
                name = fold(param.record, scope.dialect)
                scope.row_sources[name] = RowSource(name, query, param.line)
            continue

        if param.index is not None:
            # A plain FOR index is an ordinary local: it can appear in predicates and
            # decide which rows a statement reads.
            name = fold(param.index, scope.dialect)
            scope.variables.setdefault(name, VariableDecl(name, name, "local", param.line))


def _collect_cursors(frontend: Frontend, ctx: Any, scope: UnitScope) -> None:
    """Declared cursors, so FETCH targets can be bound to their select lists."""
    for cursor in frontend.cursors(ctx):
        scope.cursors[fold(cursor.name, scope.dialect)] = cursor.query


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


@dataclass(frozen=True)
class Subscripted:
    """What a collection subscript takes away, and what it puts back.

    The two halves are returned TOGETHER because separating them is the defect. See
    `_subscripted`.
    """

    # Columns that only index a collection, by node id. Never value or filter sources.
    index_ids: frozenset[int]
    # The collections those subscripts read, as declarations. These ARE the sources.
    collections: tuple[Any, ...]


def _subscripted(expression: Any, scope: UnitScope) -> Subscripted:
    """A collection subscript: the index is not a source, and the collection is.

    Stress finding S3-06 (the index) and S4-10 (the collection), which are one fact stated
    from two directions and were fixed nine days apart.

    `l_batch(i).refund_amount` parses as `Dot(Anonymous(l_batch, [Column(i)]), refund_amount)`,
    so the subscript `i` is the ONLY `exp.Column` in it - the collection name is the
    function name and the field is a bare identifier. Every path that looks for value
    sources therefore found `i` and nothing else, and emitted `i -> l_adjusted`.

    **A subscript chooses WHICH element, exactly as a join key chooses which row.** None of
    the loop counter is in the number that comes out; incrementing it moves you to a
    different element rather than changing any value. This is the collection form of the
    argument D-5 made for join conditions and D-4 made for a window's ordering, and it
    reaches the same answer: no value edge, and no filter edge either - the index decides
    nothing about which ROWS are read.

    **BUT SOMETHING IS READ, AND IT HAS A NAME.** S3-06 removed `i` from four call sites
    and put nothing in any of them. The assignment path kept working by accident: it scans
    identifiers out of TEXT, so `l_batch` arrives as a name regardless. Every path that
    walks the AST instead saw the collection only as `Anonymous.this` - never an
    `exp.Column`, so never a candidate - and fell silent about the one variable the
    statement actually reads. That is S4-10, and it was silent in a `VALUES` list, an
    `UPDATE ... SET`, and every `WHERE`.

    So the exclusion and the replacement are returned as one object. A call site that
    drops `index_ids` from its walk has `collections` in its hand at the same moment, and
    skipping it is then a visible omission rather than the default.

    THE SCOPE LOOKUP IS WHAT MAKES IT SAFE, in both directions. `pkg.f(amt)` is a real
    function call: its arguments really are value sources and `pkg.f` is not a variable to
    report. Only a call on a name that is a DECLARED VARIABLE is an index, because PL/SQL
    has no way to call a variable.

    `index_ids` holds ids rather than names so a name used both as a subscript and as a
    value keeps its value edge - `VALUES (i, l_amts(i))` is contrived, but excluding by
    name would quietly drop the first item.
    """
    index_ids: set[int] = set()
    collections: list[Any] = []
    seen: set[str] = set()
    for call in expression.find_all(exp.Anonymous):
        name = str(call.this or "")
        if not name:
            continue
        declaration = scope.lookup(name)
        if declaration is None:
            continue  # a genuine function call: its arguments ARE value sources
        for argument in call.expressions:
            index_ids.update(id(column) for column in argument.find_all(exp.Column))
        if fold(name, scope.dialect) not in seen:
            seen.add(fold(name, scope.dialect))
            collections.append(declaration)
    return Subscripted(frozenset(index_ids), tuple(collections))


def _transform_of_element(expression: Any, scope: UnitScope) -> Transform:
    """`_transform_of`, with a collection subscript read as the element it names.

    `l_amts(i)` parses as an `Anonymous` call, and to the transform ladder a call is a
    function application - so the element of a collection scored `derived` when nothing had
    been done to it. ADR-0001 §4 makes a wrong transform a MISS ON BOTH SIDES, so that is a
    false positive and a miss for every row written from a collection, not a cosmetic slip.

    Each declared-variable call is rewritten to a plain name and the LADDER IS THEN ASKED
    THE ORDINARY QUESTION, rather than a second ladder being written for this case:
    `l_amts(i)` -> identity, `l_amts(i) * 1.05` -> derived, `CASE WHEN l_amts(i) ...` ->
    conditional. Reimplementing the ranking here is how S2-08 got two copies of
    `_transform_of` that had already drifted.
    """

    def _collection(node: Any) -> str | None:
        """The declared collection a subscript call names, or None if it is a real call."""
        if not isinstance(node, exp.Anonymous):
            return None
        name = str(node.this or "")
        return name if name and scope.lookup(name) is not None else None

    def _unsubscript(node: Any) -> Any:
        # `l_batch(i).refund_amount` is `Dot(<the call>, refund_amount)`. Reading a field
        # of a record is no more a transformation than reading the record, but
        # `transform_of` returns IDENTITY only for a bare `exp.Column`, so a `Dot` fell
        # through to DERIVED. Collapsed to the collection's name for that reason.
        #
        # HANDLED HERE RATHER THAN AFTER THE CALL IS REPLACED BECAUSE `transform` IS
        # TOP-DOWN: the Dot is visited while its child is still the Anonymous, so a guard
        # written against the rewritten shape matches nothing. Measured, after writing that
        # guard and finding the record form still scoring `derived`.
        #
        # `rec.email` is untouched: a cursor record lives in `row_sources`, not
        # `variables`, so `_collection` does not resolve it.
        if isinstance(node, exp.Dot):
            name = _collection(node.this)
            if name is not None:
                return exp.column(name)
        name = _collection(node)
        return exp.column(name) if name is not None else node

    return _transform_of(expression.copy().transform(_unsubscript))


def _collection_edges(
    subscripted: Subscripted,
    target: Node,
    flow: Flow,
    transform: Transform,
    origin: Origin,
    guard: str | None,
) -> list[IREdge]:
    """The replacement half of `_subscripted`, as edges (S4-10).

    One emitter for all three AST call sites. `l_batch(i).refund_amount` and `l_batch(i)`
    both report `L_BATCH`, not a field-qualified name: a locally declared record type has
    no query behind it to resolve a field against, and stress 3's key has named the
    collection itself since 2026-09-12. A `L_BATCH.REFUND_AMOUNT` node would be a
    relation-shaped name for something that is not a relation.

    Always band 1 and always `DEF_USE`: the source is a declared variable by construction -
    `_subscripted` only returns names `scope.lookup` resolved.
    """
    return [
        _edge(
            declaration.as_node(),
            target,
            flow,
            transform,
            origin,
            guard,
            Mechanism.DEF_USE,
            band=_band_for("variable", target_is_variable=False),
        )
        for declaration in subscripted.collections
    ]


def _subscript_only_names(expression_text: str, scope: UnitScope, dialect: str) -> set[str]:
    """Names appearing ONLY as collection subscripts in this expression (S3-06).

    The assignment path scans identifiers out of TEXT rather than an AST, so it needs names
    rather than node ids. Built on `_subscripted` rather than repeating the rule: two
    copies of one rule is how S2-08 happened, and this one would drift the same way.

    This path needs only the exclusion half. It is the one path where that is not the
    S4-10 mistake: the text scan finds `l_batch` as an identifier by itself, so the
    collection already reaches `scope.lookup` and gets its edge a few lines below.
    """
    try:
        parsed = sqlglot.parse_one(expression_text, dialect=dialect)
    except Exception:
        # An unparseable right-hand side is the caller's problem; here it just means no
        # subscript can be proven, and proving none is the safe direction.
        return set()

    indexes = _subscripted(parsed, scope).index_ids
    as_subscript = {fold(c.name, dialect) for c in parsed.find_all(exp.Column) if id(c) in indexes}
    elsewhere = {fold(c.name, dialect) for c in parsed.find_all(exp.Column) if id(c) not in indexes}
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
        real = resolved.name or fold(table.name, dictionary.dialect)
        relations[fold(table.name, dictionary.dialect)] = real
        if table.alias:
            relations[fold(table.alias, dictionary.dialect)] = real
    return relations


def _classify(
    column: exp.Column, scope: UnitScope, relations: dict[str, str], dictionary: Dictionary
) -> tuple[str, str] | None:
    """Resolve a bare identifier to ('variable', qualified) or ('column', TABLE.COLUMN).

    Variables are checked FIRST. To a SQL parser `v_cutoff` looks exactly like a column,
    and binding it to whichever table is in scope is the silent failure this module
    exists to prevent.
    """
    name = fold(column.name, dictionary.dialect)

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

    qualifier = fold(column.table, dictionary.dialect) if column.table else None
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
        parsed = sqlglot.parse_one(text, dialect=dictionary.dialect)
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
    phase: FilterPhase = FilterPhase.PRE_AGGREGATION,
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
        phase=phase,
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
    #
    # Asked of the FRONT END rather than of ANTLR's `source_slice`: a statement context is
    # whatever the dialect's parser produced, and only that parser knows how to read it
    # back. This was the last place the analysis assumed an ANTLR context.
    text = for_name(dictionary.dialect).frontend.text(node.ctx)
    recovered = dynamic is not None and node.line in dynamic.recovered
    if dynamic is not None and recovered:
        text = dynamic.recovered[node.line]
    try:
        statement: Any = sqlglot.parse_one(
            strip_unparseable_clauses(text), dialect=dictionary.dialect
        )
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
        parsed: Any = sqlglot.parse_one(expression_text, dialect=dictionary.dialect)
    except Exception:
        return []

    edges: list[IREdge] = []
    seen: set[str] = set()
    for column in _value_columns(parsed):
        qualifier = fold(column.table or "", dictionary.dialect)
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

    assignment = for_name(dictionary.dialect).frontend.assignment(node.ctx)
    if assignment is None:
        return result

    target_name = fold(assignment.target, dictionary.dialect)
    if target_name in carriers:
        return result
    target_decl = scope.lookup(target_name)
    if target_decl is None:
        result.unresolved.append(f"line {node.line}: assignment to undeclared {target_name}")
        return result

    expression_text = assignment.expression
    transform = _assignment_transform(expression_text)

    subscripts = _subscript_only_names(expression_text, scope, dictionary.dialect)

    # A cursor record's fields, which the identifier scan below cannot see: `rec` is not a
    # declared variable and `gross_total` is not a column of anything (S4-07).
    result.edges.extend(
        _row_field_edges(
            expression_text, scope, dictionary, target_decl, transform, origin, guard
        )
    )

    for name in _identifiers_in(expression_text, dictionary.dialect):
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

    fetch = for_name(dictionary.dialect).frontend.fetch(node.ctx)
    if fetch is None:
        return result
    query = scope.cursors.get(fold(fetch.cursor, dictionary.dialect))
    if query is None:
        result.unresolved.append(f"line {node.line}: FETCH from an undeclared cursor")
        return result

    targets = [fold(target, dictionary.dialect) for target in fetch.targets]
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
            table = fold(column.table, dictionary.dialect) if column.table else None
            relation = relations.get(table) if table else next(iter(relations.values()), None)
            if relation is None:
                continue
            try:
                if fold(column.name, dictionary.dialect) not in dictionary.columns_of(relation):
                    continue
            except UnknownObjectError:
                continue
            result.edges.append(
                _edge(
                    Node(
                        kind=IRNodeKind.COLUMN,
                        name=f"{relation}.{fold(column.name, dictionary.dialect)}",
                    ),
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


def _identifiers_in(text: str, dialect: str) -> list[str]:
    return [
        fold(match.group(0), dialect)
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
    targets = [fold(t.name, dictionary.dialect) for t in into.find_all(exp.Identifier)] or [
        fold(into.this.name, dictionary.dialect)
    ]
    projections = statement.selects

    if len(targets) != len(projections):
        result.unresolved.append(
            f"line {origin.line}: INTO arity mismatch "
            f"({len(projections)} selected into {len(targets)} targets)"
        )
        return result

    # Built once and shared by the value path (S4-05) and the influence path (S4-09), both
    # of which need the statement without its INTO: `SELECT x INTO v FROM t` is not a query
    # SQLGlot's optimiser can qualify, because `v` sits where a table would.
    probe = statement.copy()
    probe.set("into", None)
    probe_sql = probe.sql(dialect=dictionary.dialect)

    receivers: dict[str, VariableDecl] = {}

    for target_name, projection in zip(targets, projections, strict=True):
        declaration = scope.lookup(target_name)
        if declaration is None:
            result.unresolved.append(f"line {origin.line}: INTO undeclared {target_name}")
            continue
        receivers[fold(projection.alias_or_name or "", dictionary.dialect)] = declaration
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
            resolved = resolve_projection(probe_sql, projection.alias_or_name, dictionary)
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

    # A predicate on the read relation decides which row the variable receives. Variables in
    # that predicate are this module's to classify - `WHERE cust_id = v_cutoff` is the whole
    # reason band 1 exists, and band 0 would bind that name to whichever table is in scope -
    # so the local walk stays for the VARIABLE side only. The column side is delegated below,
    # because a single `target_relation` cannot express the convention; see `_filter_edges_for`.
    result.edges += _filter_edges_for(
        statement.args.get("where"),
        scope,
        relations,
        dictionary,
        _read_relation(statement, relations, dictionary.dialect),
        origin,
        guard,
        result,
        variables_only=True,
    )

    # THE COLUMN SIDE, AT ANY DEPTH (stress finding S4-09).
    #
    # The walk above sees one `WHERE` and needs `_read_relation` to name a target, which
    # returns None as soon as the FROM is a subquery. So a statement reading into variables
    # said nothing about a `HAVING`, nothing about a `GROUP BY`, and nothing about a `WHERE`
    # written one level down - while the identical query writing a TABLE produced all three.
    # Delegated for the same reason S4-02 and S4-05 delegated the value side.
    #
    # **WHICH RELATION A FILTER EDGE TARGETS**, and the convention has two clauses that are
    # easy to read as one: *the relation the statement WRITES; when it writes a variable,
    # the relation read.* A `SELECT ... INTO` is the second clause, and "the relation read"
    # resolves to **the relation the filtered column itself belongs to** - which is what
    # every band-1 claim in every key shows, ten of them across three packages.
    #
    # I implemented the other reading in between, targeting the relations the statement's
    # VALUES come from, on the strength of the band-0 claims: `DIM_CUSTOMER.IS_ACTIVE`
    # targets `FCT_REVENUE`, `GTT_STAGE` and `TMP_RECENT` there, never `DIM_CUSTOMER`. But
    # those statements WRITE a relation, so they are the first clause and say nothing about
    # this one. `fn_stress_net` settled it in one run: it reads values from two relations
    # through a join and its key claims exactly ONE filter edge,
    # `STG_ORDERS.ORDER_ID -> STG_ORDERS`. The value-relations reading emitted two, scoring
    # a true positive and a false one off a single predicate.
    #
    # The two claims that look like counter-examples - `STG_ORDERS.CURRENCY` and
    # `DIM_CUSTOMER.IS_ACTIVE` onto `FCT_REVENUE_PART` at band 1 - are the CURSOR case,
    # where the loop does write a relation. First clause again, and still open as S4-09's
    # remaining half.
    #
    # Influence targets the VARIABLE receiving that projection - the variable standing where
    # D-2 puts the aggregated column.
    influencing = resolve_query_influence(probe_sql, dictionary)

    for relation, column_name, phase in influencing.filters:
        result.edges.append(
            _edge(
                Node(kind=IRNodeKind.COLUMN, name=f"{relation}.{column_name}"),
                Node(kind=IRNodeKind.RELATION, name=relation),
                Flow.FILTER,
                Transform.IDENTITY,
                origin,
                guard,
                Mechanism.AST,
                band=1,  # the statement's target is a variable, so the path is band 1
                phase=phase,
            )
        )

    for projection_name, relation, column_name in influencing.influence:
        declaration = receivers.get(projection_name)
        if declaration is None:
            continue
        result.edges.append(
            _edge(
                Node(kind=IRNodeKind.COLUMN, name=f"{relation}.{column_name}"),
                declaration.as_node(),
                Flow.INFLUENCE,
                Transform.IDENTITY,
                origin,
                guard,
                Mechanism.AST,
                band=1,
            )
        )
    return result


def _read_relation(statement: Any, relations: dict[str, str], dialect: str) -> str | None:
    # sqlglot stores the FROM clause under "from_", not "from".
    source = statement.args.get("from_") or statement.args.get("from")
    if source is None:
        return next(iter(relations.values()), None)
    table = source.this if isinstance(source, exp.From) else source
    if isinstance(table, exp.Table) and table.name:
        return relations.get(fold(table.name, dialect), fold(table.name, dialect))
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
        target_name = relations.get(
            fold(target.name, dictionary.dialect),
            fold(target.name, dictionary.dialect),
        )
    if target_name is None:
        result.unresolved.append(f"line {origin.line}: UPDATE target not resolved")
        return result

    for setter in statement.args.get("expressions") or []:
        if not isinstance(setter, exp.EQ):
            continue
        column_name = fold(setter.this.name, dictionary.dialect)
        own = _transform_of_element(setter.expression, scope)
        target_node = Node(kind=IRNodeKind.COLUMN, name=f"{target_name}.{column_name}")

        # `SET amt = l_amts(i)` reads the collection, not the index (S3-06, S4-10). This
        # path had NEITHER half: it emitted `I -> TGT.AMT` as a value, which is the S3-06
        # false positive still live nine days after S3-06 was closed - no stress package
        # subscripts a collection inside an UPDATE, so nothing measured it until a probe did.
        subscripted = _subscripted(setter.expression, scope)
        result.edges.extend(
            _collection_edges(subscripted, target_node, Flow.VALUE, own, origin, guard)
        )

        # A correlation predicate inside the assigned expression selects rows; it does
        # not supply the value. Its columns become filter edges on the updated relation.
        for column in _predicate_columns(setter.expression):
            if id(column) in subscripted.index_ids:
                continue
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
            if id(column) in subscripted.index_ids:
                continue
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
    target_name = relations.get(
        fold(target.name, dictionary.dialect),
        fold(target.name, dictionary.dialect),
    )

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
        # `WHERE s.sid = l_ids(i)` - the collection decides which rows load, the index does
        # not (S3-06, S4-10). The fourth predicate walk in this module, and the fourth to
        # need this; D-5 took four call sites too.
        subscripted = _subscripted(where, scope)
        result.edges.extend(
            _collection_edges(
                subscripted,
                Node(kind=IRNodeKind.RELATION, name=target_name),
                Flow.FILTER,
                Transform.IDENTITY,
                origin,
                guard,
            )
        )
        for column in predicate_columns(where):
            if id(column) in subscripted.index_ids:
                continue
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

    columns = [fold(c.name, dictionary.dialect) for c in schema.expressions]

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
            subscripted = _subscripted(item, scope)
            # `VALUES (l_batch(i).product_id, ...)` - `i` is not a source and `l_batch` is
            # (S3-06, S4-10). Emitted before the walk below because the walk cannot see it:
            # the collection is never an `exp.Column`.
            result.edges.extend(
                _collection_edges(
                    subscripted,
                    target,
                    Flow.VALUE,
                    _transform_of_element(item, scope),
                    origin,
                    guard,
                )
            )
            for reference in item.find_all(exp.Column):
                if id(reference) in subscripted.index_ids:
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
                        _transform_of_element(item, scope),
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
    variables_only: bool = False,
) -> list[IREdge]:
    """Every operand of a predicate influences which rows are selected.

    Except a collection subscript. `WHERE sid = l_ids(i)` is decided by what is IN `l_ids`;
    the index only says which element of it to compare against, and no rows depend on that
    (S3-06's argument, and S4-10's answer to what replaces it). Before this, the loop
    counter was reported as a filter source of every table read this way.

    **`variables_only` exists because `target_relation` is a single name and a predicate's
    columns do not all belong to it** (stress finding S4-09). Callers that write a relation
    have one correct target and pass nothing here. `_analyse_select_into` does not: it writes
    variables, the convention makes each filter edge target the relation its own column
    belongs to, and one target cannot express that.

    Passing `_read_relation`'s answer for every column was the old behaviour, and it was
    right only when the filtered column happened to sit on the outermost FROM's first table.
    `WHERE d.is_active = 1` over `FROM src s JOIN dim d` emitted `DIM.IS_ACTIVE -> SRC`. No
    stress package caught it, because in every package the predicate is on the driving table.
    The column side now belongs to `resolve_query_influence`, which knows each column's own
    relation; this keeps the variable side, which only this module can classify at all.
    """
    if where is None or target_relation is None:
        return []

    edges: list[IREdge] = []
    target = Node(kind=IRNodeKind.RELATION, name=target_relation)

    subscripted = _subscripted(where, scope)
    edges.extend(
        _collection_edges(subscripted, target, Flow.FILTER, Transform.IDENTITY, origin, guard)
    )

    for column in predicate_columns(where):
        if id(column) in subscripted.index_ids:
            continue
        classified = _classify(column, scope, relations, dictionary)
        if classified is None:
            if variables_only:
                # Not an omission to declare: the caller's delegated walk resolves the
                # column side through the whole query, and will either name it or declare
                # its own boundary. Reporting it here would double-count one name.
                continue
            result.unresolved.append(f"line {origin.line}: unresolved {column.name}")
            continue
        kind, name = classified
        if variables_only and kind != "variable":
            continue
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


def definitions_and_uses(
    node: CfgNode, scope: UnitScope, dialect: str
) -> tuple[set[str], set[str]]:
    """Variables this statement defines, and variables it reads.

    Names only — the edges themselves are built elsewhere. This feeds reaching-definitions
    analysis, which asks a different question: not *what* flows, but *whether anything
    was assigned before the read*.
    """
    defined: set[str] = set()
    used: set[str] = set()
    if node.ctx is None:
        return defined, used

    text = for_name(dialect).frontend.text(node.ctx)

    frontend = for_name(dialect).frontend

    if node.statement_kind == "assignment_statement":
        assignment = frontend.assignment(node.ctx)
        if assignment is not None:
            if scope.lookup(assignment.target) is not None:
                defined.add(fold(assignment.target, dialect))
            if assignment.expression:
                for name in _identifiers_in(assignment.expression, dialect):
                    if scope.lookup(name) is not None:
                        used.add(name)
        return defined, used

    if node.statement_kind == "fetch_statement":
        fetch = frontend.fetch(node.ctx)
        if fetch is not None:
            for target in fetch.targets:
                name = fold(target, dialect)
                if scope.lookup(name) is not None:
                    defined.add(name)
        return defined, used

    # SQL statements: an INTO target is a definition, everything else a read.
    try:
        statement: Any = sqlglot.parse_one(
            strip_unparseable_clauses(text), dialect=dialect
        )
    except Exception:
        return defined, used

    into = statement.args.get("into") if isinstance(statement, exp.Select) else None
    into_names: set[str] = set()
    if into is not None:
        into_names = {fold(i.name, dialect) for i in into.find_all(exp.Identifier)}
        for name in into_names:
            if scope.lookup(name) is not None:
                defined.add(name)

    for column in statement.find_all(exp.Column):
        if column.table:
            continue
        name = fold(column.name, dialect)
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
