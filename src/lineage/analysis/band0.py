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
from lineage.analysis.refusal import (
    Refusal,
    RefusalCode,
    classify_statement,
    conditional_compilation_spans,
    last_line,
    not_cross_checkable,
    violations,
)
from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow, Node, NodeKind, Origin, Transform
from lineage.harness.scoring import Mechanism, PredictedEdge, Tier
from lineage.ir.model import Boundary, BoundaryKind, FilterPhase
from lineage.parsing.plsql import ParsedStatement, Program, parse_program
from lineage.parsing.rewrite import strip_unparseable_clauses
from lineage.resolution.dictionary import Dictionary, UnknownObjectError

DIALECT = "oracle"

# Statement kinds this module claims to handle. Anything else is refused rather than
# half-analysed - band 1 constructs belong to the procedural analyser, not here.
#
# `delete_statement` joined this set under stress finding S1-04 / decision D-1. Before
# that a DELETE was not in SUPPORTED, so the loop below skipped it with `continue` BEFORE
# `statements_seen += 1` - it produced no edges, no refusal, and was not even counted as
# seen. Def-use did not claim it either. That is the S2-04 shape: two passes each correctly
# deciding the statement was not theirs, and nobody owning the result.
#
# `update_statement` is deliberately NOT here. An UPDATE's SET clause assigns from
# variables as often as from columns, and `defuse._analyse_update` already owns it with
# the unit scope needed to tell those apart - see the note there.
SUPPORTED = {"insert_statement", "merge_statement", "delete_statement"}




@dataclass
class AnalysisResult:
    edges: list[PredictedEdge] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    boundaries: list[Boundary] = field(default_factory=list)
    statements_seen: int = 0
    incomparable_units: set[str] = field(default_factory=set)
    """Units whose edges are numbered in a different line space from their refusals.

    Trigger bodies, in practice (stress finding S2-10). Populated by `procedure` from the
    dictionary, because band 0 cannot know which units came from there.
    """

    @property
    def statements_analysed(self) -> int:
        return self.statements_seen - len(self.refusals)

    def refusal_violations(self) -> list[tuple[Refusal, list[PredictedEdge]]]:
        """Flagged statements that produced edges anyway (T3.1c). Must be empty."""
        return violations(self.refusals, self.edges)

    def refusals_not_cross_checkable(self) -> list[Refusal]:
        """Refusals whose contradiction check could not run at all (S2-10).

        Reported beside `refusal_violations` so an empty violations list is never read as
        "checked and clean" when part of it was "could not check".
        """
        return not_cross_checkable(self.refusals, self.edges, self.incomparable_units)

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


def _correlated_filter_columns(expression: Any) -> list[Any]:
    """Columns in a nested `WHERE` — a correlated scalar subquery's predicate.

    These genuinely SELECT the row the subquery reads and do not supply what comes back,
    so they are filter influence on the written relation. Counting one as a value source
    produces an edge that type-checks, reads sensibly and is false - the same category
    error as `order_id -> net_amount`.
    """
    found: list[Any] = []
    for where in expression.find_all(exp.Where):
        found.extend(predicate_columns(where))
    return found


def _window_influence_columns(expression: Any) -> list[Any]:
    """A window's `PARTITION BY` and `ORDER BY` columns (stress finding S2-12, D-4).

    These used to be returned alongside the correlated-predicate columns above and emitted
    as `filter`, which was half right. They are certainly not value sources -
    `ROW_NUMBER() OVER (ORDER BY total DESC)` does not take its value from `total` - but
    **a window function removes no rows**, so an edge claiming they decided which rows
    landed in the target is false.

    They get `Flow.INFLUENCE` and point at the OUTPUT COLUMN, because what actually
    depends on them is that column's value: change the partition or the ordering and
    `rank_in_month` changes, while nothing is copied into it.
    """
    found: list[Any] = []
    for window in expression.find_all(exp.Window):
        for part in window.args.get("partition_by") or []:
            found.extend(part.find_all(exp.Column))
        order = window.args.get("order")
        if order is not None:
            found.extend(order.find_all(exp.Column))
    return found


def _value_columns(expression: Any) -> list[Any]:
    """Columns that genuinely supply the value of an expression."""
    excluded = {
        id(column)
        for column in _correlated_filter_columns(expression) + _window_influence_columns(expression)
    }
    return [c for c in expression.find_all(exp.Column) if id(c) not in excluded]


