"""Band 0 analyser: set-based SQL, end to end (T1.5).

The baseline. `INSERT ... SELECT`, `MERGE`, `SELECT *`, views on views, and alias chains.
If this band does not work, the spike stops here — that is the finding.

Division of labour, kept strict:

* **ANTLR** says where statements begin and end. It does not interpret SQL.
* **SQLGlot** interprets one statement. It knows nothing about variables or control flow.
* **The dictionary** (T1.4) resolves every name before analysis, so a synonym cannot
  redirect an edge onto the wrong object.

Three behaviours matter more than coverage here:

1. **Never emit a guessed edge.** A statement this module cannot handle is *refused*,
   with a reason, and contributes no edges. Refusals are counted and reported — an
   analyser that silently produces nothing for a statement is indistinguishable from one
   that correctly found nothing.
2. **Views are inlined before analysis**, so lineage lands on base tables rather than
   stopping at the view that hides them.
3. **Transform class is derived, not assumed.** `SUM(x)` and a copy of `x` share
   endpoints and are different facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope

from lineage.analysis.ddl import exchange_partition_edges
from lineage.analysis.dynamic import resolve_dynamic_sql
from lineage.analysis.refusal import (
    Refusal,
    RefusalCode,
    classify_statement,
    conditional_compilation_spans,
    last_line,
    violations,
)
from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow, Node, NodeKind, Origin, Transform
from lineage.harness.scoring import Mechanism, PredictedEdge, Tier
from lineage.ir.model import Boundary, BoundaryKind
from lineage.parsing.plsql import ParsedStatement, Program, parse_program
from lineage.resolution.dictionary import Dictionary, UnknownObjectError

DIALECT = "oracle"

# Statement kinds this module claims to handle. Anything else is refused rather than
# half-analysed - band 1 constructs belong to the procedural analyser, not here.
SUPPORTED = {"insert_statement", "merge_statement"}

# Strongest transform on a path wins: an aggregation over a derived expression is an
# aggregation, and calling it identity would be a miss under ADR-0001 4.
TRANSFORM_RANK = {
    Transform.IDENTITY: 0,
    Transform.DERIVED: 1,
    Transform.CONDITIONAL: 2,
    Transform.AGGREGATED: 3,
}

AGGREGATE_FUNCTIONS = (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max, exp.AggFunc)

# The output value is SELECTED from alternatives by a test, rather than computed from the
# input. Stress finding S2-05: this used to be `(exp.Case, exp.If)` alone, so DECODE -
# which Oracle's own documentation defines as equivalent to CASE - was classified
# `derived`, and a wrong transform class is a MISS on BOTH sides under ADR-0001 4. Four
# expressions cost eight cells.
#
# GREATEST and LEAST belong here because they choose between two SOURCES; NULLIF because
# it replaces the value with NULL on a test; NVL2 because it tests one argument and
# returns one of two others.
CONDITIONAL_EXPRESSIONS = (
    exp.Case,
    exp.If,
    exp.DecodeCase,
    exp.Nullif,
    exp.Greatest,
    exp.Least,
    exp.Nvl2,
)


@dataclass
class AnalysisResult:
    edges: list[PredictedEdge] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    boundaries: list[Boundary] = field(default_factory=list)
    statements_seen: int = 0

    @property
    def statements_analysed(self) -> int:
        return self.statements_seen - len(self.refusals)

    def refusal_violations(self) -> list[tuple[Refusal, list[PredictedEdge]]]:
        """Flagged statements that produced edges anyway (T3.1c). Must be empty."""
        return violations(self.refusals, self.edges)

    def refusal_codes(self) -> dict[str, int]:
        """How many refusals of each kind. The taxonomy is closed so this is comparable."""
        counts: dict[str, int] = {}
        for refusal in self.refusals:
            counts[refusal.code.value] = counts.get(refusal.code.value, 0) + 1
        return counts

    @property
    def parse_coverage(self) -> float | None:
        """Statements handled versus statements seen.

        Refusals are fine and expected; what this must never hide is a crash.
        """
        if not self.statements_seen:
            return None
        return self.statements_analysed / self.statements_seen


def _schema_for(dictionary: Dictionary) -> dict[str, Any]:
    """SQLGlot schema built from the captured dictionary.

    Unqualified table names are used as keys because the corpus lives in one schema;
    cross-schema binding is a band-1 concern (silent failure s2) handled by the
    dictionary itself rather than by SQLGlot.
    """
    schema: dict[str, dict[str, str]] = {}
    for qualified, columns in dictionary.columns.items():
        _, _, table = qualified.partition(".")
        schema[table] = dict.fromkeys(columns, "UNKNOWN")
    return schema


def _inline_views(
    expression: exp.Expression, dictionary: Dictionary, depth_cap: int
) -> tuple[exp.Expression, list[str]]:
    """Replace view references with their definitions, so lineage reaches base tables.

    Without this, a chain of views on views reports the outermost view as the source and
    the real table never appears. Depth-capped and cycle-protected: beyond the cap the
    view is left in place and reported as a boundary rather than silently truncated.
    """
    notes: list[str] = []
    seen: set[str] = set()

    for _ in range(depth_cap):
        replaced = False
        for table in list(expression.find_all(exp.Table)):
            name = table.name
            if not name:
                continue
            try:
                resolved = dictionary.resolve(name)
            except Exception:
                continue
            if resolved.object_type != "VIEW" or resolved.qualified is None:
                continue
            if resolved.qualified in seen:
                notes.append(f"view cycle at {resolved.qualified}")
                continue
            text = dictionary.view_text.get(resolved.qualified)
            if not text:
                notes.append(f"view {resolved.qualified} has no captured text")
                continue
            seen.add(resolved.qualified)
            try:
                inner = sqlglot.parse_one(text, dialect=DIALECT)
            except Exception:
                notes.append(f"view {resolved.qualified} did not parse")
                continue
            alias = table.alias or table.name
            table.replace(
                exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(alias)))
            )
            replaced = True
        if not replaced:
            break
    else:
        notes.append(f"view expansion hit depth cap {depth_cap}")

    return expression, notes


def _is_conditional(node: Any) -> bool:
    """Does this node SELECT the output from alternatives, rather than compute it?

    `COALESCE` is the one that needs a rule rather than a list, and sqlglot gives `NVL`
    the same node, so the two cannot be told apart by type:

    * `COALESCE(a, b)` over two COLUMNS is a genuine choice of source - the value comes
      from `a` or from `b` depending on a test, which is what conditional means.
    * `NVL(x, 0)` has one column and a constant floor. The column's value flows through
      unchanged whenever it exists; the literal is null-safety, not business logic.
      Calling that conditional would tell a reader there is a branch in the rule when the
      only branch is a null guard.

    So: conditional when more than one argument can actually supply a column. Measured
    rather than assumed - classifying every `COALESCE` as conditional costs one phase-0
    label (`sq_06`'s `NVL(parent.depth, 0) + 1`), and this rule leaves it alone.
    """
    if isinstance(node, exp.Coalesce):
        candidates = [node.this, *(node.expressions or [])]
        supplying = [
            arg for arg in candidates if arg is not None and list(arg.find_all(exp.Column))
        ]
        return len(supplying) > 1
    return isinstance(node, CONDITIONAL_EXPRESSIONS)


def _transform_of(expression: Any) -> Transform:
    """Classify what the expression does to the value.

    Typed as Any because sqlglot's node accessors return loosely-typed expressions; the
    narrowing happens here rather than being asserted at every call site.
    """
    if isinstance(expression, exp.Alias):
        expression = expression.this
    if isinstance(expression, exp.Column):
        return Transform.IDENTITY
    if any(isinstance(node, AGGREGATE_FUNCTIONS) for node in expression.walk()):
        return Transform.AGGREGATED
    if any(_is_conditional(node) for node in expression.walk()):
        return Transform.CONDITIONAL
    return Transform.DERIVED


def _combine(first: Transform, second: Transform) -> Transform:
    return first if TRANSFORM_RANK[first] >= TRANSFORM_RANK[second] else second


def _influence_columns(expression: Any) -> list[Any]:
    """Columns that decide WHICH row, rather than supplying the value.

    Two shapes, and both were previously counted as value sources:

    * a nested `WHERE` — a correlated scalar subquery's predicate selects the row it
      reads; it does not supply what comes back;
    * a window's `PARTITION BY` / `ORDER BY` — these decide which row gets which rank.
      `ROW_NUMBER() OVER (ORDER BY total DESC)` does not take its value from `total`.

    Counting either as a value source produces an edge that type-checks, reads sensibly
    and is false - the same category error as `order_id -> net_amount`.
    """
    found: list[Any] = []
    for where in expression.find_all(exp.Where):
        found.extend(where.find_all(exp.Column))
    for window in expression.find_all(exp.Window):
        for part in window.args.get("partition_by") or []:
            found.extend(part.find_all(exp.Column))
        order = window.args.get("order")
        if order is not None:
            found.extend(order.find_all(exp.Column))
    return found


def _value_columns(expression: Any) -> list[Any]:
    """Columns that genuinely supply the value of an expression."""
    excluded = {id(column) for column in _influence_columns(expression)}
    return [c for c in expression.find_all(exp.Column) if id(c) not in excluded]


def _projection_named(scope: Scope, name: str) -> Any:
    projections: list[Any] = getattr(scope.expression, "selects", []) or []
    for projection in projections:
        if (projection.alias_or_name or "").upper() == name.upper():
            return projection
    return None


def _trace(
    column: exp.Column,
    scope: Scope,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int = 0,
) -> list[tuple[str, str, Transform]]:
    """Follow a column reference down to base-table columns.

    Recurses through subqueries so an alias chain - a column renamed four times on the
    way - resolves to the real source rather than to the innermost alias.

    **Every landing is checked against the dictionary.** A PL/SQL local or package
    variable used inside SQL looks exactly like a column reference to a SQL parser, and
    with one table in scope it will happily bind `v_cutoff` to `stg_customer`. That
    produces a confident, corroborated, entirely fictional edge - the silent-failure
    shape this project exists to prevent. If the name is not a real column of the
    relation it resolved to, no edge is emitted and the reference is declared instead.
    """
    if depth > 20:  # pathological nesting; refuse rather than recurse forever
        return []

    source = scope.sources.get(column.table) if column.table else None

    if source is None and column.table:
        # A scalar subquery in the select list brings its own FROM. `MAX(s.email)` inside
        # `(SELECT MAX(s.email) FROM stg_customer s WHERE ...)` resolves against that
        # subquery's scope, not the outer one - and looking only at the outer scope loses
        # the edge entirely rather than getting it wrong.
        for nested in getattr(scope, "subquery_scopes", []) or []:
            if column.table in nested.sources:
                return _trace(column, nested, dictionary, unresolved, depth + 1)

    if source is None and len(scope.sources) == 1:
        source = next(iter(scope.sources.values()))

    if isinstance(source, exp.Table):
        table = source.name.upper()
        name = column.name.upper()

        # AN EXPLICIT SCHEMA QUALIFIER IS PART OF THE NAME (silent failure s2).
        #
        # `_schema_for_sqlglot` keys on the bare table name, so SQLGlot happily binds
        # `reporting.stg_customer` to the local `stg_customer` and the qualifier vanishes.
        # Measured 2026-09-09: that produced three edges sourced from a table the statement
        # never named, with no boundary, no refusal, and the statement counted as analysed.
        # In s2 they then merged onto the identical edges of the statement above, so the
        # whole thing was invisible - the exact "most dangerous repair" that package's key
        # warns about, silently merging a refused schema into one we own.
        #
        # So resolve the QUALIFIED name. An unqualified name is untouched and still binds
        # through the execution schema, which is the other half of s2 and is correct.
        qualifier = (source.text("db") or "").upper()
        lookup = f"{qualifier}.{table}" if qualifier else table
        try:
            known = dictionary.columns_of(lookup)
        except UnknownObjectError:
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.DANGLING_REFERENCE,
                    subject=lookup,
                    detail=f"{lookup}.{name} (relation not in dictionary)",
                )
            )
            return []
        if qualifier:
            # Granted after all - report it under the object it really is, not the
            # qualifier the code happened to spell.
            table = dictionary.resolve(lookup).name or table
        if name not in known:
            # Not a column of this table. Almost always a PL/SQL variable, which is
            # band-1 territory and must be resolved by def-use analysis, not guessed at.
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                    subject=f"{table}.{name}",
                    detail=f"{name} (not a column of {table} - unresolved identifier)",
                )
            )
            return []
        return [(table, name, Transform.IDENTITY)]

    if isinstance(source, Scope):
        # A set operation has no select list of its own - each arm has one, and SQL
        # binds them BY POSITION (silent failure s5). Every arm feeds the same output
        # column, so missing one is a silent under-report of a whole feed.
        arms = getattr(source, "union_scopes", None)
        if arms:
            return _trace_through_set_operation(column, arms, dictionary, unresolved, depth)

        projection = _projection_named(source, column.name)
        if projection is None:
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                    subject=column.name.upper(),
                    detail=f"{column.name.upper()} (no matching projection in subquery)",
                )
            )
            return []
        own = _transform_of(projection)
        traced: list[tuple[str, str, Transform]] = []
        for inner in projection.find_all(exp.Column):
            for table, name, transform in _trace(inner, source, dictionary, unresolved, depth + 1):
                traced.append((table, name, _combine(own, transform)))
        return traced

    unresolved.append(
        Boundary(
            kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
            subject=column.name.upper(),
            detail=f"{column.name.upper()} (could not resolve which relation it belongs to)",
        )
    )
    return []


def _flatten_arms(arms: list[Any]) -> list[Any]:
    """Expand nested set operations into a flat list of leaf arms."""
    flat: list[Any] = []
    for arm in arms:
        nested = getattr(arm, "union_scopes", None)
        if nested:
            flat.extend(_flatten_arms(nested))
        else:
            flat.append(arm)
    return flat


def _trace_through_set_operation(
    column: exp.Column,
    arms: list[Any],
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int,
) -> list[tuple[str, str, Transform]]:
    """Follow a column into every arm of a UNION / INTERSECT / MINUS.

    Binding is POSITIONAL, not by name. The first arm's select list fixes the output
    column names; every later arm supplies the same positions whatever it calls them.
    Matching on name here would wire the wrong sources together and, worse, would look
    entirely reasonable - which is the whole point of silent failure s5.
    """
    # `A UNION B UNION C` parses as Union(Union(A, B), C), so the first "arm" is itself
    # a set operation with no select list of its own. Flatten before binding by position,
    # or only the outermost arm resolves and the rest of the feeds vanish silently.
    flattened = _flatten_arms(arms)

    first = getattr(flattened[0].expression, "selects", []) or [] if flattened else []
    name = column.name.upper()
    position = next(
        (i for i, item in enumerate(first) if (item.alias_or_name or "").upper() == name),
        None,
    )
    if position is None:
        unresolved.append(
            Boundary(
                kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                subject=name,
                detail=f"{name} (no matching position in the set operation)",
            )
        )
        return []

    traced: list[tuple[str, str, Transform]] = []
    for arm in flattened:
        projections = getattr(arm.expression, "selects", []) or []
        if position >= len(projections):
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                    subject=name,
                    detail=f"{name} (set operation arm has fewer columns than position "
                    f"{position + 1})",
                )
            )
            continue
        projection = projections[position]
        own = _transform_of(projection)
        for inner in _value_columns(projection):
            for table, source_column, transform in _trace(
                inner, arm, dictionary, unresolved, depth + 1
            ):
                traced.append((table, source_column, _combine(own, transform)))
    return traced


def _edge(
    source_table: str,
    source_column: str,
    target: str,
    target_column: str,
    transform: Transform,
    band: int,
    origin: Origin,
    flow: Flow = Flow.VALUE,
) -> PredictedEdge:
    """Build one IR edge.

    Mechanism is AST throughout this module by construction: every edge here comes from
    a resolved syntax tree, not from dataflow, the query log, or inference. Tier is A for
    the same reason - a parser-only derivation with nothing contradicting it.
    """
    return PredictedEdge(
        source=Node(kind=NodeKind.COLUMN, name=f"{source_table}.{source_column}"),
        target=(
            Node(kind=NodeKind.RELATION, name=target)
            if flow is Flow.FILTER
            else Node(kind=NodeKind.COLUMN, name=f"{target}.{target_column}")
        ),
        flow=flow,
        transform=transform,
        band=band,
        mechanism=Mechanism.AST,
        tier=Tier.A,
        origin=origin,
    )


def _target_of(insert: exp.Insert, dictionary: Dictionary) -> tuple[str, list[str] | None]:
    """Target relation and explicit column list, both dictionary-resolved."""
    target = insert.this
    columns: list[str] | None = None
    if isinstance(target, exp.Schema):
        columns = [c.name.upper() for c in target.expressions]
        target = target.this
    name = target.name
    resolved = dictionary.resolve(name)
    return (resolved.name or name.upper()), columns


def _set_operation_arms(node: Any) -> list[tuple[Any, bool]]:
    """Flatten a set operation into its arms, in source order, with what each one does.

    The boolean is **whether this arm supplies values**, and the distinction is the whole
    point (stress finding S1-02):

    * A `UNION` arm ADDS rows, so it genuinely supplies the values of the rows it
      contributes. Every arm is a source.
    * `INTERSECT` and `MINUS` only REMOVE rows from the first arm. The value written
      always comes from arm 1; the later arms decide which of those rows survive, which is
      filter influence and not lineage. Claiming `gtt_stage.cust_id -> gtt_stage.cust_id`
      for a `MINUS` against the target itself would be a value edge for rows that were
      specifically EXCLUDED.

    sqlglot nests these left-associatively - `A UNION B UNION C` is `Union(Union(A,B),C)` -
    so the left side recurses and keeps its own flags.
    """
    if isinstance(node, exp.SetOperation):
        adds_rows = isinstance(node, exp.Union)
        return [*_set_operation_arms(node.this), (node.expression, adds_rows)]
    return [(node, True)]


def _as_filter_influence(edge: PredictedEdge, target_name: str) -> PredictedEdge:
    """Restate a value edge as filter influence on the written relation.

    Used for the constraining arms of INTERSECT and MINUS. Their columns are a real
    dependency of what ends up in the table and supply none of its values, which is
    exactly what a filter edge says.
    """
    if edge.flow is Flow.FILTER:
        return edge
    return edge.model_copy(
        update={
            "target": Node(kind=NodeKind.RELATION, name=target_name),
            "flow": Flow.FILTER,
            "transform": Transform.IDENTITY,
        }
    )


def _analyse_insert(
    statement: exp.Insert,
    dictionary: Dictionary,
    band: int,
    unresolved: list[Boundary],
    origin: Origin,
    summaries: dict[str, Any],
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None]:
    target_name, target_columns = _target_of(statement, dictionary)

    body = statement.expression

    # A TOP-LEVEL SET OPERATION IS AN `INSERT ... SELECT`, NOT AN `INSERT ... VALUES`.
    #
    # Stress finding S1-02. sqlglot parses `INSERT INTO t SELECT ... UNION ALL SELECT ...`
    # with an `exp.Union` where the plain form has an `exp.Select`, and the check below
    # treated everything that was not a Select as VALUES - so the whole statement was
    # refused, with a reason naming a clause that is not in it.
    #
    # The corpus could not see this because `s5_positional_union` wraps its UNION in a
    # subquery, which keeps the INSERT's expression a Select and reaches the set-operation
    # path through `_trace`. The form real ETL writes was never covered. Stress 2 then
    # showed the failure is not UNION-specific: INTERSECT and MINUS failed identically.
    #
    # Each arm is analysed as its own SELECT against the same target - the same treatment
    # MERGE's two arms already get - and identical facts are deduplicated downstream.
    if isinstance(body, exp.SetOperation):
        arms = _set_operation_arms(body)
        edges: list[PredictedEdge] = []
        for arm, supplies_values in arms:
            if not isinstance(arm, exp.Select):
                return [], (
                    RefusalCode.UNSUPPORTED_CONSTRUCT,
                    f"set-operation arm is {type(arm).__name__}, not a SELECT",
                )
            arm_edges, refusal = _analyse_select_into(
                arm, target_name, target_columns, dictionary, band, unresolved, origin, summaries
            )
            if refusal is not None:
                return [], refusal
            edges.extend(
                arm_edges
                if supplies_values
                else [_as_filter_influence(edge, target_name) for edge in arm_edges]
            )
        return edges, None

    if not isinstance(body, exp.Select):
        return [], (
            RefusalCode.UNSUPPORTED_CONSTRUCT,
            "INSERT ... VALUES carries no column lineage from a relation",
        )

    return _analyse_select_into(
        body, target_name, target_columns, dictionary, band, unresolved, origin, summaries
    )


def _analyse_select_into(
    select: exp.Select,
    target_name: str,
    target_columns: list[str] | None,
    dictionary: Dictionary,
    band: int,
    unresolved: list[Boundary],
    origin: Origin,
    summaries: dict[str, Any],
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None]:
    """One SELECT feeding one target. Also one arm of a set operation."""
    scope = build_scope(select)
    if scope is None:
        return [], (RefusalCode.PARSE_FAILED, "could not build a scope for the SELECT")

    if target_columns is None:
        try:
            target_columns = dictionary.columns_of(target_name)
        except UnknownObjectError as exc:
            return [], (RefusalCode.NAME_NOT_RESOLVED, str(exc))

    projections = select.selects
    if len(projections) != len(target_columns):
        return [], (
            RefusalCode.SHAPE_NOT_DECIDABLE,
            f"projection/target arity mismatch: {len(projections)} selected into "
            f"{len(target_columns)} columns",
        )

    edges: list[PredictedEdge] = []
    for target_column, projection in zip(target_columns, projections, strict=True):
        own = _transform_of(projection)

        # A scalar UDF looks like a column expression and contains a query. Its ARGUMENTS
        # select which row the callee reads; they do not supply the value. Treating them
        # as value sources produces `order_id -> net_amount`, which type-checks, reads
        # sensibly, and is false.
        call_edges, consumed = _call_site_edges(
            projection,
            target_name,
            target_column,
            band,
            origin,
            summaries,
            dictionary,
            unresolved,
        )
        if consumed:
            edges.extend(call_edges)
            continue

        for column in _value_columns(projection):
            for source_table, source_column, traced in _trace(
                column, scope, dictionary, unresolved
            ):
                edges.append(
                    _edge(
                        source_table,
                        source_column,
                        target_name,
                        target_column,
                        _combine(own, traced),
                        band,
                        origin,
                    )
                )

        # Partition/order columns and correlated predicates decide which row, so they
        # are filter influence on the written relation rather than value sources.
        for column in _influence_columns(projection):
            for source_table, source_column, _ in _trace(column, scope, dictionary, unresolved):
                edges.append(
                    _edge(
                        source_table,
                        source_column,
                        target_name,
                        "",
                        Transform.IDENTITY,
                        band,
                        origin,
                        flow=Flow.FILTER,
                    )
                )

    edges.extend(_filter_edges(select, scope, target_name, band, dictionary, unresolved, origin))
    return edges, None


def _call_site_edges(
    projection: Any,
    target_name: str,
    target_column: str,
    band: int,
    origin: Origin,
    summaries: dict[str, Any],
    dictionary: Dictionary,
    unresolved: list[Boundary],
) -> tuple[list[PredictedEdge], bool]:
    """Inline a callee's summary at the call site.

    Returns (edges, consumed). `consumed` is True when this projection contained a
    user-defined call and has been handled, so the caller must not also treat its
    argument columns as value sources.

    SQL built-ins are left alone. `TRUNC(order_date)` genuinely derives its value from
    its argument; a user function does not, and sqlglot distinguishes them by parsing
    what it knows into typed nodes and everything else into `Anonymous`.
    """
    # Unknown user-defined calls: cannot be summarised, so no value edge may be claimed.
    # In a real estate most callees start outside the file being analysed, which makes
    # this the common case rather than the exotic one.
    unknown = [
        name
        for node in projection.find_all(exp.Anonymous)
        if (name := (getattr(node, "name", "") or "").upper()) and name not in summaries
    ]

    called = [
        summaries[name]
        for node in projection.find_all(exp.Anonymous, exp.Func)
        if (name := (getattr(node, "name", "") or "").upper()) in summaries
    ]

    if unknown and not called:
        for name in dict.fromkeys(unknown):
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.SOURCE_UNAVAILABLE,
                    subject=name,
                    detail=f"call to {name} could not be summarised - its source is not "
                    f"in this analysis, so the value it supplies is out of coverage "
                    f"rather than derived from the arguments at the call site",
                )
            )
        return [], True

    if not called:
        return [], False

    edges: list[PredictedEdge] = []
    for summary in called:
        for item in summary.consolidated():
            source_table, _, source_column = item.column.partition(".")
            edges.append(
                _edge(
                    source_table,
                    source_column,
                    target_name,
                    target_column,
                    item.transform,
                    1,  # the path goes through a call, so it is band 1
                    origin,
                )
            )
        # The argument selects which row the callee reads: filter influence, on the
        # relation the callee reads rather than on the statement's target.
        for column in projection.find_all(exp.Column):
            for relation in sorted(summary.reads):
                try:
                    known = dictionary.columns_of(relation)
                except UnknownObjectError:
                    continue
                if column.name.upper() in known:
                    edges.append(
                        _edge(
                            relation,
                            column.name.upper(),
                            relation,
                            "",
                            Transform.IDENTITY,
                            1,
                            origin,
                            flow=Flow.FILTER,
                        )
                    )

    return edges, True


def _filter_edges(
    select: exp.Select,
    scope: Scope,
    target_name: str,
    band: int,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    origin: Origin,
) -> list[PredictedEdge]:
    """Columns that decide WHICH rows land, rather than what value they carry.

    A policy table silently governing which customers load is exactly the finding no
    table-level tool produces, so filter influence is an edge (ADR-0001 2) - scored
    separately from value flow.

    Convention: filter edges always carry `identity`. The transform class describes how
    a *value* was changed in transit, which does not apply to a row predicate; using it
    here would make the class mean two different things.
    """
    edges: list[PredictedEdge] = []

    # Every scope, not just the outermost. A predicate inside an inlined view still
    # decides which rows reach the target, and it is exactly the kind of hidden
    # constraint that makes filter lineage worth reporting.
    for current in scope.traverse():
        where = current.expression.args.get("where")
        if where is None:
            continue
        for column in where.find_all(exp.Column):
            for source_table, source_column, _ in _trace(column, current, dictionary, unresolved):
                edges.append(
                    _edge(
                        source_table,
                        source_column,
                        target_name,
                        "",
                        Transform.IDENTITY,
                        band,
                        origin,
                        flow=Flow.FILTER,
                    )
                )
    return edges


def _analyse_merge(
    statement: exp.Merge,
    dictionary: Dictionary,
    band: int,
    unresolved: list[Boundary],
    origin: Origin,
    summaries: dict[str, Any],
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None]:
    """MERGE has multiple targets and conditional arms inside one statement.

    The arms are analysed separately: collapsing them would lose which source populates
    a column in which case, which is the whole difficulty of the construct.
    """
    target = statement.this
    target_name = dictionary.resolve(target.name).name or target.name.upper()

    using = statement.args.get("using")
    if using is None:
        return [], (RefusalCode.UNSUPPORTED_CONSTRUCT, "MERGE without a USING clause")

    # Build a scope over the USING source so its projections can be traced.
    wrapper = exp.Select().select(exp.Star()).from_(using)
    scope = build_scope(wrapper)
    if scope is None:
        return [], (RefusalCode.PARSE_FAILED, "could not build a scope for the MERGE source")

    whens = statement.args.get("whens")
    when_clauses = getattr(whens, "expressions", whens) or []

    edges: list[PredictedEdge] = []
    for when in when_clauses:
        action = when.args.get("then")
        if isinstance(action, exp.Update):
            for setter in action.args.get("expressions") or []:
                if not isinstance(setter, exp.EQ):
                    continue
                target_column = setter.this.name.upper()
                own = _transform_of(setter.expression)
                for column in setter.expression.find_all(exp.Column):
                    for src_table, src_column, traced in _trace(
                        column, scope, dictionary, unresolved
                    ):
                        edges.append(
                            _edge(
                                src_table,
                                src_column,
                                target_name,
                                target_column,
                                _combine(own, traced),
                                band,
                                origin,
                            )
                        )
        elif isinstance(action, exp.Insert):
            # In a MERGE the insert arm carries a Tuple of columns and a Tuple of values,
            # not the Schema/Values pair a standalone INSERT uses.
            target_columns = [c.name.upper() for c in getattr(action.this, "expressions", []) or []]
            values = action.expression
            if isinstance(values, exp.Values):
                items = list(values.expressions[0].expressions)
            else:
                items = list(getattr(values, "expressions", []) or [])
            if not target_columns or len(target_columns) != len(items):
                continue
            for target_column, item in zip(target_columns, items, strict=True):
                own = _transform_of(item)
                for column in item.find_all(exp.Column):
                    for src_table, src_column, traced in _trace(
                        column, scope, dictionary, unresolved
                    ):
                        edges.append(
                            _edge(
                                src_table,
                                src_column,
                                target_name,
                                target_column,
                                _combine(own, traced),
                                band,
                                origin,
                            )
                        )
    return edges, None


def analyse_source(
    source: str,
    dictionary: Dictionary,
    config: AnalysisConfig | None = None,
    band: int = 0,
    summaries: dict[str, Any] | None = None,
) -> AnalysisResult:
    """Analyse one PL/SQL source unit for band-0 lineage."""
    settings = config or AnalysisConfig()
    known_calls = summaries or {}
    result = AnalysisResult()

    program = parse_program(source)

    # Dynamic SQL that is decidable is analysed exactly like static SQL, at mechanism AST.
    # A recovered statement stays BAND 2 though (ADR-0001 §6): the path runs through
    # EXECUTE IMMEDIATE whether or not we could see through it, and crediting band 0 with
    # these would let the band's score claim work it did not do.
    dynamic = resolve_dynamic_sql(program)
    for entry in dynamic.boundaries:
        if entry not in result.boundaries:
            result.boundaries.append(entry)

    recovered = [(statement, 2) for statement in dynamic.statements]
    static = [(statement, band) for statement in program.statements]

    # `$IF` poisons the statements around it rather than itself, so the span is computed
    # once here and every statement inside it is refused. Measured, not assumed: without
    # this the analyser emitted edges for BOTH arms of `u1_conditional_compilation` - two
    # contradictory answers, each stated as fact, and only one of them is in the compiled
    # unit.
    ccflag_spans = conditional_compilation_spans(source)

    for statement, statement_band in static + recovered:
        # DDL that moves data (T3.4b). Checked before the SUPPORTED gate because an
        # exchange is not a DML statement and never will be - and the failure it causes is
        # the worst-shaped one there is: `fct_revenue_part` acquires its entire contents
        # and a DML-only reading reports it as having no writer, which reads as a finding.
        moved, notes = exchange_partition_edges(
            statement.text,
            Origin(unit=_enclosing_unit(program, statement), line=statement.line),
            dictionary,
            band=max(statement_band, 2),
        )
        if moved or notes:
            result.statements_seen += 1
            result.edges.extend(moved)
            for note in notes:
                if note not in result.boundaries:
                    result.boundaries.append(note)
            continue

        if statement.kind not in SUPPORTED:
            continue
        result.statements_seen += 1
        unit = _enclosing_unit(program, statement)
        origin = Origin(unit=unit, line=statement.line)

        # The classifier gates the analyser rather than auditing it (T3.1). Checking
        # afterwards would mean the guessed edge had already been built, and something
        # would have to remember to throw it away. Found by measurement: `CONNECT BY`
        # parsed cleanly and emitted three `x -> x` self-edges, which look entirely
        # ordinary next to a real projection.
        conditional = next(
            (span for span in ccflag_spans if span[0] <= statement.line <= span[1]), None
        )
        if conditional is not None:
            result.refusals.append(
                Refusal(
                    kind=statement.kind,
                    line=statement.line,
                    reason=f"inside the conditional-compilation block at line "
                    f"{conditional[0]} - which arm is compiled depends on PLSQL_CCFLAGS, "
                    f"so claiming this statement's edges would state one of several "
                    f"possible programs as fact",
                    excerpt=" ".join(statement.text.split())[:80],
                    code=RefusalCode.CONTEXT_DEPENDENT,
                    unit=unit,
                    end_line=last_line(statement),
                )
            )
            continue

        construct = classify_statement(statement.text)
        if construct is not None:
            result.refusals.append(
                Refusal(
                    kind=statement.kind,
                    line=statement.line,
                    reason=construct.reason,
                    excerpt=" ".join(statement.text.split())[:80],
                    code=construct.code,
                    unit=unit,
                    end_line=last_line(statement),
                )
            )
            continue

        edges, refusal, unresolved = _analyse_statement(
            statement, dictionary, settings, statement_band, origin, known_calls
        )
        # Declared, counted, and never silent. An identifier the analyser could not
        # resolve is a stated boundary - which is what makes the coverage number
        # defensible rather than decorative.
        for item in unresolved:
            prefix = f"{statement.kind}@{statement.line}: "
            entry = (
                item.prefixed(prefix)
                if isinstance(item, Boundary)
                else Boundary(
                    kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                    subject=str(item),
                    detail=f"{prefix}{item}",
                )
            ).model_copy(update={"unit": unit, "line": statement.line})
            if entry not in result.boundaries:
                result.boundaries.append(entry)
        if refusal is not None:
            code, reason = refusal
            result.refusals.append(
                Refusal(
                    kind=statement.kind,
                    line=statement.line,
                    reason=reason,
                    excerpt=" ".join(statement.text.split())[:80],
                    code=code,
                    unit=unit,
                    end_line=last_line(statement),
                )
            )
            continue
        result.edges.extend(edges)

    # De-duplicate: the same edge derived twice is one fact, and counting it twice would
    # inflate nothing but confusion.
    unique: dict[tuple[str, ...], PredictedEdge] = {}
    for edge in result.edges:
        unique.setdefault(edge.identity(), edge)
    result.edges = [unique[key] for key in sorted(unique)]

    return result


def _enclosing_unit(program: Program, statement: ParsedStatement) -> str:
    """Which program unit a statement sits inside.

    Origin is required on every IR edge: a fact whose origin cannot be stated is not
    evidence. Units are sorted by line, so the last one starting at or before the
    statement is its parent.
    """
    candidates = [unit for unit in program.units if unit.line <= statement.line]
    return candidates[-1].name.upper() if candidates else "<anonymous>"


def _analyse_statement(
    statement: ParsedStatement,
    dictionary: Dictionary,
    config: AnalysisConfig,
    band: int,
    origin: Origin,
    summaries: dict[str, Any],
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None, list[Boundary]]:
    unresolved: list[Boundary] = []
    try:
        parsed: Any = sqlglot.parse_one(statement.text, dialect=DIALECT)
    except Exception as exc:
        return (
            [],
            (RefusalCode.PARSE_FAILED, f"SQLGlot could not parse: {str(exc).splitlines()[0]}"),
            unresolved,
        )

    parsed, notes = _inline_views(parsed, dictionary, config.budgets.view_expansion_depth_cap)

    try:
        parsed = qualify(
            parsed,
            schema=_schema_for(dictionary),
            dialect=DIALECT,
            validate_qualify_columns=False,
            infer_schema=True,
        )
    except Exception as exc:
        return (
            [],
            (
                RefusalCode.NAME_NOT_RESOLVED,
                f"could not qualify names: {str(exc).splitlines()[0]}",
            ),
            unresolved,
        )

    if isinstance(parsed, exp.Insert):
        edges, refusal = _analyse_insert(parsed, dictionary, band, unresolved, origin, summaries)
    elif isinstance(parsed, exp.Merge):
        edges, refusal = _analyse_merge(parsed, dictionary, band, unresolved, origin, summaries)
    else:
        return (
            [],
            (
                RefusalCode.UNSUPPORTED_CONSTRUCT,
                f"unsupported statement type {type(parsed).__name__}",
            ),
            unresolved,
        )

    if refusal is not None:
        return [], refusal, unresolved

    # Notes from view inlining are boundaries too - a view left unexpanded means the
    # lineage below it was not reached.
    #
    # View-inlining notes arrive as prose from the resolver; classify them here, where the
    # reason they exist is still in scope. A view left unexpanded means the lineage below
    # it was never reached, which is a source that was unavailable to us.
    return (
        edges,
        None,
        unresolved
        + [
            Boundary(
                kind=BoundaryKind.SOURCE_UNAVAILABLE,
                subject=str(note).split(" ")[1] if " " in str(note) else str(note),
                detail=str(note),
            )
            for note in notes
        ],
    )
