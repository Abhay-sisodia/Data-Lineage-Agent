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

from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow, Node, NodeKind, Transform
from lineage.harness.scoring import Mechanism, PredictedEdge, Tier
from lineage.parsing.plsql import ParsedStatement, parse_program
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
CONDITIONAL_EXPRESSIONS = (exp.Case, exp.If)


@dataclass(frozen=True)
class Refusal:
    """A statement the analyser declined to interpret.

    The classifier that says "I cannot resolve this" is itself a deliverable. A refusal
    is a declared boundary; a silent omission is a hole.
    """

    kind: str
    line: int
    reason: str
    excerpt: str


@dataclass
class AnalysisResult:
    edges: list[PredictedEdge] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)
    statements_seen: int = 0

    @property
    def statements_analysed(self) -> int:
        return self.statements_seen - len(self.refusals)

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
    if any(isinstance(node, CONDITIONAL_EXPRESSIONS) for node in expression.walk()):
        return Transform.CONDITIONAL
    return Transform.DERIVED


def _combine(first: Transform, second: Transform) -> Transform:
    return first if TRANSFORM_RANK[first] >= TRANSFORM_RANK[second] else second


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
    unresolved: list[str],
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

    if source is None and len(scope.sources) == 1:
        source = next(iter(scope.sources.values()))

    if isinstance(source, exp.Table):
        table = source.name.upper()
        name = column.name.upper()
        try:
            known = dictionary.columns_of(table)
        except UnknownObjectError:
            unresolved.append(f"{table}.{name} (relation not in dictionary)")
            return []
        if name not in known:
            # Not a column of this table. Almost always a PL/SQL variable, which is
            # band-1 territory and must be resolved by def-use analysis, not guessed at.
            unresolved.append(f"{name} (not a column of {table} - unresolved identifier)")
            return []
        return [(table, name, Transform.IDENTITY)]

    if isinstance(source, Scope):
        projection = _projection_named(source, column.name)
        if projection is None:
            unresolved.append(f"{column.name.upper()} (no matching projection in subquery)")
            return []
        own = _transform_of(projection)
        traced: list[tuple[str, str, Transform]] = []
        for inner in projection.find_all(exp.Column):
            for table, name, transform in _trace(inner, source, dictionary, unresolved, depth + 1):
                traced.append((table, name, _combine(own, transform)))
        return traced

    unresolved.append(f"{column.name.upper()} (could not resolve which relation it belongs to)")
    return []


def _edge(
    source_table: str,
    source_column: str,
    target: str,
    target_column: str,
    transform: Transform,
    band: int,
    flow: Flow = Flow.VALUE,
) -> PredictedEdge:
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


def _analyse_insert(
    statement: exp.Insert, dictionary: Dictionary, band: int, unresolved: list[str]
) -> tuple[list[PredictedEdge], str | None]:
    target_name, target_columns = _target_of(statement, dictionary)

    select = statement.expression
    if not isinstance(select, exp.Select):
        return [], "INSERT ... VALUES carries no column lineage from a relation"

    scope = build_scope(select)
    if scope is None:
        return [], "could not build a scope for the SELECT"

    if target_columns is None:
        try:
            target_columns = dictionary.columns_of(target_name)
        except UnknownObjectError as exc:
            return [], str(exc)

    projections = select.selects
    if len(projections) != len(target_columns):
        return [], (
            f"projection/target arity mismatch: {len(projections)} selected into "
            f"{len(target_columns)} columns"
        )

    edges: list[PredictedEdge] = []
    for target_column, projection in zip(target_columns, projections, strict=True):
        own = _transform_of(projection)
        for column in projection.find_all(exp.Column):
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
                    )
                )

    edges.extend(_filter_edges(select, scope, target_name, band, dictionary, unresolved))
    return edges, None


def _filter_edges(
    select: exp.Select,
    scope: Scope,
    target_name: str,
    band: int,
    dictionary: Dictionary,
    unresolved: list[str],
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
                        flow=Flow.FILTER,
                    )
                )
    return edges


def _analyse_merge(
    statement: exp.Merge, dictionary: Dictionary, band: int, unresolved: list[str]
) -> tuple[list[PredictedEdge], str | None]:
    """MERGE has multiple targets and conditional arms inside one statement.

    The arms are analysed separately: collapsing them would lose which source populates
    a column in which case, which is the whole difficulty of the construct.
    """
    target = statement.this
    target_name = dictionary.resolve(target.name).name or target.name.upper()

    using = statement.args.get("using")
    if using is None:
        return [], "MERGE without a USING clause"

    # Build a scope over the USING source so its projections can be traced.
    wrapper = exp.Select().select(exp.Star()).from_(using)
    scope = build_scope(wrapper)
    if scope is None:
        return [], "could not build a scope for the MERGE source"

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
                            )
                        )
    return edges, None


def analyse_source(
    source: str,
    dictionary: Dictionary,
    config: AnalysisConfig | None = None,
    band: int = 0,
) -> AnalysisResult:
    """Analyse one PL/SQL source unit for band-0 lineage."""
    settings = config or AnalysisConfig()
    result = AnalysisResult()

    program = parse_program(source)
    for statement in program.statements:
        if statement.kind not in SUPPORTED:
            continue
        result.statements_seen += 1
        edges, refusal, unresolved = _analyse_statement(statement, dictionary, settings, band)
        # Declared, counted, and never silent. An identifier the analyser could not
        # resolve is a stated boundary - which is what makes the coverage number
        # defensible rather than decorative.
        for item in unresolved:
            entry = f"{statement.kind}@{statement.line}: {item}"
            if entry not in result.boundaries:
                result.boundaries.append(entry)
        if refusal is not None:
            result.refusals.append(
                Refusal(
                    kind=statement.kind,
                    line=statement.line,
                    reason=refusal,
                    excerpt=" ".join(statement.text.split())[:80],
                )
            )
            continue
        result.edges.extend(edges)

    # De-duplicate: the same edge derived twice is one fact, and counting it twice would
    # inflate nothing but confusion.
    unique: dict[tuple[str, str, str, str], PredictedEdge] = {}
    for edge in result.edges:
        unique.setdefault(edge.key(), edge)
    result.edges = [unique[key] for key in sorted(unique)]

    return result


def _analyse_statement(
    statement: ParsedStatement,
    dictionary: Dictionary,
    config: AnalysisConfig,
    band: int,
) -> tuple[list[PredictedEdge], str | None, list[str]]:
    unresolved: list[str] = []
    try:
        parsed: Any = sqlglot.parse_one(statement.text, dialect=DIALECT)
    except Exception as exc:
        return [], f"SQLGlot could not parse: {str(exc).splitlines()[0]}", unresolved

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
        return [], f"could not qualify names: {str(exc).splitlines()[0]}", unresolved

    if isinstance(parsed, exp.Insert):
        edges, refusal = _analyse_insert(parsed, dictionary, band, unresolved)
    elif isinstance(parsed, exp.Merge):
        edges, refusal = _analyse_merge(parsed, dictionary, band, unresolved)
    else:
        return [], f"unsupported statement type {type(parsed).__name__}", unresolved

    if refusal is not None:
        return [], refusal, unresolved

    # Notes from view inlining are boundaries too - a view left unexpanded means the
    # lineage below it was not reached.
    return edges, None, unresolved + notes