def _projection_named(scope: Scope, name: str) -> Any:
    projections: list[Any] = getattr(scope.expression, "selects", []) or []
    for projection in projections:
        if (projection.alias_or_name or "").upper() == name.upper():
            return projection
    return None


def _row_source_subject(source: exp.Table) -> str:
    """A comparable subject for a row source that has no relation name.

    `FROM TABLE(f(1))` has no table to name, but a boundary's identity is its kind plus
    its subject (IR v0, amendment on BoundaryKind), so "" would make every such boundary
    the same boundary. The function being called is the thing we could not see, so that
    is what gets named: `TABLE(F)`.

    Falls back to the rendered expression when no function name can be found, because an
    unreadable subject is still better than an empty one - it stays comparable, and it
    stays visible in the boundary listing where someone can act on it.
    """
    function = source.this
    arguments = getattr(function, "expressions", None) or []
    if arguments:
        # The called expression rendered without its arguments: `pkg.f(1)` -> `PKG.F`.
        # Walking for the first string `this` finds `PKG` instead, which names the wrong
        # thing - two functions in one package would share a subject and merge into one
        # boundary.
        # Quotes are stripped so `pkg.f` and `"PKG".f` produce the same subject - a
        # boundary's identity is what makes two statements of the same fact comparable,
        # and quoting is not part of the fact.
        called = arguments[0].sql(dialect=DIALECT).strip().upper().replace('"', "")
        head = called.split("(", 1)[0].strip()
        if head:
            return f"TABLE({head})"
    rendered = source.sql(dialect=DIALECT).strip().upper()
    return rendered or "TABLE(?)"


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

        # AND OUTWARD, for a CORRELATED reference (stress finding S3-10). Inside
        # `(SELECT SUM(r.net) FROM rev r WHERE r.rid = s.sid)` the alias `s` belongs to the
        # ENCLOSING query, so it is in neither this scope nor any scope below it. Searching
        # only downward left it unresolved, and the fallback below then bound it to
        # whichever relation this subquery happened to select from.
        outer = getattr(scope, "parent", None)
        climbed = 0
        while outer is not None and climbed < 20:
            if column.table in outer.sources:
                return _trace(column, outer, dictionary, unresolved, depth + 1)
            outer = getattr(outer, "parent", None)
            climbed += 1

    # THE FALLBACK IS FOR UNQUALIFIED NAMES ONLY (S3-10). With one relation in scope,
    # `amount` can only mean that relation's column and binding it is right. `s.sid` is a
    # different claim: the statement said WHICH relation it meant, and if that relation is
    # not reachable from here then guessing the only one that is produces an edge from a
    # relation the reference never named - the s2 silent-failure shape, arrived at from the
    # other direction.
    #
    # It stayed invisible because the corpus's correlations compare columns of the SAME
    # NAME: the wrong binding produced the same edge as the right one and deduplicated into
    # a correct-looking answer. Only a probe with distinguishable names showed it.
    if source is None and len(scope.sources) == 1 and not column.table:
        source = next(iter(scope.sources.values()))

    if isinstance(source, exp.Table):
        table = source.name.upper()
        name = column.name.upper()

        # A ROW SOURCE WITH NO NAME. `FROM TABLE(f(1))` parses as a Table whose `this` is
        # the function call, so `name` is the empty string - there is no relation to look
        # up, and until 2026-09-11 this reached `Boundary(subject="")` and raised
        # ValidationError out of `analyse_source`.
        #
        # A CRASH IS THE WORST OUTCOME AVAILABLE HERE, which is why this guard is separate
        # from the UnknownObjectError path below rather than folded into it. Every other
        # failure in this analyser costs one statement: a refusal, a boundary, at worst a
        # silent miss that still leaves the statement counted. An exception escaping
        # `analyse_source` costs THE WHOLE FILE - every other unit in the package produces
        # nothing, and no refusal, boundary or count records that it happened.
        #
        # The shape of a table function's result is decided by its return type, which is
        # not in the dictionary, so the honest answer is the same one we give an ungranted
        # schema: name what we could not see and emit nothing.
        if not table:
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.DANGLING_REFERENCE,
                    subject=_row_source_subject(source),
                    detail=(
                        f"{_row_source_subject(source)}.{name} "
                        "(row source is a function; its shape is its return type, "
                        "which is not in the dictionary)"
                    ),
                )
            )
            return []

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
        # `_value_columns`, NOT `find_all` (stress finding S3-02). A window's PARTITION BY
        # and ORDER BY columns supply no value, and the caller already excludes them - but
        # only from the projection IT can see. One level of nesting put the window inside a
        # CTE, and this recursion then walked every column in it, so `PARTITION BY cust_id`
        # came back out as a VALUE edge into whatever column the window fed.
        #
        # That is the exact claim D-4 exists to deny: it says a column contributed to a
        # number when it only decided the row ordering. The rule was right and reached one
        # scope; a rule that depends on how the CTEs are stacked is not a rule.
        for inner in _value_columns(projection):
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


def _window_influence(
    projection: Any,
    scope: Scope,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int = 0,
) -> list[tuple[str, str, Transform]]:
    """A window's `PARTITION BY` / `ORDER BY` columns, found at whatever depth they sit.

    The second half of stress finding S3-02. `_window_influence_columns` reads one
    expression, so it only ever saw the projection in the writing statement. Move the
    window into a CTE and the outer projection is a bare `r.prev_amt` - no window in it,
    no influence emitted, and the dependency vanished from the IR entirely.

    **The leak and the loss were one defect with two faces.** The partition columns came
    out as `value` because the recursion dropped the exclusion, and did not come out as
    `influence` because the search never descended. Fixing only the first would have left
    the analyser silent about a real dependency, which is the worse half to leave.

    This deliberately mirrors `_grouping_influence` - same descent, same depth cap, same
    reason. Both answer "which columns govern this projection's value without supplying
    it", and both have to walk the path `_trace` walks rather than assume a depth. Keeping
    the two shapes identical is the point: S2-08 is on the register for a rule that lived
    in two places and drifted, and these two are now visibly the same walk.

    The descent uses `_value_columns` rather than `find_all`: a window's own partition
    column resolves to a base table and would stop there anyway, but descending through it
    would mean re-deriving, at every level, the exclusion this function exists to apply.
    """
    if depth > 20:
        return []

    found: list[tuple[str, str, Transform]] = []
    for column in _window_influence_columns(projection):
        found.extend(_trace(column, scope, dictionary, unresolved, depth + 1))

    for column in _value_columns(projection):
        source = scope.sources.get(column.table) if column.table else None
        if source is None and len(scope.sources) == 1:
            source = next(iter(scope.sources.values()))
        if not isinstance(source, Scope):
            continue
        inner = _projection_named(source, column.name)
        if inner is None:
            continue
        found.extend(_window_influence(inner, source, dictionary, unresolved, depth + 1))
    return found


def _grouping_influence(
    projection: Any,
    scope: Scope,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int = 0,
) -> list[tuple[str, str, Transform]]:
    """`GROUP BY` columns that govern THIS projection's aggregation (S1-03, decision D-2).

    A `GROUP BY` removes no rows - it decides which rows collapse together - so it is not a
    filter. What it decides is the VALUE of every aggregated output column: change the
    grouping and `SUM(amount)` changes, while no part of `cust_id` is in the number. That is
    `Flow.INFLUENCE`, the flow D-4 created for exactly this shape.

    **Only aggregated columns are influenced.** `cust_id` and `TRUNC(order_date)` are in the
    `GROUP BY` and also projected - their values are copied through unchanged, and the
    grouping does nothing to them. Emitting influence onto a grouping key would say the
    column decides its own value.

    **The scope has to be found, not assumed, or the edges are wrong rather than missing.**
    A statement with two aggregating CTEs has two independent groupings, and attaching the
    union of them to every aggregated column would claim `refund_total` is governed by the
    revenue CTE's `GROUP BY`. `stress_cte_window` is exactly that shape. So this walks the
    same path `_trace` walks: a scope contributes its grouping when the projection
    aggregates *there*.

    **EVERY aggregating scope on the path contributes, not just the nearest one.** The first
    cut of this function returned as soon as it found one, and `sq_02_cte_chain` showed what
    that costs: `net_sales` is `gross - refunded` where `refunded` is `MAX(...)` in one CTE
    over a `SUM(...)` from another, each with its own `GROUP BY`. Stopping at the first meant
    the deeper grouping was silently dropped - and it only looked correct in
    `stress_cte_window` because there the two aggregations are siblings rather than nested,
    so both happen to be found at depth 1. A rule that depends on how the CTEs are stacked
    is not a rule. Tracing transitively is what this analyser does everywhere else - through
    views, CTEs and alias chains - and a grouping two levels down is a real dependency of
    the value that comes out.
    """
    if depth > 20:
        return []

    found: list[tuple[str, str, Transform]] = []
    group = scope.expression.args.get("group")
    if group is not None and any(_is_aggregate(node) for node in projection.walk()):
        # WALK THE WHOLE GROUP NODE, not `group.expressions` (stress finding S3-01).
        # SQLGlot files the extended grouping forms under their own args - `rollup`,
        # `cube`, `grouping_sets` - and leaves `expressions` EMPTY, so reading only
        # `expressions` produced no influence at all for any of them. Every column
        # underneath a GROUP BY is a grouping column, whichever form spells it, and
        # walking the node is what makes that true by construction rather than by a list
        # of arg names that has to be kept in step with a third-party parser.
        #
        # The mixed form is why this is the fix and not "add rollup to the loop":
        # `GROUP BY a, ROLLUP (b, c)` fills BOTH args, so an arg-by-arg version that
        # missed one would emit a PARTIAL grouping - and a partial answer here is worse
        # than none, because it looks complete. S2-13 is on the register for exactly that
        # shape: a uniform rule applied to the first instance only.
        for column in group.find_all(exp.Column):
            found.extend(_trace(column, scope, dictionary, unresolved, depth + 1))

    for column in projection.find_all(exp.Column):
        source = scope.sources.get(column.table) if column.table else None
        if source is None and len(scope.sources) == 1:
            source = next(iter(scope.sources.values()))
        if not isinstance(source, Scope):
            continue
        inner = _projection_named(source, column.name)
        if inner is None:
            continue
        found.extend(_grouping_influence(inner, source, dictionary, unresolved, depth + 1))
    return found


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
    phase: FilterPhase = FilterPhase.PRE_AGGREGATION,
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
        phase=phase,
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

    # AN `INSERT ... VALUES` IS NOT A REFUSAL. Stress finding S1-04, decision D-1.
    #
    # This used to refuse the statement as "INSERT ... VALUES carries no column lineage
    # from a relation" - true of relations, false of the statement. ADR-0001 §3 makes
    # variables first-class, so `VALUES (v_cust_id, v_last_login)` is ordinary lineage and
    # writing a temp table row-by-row from cursor variables is ordinary PL/SQL. It was the
    # one false abstention in the signed phase-0 measurement.
    #
    # BAND 0 DELIBERATELY CLAIMS NOTHING HERE, and that is not the same as claiming there
    # is nothing. An unqualified name in a VALUES list is a PL/SQL variable far more often
    # than a column, and this module cannot tell the two apart - it has no unit scope.
    # Binding `v_cust_id` to whichever relation happens to be around is precisely the
    # silent failure `defuse` exists to prevent, so the VALUES list is analysed there
    # (`_analyse_insert_values`) and by `triggers._insert_values_edges` for `:NEW.`
    # correlations.
    #
    # The statement is therefore ANALYSED rather than refused, and a VALUES list of pure
    # literals correctly yields nothing: a literal has no upstream to name, which is the
    # same rule that gives `is_active <- 1` no edge inside a SELECT.
    if isinstance(body, exp.Values):
        return [], None

    if not isinstance(body, exp.Select):
        return [], (
            RefusalCode.UNSUPPORTED_CONSTRUCT,
            f"INSERT from {type(body).__name__}, which is neither a SELECT, a set "
            f"operation nor a VALUES list",
        )

    return _analyse_select_into(
        body, target_name, target_columns, dictionary, band, unresolved, origin, summaries
    )


def _pivot_columns(select: exp.Select) -> tuple[dict[str, tuple[list[Any], Transform]], list[Any]]:
    """What a PIVOT or UNPIVOT's output columns are actually fed by (stress finding S2-03).

    Returns ``(output column -> (source expressions, transform), FOR columns)``.

    The transposed columns are the whole point of the construct and the one part the
    ordinary projection walk cannot see: they exist only in the pivot clause, so tracing
    the outer select list finds the pass-through columns and silently nothing for the
    rest. That partial answer is worse than the refusal it replaced - a reader sees two
    columns traced and reasonably assumes the statement was understood.

    **PIVOT** ``PIVOT (SUM(line_amount) FOR currency IN ('GBP' AS gbp, 'USD' AS usd))``
    gives one output column per IN-list alias, each fed by the aggregate's argument. The
    FOR column decides WHICH output column a row lands in, so it is filter influence.

    **UNPIVOT** ``UNPIVOT (amount FOR measure IN (net_amount, order_count))`` is the
    reverse: one output column fed by SEVERAL inputs at once. `measure` is a generated
    label naming which input a row came from and has no upstream at all.

    Only the literal IN-list form is handled. The subquery form stays refused by
    `PIVOT_SUBQUERY` in the register, because there the output columns ARE the data.
    """
    mapping: dict[str, tuple[list[Any], Transform]] = {}
    for_columns: list[Any] = []

    for pivot in select.find_all(exp.Pivot):
        fields = pivot.args.get("fields") or []
        for clause in fields:
            if not isinstance(clause, exp.In):
                continue
            for_columns.append(clause.this)

            if pivot.unpivot:
                # The value column is named in the pivot's expressions; every column in
                # the IN list feeds it, unchanged.
                for value_column in pivot.expressions:
                    mapping[value_column.alias_or_name.upper()] = (
                        list(clause.expressions),
                        Transform.IDENTITY,
                    )
                continue

            for alias in clause.expressions:
                name = alias.alias_or_name
                if not name:
                    continue
                mapping[name.upper()] = (list(pivot.expressions), Transform.AGGREGATED)

    return mapping, for_columns


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

    pivot_map, pivot_for_columns = _pivot_columns(select)

    edges: list[PredictedEdge] = []
    for target_column, projection in zip(target_columns, projections, strict=True):
        # A transposed column's sources live in the pivot clause, not in the select list,
        # so the ordinary walk below would find nothing for it and say nothing about it.
        pivoted = pivot_map.get(projection.alias_or_name.upper())
        if pivoted is not None:
            expressions, pivot_transform = pivoted
            for expression in expressions:
                for column in expression.find_all(exp.Column):
                    for source_table, source_column, traced in _trace(
                        column, scope, dictionary, unresolved
                    ):
                        edges.append(
                            _edge(
                                source_table,
                                source_column,
                                target_name,
                                target_column,
                                _combine(pivot_transform, traced),
                                band,
                                origin,
                            )
                        )
            continue

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

        # A correlated predicate selects the row the subquery reads, so it is filter
        # influence on the written relation.
        for column in _correlated_filter_columns(projection):
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

        # A window's PARTITION BY / ORDER BY decides which value THIS COLUMN receives, and
        # removes no rows at all (S2-12, D-4). Target is the output column, not the
        # relation - the relation is not what depends on it.
        #
        # Resolved through nested scopes rather than read off this projection (S3-02): the
        # window is as likely to be in a CTE as in the statement that writes.
        for source_table, source_column, _ in _window_influence(
            projection, scope, dictionary, unresolved
        ):
            edges.append(
                _edge(
                    source_table,
                    source_column,
                    target_name,
                    target_column,
                    Transform.IDENTITY,
                    band,
                    origin,
                    flow=Flow.INFLUENCE,
                )
            )

        # A GROUP BY decides which rows collapse together, so it decides the VALUE of an
        # aggregated column without supplying any of it (S1-03, decision D-2). Same flow as
        # the window case above, for the same reason, and the scope is resolved rather than
        # assumed - see `_grouping_influence`.
        for source_table, source_column, _ in _grouping_influence(
            projection, scope, dictionary, unresolved
        ):
            edges.append(
                _edge(
                    source_table,
                    source_column,
                    target_name,
                    target_column,
                    Transform.IDENTITY,
                    band,
                    origin,
                    flow=Flow.INFLUENCE,
                )
            )

    # The pivot's FOR column decides WHICH output column a row lands in. It supplies no
    # value to any of them, which is exactly what a filter edge says.
    for for_column in pivot_for_columns:
        for column in for_column.find_all(exp.Column):
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
        if where is not None:
            for column in predicate_columns(where):
                for source_table, source_column, _ in _trace(
                    column, current, dictionary, unresolved
                ):
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

        # A HAVING removes GROUPS, after every row has been counted (S1-03, decision D-2).
        # It is a filter - rows do disappear - but `WHERE amount > 100` and
        # `HAVING SUM(amount) > 100` produce different results from the same-looking
        # predicate, so the phase is on the edge and the two are distinguishable.
        #
        # THIS CLAUSE WAS NOT READ AT ALL BEFORE D-2. `args.get("having")` appears nowhere
        # in the pre-D-2 source, which is why S1-03's premise - "GROUP BY columns emitted
        # as filter edges" - was false for three stress runs: no GROUP BY or HAVING edge
        # had ever been emitted.
        having = current.expression.args.get("having")
        if having is not None:
            for column in predicate_columns(having):
                for source_table, source_column, _ in _trace(
                    column, current, dictionary, unresolved
                ):
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
                            phase=FilterPhase.POST_AGGREGATION,
                        )
                    )
    return edges


def _analyse_delete(
    statement: exp.Delete,
    dictionary: Dictionary,
    band: int,
    unresolved: list[Boundary],
    origin: Origin,
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None]:
    """`DELETE FROM t WHERE ...` — stress finding S1-04, decision D-1, convention (c).

    A DELETE writes no value anywhere, so it has no value edges at all. What it does have
    is a **predicate that decides which rows stop existing**, and the resulting contents of
    the table depend on those columns exactly as much as an INSERT's contents depend on its
    select list. `stg_customer.status_code` deciding which revenue rows are destroyed is
    the kind of dependency that never appears in a table-level tool and is the reason
    filter influence is an edge at all (ADR-0001 §2).

    THIS STATEMENT USED TO PRODUCE NOTHING AND SAY NOTHING. `delete_statement` was not in
    `SUPPORTED`, so band 0 skipped it *before* `statements_seen += 1` and def-use never
    claimed it either - the S2-04 shape exactly, two passes each correctly deciding the
    statement was not theirs and nobody owning the result. It was logged under S2-06 as an
    open convention question, which understated it: whatever convention (c) was decided,
    silence was never the right output.

    The target is the deleted relation and it is also, unavoidably, the thing the edges
    point AT. A self-referencing filter edge reads oddly the first time - it says "these
    columns determine what this table contains", which is precisely the fact.
    """
    target = statement.this
    if isinstance(target, exp.Table):
        name = target.name
    else:
        return [], (
            RefusalCode.UNSUPPORTED_CONSTRUCT,
            f"DELETE from {type(target).__name__}, which is not a table reference",
        )

    resolved = dictionary.resolve(name)
    target_name = resolved.name or name.upper()

    where = statement.args.get("where")
    if where is None:
        # `DELETE FROM t` with no predicate empties the table unconditionally. There are no
        # columns to name, and that is a complete answer rather than a missing one - the
        # same shape as an INSERT whose every value is a literal.
        return [], None

    # A DELETE has no projection, so `qualify` has nothing to bind against and
    # `build_scope` is not usable the way it is for a SELECT. The predicate is walked
    # directly instead, and every column in it - including the ones inside an IN or EXISTS
    # subquery - is filter influence on the deleted relation. Nesting does not change what
    # the column does: `status_code` two levels down still decides which rows go.
    edges: list[PredictedEdge] = []
    for column in predicate_columns(where):
        source_table = _relation_for(column, statement, dictionary, unresolved)
        if source_table is None:
            continue
        edges.append(
            _edge(
                source_table,
                column.name.upper(),
                target_name,
                "",
                Transform.IDENTITY,
                band,
                origin,
                flow=Flow.FILTER,
            )
        )
    return edges, None


def _relation_for(
    column: exp.Column,
    statement: Any,
    dictionary: Dictionary,
    unresolved: list[Boundary],
) -> str | None:
    """Which relation does this column belong to, inside a statement with no select scope?

    Qualified is the easy case: the alias is looked up among the statement's table
    references. Unqualified means searching them, and **an unqualified name that more than
    one table could supply is declared rather than guessed** - binding it to whichever
    table happens to be first is the `sq_07` failure, and a plausible wrong source is worse
    than a stated boundary.
    """
    tables: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        resolved = dictionary.resolve(table.name)
        real = resolved.name or table.name.upper()
        tables[(table.alias or table.name).upper()] = real

    qualifier = (column.table or "").upper()
    if qualifier:
        if qualifier in tables:
            return tables[qualifier]
        unresolved.append(
            Boundary(
                kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                subject=f"{qualifier}.{column.name.upper()}",
                detail=f"qualifier {qualifier} names no table in the statement",
            )
        )
        return None

    name = column.name.upper()
    owners = []
    for real in dict.fromkeys(tables.values()):
        try:
            if name in dictionary.columns_of(real):
                owners.append(real)
        except UnknownObjectError:
            continue
    if len(owners) == 1:
        return owners[0]
    if not owners:
        return None
    unresolved.append(
        Boundary(
            kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
            subject=name,
            detail=f"unqualified {name} could come from {' or '.join(sorted(owners))}",
        )
    )
    return None


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

    # THE TARGET IS IN SCOPE IN A MERGE, AND `scope` DOES NOT KNOW IT (stress finding S3-07).
    # `scope` is built over the USING clause alone, so `t.lifetime_value` in
    # `SET lifetime_value = NVL(t.lifetime_value, 0) + s.amount` resolved against the wrong
    # side and came out as `unresolved_identifier`. That accumulator pattern is how a MERGE
    # adds to a running total, and the self-edge it produces is a real one - `trg_recent_audit`
    # has the same shape and `b2_05` has labelled it since it was written.
    target_alias = (target.alias or target.name or "").upper()

    def _trace_in_merge(column: exp.Column) -> list[tuple[str, str, Transform]]:
        """Trace against the USING source, or against the MERGE target itself.

        The USING scope is consulted FIRST: an alias that exists there is that source's,
        whatever it is called, and preferring the target would silently redirect a real
        source reference at the table being written.
        """
        qualifier = (column.table or "").upper()
        if qualifier and qualifier not in scope.sources and qualifier == target_alias:
            name = column.name.upper()
            try:
                known = dictionary.columns_of(target_name)
            except UnknownObjectError:
                known = []
            if name in known:
                return [(target_name, name, Transform.IDENTITY)]
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                    subject=f"{target_name}.{name}",
                    detail=f"{name} (not a column of MERGE target {target_name})",
                )
            )
            return []
        return _trace(column, scope, dictionary, unresolved)

    edges: list[PredictedEdge] = []

    # A MERGE FILTERS IN TWO PLACES AND USED TO REPORT NEITHER (stress finding S3-03).
    # Everything below built value edges only, so `WHERE c.signup_date < SYSDATE` inside the
    # USING clause - a predicate that decides which customers the load touches at all -
    # produced nothing. That is the finding filter lineage exists for: ADR-0001 2 makes the
    # case on a policy table silently governing which rows load, and this is that case in
    # the statement type a warehouse does its upserts with.
    #
    # The ON clause is deliberately NOT here. It is a join condition, structural wherever
    # written (D-5), and reading it as a filter is what S2-14 was.
    edges.extend(
        _filter_edges(wrapper, scope, target_name, band, dictionary, unresolved, origin)
    )

    for when in when_clauses:
        action = when.args.get("then")
        if isinstance(action, exp.Update):
            # The arm's own WHERE - `WHEN MATCHED THEN UPDATE SET ... WHERE x.b > 0`. It
            # sits on the Update rather than on the WHEN, and it decides which matched rows
            # are actually written, so it filters this statement's effect as surely as the
            # USING predicate does.
            arm_where = action.args.get("where")
            if arm_where is not None:
                for column in predicate_columns(arm_where):
                    for source_table, source_column, _ in _trace_in_merge(column):
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

            for setter in action.args.get("expressions") or []:
                if not isinstance(setter, exp.EQ):
                    continue
                target_column = setter.this.name.upper()
                own = _transform_of(setter.expression)
                for column in setter.expression.find_all(exp.Column):
                    for src_table, src_column, traced in _trace_in_merge(column):
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
        parsed: Any = sqlglot.parse_one(
            strip_unparseable_clauses(statement.text), dialect=DIALECT
        )
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
    elif isinstance(parsed, exp.Delete):
        edges, refusal = _analyse_delete(parsed, dictionary, band, unresolved, origin)
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
