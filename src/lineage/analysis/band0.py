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

import dataclasses
import re

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, ScopeType, build_scope

from lineage.analysis.builtins import is_builtin
from lineage.analysis.pg_dml import expand as expand_postgres_writes
from lineage.analysis.catalog import ScriptCatalog
from lineage.analysis.relations import AMBIGUOUS, identifier
from lineage.analysis.srf import restate as restate_srfs
from lineage.analysis.relations import bind as bind_relations
from lineage.analysis.relations import (
    SEARCH_PATH,
    expand_qualified_stars,
    is_path_change,
    search_path_change,
    session_keywords,
)
from lineage.analysis.system_columns import restate as restate_system_columns
from lineage.analysis.whole_rows import restate as restate_whole_rows
from lineage.analysis.whole_rows import star_rows
from lineage.analysis.ddl import exchange_partition_edges
from lineage.analysis.dynamic import resolve_dynamic_sql
from lineage.analysis.predicates import (
    in_grouping_function,
    is_outer_join,
    on_condition_columns,
    predicate_columns,
)
from lineage.analysis.transforms import (
    AGGREGATE_FUNCTIONS,
    CONDITIONAL_EXPRESSIONS,
    TRANSFORM_RANK,
    VALUE_SELECTING_WINDOW_FUNCTIONS,
    combine as _combine,
    is_aggregate as _is_aggregate,
    is_conditional as _is_conditional,
    transform_of as _transform_of,
    transform_of_path as _transform_of_path,
)
from lineage.analysis.refusal import (
    Refusal,
    RefusalCode,
    classify_statement,
    conditional_compilation_spans,
    cte_scoped_construct,
    last_line,
    not_cross_checkable,
    violations,
)
from lineage.config import AnalysisConfig
from lineage.dialects import fold, for_name
from lineage.harness.labels import Flow, Node, NodeKind, Origin, Transform
from lineage.harness.scoring import Mechanism, PredictedEdge, Tier
from lineage.ir.model import Boundary, BoundaryKind, FilterPhase
from lineage.parsing.sqlscript import SCRIPT_UNIT, skip_lexeme, statement_kind
from lineage.parsing.plsql import ParsedStatement, Program, parse_program
from lineage.parsing.rewrite import strip_unparseable_clauses
from lineage.resolution.dictionary import Dictionary, UnknownObjectError

# The dialect is not a constant here any more. It travels on the captured dictionary,
# because a dictionary belongs to one database and already reaches every resolver in this
# module - see `lineage.dialects.base` for what the seam holds and what it deliberately
# does not. `AnalysisConfig.dialect` stays the declared authority and the entry points
# check the two agree.

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
SUPPORTED = {
    "insert_statement",
    "merge_statement",
    "delete_statement",
    # CREATE TABLE AS SELECT. Added 2026-09-23 after `probe_postgres.py` found it silent;
    # `_analyse_create_table_as` declines every other kind of CREATE by name.
    "create_statement",
}




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

    partial_statements: int = 0
    """Statements analysed with one or more CTEs refused out of them (finding PG-20).

    A category of its own rather than a share of an existing number. These statements DID
    produce lineage, so counting them as unanalysed would be false; they also withheld a
    named part of themselves, so counting them as wholly analysed would be false too. The
    honest report is both numbers plus this one, and a reader who wants the strict figure
    can subtract it.
    """

    join_keys: list[PredictedEdge] = field(default_factory=list)
    """Lookup-join keys, `Flow.JOIN_KEY` (D-57). Kept OUT of `edges` on purpose.

    D-5 gives a join key no edge and every closed-world key is written against `edges`, so
    a key here changes no score. Filled by `procedure.analyse_source` only; a direct band-0
    call leaves it empty.
    """

    scoped_statements: set[tuple[str, int]] = field(default_factory=set)
    """`(unit, line)` of each statement whose refusal was narrowed to a CTE.

    Read by `procedure.analyse_source`, whose second classifier pass would otherwise
    re-refuse the same statement WHOLE - counting it twice and contradicting the edges the
    first pass legitimately produced. The dedup there keys on the statement's own address,
    which a CTE-scoped refusal deliberately does not carry.
    """

    @property
    def statements_analysed(self) -> int:
        """Statements that produced whatever lineage they had to give.

        **CTE-scoped refusals are not subtracted** (PG-20). A refusal that withdraws one
        CTE of eighty-five leaves a statement that was analysed, and whose edges are real;
        subtracting it would report `analysed 0` beside a non-empty edge list, which is
        not conservatism but an arithmetic that contradicts its own output. The withheld
        part is counted by `partial_statements` instead, where it stays visible.
        """
        whole = [refusal for refusal in self.refusals if refusal.scope == "statement"]
        return self.statements_seen - len(whole)

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

    **A relation with NO columns is omitted, and the omission is load-bearing.** SQLGlot's
    `qualify` raises `Table <name> must have at least one column` on an empty entry, and it
    raises for the STATEMENT - so one column-less relation anywhere in the dictionary
    refuses every statement in the run, including statements that never reference it.
    Measured 2026-09-24: a two-relation dictionary with one empty entry refused
    `SELECT o.cust_id FROM s.orders o`, which does not mention the empty table at all.

    Neither shipped dictionary has an empty entry, which is why this was never seen here.
    A CAPTURE easily can: a table whose columns the capturing user lacks privileges to read
    comes back with an empty list, and a permission gap on one unrelated table would then
    take the whole run to zero edges.

    Omitting it is the honest behaviour rather than a workaround. An entry with no columns
    tells SQLGlot nothing it can use; leaving it out means references to that table resolve
    as DANGLING - visible, counted, local to the statement that touches it - which is what
    the taxonomy says an unknown object should be. The alternative is a catastrophic
    refusal whose stated reason names a table the reader never asked about.
    """
    # KEYED BY SCHEMA, THEN TABLE (finding B9-01). Keyed by bare table name, the last of
    # two same-named tables won: core.ledger replaced staging.ledger's columns. Every
    # unqualified relation is bound to a schema before `qualify` (`relations.bind`), so
    # SQLGlot never has to choose. A name whose stored case is not what folding would give
    # - a quoted mixed-case object - is passed quoted, or SQLGlot would fold it (B9-02).
    schema: dict[str, dict[str, dict[str, str]]] = {}
    for qualified, columns in dictionary.columns.items():
        if not columns:
            continue
        owner, _, table = qualified.partition(".")
        schema.setdefault(_as_key(owner, dictionary), {})[_as_key(table, dictionary)] = {
            _as_key(column, dictionary): "UNKNOWN" for column in columns
        }
    return schema


_SCHEMA_QUALIFIED_DIALECTS = frozenset({"postgres", "spark", "databricks"})
"""Dialects whose relation nodes are `schema.table` (D-25; Spark and Databricks by PO-0)."""


def _relation_name(resolved: Any, fallback: str, dictionary: Dictionary) -> str:
    """How a relation is named in the lineage graph.

    **D-25: a PostgreSQL relation is named `schema.table`.** Bare names merged
    `staging.ledger` and `core.ledger` into one node, so a copy from one to the other read
    as a self-edge and their lineage ran together. Oracle keeps the bare name its keys and
    corpus were written against.

    Spark and Databricks follow PostgreSQL (finding PO-0): a lakehouse model reads
    `silver_s2p.x` and `gold_common.x` side by side, which is exactly the merge D-25 exists
    to prevent. Named rather than "everything but Oracle", so a dialect nobody has measured
    does not acquire a naming rule by default.
    """
    if resolved.name is None:
        return fallback
    if dictionary.dialect in _SCHEMA_QUALIFIED_DIALECTS and resolved.owner:
        return f"{resolved.owner}.{resolved.name}"
    return resolved.name


def _as_key(name: str, dictionary: Dictionary) -> str:
    return name if fold(name, dictionary.dialect) == name else f'"{name}"'


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
            # A write's TARGET is never a view to expand (D-31: a view registered in the
            # script is briefly a table while its own CREATE is analysed).
            holder = table.parent.parent if isinstance(table.parent, exp.Schema) else table.parent
            if isinstance(holder, (exp.Insert, exp.Create)) and (
                holder.this is table or holder.this is table.parent
            ):
                continue
            # The schema is part of the name: a bare lookup sent `mart.v_paid` through the
            # default schema and missed it (B7, D-31).
            if table.args.get("db"):
                name = f"{table.text('db')}.{name}"
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
                inner = sqlglot.parse_one(text, dialect=dictionary.dialect)
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


def _named_window_definitions(select: Any) -> dict[str, Any]:
    """`WINDOW w AS (...)` clauses of ONE select, keyed by name.

    The key is upper-cased on both sides of the comparison, which is a same-normalisation
    test rather than a folding decision: both names come from the same source text, and a
    window name is local to its select - it never reaches the dictionary, an IR node or a
    match key. This is one of the residue sites `dialects/base.py` enumerates.
    """
    found: dict[str, Any] = {}
    for definition in select.args.get("windows") or []:
        name = definition.this
        if isinstance(name, exp.Identifier):
            found[name.name.upper()] = definition
    return found


def _resolve_named_windows(expression: Any) -> Any:
    """Copy each `WINDOW w AS (...)` definition into the `OVER w` sites that name it.

    **Stress finding PG-14, and it was SILENT.** SQLGlot parses `RANK() OVER w` as a
    `Window` carrying an `alias` and NOTHING ELSE - the `PARTITION BY` and `ORDER BY` live
    in the select's `windows` argument, outside every projection. `_window_influence_columns`
    reads a window's own `partition_by` and `order`, finds both empty, and emits no
    influence at all. No refusal, no boundary, 100% parse coverage.

    Worse in the form the construct exists for. A named window is written to be SHARED, and
    with two functions on one window `RANK() OVER w` produces no edge of any kind - the
    target column leaves the lineage entirely, and the value edge from `SUM(x) OVER w`
    survives beside it so the output still looks populated.

    Found by a metamorphic pair, not by a key: `OVER (PARTITION BY ...)` and
    `OVER w ... WINDOW w AS (PARTITION BY ...)` are two spellings of one query, and the
    lineage differed. No ground truth was needed to know one of them was wrong.

    Done as a REWRITE rather than by teaching `_window_influence_columns` to look upwards,
    because the rewrite is what the SQL already means - a named window is defined as
    textual substitution - and because every other consumer (value columns, the transform
    ladder, the influence walk) then works unchanged rather than each needing its own
    lookup. S2-08 is on the register for one rule that acquired two homes and drifted.

    **A no-op for any statement without a `WINDOW` clause**, which is every Oracle package
    in the corpus and every statement in the four Oracle stress files.

    **Inheritance (finding TT-2).** A window may name another as its base -
    `WINDOW b AS (a ORDER BY d)`, or inline `OVER (a ORDER BY y)` - and SQLGlot carries the
    base's name in `alias` exactly as it does for `OVER a`. PostgreSQL copies the base's
    PARTITION BY, and its ORDER BY when the new window gives none. Only the plain `OVER a`
    form used to be resolved: a definition's own base was never followed, and a window
    carrying an ORDER BY of its own was skipped, so in the shipment torture query `lag`
    over `w_cust_ship AS (w_cust ORDER BY ...)` lost w_cust's partition influence.
    """
    for window in list(expression.find_all(exp.Window)):
        _inherit_window(window, frozenset())
    return expression


def _inherit_window(window: Any, resolving: frozenset[str]) -> None:
    """Fill a window's missing PARTITION BY / ORDER BY from the window its `alias` names.

    The base is resolved first, so a chain `c AS (b ...)`, `b AS (a ...)` arrives whole.
    `resolving` guards a cycle in malformed SQL. Only absent parts are filled: what a
    window states itself always wins, which is also what makes this safe to run twice.
    """
    alias = window.args.get("alias")
    if alias is None:
        return  # an inline OVER (...) with no base, or a definition with none
    name = (alias.name if isinstance(alias, exp.Identifier) else str(alias)).upper()
    if name in resolving:
        return

    # A window name is visible in its own select, so resolution walks outwards. It stops
    # at the first select that defines the name - an inner definition shadows an outer
    # one, exactly as a column alias does.
    definition = None
    scope = window.parent_select
    while scope is not None:
        definition = _named_window_definitions(scope).get(name)
        if definition is not None and definition is not window:
            break
        definition = None
        scope = scope.parent_select
    if definition is None:
        return

    _inherit_window(definition, resolving | {name})
    partition = definition.args.get("partition_by")
    if partition and not window.args.get("partition_by"):
        window.set("partition_by", [part.copy() for part in partition])
    order = definition.args.get("order")
    if order is not None and window.args.get("order") is None:
        window.set("order", order.copy())


def _correlated_filter_columns(expression: Any) -> list[Any]:
    """Columns in a nested `WHERE` — a correlated scalar subquery's predicate.

    These genuinely SELECT the row the subquery reads and do not supply what comes back,
    so they are filter influence on the written relation. Counting one as a value source
    produces an edge that type-checks, reads sensibly and is false - the same category
    error as `order_id -> net_amount`.
    """
    found: list[Any] = []
    for where in expression.find_all(exp.Where):
        # `agg(x) FILTER (WHERE c)` is not a subquery predicate (decision D-10, closing
        # PG-15). SQLGlot gives the FILTER clause its own `Where`, so this walk took `c`
        # for a correlation and reported it as deciding which rows the TABLE holds - while
        # `agg(CASE WHEN c THEN x END)`, the same number, reported `c` as a value of the
        # column. A FILTER changes one aggregate's value and removes no row from the result,
        # so its columns are left to `_value_columns`, exactly as the CASE spelling's are.
        if isinstance(where.parent, exp.Filter):
            continue
        found.extend(predicate_columns(where))
    return found


def _window_influence_columns(expression: Any) -> list[Any]:
    """Columns that ORDER or PARTITION a computation without supplying its value (D-4).

    Two constructs, one rule:

    * a **window's** `PARTITION BY` / `ORDER BY` (stress finding S2-12);
    * an **ordered aggregate's** own `ORDER BY` - `string_agg(code, ',' ORDER BY d)`.

    These used to be returned alongside the correlated-predicate columns above and emitted
    as `filter`, which was half right. They are certainly not value sources -
    `ROW_NUMBER() OVER (ORDER BY total DESC)` does not take its value from `total` - but
    **neither construct removes a row**, so an edge claiming they decided which rows
    landed in the target is false.

    They get `Flow.INFLUENCE` and point at the OUTPUT COLUMN, because what actually
    depends on them is that column's value: change the partition or the ordering and
    `rank_in_month` changes, while nothing is copied into it.

    **The ordered-aggregate half was added after PG-03 unmasked it**, and the two halves
    live in ONE function deliberately. `stress_pg_02`'s key had always labelled
    `order_date -> note` as influence and said why - "that is the definition of influence
    (D-4), arrived at for window functions and applying unchanged to an ordered aggregate" -
    but the column sat behind a LATERAL alias, so it never resolved and the gap could not
    show. The moment laterals resolved, it came out as a VALUE edge and the key's forbidden
    list caught it. S2-08 is on the register for a rule that lived in two places and
    drifted; this is the same rule, so it gets one home.

    SQLGlot models `string_agg(a, ',' ORDER BY b)` as `GroupConcat(this=Order(...))`, so the
    ordering keys are the `Order` node's `expressions` and the aggregated value is its
    `this` - taking the whole `Order` would exclude the value itself.
    """
    found: list[Any] = []
    for window in expression.find_all(exp.Window):
        for part in window.args.get("partition_by") or []:
            found.extend(part.find_all(exp.Column))
        order = window.args.get("order")
        if order is not None:
            found.extend(order.find_all(exp.Column))

    for order in expression.find_all(exp.Order):
        # A built-in aggregate SQLGlot does not type - `jsonb_agg(x ORDER BY y)`,
        # `xmlagg(...)` - is an Anonymous, and its ORDER BY became a VALUE (finding B5-03).
        parent = order.parent
        if not (
            isinstance(parent, exp.AggFunc)
            or (isinstance(parent, exp.Anonymous) and _is_aggregate(parent))
        ):
            continue
        for ordered in order.expressions or []:
            found.extend(ordered.find_all(exp.Column))
    return found


_NON_VALUE_CLAUSES = (
    "where",
    "having",
    "group",
    "order",
    "qualify",
    "limit",
    "offset",
    "fetch",
    # A derived table inside a nested query supplies its value through the alias the
    # nested query projects (`MAX(z.amt)`), which is traced with its full path. Sweeping
    # the derived table's own columns too skipped the MAX on the way (B2 T2 S10).
    "from",
    "from_",
    "joins",
)


def _in_non_value_clause(column: Any, root: Any) -> bool:
    """Is this column, below `root`, inside a clause of a NESTED query that supplies no value?

    A subquery inside an expression supplies its value through its PROJECTION. Its WHERE,
    HAVING, GROUP BY and ORDER BY decide which rows or which order, and its join conditions
    are structural (D-5). Walking `find_all(exp.Column)` over the whole expression swept all
    of them in as value sources:

    * `(SELECT p.amount FROM p WHERE ... ORDER BY p.paid_at DESC LIMIT 1)` reported
      `paid_at` as a VALUE of the column (finding B2-02, S2-12's defect in a new position);
    * a join inside a scalar subquery reported its keys as values (B2-03).
    """
    node = column
    while node is not None and node is not root:
        parent = node.parent
        if isinstance(parent, exp.Select) and parent is not root:
            arg = node.arg_key
            if arg in _NON_VALUE_CLAUSES:
                return True
        if isinstance(parent, exp.Join) and node.arg_key in ("on", "using"):
            return True
        node = parent
    return False


def _value_columns(expression: Any) -> list[Any]:
    """Columns that genuinely supply the value of an expression."""
    excluded = {
        id(column)
        for column in _correlated_filter_columns(expression) + _window_influence_columns(expression)
    }
    return [
        c
        for c in expression.find_all(exp.Column)
        if id(c) not in excluded
        and not _in_non_value_clause(c, expression)
        and not in_grouping_function(c)  # D-50: grouping(a) carries no lineage
    ]


def _scope_index(scope: Scope) -> dict[int, Scope]:
    """Every scope under `scope`, keyed by the identity of the query node it analyses.

    LATERAL and subquery wrappers are unwrapped (`_query_of_scope`) so a SELECT found by
    walking up from a column maps to the scope that owns it.
    """
    index: dict[int, Scope] = {}
    for current in scope.traverse():
        index.setdefault(id(_query_of_scope(current.expression)), current)
        index.setdefault(id(current.expression), current)
    return index


def _is_recursive_anchor_shell(scope: Scope) -> bool:
    """Is this SQLGlot's source-less scope for a recursive CTE's self-reference? (TT-3)

    It covers the anchor arm - the left side of the UNION that is the body of a CTE in a
    RECURSIVE WITH - and has no sources, while the anchor's real scope (a union arm) does.
    """
    if scope.sources:
        return False
    union = scope.expression.parent
    if not isinstance(union, exp.Union) or union.this is not scope.expression:
        return False
    cte = union.parent
    return (
        isinstance(cte, exp.CTE)
        and isinstance(cte.parent, exp.With)
        and bool(cte.parent.args.get("recursive"))
    )


def _home_scope(column: Any, fallback: Scope, index: dict[int, Scope]) -> Scope:
    """The scope a column was WRITTEN in: the innermost query that contains it.

    **Finding B2-08.** A predicate sweep over an outer WHERE collects the columns of every
    subquery nested in it, and they were all traced in the OUTER scope. PostgreSQL resolves
    a name innermost-first, so in `FROM orders o WHERE EXISTS (SELECT 1 FROM payments o
    WHERE o.order_id = o.id)` the inner `o` is payments - and the outer-scope trace bound it
    to orders, a confident wrong edge. Tracing from the home scope gets shadowing right by
    construction; a genuine correlation still resolves outward, because `_trace` climbs to
    the parent scope when the alias is not local.
    """
    node = getattr(column, "parent", None)
    while node is not None:
        if isinstance(node, (exp.Select, exp.SetOperation)):
            home = index.get(id(node))
            if home is not None:
                return home
        node = getattr(node, "parent", None)
    return fallback


def _ancestors_until(column: Any, stop: Any) -> list[Any]:
    found: list[Any] = []
    node = getattr(column, "parent", None)
    while node is not None and node is not stop:
        found.append(node)
        node = getattr(node, "parent", None)
    return found


def _query_of_scope(expression: Any) -> Any:
    """The SELECT a scope's expression ultimately holds, past LATERAL and subquery wrappers.

    A LATERAL source's `scope.expression` is an `exp.Lateral`, NOT the query inside it, and
    `Lateral.selects` is an empty list rather than an error. That made every column
    projected through a LATERAL alias look like a name the subquery does not have (finding
    PG-03): `_projection_named` returned None and the reference was declared
    `unresolved_identifier` while the analyser had the projection two nodes away.

    The loop is bounded because `Lateral(Subquery(Select))` is two unwraps and anything
    needing five is a shape this has not seen; stopping is better than spinning.
    """
    node = expression
    for _ in range(5):
        if isinstance(node, exp.Lateral):
            node = node.this
        elif isinstance(node, exp.Subquery):
            node = node.this
        else:
            break
    return node


def _qualify(
    parsed: Any,
    dictionary: Dictionary,
    search_path: list[str] | None = None,
    partial: bool = False,
) -> Any:
    """SQLGlot's `qualify`, with the merged column of USING / NATURAL put right (B1-02).

    Every analysis entry point qualifies through here, so the correction has one home.
    `partial` lets a reference to a column its relation lacks stay unqualified instead of
    raising - only for the retry in `_analyse_parsed`, which declares it unresolved.
    """
    for written in parsed.find_all(exp.Coalesce):
        written.meta["written"] = True
    parsed = restate_srfs(parsed, dictionary.dialect)
    parsed = bind_relations(parsed, dictionary, search_path)
    if dictionary.dialect == "postgres":
        parsed = session_keywords(parsed)
        parsed = restate_system_columns(parsed)
        parsed = _return_types_as_types(parsed)
        parsed = star_rows(parsed)
    parsed = expand_qualified_stars(parsed, dictionary)
    parsed = qualify(
        parsed,
        schema=_schema_for(dictionary),
        dialect=dictionary.dialect,
        validate_qualify_columns=False,
        infer_schema=True,
        allow_partial_qualification=partial,
    )
    # A whole-row reference comes out of `qualify` as `TableColumn`, which no column walk
    # sees; restated here as columns so it is traced or declared, never dropped (B4-01).
    return restate_whole_rows(_merged_join_columns(parsed), dictionary)


def _return_types_as_types(parsed: Any) -> Any:
    """`JSON_OBJECT(... RETURNING jsonb)`: the return type as a type, not a column (TT-3).

    SQLGlot parses the type after RETURNING as a `Column` named `jsonb`, which then reached
    the trace as a column reference and was declared `unresolved_identifier` - a type
    named as missing data. It carries no lineage, so it is restated as the type it names.
    """
    for node in list(parsed.find_all(exp.Expression)):
        written = node.args.get("return_type") if node.args else None
        if isinstance(written, exp.Column):
            try:
                node.set("return_type", exp.DataType.build(written.sql(dialect="postgres")))
            except Exception:
                node.set("return_type", None)
    return parsed


def _merged_join_columns(parsed: Any) -> Any:
    """Replace `qualify`'s merged-column COALESCE with the column PostgreSQL actually uses.

    **Finding B1-02.** For `JOIN ... USING (k)` and `NATURAL JOIN`, `qualify` rewrites an
    unqualified `k` as `COALESCE(left.k, right.k)` whatever the join type. PostgreSQL does
    that only for a FULL join; for INNER and LEFT the merged column IS the left input's, for
    RIGHT the right input's. The COALESCE reached the transform ladder as a genuine choice
    between two sources - a `conditional` edge from each side where the truth is one
    `identity` edge, and `USING (k)` disagreed with the `ON a.k = b.k` it abbreviates.

    Only a COALESCE `qualify` GENERATED is touched: every COALESCE in the text is marked
    before qualifying, because `COALESCE(a.k, b.k)` written by hand over an inner join is a
    real choice someone made, and rewriting it would be the analyser editing the query.
    Walked innermost first, so a chain of USING joins - `COALESCE(COALESCE(a.k, b.k), c.k)`
    - reduces one join at a time.
    """
    for coalesce in reversed(list(parsed.find_all(exp.Coalesce))):
        if coalesce.meta.get("written"):
            continue
        arguments = [coalesce.this, *(coalesce.expressions or [])]
        if len(arguments) != 2 or not all(
            isinstance(argument, exp.Column) and argument.table for argument in arguments
        ):
            continue
        select = coalesce.parent_select
        joins = (select.args.get("joins") or []) if select is not None else []
        side = next(
            (
                (join.side or "").upper()
                for join in joins
                if join.this.alias_or_name == arguments[1].table
            ),
            None,
        )
        if side is None or side == "FULL":
            continue
        coalesce.replace((arguments[1] if side == "RIGHT" else arguments[0]).copy())
    return parsed



def _position_named(names: list[str], name: str) -> int | None:
    """Which of `names` a reference to `name` means: exactly, then case-blind if unique.

    **Finding B1-05.** This used to compare upper-cased on both sides, so with `"Region"`
    and `region` both projected, `x.region` bound to whichever came first - a confident
    wrong edge with no boundary. A quoted identifier keeps its case and is a DIFFERENT
    name from its folded twin; that is the whole point of quoting it.

    After `qualify`, every identifier in the tree already carries its real case (unquoted
    names folded by the dialect, quoted ones kept), so an exact match is the correct one.
    The case-blind fallback exists for callers that pass a name folded elsewhere - def-use
    upper-cases a cursor field - and is taken only when it is UNAMBIGUOUS. Two case-blind
    candidates and no exact one means the caller's spelling cannot say which was meant,
    and the honest answer is no match, which the caller declares.
    """
    for index, candidate in enumerate(names):
        if candidate == name:
            return index
    folded = [index for index, candidate in enumerate(names) if candidate.upper() == name.upper()]
    return folded[0] if len(folded) == 1 else None


def _projection_named(scope: Scope, name: str) -> Any:
    projections: list[Any] = getattr(_query_of_scope(scope.expression), "selects", []) or []
    index = _position_named([p.alias_or_name or "" for p in projections], name)
    return projections[index] if index is not None else None


def resolve_projection(
    query: str,
    projection_name: str,
    dictionary: Dictionary,
    config: AnalysisConfig | None = None,
) -> tuple[str, str, Transform] | None:
    """Public: which base column does ONE output column of this SELECT come from?

    Band 1 needs exactly the question band 0 spends its whole module answering. A cursor
    record's field is an output column of the cursor's query, and `rec.gross_total` is a
    lineage fact only if that query can be followed - through its CTEs, its joins, its
    renames and any views underneath them.

    **This exists because `defuse` had its own one-level version** (stress finding S4-02).
    `_field_of_row` read the outermost select list and bound each column straight to a base
    relation, so a cursor selecting `FROM summarised m` - where `summarised` is a CTE -
    resolved nothing at all, silently. Real cursors are exactly where CTE chains live,
    because a query long enough to deserve a name is usually long enough to need one.

    Rather than teach that copy to traverse, it delegates here: the same `_inline_views`,
    the same `qualify`, the same `build_scope` and the same `_trace` the set-based path
    uses. **S2-08 is on the register for a rule that was duplicated across these two
    modules and drifted**, and a second traversal would have drifted the same way.

    Returns the transform as well as the column, so a caller that wants to combine an
    aggregation inside the cursor query with the transform at the use site can.
    """
    settings = config or AnalysisConfig()

    text = query.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    try:
        parsed: Any = sqlglot.parse_one(text, dialect=dictionary.dialect)
    except Exception:
        return None
    if not isinstance(parsed, exp.Select):
        return None

    parsed, _ = _inline_views(parsed, dictionary, settings.budgets.view_expansion_depth_cap)
    parsed = _resolve_named_windows(parsed)
    try:
        parsed = _qualify(parsed, dictionary)
    except Exception:
        return None

    scope = build_scope(parsed)
    if scope is None:
        return None

    wanted = projection_name.strip().upper()
    for item in getattr(scope.expression, "selects", []) or []:
        if (item.alias_or_name or "").upper() != wanted:
            continue
        # Discarded rather than surfaced: a boundary raised here would be attributed to the
        # cursor's query rather than to the statement that read the record, and the caller
        # already declares an unresolved field in its own terms.
        for column in _value_columns(item):
            for table, name, traced in _trace(column, scope, dictionary, []):
                return (table, name, _combine(_transform_of_path(item, column), traced))
    return None


@dataclass(frozen=True)
class QueryInfluence:
    """What a query's predicates and grouping decide, resolved to base columns.

    The non-value half of `resolve_projection`: everything about a query that governs its
    result without being in it. Returned as facts rather than edges because the caller owns
    the target — band 0 points them at the written relation, band 1 at a variable — and
    getting that wrong is how a filter edge ends up claiming a variable is a table.
    """

    # (relation, column, phase) — a predicate column and when it acts.
    filters: tuple[tuple[str, str, FilterPhase], ...]
    # (projection name, relation, column) — a grouping or window column governing that
    # output column's value (D-2, D-4).
    influence: tuple[tuple[str, str, str], ...]


def resolve_query_influence(
    query: str,
    dictionary: Dictionary,
    config: AnalysisConfig | None = None,
) -> QueryInfluence:
    """Public: which columns decide this SELECT's rows and groups, at any depth?

    **Stress finding S4-09, and the third time this exact delegation has been needed.**
    S4-02 gave band 1 `resolve_projection` for a cursor record's field, S4-05 reused it for
    a projection computed in a derived table, and this is the same handoff for the clauses
    that decide WHICH rows a statement reads rather than what value comes out.

    `defuse._analyse_select_into` had its own one-level version: a top-level `WHERE`, whose
    target came from `_read_relation` — which returns `None` the moment the `FROM` is a
    subquery. So `SELECT ... BULK COLLECT INTO c FROM (SELECT ... WHERE ... GROUP BY ...
    HAVING ...) g` reported none of it. No `HAVING`, no `GROUP BY` influence, and not even
    the inner `WHERE`: **every band-1 statement that reads into a variable was silent about
    its own predicates**, while the identical query writing a table produced all of them.
    That is the S2-04 shape once more — a construct each pass correctly decides is not
    quite its own.

    Reuses `_filter_edges`' traversal and both influence resolvers unchanged, rather than
    growing a second copy in `defuse`. S2-08 is on the register for precisely a rule that
    lived in two modules and drifted, and the one-level version this replaces is what that
    drift looks like before anyone notices.
    """
    settings = config or AnalysisConfig()

    text = query.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    try:
        parsed: Any = sqlglot.parse_one(text, dialect=dictionary.dialect)
    except Exception:
        return QueryInfluence((), ())
    if not isinstance(parsed, exp.Select):
        return QueryInfluence((), ())

    parsed, _ = _inline_views(parsed, dictionary, settings.budgets.view_expansion_depth_cap)
    parsed = _resolve_named_windows(parsed)
    try:
        parsed = _qualify(parsed, dictionary)
    except Exception:
        return QueryInfluence((), ())

    scope = build_scope(parsed)
    if scope is None:
        return QueryInfluence((), ())

    # Discarded rather than surfaced, for the reason `resolve_projection` gives: a boundary
    # raised here would be attributed to this query rather than to the statement that read
    # from it, and the caller declares what it could not resolve in its own terms.
    discarded: list[Boundary] = []

    filters: list[tuple[str, str, FilterPhase]] = []
    for current in scope.traverse():
        for clause, phase in (
            ("where", FilterPhase.PRE_AGGREGATION),
            ("having", FilterPhase.POST_AGGREGATION),
        ):
            predicate = current.expression.args.get(clause)
            if predicate is None:
                continue
            for column in predicate_columns(predicate):
                for table, name, reached_by in _trace(column, current, dictionary, discarded):
                    filters.append((table, name, _filter_phase(phase, reached_by)))

    influence: list[tuple[str, str, str]] = []
    for item in getattr(scope.expression, "selects", []) or []:
        name = fold(item.alias_or_name or "", dictionary.dialect)
        if not name:
            continue
        for table, column, _ in _grouping_influence(item, scope, dictionary, discarded):
            influence.append((name, table, column))
        for table, column, _ in _window_influence(item, scope, dictionary, discarded):
            influence.append((name, table, column))

    return QueryInfluence(tuple(dict.fromkeys(filters)), tuple(dict.fromkeys(influence)))


def _row_source_subject(source: exp.Table, dialect: str) -> str:
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
    # A function called directly as the row source - PostgreSQL's `FROM s.f(x) AS r(...)`
    # - IS the thing we could not see. Only Oracle's `TABLE(f(x))` wraps it, and there the
    # call is the first argument; here the first argument is data (`TABLE(O.PAYLOAD)`).
    if isinstance(function, exp.Anonymous) and function.name.upper() != "TABLE":
        owner = source.text("db")
        head = f"{owner}.{function.name}" if owner else function.name
        return f"TABLE({head.upper()})"
    arguments = getattr(function, "expressions", None) or []
    if arguments:
        # The called expression rendered without its arguments: `pkg.f(1)` -> `PKG.F`.
        # Walking for the first string `this` finds `PKG` instead, which names the wrong
        # thing - two functions in one package would share a subject and merge into one
        # boundary.
        # Quotes are stripped so `pkg.f` and `"PKG".f` produce the same subject - a
        # boundary's identity is what makes two statements of the same fact comparable,
        # and quoting is not part of the fact.
        called = arguments[0].sql(dialect=dialect).strip().upper().replace('"', "")
        head = called.split("(", 1)[0].strip()
        if head:
            return f"TABLE({head})"
    rendered = source.sql(dialect=dialect).strip().upper()
    return rendered or "TABLE(?)"


def _is_recursive_self_reference(column: Any, scope: Scope) -> bool:
    """Does `column` read the recursive CTE that this scope is the definition of?

    True only when the alias names a table whose NAME is the enclosing CTE's own name, and
    that WITH is RECURSIVE. Anything looser matches every CTE reference: a CTE's query is
    always inside the statement it belongs to.

    A schema-qualified table is never the self-reference (finding FH-01): a CTE name cannot
    be qualified, so `item_master AS (... FROM lgx.item_master)` in a RECURSIVE WITH reads the
    base table. Matching the name alone took it for the CTE and dropped every column read
    through it, silently.
    """
    selected = getattr(scope, "selected_sources", None) or {}
    entry = selected.get(column.table)
    node = entry[0] if isinstance(entry, tuple) and entry else None
    if not isinstance(node, exp.Table) or node.args.get("db") or node.args.get("catalog"):
        return False
    name = node.name.upper()
    walk = getattr(_query_of_scope(scope.expression), "parent", None)
    while walk is not None:
        if isinstance(walk, exp.CTE):
            with_ = walk.parent
            return (
                (walk.alias_or_name or "").upper() == name
                and isinstance(with_, exp.With)
                and bool(with_.args.get("recursive"))
            )
        walk = getattr(walk, "parent", None)
    return False


TRACE_DEPTH_CAP = 256
"""How many relations deep a column is followed before the trace declares a `depth_cap`.

**Finding TE-1.** This was a literal `20` in three walkers, and exceeding it returned `[]`:
no edge, no boundary, the statement counted clean. A 112-stage CTE pipeline (the entity
torture test) traces about 117 levels from its output to its base table, and lost every
spine fact with a clean report - the R1 shape exactly. A chain of CTEs is ordinary in
generated models, so the cap is set far above any real pipeline, and reaching it is now
declared. It stays well inside Python's recursion limit; `_analyse_parsed` refuses the
statement if the stack runs out anyway.
"""

_TRACE_MEMO = "_lineage_trace_memo"
"""The attribute a scope keeps its traced columns under; it lives and dies with the scope."""


def _depth_cap(subject: str, unresolved: list[Boundary]) -> list[tuple[str, str, Transform]]:
    """Declare that a trace stopped at `TRACE_DEPTH_CAP`, and give it no edge (TE-1)."""
    unresolved.append(
        Boundary(
            kind=BoundaryKind.DEPTH_CAP,
            subject=subject,
            detail=(
                f"{subject} (traced more than {TRACE_DEPTH_CAP} relations deep; the "
                "lineage below that depth is not followed)"
            ),
        )
    )
    return []


def _recall(
    scope: Scope, key: tuple[Any, ...], unresolved: list[Boundary]
) -> list[tuple[str, str, Transform]] | None:
    """An influence walk already made from this scope, its boundaries replayed; else None.

    The influence walkers' half of `_trace`'s memo (TE-1): they descend through every value
    column exactly as `_trace` does, so they fan out the same way. Two helpers rather than
    a wrapper, so a walker still costs ONE stack frame per level: a wrapper made it four,
    and the stack ran out before `TRACE_DEPTH_CAP` was reached.
    """
    stored = scope.__dict__.get(_TRACE_MEMO, {}).get(key)
    if stored is None:
        return None
    found, declared = stored
    unresolved.extend(declared)
    return list(found)


def _remember(
    scope: Scope,
    key: tuple[Any, ...],
    unresolved: list[Boundary],
    first_declared: int,
    found: list[tuple[str, str, Transform]],
) -> list[tuple[str, str, Transform]]:
    """Store a finished influence walk (once per source), unless it hit the depth cap."""
    found = list(dict.fromkeys(found))
    declared = unresolved[first_declared:]
    if not any(boundary.kind is BoundaryKind.DEPTH_CAP for boundary in declared):
        memo = scope.__dict__.setdefault(_TRACE_MEMO, {})
        memo[key] = (tuple(found), tuple(declared))
    return found


def _trace(
    column: exp.Column,
    scope: Scope,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int = 0,
    constraining: list[tuple[str, str, Transform]] | None = None,
) -> list[tuple[str, str, Transform]]:
    """`_follow`, memoised per scope and column (finding TE-1).

    A column of a CTE is traced once however many projections read it. Without this, a
    chain of CTEs whose columns each read two columns of the stage before costs 2^depth;
    the old cap of 20 was what bounded it. A trace appends boundaries (and, for a set
    operation's constraining arm, sources) as it goes, so a hit replays exactly what the
    first trace appended. A trace that hit the depth cap is not stored: from a shallower
    depth the same column may resolve in full.
    """
    if depth > TRACE_DEPTH_CAP:
        return _depth_cap(column.name.upper(), unresolved)
    memo = scope.__dict__.setdefault(_TRACE_MEMO, {})
    key = (column.table, column.name, constraining is not None)
    stored = memo.get(key)
    if stored is not None:
        traced, declared, constrained = stored
        unresolved.extend(declared)
        if constraining is not None:
            constraining.extend(constrained)
        return list(traced)

    first_declared = len(unresolved)
    first_constrained = len(constraining) if constraining is not None else 0
    # Once per source, in first-found order: two paths to the same column with the same
    # transform are one fact, and keeping both doubles the list at every fanning stage.
    traced = list(
        dict.fromkeys(_follow(column, scope, dictionary, unresolved, depth, constraining))
    )
    declared = unresolved[first_declared:]
    if not any(boundary.kind is BoundaryKind.DEPTH_CAP for boundary in declared):
        constrained = constraining[first_constrained:] if constraining is not None else []
        memo[key] = (tuple(traced), tuple(declared), tuple(constrained))
    return traced


def _follow(
    column: exp.Column,
    scope: Scope,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int = 0,
    constraining: list[tuple[str, str, Transform]] | None = None,
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
    source = scope.sources.get(column.table) if column.table else None

    if source is None and column.table:
        # A scalar subquery in the select list brings its own FROM. `MAX(s.email)` inside
        # `(SELECT MAX(s.email) FROM stg_customer s WHERE ...)` resolves against that
        # subquery's scope, not the outer one - and looking only at the outer scope loses
        # the edge entirely rather than getting it wrong.
        for nested in getattr(scope, "subquery_scopes", []) or []:
            if column.table in nested.sources:
                return _trace(column, nested, dictionary, unresolved, depth + 1, constraining)

        # AND OUTWARD, for a CORRELATED reference (stress finding S3-10). Inside
        # `(SELECT SUM(r.net) FROM rev r WHERE r.rid = s.sid)` the alias `s` belongs to the
        # ENCLOSING query, so it is in neither this scope nor any scope below it. Searching
        # only downward left it unresolved, and the fallback below then bound it to
        # whichever relation this subquery happened to select from.
        #
        # The scope that SELECTS the alias is preferred over one that only lists it (finding
        # TT-3). A LATERAL's own scope lists the relations to its left without selecting
        # them, so a correlation from inside it to a recursive CTE's self-reference
        # (`CROSS JOIN LATERAL (... WHERE x.parent = be.child)` in the recursive arm) landed
        # there, where the self-reference test - which reads selected sources - could not
        # see it, and was declared unresolved. The first scope that lists it stays the
        # fallback, which is what every other correlation already resolved through.
        outer = getattr(scope, "parent", None)
        climbed = 0
        listing: Scope | None = None
        while outer is not None and climbed < 20:
            if column.table in outer.sources:
                if column.table in (getattr(outer, "selected_sources", None) or {}):
                    listing = outer
                    break
                listing = listing or outer
            outer = getattr(outer, "parent", None)
            climbed += 1
        if listing is not None:
            return _trace(column, listing, dictionary, unresolved, depth + 1, constraining)

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
    # A recursive CTE's arm reading the CTE itself (`JOIN tree t ON ...`) adds no base
    # source: everything `t` carries came from the anchor arm or from an earlier pass of
    # this same arm, and both are traced through the anchor. SQLGlot does not map the
    # self-reference to a scope, so it used to be declared unresolved (finding B2-10).
    if column.table and _is_recursive_self_reference(column, scope):
        return []
    # The same self-reference reached from a scope that does not select it - a correlation
    # inside a LATERAL in the recursive arm (finding TT-3) - lands on SQLGlot's source-less
    # shell over the anchor, where the anchor's projection could only be declared
    # unresolved.
    if isinstance(source, Scope) and _is_recursive_anchor_shell(source):
        return []

    if source is None and len(scope.sources) == 1 and not column.table:
        source = next(iter(scope.sources.values()))

    if isinstance(source, exp.Table):
        table = identifier(source, dictionary.dialect)
        name = identifier(column, dictionary.dialect)

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
                    subject=_row_source_subject(source, dictionary.dialect),
                    detail=(
                        f"{_row_source_subject(source, dictionary.dialect)}.{name} "
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
        db = source.args.get("db")
        qualifier = identifier(db, dictionary.dialect) if db else ""
        lookup = f"{qualifier}.{table}" if qualifier else table
        if qualifier == AMBIGUOUS:
            # D-24: two or more non-default schemas have this name and no search_path says
            # which - declared, and nothing is emitted from either table.
            owners = sorted(
                info.owner for info in dictionary.objects.values() if info.name == table
            )
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.DANGLING_REFERENCE,
                    subject=table,
                    detail=(
                        f"{table}.{name} (ambiguous: {table} is in {', '.join(owners)} and no "
                        "search_path names one - D-24)"
                    ),
                )
            )
            return []
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
            table = _relation_name(dictionary.resolve(lookup), table, dictionary)
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
            return _trace_through_set_operation(
                column, source, dictionary, unresolved, depth, constraining
            )

        if isinstance(_query_of_scope(source.expression), exp.Unnest):
            return _trace_through_unnest(
                column, source, scope, dictionary, unresolved, depth, constraining
            )

        if isinstance(_query_of_scope(source.expression), exp.Values):
            traced_values = _trace_through_values(
                column, source, scope, dictionary, unresolved, depth, constraining
            )
            if traced_values is not None:
                return traced_values

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
        traced: list[tuple[str, str, Transform]] = []
        # D-53 (B4-05): a call whose body is not in the analysis gives its arguments as
        # derived values here as at the top level, and is declared here as there.
        for name in _unknown_calls(projection, scope, dictionary.dialect):
            unresolved.append(_unsummarised_call(name))
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
            for table, name, transform in _trace(
                inner, source, dictionary, unresolved, depth + 1, constraining
            ):
                traced.append(
                    (table, name, _combine(_transform_of_path(projection, inner), transform))
                )
        # D-12 one level down: `o.amount * (SELECT fx.rate FROM fx WHERE fx.ccy = o.ccy)`
        # in a derived table. The lookup keys are values of THIS projection, so they travel
        # up with it exactly as its value columns do (B2 T3 S1).
        traced.extend(_scalar_predicate_values(projection, source, dictionary, unresolved))
        return traced

    unresolved.append(
        Boundary(
            kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
            subject=column.name.upper(),
            detail=f"{column.name.upper()} (could not resolve which relation it belongs to)",
        )
    )
    return []


def _trace_through_values(
    column: exp.Column,
    source: Scope,
    scope: Scope,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int,
    constraining: list[tuple[str, str, Transform]] | None,
) -> list[tuple[str, str, Transform]] | None:
    """A column of `(VALUES (...), (...)) AS v(a, b)` is that position of every row.

    **Finding TT-3.** Under LATERAL the alias list sits on the `Lateral`, not on the
    `Values`, so `_projection_named` found no name and `u.rev` in the unpivot
    `CROSS JOIN LATERAL (VALUES ('Q1', p.q1_rev), ('Q2', p.q2_rev)) AS u (qtr, rev)` was
    declared unresolved. PostgreSQL binds the alias columns BY POSITION, and every row
    feeds the column the way every arm of a UNION ALL does. The elements are traced in the
    scope that references the VALUES, because a LATERAL row reads its outer relation
    (`p.q1_rev`) and a plain one reads nothing but literals.

    Returns None when the column cannot be bound by position (no alias list, or a name not
    in it), so the caller declares it as before.
    """
    wrapper = source.expression
    values = _query_of_scope(wrapper)
    alias = wrapper.args.get("alias") or values.args.get("alias")
    names = [part.name.upper() for part in (alias.columns if alias is not None else [])]
    wanted = column.name.upper()
    if wanted not in names:
        return None
    index = names.index(wanted)
    traced: list[tuple[str, str, Transform]] = []
    for row in values.expressions:
        elements = row.expressions if isinstance(row, exp.Tuple) else [row]
        if index >= len(elements):
            continue
        element = elements[index]
        for inner in _value_columns(element):
            for table, name, transform in _trace(
                inner, scope, dictionary, unresolved, depth + 1, constraining
            ):
                path = _transform_of_path(element, inner)
                traced.append((table, name, _combine(path, transform)))
    return traced


def _trace_through_unnest(
    column: exp.Column,
    source: Scope,
    scope: Scope,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int,
    constraining: list[tuple[str, str, Transform]] | None,
) -> list[tuple[str, str, Transform]]:
    """A column of `UNNEST(arr, ...) AS t(x, ...)` is an element of the array in its position.

    **Finding PG-05, the one column-level silence in the PostgreSQL path.** SQLGlot gives an
    unnest a scope of its own whose `selects` are the ALIAS IDENTIFIERS - `tag` in
    `AS t(tag)` - not expressions. `_projection_named` took that bare identifier for a
    projection, found no column inside it, and `t.tag` traced to nothing: no edge, no
    boundary, the statement counted clean. `generate_series` in the same position declared
    `dangling_reference`, which is what made the gap look like an oversight rather than a
    rule.

    The answer is not a boundary, because nothing here is unknowable: PostgreSQL binds the
    alias columns to the arrays BY POSITION, so `t.x` is an element of the first array. The
    transform is read off the `UNNEST` node itself, so this spelling and
    `SELECT unnest(c.tags)` produce the same edge - one computation, one answer.

    The arrays are traced in the scope that REFERENCES the unnest, not in the unnest's own
    scope: `c.tags` names a sibling of the unnest in that `FROM`, which is the scope in
    which the reference was written.

    `WITH ORDINALITY`'s column is a counter, like `ROW_NUMBER()`: no column's value is in
    it, so it has no value source and gets no edge. Any shape that does not map one alias
    to one array - an array of records expanded into its fields, or an alias list longer
    than the arrays - is declared rather than guessed, because a positional guess there
    binds a field to the wrong array.
    """
    wrapper = source.expression
    unnest = _query_of_scope(wrapper)
    alias = wrapper.args.get("alias") or unnest.args.get("alias")
    names = [part.name.upper() for part in (alias.columns if alias is not None else [])]
    arrays = list(unnest.expressions)
    wanted = column.name.upper()

    ordinality = unnest.args.get("offset")
    if isinstance(ordinality, exp.Expression) and ordinality.name.upper() == wanted:
        return []

    if names and len(names) == len(arrays) and wanted in names:
        array = arrays[names.index(wanted)]
        traced: list[tuple[str, str, Transform]] = []
        for inner in _value_columns(array):
            for table, name, transform in _trace(
                inner, scope, dictionary, unresolved, depth + 1, constraining
            ):
                traced.append((table, name, _combine(_transform_of_path(unnest, inner), transform)))
        return traced

    unresolved.append(
        Boundary(
            kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
            subject=wanted,
            detail=(
                f"{wanted} (an UNNEST column that cannot be bound to one array by position: "
                f"{len(names)} alias column(s) over {len(arrays)} array(s))"
            ),
        )
    )
    return []


def _read_scopes(current: Scope) -> list[Scope]:
    """The scopes this query actually READS - not every CTE visible to it.

    SQLGlot's `sources` lists every CTE in scope, read or not. A closure walked over it
    swept a sibling CTE's WHERE into a scalar subquery's value (stress_04: `window_days`
    and `last_login` appeared as values of `lifetime_value`). `selected_sources` is what
    the FROM and JOINs name.
    """
    selected = getattr(current, "selected_sources", None)
    names = selected.keys() if selected is not None else current.sources.keys()
    return [
        source
        for name in names
        if isinstance(source := current.sources.get(name), Scope)
    ]


def _scalar_predicate_values(
    projection: Any, scope: Scope, dictionary: Dictionary, unresolved: list[Boundary]
) -> list[tuple[str, str, Transform]]:
    """Predicate columns that decide this projection's VALUE, reached while tracing it.

    Two kinds of scope decide a value without being able to remove a row: a scalar subquery
    in the projection (D-12), and a source the projection reads that sits on an outer
    join's null-supplying side or is a one-row LATERAL (D-14). Their WHERE / HAVING columns
    - and those of everything they read - are values of the projection: `aggregated` when
    that scope aggregates, `conditional` otherwise.

    Used wherever a projection is reached by TRACING rather than written into the target
    directly: a derived table's column, a CTE's, a MERGE's USING source. Without it the
    D-14 side of `MERGE ... USING (... LEFT JOIN (... WHERE status = 'A') b ...)` lost its
    predicate entirely once it stopped being a filter - a silent loss found by diffing the
    Oracle stress packages (stress_04 s4_staged_upsert). The target-level case is
    `_value_only_edges`; both go through `_value_deciding_sweep`, so the rule has one home,
    and the influence half (D-13) reaches the target through `_window_influence`.
    """
    found: list[tuple[str, str, Transform]] = []
    for root in _value_deciding_roots(projection, scope):
        values, _ = _value_deciding_sweep(root, _statement_deciding(scope), dictionary, unresolved)
        found.extend(values)
    found.extend(_null_side_on_values(projection, scope, dictionary, unresolved))
    return found


def _outermost_subqueries(projection: Any) -> list[Any]:
    """The subqueries written directly in a projection, not those nested inside them.

    A nested one - the IN inside a scalar subquery's WHERE - is reached by its parent's
    sweep, under its parent's transform. Taken as a root of its own it was swept again
    with its own, and `aggregated` facts came back a second time as `conditional`.
    """
    owner = projection.find_ancestor(exp.Select)
    return [
        nested
        for nested in projection.find_all(exp.Select)
        if nested is not owner and nested.find_ancestor(exp.Select) is owner
    ]


def _hardest_per_path(edges: list[PredictedEdge]) -> list[PredictedEdge]:
    """One value fact per (source, target) within a statement, at its hardest transform (D-51).

    A source can reach one target along several paths - `jsonb_build_object(f.id, CASE WHEN
    f.flag ..., f.total)` reads `id` directly, through a CASE and through an aggregate - and
    each path used to give its own edge. D-51 (2026-10-06): the fact is the column reaching
    the target, and its transform is the hardest over every path (D-7's ladder, applied
    across paths), so the answer does not depend on how many ways the query gets there.
    """
    hardest: dict[tuple[Any, ...], Transform] = {}
    for edge in edges:
        if edge.flow is Flow.VALUE:
            key = (str(edge.source), str(edge.target), edge.band, edge.guard)
            held = hardest.get(key)
            if held is None or TRANSFORM_RANK[edge.transform] > TRANSFORM_RANK[held]:
                hardest[key] = edge.transform
    collapsed: list[PredictedEdge] = []
    emitted: set[tuple[Any, ...]] = set()
    for edge in edges:
        if edge.flow is not Flow.VALUE:
            collapsed.append(edge)
            continue
        key = (str(edge.source), str(edge.target), edge.band, edge.guard)
        if key in emitted:
            continue
        emitted.add(key)
        transform = hardest[key]
        if edge.transform != transform:
            edge = edge.model_copy(update={"transform": transform})
        collapsed.append(edge)
    return collapsed


def _statement_root(scope: Scope) -> Scope:
    """The root scope of the statement `scope` belongs to."""
    root = scope
    while getattr(root, "parent", None) is not None:
        root = root.parent
    return root


def _statement_deciding(scope: Scope) -> set[int]:
    """The row-deciding scopes of the statement `scope` belongs to, cached on its root."""
    root = _statement_root(scope)
    cached = root.__dict__.get("_lineage_deciding")
    if cached is None:
        cached = _row_deciding_scopes(root)
        root.__dict__["_lineage_deciding"] = cached
    return cached


def _value_deciding_roots(projection: Any, scope: Scope) -> list[Scope]:
    """The scopes that decide this projection's value without being able to remove a row.

    A scalar subquery in the projection (D-12), and a source the projection reads that sits
    on an outer join's null-supplying side or is a one-row LATERAL (D-14).
    """
    index = _scope_index(scope)
    query = _query_of_scope(scope.expression)
    roots: list[Scope] = []
    for nested in _outermost_subqueries(projection):
        home = index.get(id(nested))
        if home is not None and home is not scope:
            roots.append(home)
    for column in projection.find_all(exp.Column):
        source = scope.sources.get(column.table) if column.table else None
        if isinstance(source, Scope) and _cannot_remove_rows(column.table, source, query):
            roots.append(source)
    return list({id(root): root for root in roots}.values())


def _value_deciding_sweep(
    root: Scope, statement: set[int], dictionary: Dictionary, unresolved: list[Boundary]
) -> tuple[list[tuple[str, str, Transform]], list[tuple[str, str, Transform]]]:
    """What a scope that cannot remove a result row decides, ALL THE WAY DOWN (D-45).

    Returns (values, influence). The scopes swept are the ones that can remove rows from
    `root` - itself, what it inner-joins, its set-operation arms, the subqueries in its
    WHERE - at any depth (`_row_deciding_scopes`): exactly what the main path reads for
    filters, applied below a scope whose rows decide a value instead. In each:

    * WHERE and HAVING columns are VALUES - `aggregated` when `root` aggregates, else
      `conditional` (D-12);
    * a ranking cut (`WHERE rn = 1`), DISTINCT ON keys and the ordering of a LIMIT are
      INFLUENCE: they decide which value arrives without supplying it (D-13).

    Not swept: a scope the statement also reads where it CAN remove rows - its predicates
    are filters there, and only there - and what cannot remove rows of `root` itself (a
    scalar subquery in a projection, a nested null-supplying side or one-row LATERAL):
    their predicates are not filters of anything, and decide a value only if that value is
    traced into the target, where they are found in their own right. The entity torture
    test carried every nested LATERAL's correlation into an unrelated column because the
    sweep followed those too (finding TE-3).
    """
    root_query = _query_of_scope(root.expression)
    aggregates = isinstance(root_query, exp.Select) and any(
        _is_aggregate(n) for item in root_query.selects for n in item.walk()
    )
    base = Transform.AGGREGATED if aggregates else Transform.CONDITIONAL
    index = _scope_index(_statement_root(root))
    values: list[tuple[str, str, Transform]] = []
    influence: list[tuple[str, str, Transform]] = []
    for current in _row_deciding_list(root):
        if id(current) in statement:
            continue
        query = _query_of_scope(current.expression)
        if not isinstance(query, exp.Select):
            continue
        for clause in ("where", "having"):
            predicate = query.args.get(clause)
            if predicate is None:
                continue
            for column in predicate_columns(predicate):
                if _in_unlimited_nested_order(column, predicate) or _in_nested_grouping(
                    column, predicate
                ):
                    continue
                home = _home_scope(column, current, index)
                for table, name, traced in _trace(column, home, dictionary, unresolved):
                    values.append((table, name, _combine(base, traced)))
                if clause == "where":
                    influence.extend(_window_filter_sources(column, home, dictionary, unresolved))
        # D-48 / D-49: an inner join's non-key ON conditions filter `current` as its WHERE
        # does, so below a value-deciding scope they are values too.
        for join in query.args.get("joins") or []:
            if is_outer_join(join):
                continue
            for column in on_condition_columns(join):
                home = _home_scope(column, current, index)
                for table, name, traced in _trace(column, home, dictionary, unresolved):
                    values.append((table, name, _combine(base, traced)))
                influence.extend(_window_filter_sources(column, home, dictionary, unresolved))
        chosen = _row_choosing_expressions(query)
        influence.extend(_ordered_sources(chosen, current, dictionary, unresolved))
    return values, influence


def _outer_join_conditions(select: Any) -> dict[str, list[Any]]:
    """Each null-supplying alias of this SELECT, with the non-key ON columns that govern it.

    A LEFT join's condition governs its own table; a RIGHT join's, everything to its left;
    a FULL join's, both. Upper-cased aliases, as `_null_side_aliases` keys them.
    """
    source = select.args.get("from_") or select.args.get("from")
    order = [source.this.alias_or_name] if source is not None else []
    found: dict[str, list[Any]] = {}
    for join in select.args.get("joins") or []:
        name = join.this.alias_or_name
        side = (join.side or "").upper()
        nulled = list(order) if side in ("RIGHT", "FULL") else []
        if side in ("LEFT", "FULL"):
            nulled.append(name)
        if nulled:
            columns = on_condition_columns(join)
            for alias in nulled:
                if alias:
                    found.setdefault(alias.upper(), []).extend(columns)
        order.append(name)
    return found


def _null_side_on_values(
    projection: Any, scope: Scope, dictionary: Dictionary, unresolved: list[Boundary]
) -> list[tuple[str, str, Transform]]:
    """D-48 / D-49: the outer-join ON conditions that decide a value this projection reads.

    `LEFT JOIN g ON g.k = w.k AND g.rollup = 0` cannot remove a result row; `g.rollup` decides
    which g row - if any - supplies the columns read from g, so it is a `conditional` value of
    every column fed from g, as g's own WHERE is (D-14). Both operands of a range condition
    are. The key equality stays structural. A null-supplying base table counts as well as a
    derived one: the condition is on the join, not inside the relation.
    """
    query = _query_of_scope(scope.expression)
    if not isinstance(query, exp.Select):
        return []
    conditions = _outer_join_conditions(query)
    if not conditions:
        return []
    index = _scope_index(_statement_root(scope))
    found: list[tuple[str, str, Transform]] = []
    read: set[str] = set()
    for column in projection.find_all(exp.Column):
        alias = (column.table or "").upper()
        if alias in conditions and alias not in read:
            read.add(alias)
            for condition in conditions[alias]:
                home = _home_scope(condition, scope, index)
                for table, name, traced in _trace(condition, home, dictionary, unresolved):
                    found.append((table, name, _combine(Transform.CONDITIONAL, traced)))
    return found


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
    if depth > TRACE_DEPTH_CAP:
        return _depth_cap(str(projection.alias_or_name).upper(), unresolved)
    key = ("window", id(projection))
    recalled = _recall(scope, key, unresolved)
    if recalled is not None:
        return recalled
    first_declared = len(unresolved)

    found: list[tuple[str, str, Transform]] = []
    own = _query_of_scope(scope.expression)
    for column in _window_influence_columns(projection):
        # Finding FH-06: only this query's own windows. One inside a nested subquery - a
        # derived table a scalar subquery reads - was traced HERE, where its columns do
        # not resolve, and declared unresolved; its own scope is reached below (TS-1).
        if column.find_ancestor(exp.Select) is not own:
            continue
        found.extend(_trace(column, scope, dictionary, unresolved, depth + 1))
        # An ordering column that is itself a window - a ROW_NUMBER one level down, or an
        # SRF's ordinal (D-26) - has no value to trace, only the ordering behind it. That
        # ordering decides this window's order too, so it is carried up (D-4, composed).
        for inner, home in _projections_behind(column, scope):
            found.extend(_window_influence(inner, home, dictionary, unresolved, depth + 1))

    for column in _value_columns(projection):
        for inner, home in _projections_behind(column, scope):
            found.extend(_window_influence(inner, home, dictionary, unresolved, depth + 1))
    # Finding TS-1, the window half: a scalar subquery's own windows decide its value.
    for nested_scope, nested in _scalar_subquery_projections(projection, scope):
        found.extend(_window_influence(nested, nested_scope, dictionary, unresolved, depth + 1))
    # D-45, the influence half at a TRACED level: a scope that cannot remove a row (a
    # null-supplying side, a one-row LATERAL, a scalar subquery) reached through a CTE or
    # derived table decides this value by its orderings too. The target level is
    # `_value_only_edges`, which also carries its values.
    if depth > 0:
        for root in _value_deciding_roots(projection, scope):
            _, influence = _value_deciding_sweep(
                root, _statement_deciding(scope), dictionary, unresolved
            )
            found.extend(influence)
    return _remember(scope, key, unresolved, first_declared, found)


JOIN_KEYS: ContextVar[bool] = ContextVar("join_keys", default=False)
"""Whether band 0 reports lookup-join keys (D-57). Set by `analyse_source(join_keys=True)`."""


def _join_key_influence(
    projection: Any,
    scope: Scope,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int = 0,
) -> list[tuple[str, str, Transform]]:
    """The keys that chose which LOOKUP row this projection's value came from (D-57).

    `LEFT JOIN ref.status_map AS m ON m.code = upper(o.status)` with `m.label` projected:
    the far side of each equality in the joined relation's `ON` - `o.status` - picked the
    row the label was read from. Only the relation a join ADDS is a lookup (`join.this`);
    the relations already in the FROM keep their rows, so their values carry no key. A
    RIGHT or FULL join preserves the joined side too and is skipped.

    The same walk as `_window_influence`: the lookup is as likely to sit in a CTE as in the
    statement that writes, and a rule that only looked at the top level would answer
    differently for two spellings of one query. Keys are traced into a throwaway list -
    a join key is supplementary, and one that does not resolve must not add a boundary to
    the closed-world answer.
    """
    if depth > TRACE_DEPTH_CAP:
        return []
    key = ("join_key", id(projection))
    recalled = _recall(scope, key, unresolved)
    if recalled is not None:
        return recalled
    first_declared = len(unresolved)

    found: list[tuple[str, str, Transform]] = []
    own = _query_of_scope(scope.expression)
    read = {
        column.table
        for column in _value_columns(projection)
        if column.table and column.find_ancestor(exp.Select) is own
    }
    joins = (own.args.get("joins") or []) if isinstance(own, exp.Select) else []
    for join in joins:
        if join.side in ("RIGHT", "FULL") or join.this is None:
            continue
        alias = join.this.alias_or_name
        condition = join.args.get("on")
        if not alias or alias not in read or condition is None:
            continue
        for equality in condition.find_all(exp.EQ):
            sides = (equality.this, equality.expression)
            owners = [{c.table for c in side.find_all(exp.Column)} for side in sides]
            for mine, other in ((0, 1), (1, 0)):
                if owners[mine] != {alias} or not owners[other] or alias in owners[other]:
                    continue
                for column in sides[other].find_all(exp.Column):
                    found.extend(_trace(column, scope, dictionary, [], depth + 1))

    for column in _value_columns(projection):
        for inner, home in _projections_behind(column, scope):
            found.extend(_join_key_influence(inner, home, dictionary, unresolved, depth + 1))
    for nested_scope, nested in _scalar_subquery_projections(projection, scope):
        found.extend(_join_key_influence(nested, nested_scope, dictionary, unresolved, depth + 1))
    return _remember(scope, key, unresolved, first_declared, found)


def _projections_behind(column: Any, scope: Scope) -> list[tuple[Any, Scope]]:
    """The projection(s) a column reads from a derived table, CTE or LATERAL, with scopes."""
    source = scope.sources.get(column.table) if column.table else None
    if source is None and len(scope.sources) == 1:
        source = next(iter(scope.sources.values()))
    if not isinstance(source, Scope):
        return []
    return _projections_in(source, column.name)


def _projections_in(source: Scope, name: str) -> list[tuple[Any, Scope]]:
    """The projection named `name` in `source`, with the scope that computes it.

    For a set operation that is one projection per arm that supplies values, bound by
    POSITION as `_trace_through_set_operation` binds them (finding FH-04). `_projection_named`
    alone returned the first arm's projection with the SET OPERATION's scope - which has no
    GROUP BY or window of its own - so every arm's grouping and ordering influence was lost
    once the union sat in a CTE or derived table, silently. At the top level the arms were
    walked directly and kept it.
    """
    if not getattr(source, "union_scopes", None):
        inner = _projection_named(source, name)
        return [(inner, source)] if inner is not None else []
    roles = _arm_roles(source)
    first = getattr(roles[0][0].expression, "selects", []) or []
    position = _position_named([item.alias_or_name or "" for item in first], name)
    if position is None:
        return []
    found: list[tuple[Any, Scope]] = []
    for arm, supplies_values in roles:
        projections = getattr(arm.expression, "selects", []) or []
        if supplies_values and position < len(projections):
            found.append((projections[position], arm))
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
    if depth > TRACE_DEPTH_CAP:
        return _depth_cap(str(projection.alias_or_name).upper(), unresolved)
    key = ("grouping", id(projection))
    recalled = _recall(scope, key, unresolved)
    if recalled is not None:
        return recalled
    first_declared = len(unresolved)

    found: list[tuple[str, str, Transform]] = []
    group = _query_of_scope(scope.expression).args.get("group")
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
        for inner, home in _projections_behind(column, scope):
            found.extend(_grouping_influence(inner, home, dictionary, unresolved, depth + 1))
    # D-55 (finding FM-03): an aggregate over an already-grouped source is decided by that
    # grouping - it fixes how many group rows there are. `MAX(i.days)` carries it through
    # `days`; a column-less `COUNT(*)` reads no column, so the walk above never left this
    # scope and the inner GROUP BY was lost.
    if _counts_rows_here(projection, scope):
        for source in _read_scopes(scope):
            found.extend(_counted_grouping(source, dictionary, unresolved, depth + 1))
    # Finding TS-1: a scalar subquery's grouping decides the value it returns, as a CTE's
    # does. `(SELECT max(margin) FROM brand_stats)` lost brand_stats' GROUP BY brand, because
    # the walk above follows only columns of THIS scope.
    for nested_scope, nested in _scalar_subquery_projections(projection, scope):
        found.extend(_grouping_influence(nested, nested_scope, dictionary, unresolved, depth + 1))
    return _remember(scope, key, unresolved, first_declared, found)


def _counts_rows_here(projection: Any, scope: Scope) -> bool:
    """Whether the projection holds a column-less aggregate (`COUNT(*)`) of THIS query.

    Not a window function - `RANK() OVER (...)`, `COUNT(*) OVER (...)` read their window's
    columns, already influence (D-4) - and not one inside a nested subquery, whose rows are
    its own sources' rather than this scope's.
    """
    query = _query_of_scope(scope.expression)
    return any(
        _is_aggregate(node)
        and not _reads_a_column(node)
        and node.find_ancestor(exp.Window, exp.Select) is query
        for node in projection.walk()
    )


def _reads_a_column(aggregate: Any) -> bool:
    """Whether an aggregate's argument names a column (`COUNT(*)`, `COUNT(1)` do not)."""
    arguments = [aggregate.this, *(aggregate.expressions or [])]
    return any(
        isinstance(argument, exp.Expression) and argument.find(exp.Column) is not None
        for argument in arguments
    )


def _counted_grouping(
    source: Scope,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int,
    seen: set[int] | None = None,
) -> list[tuple[str, str, Transform]]:
    """The GROUP BY that decides the rows a column-less aggregate counts in `source` (D-55).

    A source that groups contributes its grouping columns. One that passes rows through
    (a filter, a join, a projection) is looked through to the grouping beneath it, since
    its rows are still those groups; each arm of a set operation likewise. `seen` stops a
    recursive CTE from being entered twice.
    """
    seen = set() if seen is None else seen
    if id(source) in seen:
        return []
    seen.add(id(source))
    if depth > TRACE_DEPTH_CAP:
        return _depth_cap("COUNT(*)", unresolved)
    arms = _arm_roles(source) if getattr(source, "union_scopes", None) else [(source, True)]
    found: list[tuple[str, str, Transform]] = []
    for arm, supplies_values in arms:
        if not supplies_values:
            continue
        query = _query_of_scope(arm.expression)
        group = query.args.get("group") if isinstance(query, exp.Select) else None
        if group is not None:
            for column in group.find_all(exp.Column):
                found.extend(_trace(column, arm, dictionary, unresolved, depth + 1))
            continue
        aggregates = isinstance(query, exp.Select) and any(
            _is_aggregate(node) for item in query.selects for node in item.walk()
        )
        if not aggregates:
            for inner in _read_scopes(arm):
                found.extend(_counted_grouping(inner, dictionary, unresolved, depth + 1, seen))
    return found


def _scalar_subquery_projections(projection: Any, scope: Scope) -> list[tuple[Scope, Any]]:
    """The projections of each subquery written directly in this projection, with scopes."""
    index = _scope_index(scope)
    found: list[tuple[Scope, Any]] = []
    for nested in _outermost_subqueries(projection):
        home = index.get(id(nested))
        if home is not None and home is not scope:
            found.extend((home, item) for item in nested.selects)
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


def _arm_roles(scope: Any) -> list[tuple[Any, bool]]:
    """Leaf arms of a set-operation SCOPE, each flagged `supplies_values` (S4-04).

    `_flatten_arms` returns the arms and throws away which OPERATOR joined them, so a
    `MINUS`'s constraining arm arrived indistinguishable from a `UNION`'s contributing one.
    That is why convention (a) - *an `INTERSECT`/`MINUS` second arm decides which rows
    survive and supplies no value* - held only for a set operation at the TOP LEVEL of an
    `INSERT`, where `_set_operation_arms` computes the same roles over EXPRESSIONS. Written
    inside a CTE, the same `MINUS` reached `_trace` instead and every arm was traced as a
    value source.

    This is that function's logic over scopes rather than expressions, and the two are
    deliberately the same shape: `A UNION B MINUS C` is `Except(Union(A, B), C)`, so A and B
    supply values and C constrains, however deep the nesting goes.
    """
    arms = getattr(scope, "union_scopes", None)
    if not arms or len(arms) < 2:
        return [(scope, True)]
    supplies_values = isinstance(scope.expression, exp.Union)
    left, right = arms[0], arms[1]
    return [*_arm_roles(left), (right, supplies_values)]


def _trace_through_set_operation(
    column: exp.Column,
    scope: Any,
    dictionary: Dictionary,
    unresolved: list[Boundary],
    depth: int,
    constraining: list[tuple[str, str, Transform]] | None = None,
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
    roles = _arm_roles(scope)
    flattened = [arm for arm, _ in roles]

    first = getattr(flattened[0].expression, "selects", []) or [] if flattened else []
    name = column.name.upper()
    position = _position_named([item.alias_or_name or "" for item in first], column.name)
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
    for arm, supplies_values in roles:
        if isinstance(arm.expression, exp.Values) and arm.expression.find(exp.Column) is None:
            continue  # literal rows: no source to name, and no position to miss (F0-07)
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
        # An INTERSECT / MINUS arm decides WHICH ROWS survive and supplies none of the
        # value (convention (a), stress 2). Its columns are collected separately so the
        # caller can state them as filter influence on the written relation; tracing them
        # as value would claim a value for rows a MINUS specifically removed.
        sink = traced if supplies_values else constraining
        if sink is None:
            continue
        for inner in _value_columns(projection):
            for table, source_column, transform in _trace(
                inner, arm, dictionary, unresolved, depth + 1
            ):
                sink.append(
                    (
                        table,
                        source_column,
                        _combine(_transform_of_path(projection, inner), transform),
                    )
                )
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
    guard: str | None = None,
) -> PredictedEdge:
    """Build one IR edge.

    Mechanism is AST throughout this module by construction: every edge here comes from
    a resolved syntax tree, not from dataflow, the query log, or inference. Tier is A for
    the same reason - a parser-only derivation with nothing contradicting it.

    `guard` is None for every set-based statement except a multi-table insert, where the
    `WHEN` clause is the whole point: the same source column reaches two different tables
    under two different conditions, and an unguarded pair of edges would say it always
    reaches both.
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
        guard=guard or None,
    )


def _without_user_calls(projection: Any, summaries: dict[str, Any], dialect: str) -> Any:
    """The projection with every SUMMARISED call read as NULL.

    A summarised call supplies its summary instead of its arguments, which only select the
    row the callee reads. An unknown call keeps its arguments as derived values (D-53).
    """
    reduced = projection.copy()
    for node in list(reduced.find_all(exp.Anonymous)):
        name = (getattr(node, "name", "") or "").upper()
        if name and name in summaries:
            node.replace(exp.Null())
    return reduced


def _unsummarised_call(name: str) -> Boundary:
    """The declaration D-53 puts beside the edges an unknown call's arguments give."""
    return Boundary(
        kind=BoundaryKind.SOURCE_UNAVAILABLE,
        subject=name,
        detail=f"call to {name} could not be summarised - its source is not in this "
        f"analysis, so its arguments are taken as derived values of the result (D-53): an "
        f"assumption about a body that was not read",
    )


def _unknown_calls(projection: Any, scope: Scope, dialect: str) -> list[str]:
    """Names of user-defined calls in this projection that no summary covers (D-53)."""
    summaries = _statement_root(scope).__dict__.get("_lineage_summaries") or {}
    return list(
        dict.fromkeys(
            name
            for node in projection.find_all(exp.Anonymous)
            if (name := (getattr(node, "name", "") or "").upper())
            and name not in summaries
            and not is_builtin(name, dialect)
        )
    )


def _target_of(insert: exp.Insert, dictionary: Dictionary) -> tuple[str, list[str] | None]:
    """Target relation and explicit column list, both dictionary-resolved."""
    target = insert.this
    columns: list[str] | None = None
    if isinstance(target, exp.Schema):
        columns = [identifier(c, dictionary.dialect) for c in target.expressions]
        target = target.this
    elif target.args.get("alias") is not None and target.args["alias"].columns:
        columns = [identifier(c, dictionary.dialect) for c in target.args["alias"].columns]
    name = target.name
    # The SCHEMA is part of the name (finding B3-01). Resolving the bare name sent
    # `mart.top_customers` through the default schema, found no `core.top_customers`, and
    # refused every INSERT that omitted its column list.
    qualified = f"{target.db}.{name}" if target.db else name
    resolved = dictionary.resolve(qualified)
    # A schema `relations.bind` supplied counts as written only for PostgreSQL, where D-23
    # makes the path part of the statement's meaning. Oracle keeps declaring an omitted
    # list on an unqualified target rather than binding by a column order nobody stated.
    written_schema = bool(target.db) and (
        dictionary.dialect == "postgres" or not target.meta.get("schema_bound")
    )
    if columns is None and written_schema:
        # An omitted column list means the table's own order - read from the QUALIFIED
        # name, because the caller looks it up by the bare one otherwise.
        try:
            columns = list(dictionary.columns_of(qualified))
        except UnknownObjectError:
            columns = None
    return _relation_name(resolved, fold(name, dictionary.dialect), dictionary), columns


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

    A parenthesised arm - `(SELECT ...) UNION ALL (SELECT ...)`, `(VALUES ...)` - is the
    arm inside the parentheses (finding F0-07); it was refused as "arm is Subquery". A
    parenthesised set operation on the RIGHT is flattened too, its arms constraining when
    the operator joining it does: in `A EXCEPT (B UNION C)` neither B nor C supplies a value.
    """
    while isinstance(node, exp.Subquery) and not node.alias:
        node = node.this
    if isinstance(node, exp.SetOperation):
        adds_rows = isinstance(node, exp.Union)
        right = [(arm, adds_rows and own) for arm, own in _set_operation_arms(node.expression)]
        return [*_set_operation_arms(node.this), *right]
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


def _set_operation_selection(
    body: Any,
    arm: exp.Select,
    target_name: str,
    band: int,
    origin: Origin,
    dictionary: Dictionary,
    unresolved: list[Boundary],
) -> list[PredictedEdge]:
    """D-9 over a WHOLE set operation: `A UNION ALL B ORDER BY 2 DESC LIMIT 20` (B2-04).

    The ORDER BY of a set operation names OUTPUT columns, by ordinal or by the first arm's
    names, and every arm supplies each output column. So the ordered position, read in
    each arm, is what decides which rows survive the cut - in every arm.
    """
    limited = any(body.args.get(arg) is not None for arg in ("limit", "fetch", "offset"))
    order = body.args.get("order")
    if not limited or order is None:
        return []
    first = _set_operation_arms(body)[0][0]
    names = [item.alias_or_name or "" for item in getattr(first, "selects", []) or []]
    projections = arm.selects or []
    chosen: list[Any] = []
    for ordered in order.expressions:
        expression = ordered.this
        position: int | None = None
        if isinstance(expression, exp.Literal) and not expression.is_string:
            position = int(expression.name) - 1
        elif isinstance(expression, exp.Column) and not expression.table:
            position = _position_named(names, expression.name)
        if position is not None and 0 <= position < len(projections):
            item = projections[position]
            chosen.append(item.this if isinstance(item, exp.Alias) else item)
    scope = build_scope(arm)
    if scope is None or not chosen:
        return []
    found = _ordered_sources(chosen, scope, dictionary, unresolved)
    phase = _selection_phase(found)
    return [
        _edge(table, name, target_name, "", Transform.IDENTITY, band, origin,
              flow=Flow.FILTER, phase=phase)
        for table, name, _ in found
    ]


def _with_statement_ctes(statement: Any, body: Any) -> Any:
    """The query a write statement runs, with the CTEs written BEFORE the statement attached.

    **Finding B1-01: 84 of batch B1's 115 misses.** PostgreSQL's usual way to feed a write
    from CTEs is `WITH c AS (...) INSERT INTO t SELECT ... FROM c`. SQLGlot hangs that WITH
    on the `Insert` node, not on the query, and the analysis only ever reads the query - so
    `c` reached `qualify` as a bare table no dictionary holds. Every edge through a CTE was
    lost, declared as `dangling_reference` "relation not in dictionary": honest that
    something was missing, wrong about what. `INSERT ... WITH ... SELECT` hangs the WITH on
    the query and always worked, which is why no earlier package saw it.

    The CTEs are moved onto a COPY of the query, so the statement the caller holds is not
    rewritten under it. When the query has a WITH of its own as well, the statement's CTEs
    go first: the inner ones are in scope of the outer ones in PostgreSQL, never the
    reverse. Both SQLGlot spellings of the arg are read, `with_` and the older `with`.
    """
    outer = statement.args.get("with_") or statement.args.get("with")
    if outer is None or body is None or not isinstance(body, (exp.Select, exp.SetOperation)):
        return body
    body = body.copy()
    inner = body.args.get("with_") or body.args.get("with")
    hoisted = outer.copy()
    if inner is not None:
        hoisted.set("expressions", [*hoisted.expressions, *inner.expressions])
        hoisted.set("recursive", bool(outer.args.get("recursive") or inner.args.get("recursive")))
    body.set("with", None)
    body.set("with_", hoisted)
    return body


def _analyse_insert(
    statement: exp.Insert,
    dictionary: Dictionary,
    band: int,
    unresolved: list[Boundary],
    origin: Origin,
    summaries: dict[str, Any],
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None]:
    target_name, target_columns = _target_of(statement, dictionary)

    body = _with_statement_ctes(statement, statement.expression)

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

        # THE CTEs BELONG TO THE SET OPERATION, NOT TO EITHER ARM (stress finding S4-01).
        #
        # `INSERT INTO t WITH w AS (...) SELECT ... FROM w UNION ALL SELECT ...` hangs the
        # WITH on the Union. Each arm is then analysed on its own below, detached from it,
        # so `FROM w` resolved to a bare Table that is in no dictionary - and the whole
        # statement produced NOTHING, with no refusal and no explanatory boundary, while
        # parse coverage counted it as analysed.
        #
        # S1-02 fixed the top-level set operation and this is the half it could not see:
        # its own test case selects straight from base tables, so the arms needed no CTE to
        # be visible. **A set operation over aggregated CTEs is the normal way to write a
        # reconciliation**, which is why stress 4 hit it on the first try and three earlier
        # packages did not.
        shared = body.args.get("with_") or body.args.get("with")

        for arm, supplies_values in arms:
            inherits_ctes = (
                shared is not None
                and isinstance(arm, exp.Select)
                and arm.args.get("with_") is None
                and arm.args.get("with") is None
            )
            if inherits_ctes:
                arm = arm.copy()
                arm.set("with_", shared.copy())
            if isinstance(arm, exp.Values) and arm.find(exp.Column) is None:
                # Literal rows: no column is the source of anything they add (F0-07).
                continue
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
            edges.extend(
                _set_operation_selection(
                    body, arm, target_name, band, origin, dictionary, unresolved
                )
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
    index = _scope_index(scope)
    deciding = _row_deciding_scopes(scope)
    # What the traced path needs to tell an unknown call from a summarised one (D-53).
    _statement_root(scope).__dict__["_lineage_summaries"] = summaries

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
            # The call supplies its summary; the REST of the projection still supplies its
            # own columns. `ol.unit_price - fn_unit_cost(ol.product_id)` lost unit_price,
            # because the whole projection was taken as handled (B8). With the user calls
            # read as NULL, what remains is traced as any projection is. A projection that
            # was nothing but the call has no column left to give a value, but still gets
            # its GROUP BY influence (`SUM(f(x)) ... GROUP BY k`).
            projection = _without_user_calls(projection, summaries, dictionary.dialect)

        # `constraining` collects what an INTERSECT / MINUS arm reached on the way down
        # (stress finding S4-04). Those columns decide which rows survive and supply none
        # of the value, so they are restated as filter influence on the relation rather than
        # dropped - convention (a) from stress 2, which until now held only for a set
        # operation at the TOP LEVEL of an INSERT and not for one written inside a CTE.
        constraining: list[tuple[str, str, Transform]] = []
        for column in _value_columns(projection):
            # From the scope the column was written in (B2-08): a value column inside a
            # derived table nested in a scalar subquery belongs to THAT query, and tracing it
            # from the statement's scope declared it unresolved while finding it elsewhere.
            for source_table, source_column, traced in _trace(
                column, _home_scope(column, scope, index), dictionary, unresolved, 0, constraining
            ):
                edges.append(
                    _edge(
                        source_table,
                        source_column,
                        target_name,
                        target_column,
                        _combine(_transform_of_path(projection, column), traced),
                        band,
                        origin,
                    )
                )
        for source_table, source_column, _ in constraining:
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

        # A scalar subquery's predicate, and a source that cannot remove a row, decide the
        # VALUE of this column - not which rows exist (D-12, D-13, D-14). This replaced the
        # s7 rule that made a scalar subquery's correlation a filter on the relation.
        edges.extend(
            _value_only_edges(
                projection,
                scope,
                index,
                deciding,
                target_name,
                target_column,
                band,
                origin,
                dictionary,
                unresolved,
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

        # A lookup join's key chose which row this value was read from (D-57). Reported
        # apart from the edges: D-5 gives a join key no edge, and the keys say so.
        if JOIN_KEYS.get():
            for source_table, source_column, _ in _join_key_influence(
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
                        flow=Flow.JOIN_KEY,
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


def _value_only_edges(
    projection: Any,
    scope: Scope,
    index: dict[int, Scope],
    deciding: set[int],
    target_name: str,
    target_column: str,
    band: int,
    origin: Origin,
    dictionary: Dictionary,
    unresolved: list[Boundary],
) -> list[PredictedEdge]:
    """What scopes that CANNOT remove a result row contribute to one output column.

    Two kinds of scope decide WHICH VALUE a column receives without deciding which rows
    exist, and both used to report their predicates as filters on the whole relation:

    * a scalar subquery in this projection (decision D-12, ordered pick D-13);
    * a source on the null-supplying side of an outer join, or a one-row LATERAL, that
      this projection reads (decision D-14).

    Everything such a scope reads, transitively - the CTE a scalar subquery selects from,
    the IN nested inside its WHERE - is in the same position, unless the statement also
    reads it somewhere that can remove rows (then it is a filter there, and only there).

    Their WHERE / HAVING columns are VALUES of this column: `aggregated` when the scope
    aggregates, `conditional` otherwise - a lookup selects which value arrives, as a CASE
    condition does. Their DISTINCT ON and limited ORDER BY columns are INFLUENCE: they
    decide which value without supplying it.
    """
    roots: list[Scope] = []
    for nested in _outermost_subqueries(projection):
        found = index.get(id(nested))
        if found is not None and id(found) not in deciding:
            roots.append(found)
    for column in projection.find_all(exp.Column):
        source = scope.sources.get(column.table) if column.table else None
        if isinstance(source, Scope) and id(source) not in deciding:
            roots.append(source)

    edges: list[PredictedEdge] = []
    for table, name, transform in _null_side_on_values(projection, scope, dictionary, unresolved):
        edges.append(_edge(table, name, target_name, target_column, transform, band, origin))
    for root in {id(root): root for root in roots}.values():
        values, influence = _value_deciding_sweep(root, deciding, dictionary, unresolved)
        for table, name, transform in values:
            edges.append(_edge(table, name, target_name, target_column, transform, band, origin))
        for table, name, _ in influence:
            edges.append(
                _edge(
                    table,
                    name,
                    target_name,
                    target_column,
                    Transform.IDENTITY,
                    band,
                    origin,
                    flow=Flow.INFLUENCE,
                )
            )
    return edges


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
    # A built-in SQLGlot has no class for also parses as Anonymous; it is not a routine
    # (B1-04, PG-04), so it is left to the ordinary value rule - see `analysis.builtins`.
    unknown = [
        name
        for node in projection.find_all(exp.Anonymous)
        if (name := (getattr(node, "name", "") or "").upper())
        and name not in summaries
        and not is_builtin(name, dictionary.dialect)
    ]

    called = [
        (summaries[name], node)
        for node in projection.find_all(exp.Anonymous, exp.Func)
        if (name := (getattr(node, "name", "") or "").upper()) in summaries
    ]

    # D-53 (B4-05): its arguments are taken as derived values of the result - the ordinary
    # value rule, which `_without_user_calls` now leaves them to - AND the call is declared,
    # because that edge rests on a body this analysis did not read. Before D-53 the top
    # level withheld the arguments while the same call inside a CTE claimed them silently.
    for name in dict.fromkeys(unknown):
        unresolved.append(_unsummarised_call(name))

    if not called:
        return [], False

    edges: list[PredictedEdge] = []
    for summary, call in called:
        # What the caller's expression does to the call's VALUE counts too (D-7):
        # `x - f(y)` is derived from f's columns, `SUM(f(y))` aggregated (B8).
        # SQLGlot wraps a schema-qualified call `mart.f(x)` in a Dot, which is not a
        # computation: the path starts outside it.
        while isinstance(call.parent, exp.Dot) and call.parent.expression is call:
            call = call.parent
        around = _transform_of_path(projection, call)
        for item in summary.consolidated():
            # The LAST dot: a PostgreSQL column is `schema.table.column` (B8); an Oracle one
            # `TABLE.COLUMN` splits the same either way.
            source_table, _, source_column = item.column.rpartition(".")
            edges.append(
                _edge(
                    source_table,
                    source_column,
                    target_name,
                    target_column,
                    _combine(item.transform, around),
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
                if identifier(column, dictionary.dialect) in known:
                    edges.append(
                        _edge(
                            relation,
                            identifier(column, dictionary.dialect),
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

    def emit(found: list[tuple[str, str, Transform]], phase: FilterPhase | None = None) -> None:
        for source_table, source_column, reached_by in found:
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
                    # D-6: a WHERE over a value an inner query already aggregated
                    # acts post-aggregation, whatever the keyword says.
                    phase=phase or _filter_phase(FilterPhase.PRE_AGGREGATION, reached_by),
                )
            )

    # Every ROW-DECIDING scope, not just the outermost. A predicate inside an inlined view
    # still decides which rows reach the target, and it is exactly the kind of hidden
    # constraint that makes filter lineage worth reporting. A scope that can NOT remove a
    # result row - a scalar subquery in a projection (D-12), an outer join's null-supplying
    # side or a one-row LATERAL (D-14) - is left to `_value_only_edges`.
    index = _scope_index(scope)
    deciding = _row_deciding_scopes(scope)
    for current in scope.traverse():
        if id(current) not in deciding:
            continue
        query = _query_of_scope(current.expression)
        where = query.args.get("where") if isinstance(query, exp.Select) else None
        if where is not None:
            for column in predicate_columns(where):
                # B2-09: a subquery's ORDER BY decides nothing about a WHERE unless it
                # limits the rows - whether a row EXISTS does not depend on which is first.
                if _in_unlimited_nested_order(column, where) or _in_nested_grouping(
                    column, where
                ):
                    continue
                # B2-08: traced from the scope the column was written in, not this one.
                home = _home_scope(column, current, index)
                # B2-06: `x > (SELECT AVG(y) ...)` and a HAVING inside an IN subquery both
                # constrain an aggregate; the keyword of the OUTER clause does not decide.
                inner_aggregate = any(
                    _is_aggregate(node) or isinstance(node, exp.Having)
                    for node in _ancestors_until(column, where)
                )
                emit(
                    _trace(column, home, dictionary, unresolved),
                    FilterPhase.POST_AGGREGATION if inner_aggregate else None,
                )
                # D-9: `WHERE rn = 1` over a ranking window - the rank's PARTITION BY and
                # ORDER BY decide which row survives, though neither is in `rn`'s value.
                found = _window_filter_sources(column, home, dictionary, unresolved)
                emit(found, _selection_phase(found))

        # D-48 / D-49: a condition in an INNER join's ON that is not its key equality - a
        # test on one relation, or a range between two - removes rows exactly as the same
        # condition in WHERE would. An outer join's is a value of its null side instead
        # (`_null_side_on_values`).
        if isinstance(query, exp.Select):
            for join in query.args.get("joins") or []:
                if is_outer_join(join):
                    continue
                for column in on_condition_columns(join):
                    home = _home_scope(column, current, index)
                    emit(_trace(column, home, dictionary, unresolved))
                    # D-9 as in WHERE: `JOIN r ON ... AND r.rn = 1` keeps the first row per
                    # partition just the same (finding FH-03).
                    found = _window_filter_sources(column, home, dictionary, unresolved)
                    emit(found, _selection_phase(found))

        # D-9: DISTINCT ON and an ORDER BY under a row limit choose which rows land. Over an
        # aggregated query they count and remove GROUPS, so every sort key is post - a
        # grouping key too (D-56, finding FE-01: alone it read pre).
        found = _row_selection_sources(current, dictionary, unresolved)
        emit(
            found,
            FilterPhase.POST_AGGREGATION if _groups_rows(query) else _selection_phase(found),
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
        having = query.args.get("having") if isinstance(query, exp.Select) else None
        if having is not None:
            for column in predicate_columns(having):
                if _in_unlimited_nested_order(column, having) or _in_nested_grouping(
                    column, having
                ):
                    continue
                for source_table, source_column, _ in _trace(
                    column, _home_scope(column, current, index), dictionary, unresolved
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


def _in_nested_grouping(column: Any, root: Any) -> bool:
    """Is this column in the GROUP BY of a query nested under `root`?

    `HAVING SUM(x) > (SELECT AVG(t) FROM (SELECT SUM(y) AS t ... GROUP BY k) s)`: `k`
    decides what each inner total is - influence on a value being compared (D-2) - and
    is not itself compared to anything, so it is not a filter source.
    """
    node = column
    while node is not None and node is not root:
        parent = node.parent
        if isinstance(parent, exp.Select) and node.arg_key == "group":
            return True
        node = parent
    return False


def _in_unlimited_nested_order(column: Any, root: Any) -> bool:
    """Is this column in the ORDER BY of a query nested under `root` that has no row limit?"""
    node = column
    while node is not None and node is not root:
        parent = node.parent
        if isinstance(parent, exp.Select) and node.arg_key == "order":
            # Under EXISTS the ordering never matters, limited or not: existence does not
            # depend on which row comes first (B2-09).
            if isinstance(parent.parent, exp.Exists) or isinstance(
                getattr(parent.parent, "parent", None), exp.Exists
            ):
                return True
            return not any(parent.args.get(arg) is not None for arg in ("limit", "fetch", "offset"))
        node = parent
    return False


def _null_side_aliases(query: Any) -> set[str]:
    """Aliases of the sources an outer join can null in this query (upper-cased)."""
    if not isinstance(query, exp.Select):
        return set()
    source = query.args.get("from_") or query.args.get("from")
    order = [source.this.alias_or_name] if source is not None else []
    nullable: set[str] = set()
    for join in query.args.get("joins") or []:
        name = join.this.alias_or_name
        side = (join.side or "").upper()
        if side in ("RIGHT", "FULL"):
            nullable.update(order)
        if side in ("LEFT", "FULL"):
            nullable.add(name)
        order.append(name)
    return {name.upper() for name in nullable if name}


def _is_single_row_lateral(source: Any) -> bool:
    """A LATERAL that aggregates with no GROUP BY returns exactly one row per outer row."""
    if not isinstance(getattr(source, "expression", None), exp.Lateral):
        return False
    query = _query_of_scope(source.expression)
    return (
        isinstance(query, exp.Select)
        and query.args.get("group") is None
        and any(_is_aggregate(node) for item in query.selects for node in item.walk())
    )


def _cannot_remove_rows(alias: str, source: Any, query: Any) -> bool:
    """D-14: is this source on a side of `query` that cannot remove a result row?"""
    return alias.upper() in _null_side_aliases(query) or _is_single_row_lateral(source)


def _in_predicate_position(sub: Scope, owner: Scope) -> bool:
    """Is this subquery scope written in a WHERE / HAVING / ON of `owner`, not a projection?"""
    node = getattr(sub.expression, "parent", None)
    stop = _query_of_scope(owner.expression)
    while node is not None and node is not stop:
        if isinstance(node, (exp.Where, exp.Having)) or node.arg_key in ("on", "using"):
            return True
        node = node.parent
    return False


def _row_deciding_scopes(root: Scope) -> set[int]:
    """Scopes whose predicates and row selections can remove rows from the result.

    The root, and everything it reads EXCEPT: a scalar subquery in a projection (D-12), and
    a source that cannot remove a row - the null-supplying side of an outer join, or a
    one-row LATERAL (D-14). Those decide the VALUE of the columns they feed and are handled
    as such by `_value_only_edges`.
    """
    return {id(current) for current in _row_deciding_list(root)}


def _row_deciding_list(root: Scope) -> list[Scope]:
    """`_row_deciding_scopes` as the scopes themselves, in visiting order.

    SQLGlot's source-less shell over a recursive CTE's anchor is never visited: it is what
    the recursive arm's self-reference binds to, and it adds nothing (B2-10, TT-3).
    """
    found: dict[int, Scope] = {}

    def visit(current: Scope) -> None:
        if id(current) in found or _is_recursive_anchor_shell(current):
            return
        found[id(current)] = current
        query = _query_of_scope(current.expression)
        # `selected_sources`, not `sources`: SQLGlot lists every CTE in WITH as a source of
        # the root, including one read ONLY inside a scalar subquery - and such a CTE
        # decides one column's value, not which rows exist (D-12, B2 T2 S9).
        selected = getattr(current, "selected_sources", None)
        names = selected.keys() if selected is not None else current.sources.keys()
        for alias in names:
            source = current.sources.get(alias)
            if isinstance(source, Scope) and not _cannot_remove_rows(alias, source, query):
                visit(source)
        for arm in getattr(current, "union_scopes", None) or []:
            visit(arm)
        for sub in getattr(current, "subquery_scopes", None) or []:
            if _in_predicate_position(sub, current):
                visit(sub)

    visit(root)
    return list(found.values())


# UDTF is a LATERAL (finding B2-05): a row source like a derived table, wrapped.
_ROW_SELECTING_SCOPES = (ScopeType.ROOT, ScopeType.DERIVED_TABLE, ScopeType.CTE, ScopeType.UDTF)


def _groups_rows(query: Any) -> bool:
    """Whether this query's rows are groups: a GROUP BY, a HAVING, or an aggregate of its own.

    A window aggregate (`SUM(x) OVER ()`) does not count - it keeps every row.
    """
    if not isinstance(query, exp.Select):
        return False
    if query.args.get("group") is not None or query.args.get("having") is not None:
        return True
    return any(
        _is_aggregate(node) and node.find_ancestor(exp.Window, exp.Select) is query
        for item in query.selects
        for node in item.walk()
    )


def _selection_phase(found: list[tuple[str, str, Transform]]) -> FilterPhase:
    """One phase for every column of one row selection (D-9 with D-6).

    A rank, a DISTINCT ON or a top-N picks rows by ONE ordering. If that ordering runs over
    an aggregated value, the whole selection acts on aggregated rows - including its
    partition columns, which reach it unaggregated. Deciding per column, as `_filter_phase`
    does for a WHERE, would split one cut-off across two phases.
    """
    if any(transform == Transform.AGGREGATED for _, _, transform in found):
        return FilterPhase.POST_AGGREGATION
    return FilterPhase.PRE_AGGREGATION


def _ordering_expressions(select: Any) -> list[Any]:
    """The ORDER BY expressions, with output names and ordinals read as their projections.

    PostgreSQL resolves a bare ORDER BY name against the OUTPUT columns first and only then
    against the inputs, and `qualify` leaves exactly those references unqualified - an
    ordinal comes out as a reference to the generated alias. Reading `ORDER BY cost` as the
    input column when `cost` is also an output alias names the wrong column (T4 S4).
    """
    order = select.args.get("order")
    if order is None:
        return []
    return [_as_projection(ordered.this, select) for ordered in order.expressions]


def _as_projection(expression: Any, select: Any) -> Any:
    """An ordinal or a bare output name read as the projection it names, else itself."""
    projections = select.selects or []
    if isinstance(expression, exp.Literal) and not expression.is_string:
        index = int(expression.name) - 1
        if 0 <= index < len(projections):
            expression = projections[index]
    elif isinstance(expression, exp.Column) and not expression.table:
        index = _position_named([p.alias_or_name or "" for p in projections], expression.name)
        if index is not None:
            expression = projections[index]
    return expression.this if isinstance(expression, exp.Alias) else expression


def _row_choosing_expressions(select: Any) -> list[Any]:
    """What chooses WHICH rows a query keeps by ordering them (D-9): DISTINCT ON keys and
    tie-breakers, and the ORDER BY of a LIMIT / FETCH / OFFSET.

    A DISTINCT ON key is read through the select list like an ORDER BY name (finding TS-2):
    `qualify` rewrites `DISTINCT ON (o.customer_key)` as `DISTINCT ON ("customer_key")`, the
    output name, and in a scope with two relations the bare name bound to neither - the key
    was declared unresolved and dropped from the row selection.
    """
    chosen: list[Any] = []
    distinct = select.args.get("distinct")
    distinct_on = distinct.args.get("on") if distinct is not None else None
    limited = any(select.args.get(arg) is not None for arg in ("limit", "fetch", "offset"))
    if distinct_on is not None:
        keys = distinct_on.expressions if isinstance(distinct_on, exp.Tuple) else [distinct_on]
        chosen.extend(_as_projection(key, select) for key in keys)
    if distinct_on is not None or limited:
        chosen.extend(_ordering_expressions(select))
    return chosen


def _ordered_sources(
    expressions: list[Any], scope: Scope, dictionary: Dictionary, unresolved: list[Boundary]
) -> list[tuple[str, str, Transform]]:
    found: list[tuple[str, str, Transform]] = []
    index = _scope_index(scope)
    for expression in expressions:
        for column in expression.find_all(exp.Column):
            # A join inside an ordered-by subquery is structural (D-5, finding B2-03), and
            # the column is traced from the scope it was written in (B2-08).
            if any(
                node.arg_key in ("on", "using") and isinstance(node.parent, exp.Join)
                for node in [column, *_ancestors_until(column, expression)]
            ):
                continue
            home = _home_scope(column, scope, index)
            for table, name, traced in _trace(column, home, dictionary, unresolved):
                path = _transform_of_path(expression, column)
                found.append((table, name, _combine(path, traced)))
            # An ordering column with no value of its own - a rank or an SRF's ordinal one
            # level down (D-26) - orders by what orders IT, as the same column cut in a
            # WHERE already did (finding FM-01: `ORDER BY u.n LIMIT 5` gave nothing).
            found.extend(_window_filter_sources(column, home, dictionary, unresolved))
    return found


def _row_selection_sources(
    scope: Scope, dictionary: Dictionary, unresolved: list[Boundary]
) -> list[tuple[str, str, Transform]]:
    """Columns that choose WHICH rows a query keeps by ordering them (decision D-9).

    * `DISTINCT ON (k) ... ORDER BY k, ts DESC` - the keys and the tie-breakers.
    * `ORDER BY o ... LIMIT / FETCH / OFFSET` - the ordering, which decides the cut.

    Deliberately NOT: an ORDER BY with no row limit (it removes nothing); a LIMIT with no
    ORDER BY (it keeps arbitrary rows - no column decides which); plain DISTINCT (the rows
    it removes are decided by the values already written). Nor a scalar subquery's
    `ORDER BY ... LIMIT 1`: that picks a VALUE for one column, not which rows land, so only
    root, derived-table and CTE scopes are read.
    """
    if getattr(scope, "scope_type", None) not in _ROW_SELECTING_SCOPES:
        return []
    select = _query_of_scope(scope.expression)
    if not isinstance(select, exp.Select):
        return []
    return _ordered_sources(_row_choosing_expressions(select), scope, dictionary, unresolved)


def _window_filter_sources(
    column: exp.Column, scope: Scope, dictionary: Dictionary, unresolved: list[Boundary]
) -> list[tuple[str, str, Transform]]:
    """A filter on a window's output also filters on what orders and partitions it (D-9).

    `WHERE x.rn = 1` over `ROW_NUMBER() OVER (PARTITION BY p ORDER BY o) AS rn` keeps the
    first row per p by o. `rn` has no value source at all, so tracing it found nothing and
    the "latest row per key" selection - the commonest deduplication in warehouse SQL -
    left no trace. The window's influence columns are exactly the columns the cut depends
    on, found by the same walk `_window_influence` already makes for D-4.
    """
    source = scope.sources.get(column.table) if column.table else None
    if source is None and not column.table and len(scope.sources) == 1:
        source = next(iter(scope.sources.values()))
    if not isinstance(source, Scope) or getattr(source, "union_scopes", None):
        return []
    projection = _projection_named(source, column.name)
    if projection is None:
        return []
    found = _window_influence(projection, source, dictionary, unresolved)
    # B3-10: `ROW_NUMBER() OVER (... ORDER BY SUM(x))` with the SUM written INLINE. Each
    # ordering column traces to a plain base column, so the selection read as
    # pre-aggregation - while it ranks aggregated rows. The window's own expressions say
    # whether it orders by an aggregate; when they do, the whole cut is post (D-9, D-6).
    ordered_by_aggregate = any(
        _is_aggregate(node)
        for window in projection.find_all(exp.Window)
        for part in (window.args.get("order"), *(window.args.get("partition_by") or []))
        if part is not None
        for node in part.walk()
    )
    if ordered_by_aggregate:
        found = [(table, name, Transform.AGGREGATED) for table, name, _ in found]
    return found


def _filter_phase(clause_phase: FilterPhase, transform: Transform) -> FilterPhase:
    """When a filter acts, by what it DOES rather than by which keyword introduced it (D-6).

    **The rule: a predicate acts post-aggregation when the column it constrains reaches it
    THROUGH an aggregate.** A `HAVING` always does. A `WHERE` usually does not - and does
    when it constrains a value an inner query already aggregated, which is the ordinary
    shape of a filter over a CTE:

        WITH agg AS (SELECT cust_id, COUNT(DISTINCT x) AS buyers ... GROUP BY cust_id)
        SELECT ... FROM agg a WHERE a.buyers >= 3

    Why the keyword is the wrong test. D-2 created `FilterPhase` to express WHEN a filter
    acts, and its argument is about consequences: `WHERE quantity > 0` discards line items
    before they are summed, `HAVING SUM(quantity) > 0` discards products whose total came to
    zero. Same column, same flow, different results. The `WHERE` above has the SECOND
    behaviour and the FIRST keyword, so classifying by keyword puts two filters with
    identical consequences on opposite sides of the distinction D-2 exists to draw.

    **Decided per COLUMN, which is finer than D-6's wording and agrees with it everywhere
    the question has been asked.** D-6 speaks of the predicate; an edge is per column, and
    a column's own path is what this can actually see. The two readings differ only for a
    conjunct mixing an aggregated operand with a non-aggregated one - `WHERE buyers >
    cust_id` - which no package contains. That case is left deliberately undecided rather
    than settled here by implementation accident; the conservative answer this gives is
    `pre-aggregation` for the non-aggregated operand.

    One home, applied at both filter-emitting sites. S2-08 is on the register for a rule
    that acquired two copies and drifted.
    """
    if transform == Transform.AGGREGATED:
        return FilterPhase.POST_AGGREGATION
    return clause_phase


def _is_standalone_query(program: Any, statement: Any) -> bool:
    """A query block that stands alone at the top level of a script (PG-09).

    **This was SILENT** - not refused, not declared, and NOT COUNTED, so a file of nothing
    but `SELECT`s returned a clean run at 100% parse coverage with no output and nothing
    anywhere recording that anything had been seen. Read-only analytical SQL is a large
    share of what a lineage tool gets pointed at: view bodies, BI queries, report
    definitions, dbt models.

    **Why the test is POSITION and not statement kind.** Both frontends call a query
    `query_block`, so the kind alone cannot tell PostgreSQL's bare `SELECT` from Oracle's
    `SELECT ... INTO` - and refusing the latter would refuse a construct def-use handles
    and that the signed phase-0 measurement depends on. What actually separates them is
    where the statement sits: a query block inside a routine has an `INTO` or is a cursor's
    body, and one at the top level of a SCRIPT has neither. Only the script frontend
    produces the synthetic unit, so an Oracle program can never satisfy this by
    construction rather than by a name coincidence.

    Refused rather than analysed because the missing piece is a MODEL DECISION, not parser
    work: an edge needs a target, and a bare `SELECT` has projections rather than columns
    of a relation. Naming that target - a synthetic node, the projection's own name - is a
    decision that interacts with S1-05's identity work and is not one to make as a side
    effect. **A refusal costs one statement of parse coverage and converts a silence into a
    statement**, which is the whole difference between a gap and a hole.
    """
    if statement.kind != "query_block":
        return False
    return _enclosing_unit(program, statement).strip().lower() == SCRIPT_UNIT


def _condition_text(expression: Any, dialect: str) -> str:
    """A condition rendered the way the source wrote it, for use as a guard.

    Unquoted deliberately. By the time a statement reaches here `qualify` has quoted every
    identifier, so `.sql()` returns `"GROSS_AMOUNT" > 100` while every other guard in the
    project comes from CFG source text and reads `gross_amount > 100`. `normalise_guard`
    folds case and operand order and **does not touch quotes**, so the two would compare
    unequal - the edge would still match on its key and then be counted guard-INCORRECT.
    Guard correctness is reported separately and was 100%; a quoting artefact is not the way
    to move it.
    """
    rendered = expression.copy()
    for name in rendered.find_all(exp.Identifier):
        name.set("quoted", False)
    return str(rendered.sql(dialect=dialect))


def _analyse_multitable_insert(
    statement: Any,
    dictionary: Dictionary,
    band: int,
    unresolved: list[Boundary],
    origin: Origin,
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None]:
    """`INSERT ALL` / `INSERT FIRST` — one SELECT fanning into several tables.

    Stress finding S2-02. The statement was refused as *"unsupported statement type
    MultitableInserts"*, which was an honest refusal and cost seven labelled edges.

    **The refusal was true and was read as something it did not say.** "Unsupported" was
    taken to mean "unparseable" for the whole life of that entry, by me, in every summary
    table, until the parser probe handed SQLGlot the statement and it parsed cleanly. There
    was never a parser problem here: `MultitableInserts` is a node we are given and the
    analyser declined to walk it. That is the finding's real content, and it is why S2-02
    needed no version bump and no rewrite rule while S2-01 still does.

    Two delegations do the work, both already public for other findings:

    * `resolve_projection` binds each `VALUES` item. An arm's values name the SOURCE
      QUERY'S OUTPUT COLUMNS, not base columns - `VALUES (cust_id, period_month,
      gross_amount)` are the select list's aliases - so every one of them is exactly the
      question S4-02 made that function public to answer.
    * `resolve_query_influence` gives the source's own `WHERE`/`HAVING` and its grouping
      (S4-09), because the source is an ordinary query and its predicates decide which rows
      reach every arm.

    **The `WHEN` becomes a guard, not a filter edge.** `gross_amount` deciding which of two
    tables a row lands in is a condition on the edge, which is what `guard` is for; emitting
    it as filter influence as well would report one fact twice in two mechanisms. The source
    `WHERE` is the opposite case - it applies before any `WHEN` is evaluated - so its edges
    are unguarded and land on every target.

    **`INSERT FIRST` is not `INSERT ALL` with different spelling.** Under `FIRST` a row goes
    to the first matching arm only, so arm two fires on `NOT (c1) AND c2`. The edges are
    identical either way and only the guard differs, which is exactly the kind of difference
    that would never show up as a false positive - so it is handled here rather than left
    for a package that happens to contain one. No key covers `FIRST` yet; this is the
    behaviour a key would be written against.
    """
    source = statement.args.get("source")
    if not isinstance(source, exp.Select):
        return [], (
            RefusalCode.UNSUPPORTED_CONSTRUCT,
            f"multi-table insert whose source is {type(source).__name__}, not a SELECT",
        )

    arms = [a for a in (statement.args.get("expressions") or []) if a.this is not None]
    if not arms:
        return [], (RefusalCode.UNSUPPORTED_CONSTRUCT, "multi-table insert with no INTO arm")

    source_sql = source.sql(dialect=dictionary.dialect)
    influencing = resolve_query_influence(source_sql, dictionary)
    first_only = str(statement.args.get("kind") or "").upper() == "FIRST"

    # Collected before the loop because an ELSE arm fires when NO condition matched,
    # including conditions written after it.
    conditions = [
        _condition_text(arm.args["expression"], dictionary.dialect)
        for arm in arms
        if arm.args.get("expression") is not None
    ]

    edges: list[PredictedEdge] = []
    targets: list[str] = []
    preceding: list[str] = []

    for arm in arms:
        insert = arm.this
        try:
            target_name, columns = _target_of(insert, dictionary)
        except UnknownObjectError as exc:
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.DANGLING_REFERENCE,
                    subject=str(exc),
                    detail=(
                        f"{origin.unit}: multi-table insert names a relation "
                        f"that is not in the dictionary"
                    ),
                )
            )
            continue
        targets.append(target_name)

        condition = arm.args.get("expression")
        if arm.args.get("else_"):
            guard = " AND ".join(f"NOT ({c})" for c in conditions) or None
        elif condition is None:
            # An unconditional arm of an INSERT ALL: every row reaches it.
            guard = None
        elif first_only:
            guard = " AND ".join(
                [*(f"NOT ({c})" for c in preceding), _condition_text(condition, dictionary.dialect)]
            )
        else:
            guard = _condition_text(condition, dictionary.dialect)
        if condition is not None:
            preceding.append(_condition_text(condition, dictionary.dialect))

        values = insert.expression
        if not isinstance(values, exp.Values) or not values.expressions:
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                    subject=target_name,
                    detail=f"{origin.unit}: multi-table insert arm has no VALUES list",
                )
            )
            continue
        if columns is None:
            # Positional binding with nothing to bind TO. The target's dictionary order
            # would be a guess, and a wrong guess writes a value into the wrong column -
            # the same refusal `defuse` makes for `INSERT ... VALUES` (D-1).
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                    subject=target_name,
                    detail=(
                        f"{origin.unit}: multi-table insert arm without a column list - "
                        f"positional binding needs the target's column order stated"
                    ),
                )
            )
            continue

        items = list(values.expressions[0].expressions)
        for index, column_name in enumerate(columns):
            if index >= len(items):
                break
            item = items[index]
            for reference in _value_columns(item):
                resolved = resolve_projection(source_sql, reference.name, dictionary)
                if resolved is None:
                    unresolved.append(
                        Boundary(
                            kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                            subject=reference.name.upper(),
                            detail=(
                                f"{origin.unit}: multi-table insert reads {reference.name}, "
                                f"which the source query does not project"
                            ),
                        )
                    )
                    continue
                relation, base_column, inner = resolved
                edges.append(
                    _edge(
                        relation,
                        base_column,
                        target_name,
                        column_name,
                        _combine(inner, _transform_of_path(item, reference)),
                        band,
                        origin,
                        guard=guard,
                    )
                )

            for projection, relation, base_column in influencing.influence:
                if projection != fold(_reference_name_of(item) or "", dictionary.dialect):
                    continue
                edges.append(
                    _edge(
                        relation,
                        base_column,
                        target_name,
                        column_name,
                        Transform.IDENTITY,
                        band,
                        origin,
                        flow=Flow.INFLUENCE,
                        guard=guard,
                    )
                )

    # **THE SOURCE PREDICATE REACHES EVERY TARGET.** It is evaluated once, before any `WHEN`,
    # so the rows it admits are the rows all the arms draw from. Stress 2's key claims this
    # edge for the first target only - logged as S2-15, and it is S2-13's shape again: a
    # uniform rule applied to the first instance of it.
    for relation, column_name, phase in influencing.filters:
        for target_name in dict.fromkeys(targets):
            edges.append(
                _edge(
                    relation,
                    column_name,
                    target_name,
                    "",
                    Transform.IDENTITY,
                    band,
                    origin,
                    flow=Flow.FILTER,
                    phase=phase,
                )
            )

    return edges, None


def _reference_name_of(item: Any) -> str | None:
    """The single source projection an arm's VALUES item names, if it names exactly one.

    Influence attaches to a projection, so it can only be carried onto a target column that
    IS that projection - `VALUES (total)` takes the grouping's influence, `VALUES (total *
    rate)` is a derived value of two and naming either one as influenced would be a guess.
    """
    columns = _value_columns(item)
    return columns[0].name if len(columns) == 1 else None


def _analyse_create_table_as(
    statement: exp.Create,
    dictionary: Dictionary,
    band: int,
    unresolved: list[Boundary],
    origin: Origin,
    summaries: dict[str, Any],
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None]:
    """`CREATE TABLE x AS SELECT ...` - a write, and one of the commonest in an ETL estate.

    Found SILENT by `scripts/probe_postgres.py`: `create_statement` was not in `SUPPORTED`,
    so a CTAS was skipped before `statements_seen` was incremented - not counted, not
    refused, invisible in parse coverage. A table acquiring its entire contents and being
    reported as having no writer is the worst-shaped failure this analyser has, because it
    reads as a finding rather than as a gap.

    **Delegated to the INSERT path rather than reimplemented**, by building the statement
    CTAS is equivalent to: create the relation, then insert the select into it. That gets
    CTEs, joins, set operations, windows, the transform ladder and filter phase for free and
    keeps one implementation of each. Writing a second traversal here is how S2-08 happened.

    The target's columns are the SELECT'S OUTPUT NAMES - which is what CTAS does - unless
    the statement names them itself, as `CREATE TABLE x (a, b) AS SELECT ...` may.
    """
    target = statement.this
    columns: list[exp.Expression] = []
    if isinstance(target, exp.Schema):
        columns = list(target.expressions)
        target = target.this
    if not isinstance(target, exp.Table):
        # `CREATE INDEX`, `CREATE VIEW`, `CREATE FUNCTION` and friends reach here. None of
        # them is a row-level write, and inventing one would be worse than declining.
        return [], (
            RefusalCode.UNSUPPORTED_CONSTRUCT,
            f"CREATE of {statement.args.get('kind') or type(target).__name__}, "
            f"which does not write rows from a query",
        )

    select = statement.expression
    if not isinstance(select, exp.Select | exp.Union | exp.Except | exp.Intersect):
        return [], (
            RefusalCode.UNSUPPORTED_CONSTRUCT,
            "CREATE TABLE without AS SELECT - it declares a shape and writes no rows",
        )

    if not columns:
        named = [item.alias_or_name for item in getattr(select, "selects", []) or []]
        if not all(named):
            # An unnamed projection - `SELECT a + b FROM t` - has no column name for the
            # created table, so positional binding would be inventing one.
            unresolved.append(
                Boundary(
                    kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                    subject=target.name,
                    detail=(
                        f"{target.name}: CREATE TABLE AS SELECT with an unnamed projection - "
                        f"the created column's name is decided by the server, not by the text"
                    ),
                )
            )
            return [], None
        columns = [exp.to_identifier(name) for name in named]

    equivalent = exp.Insert(
        this=exp.Schema(this=target, expressions=columns),
        expression=select,
    )
    return _analyse_insert(equivalent, dictionary, band, unresolved, origin, summaries)


RESULT_RELATION = "<result>"
"""Target relation for a query that writes nowhere (PG-09).

Angle-bracketed like `<script>`, and for the same reason: it is a name the analyser made
up, and it must not be mistakable for a name someone's schema might contain.
"""


def _analyse_standalone_query(
    select: Any,
    dictionary: Dictionary,
    band: int,
    unresolved: list[Boundary],
    origin: Origin,
    summaries: dict[str, Any],
    result_target: str | None,
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None]:
    """A query that writes nowhere - the shape of every dbt/Databricks model (PG-09).

    **Until 2026-09-24 this was silent, and then briefly refused.** Refusing was the honest
    interim: an edge needs a target, and a bare `SELECT` has projections rather than columns
    of a relation, so naming that far end is a model decision rather than parser work. The
    decision has now been taken, and it is that a query's OUTPUT is a real place for lineage
    to land.

    **Two ways to name it, because the two uses differ:**

    * Nothing supplied - the projections land on `<result>`, so any pasted query produces
      lineage with no configuration at all. Enough to read one query; NOT enough to tell two
      apart, since every file would write into the same relation.
    * A `result_target` supplied - the projections land on that relation instead, exactly as
      an `INSERT` into it would. This is what makes a directory of model files chain: model
      A's output columns are named the same way model B's `FROM` clause names them, so the
      lineage crosses the file boundary.

    **Delegated to the INSERT path rather than reimplemented.** Building the equivalent
    `INSERT` gets CTEs, set operations, windows, the transform ladder and the filter phase
    for free, and keeps one implementation of each. The same reasoning as
    `_analyse_create_table_as`, and the reason S2-08 is on the register.

    Unnamed projections are named by POSITION and declared. That is the one place this
    differs from CTAS, which refuses them: a created table's column name is decided by the
    server and guessing it would be inventing schema, whereas a result set's third column
    IS the third column - position is its identity, not a guess about one.
    """
    projections = list(getattr(select, "selects", []) or [])
    if not projections:
        return [], (
            RefusalCode.UNSUPPORTED_CONSTRUCT,
            "a query with no select list has no output columns to attribute lineage to",
        )

    columns: list[exp.Expression] = []
    unnamed = 0
    for position, item in enumerate(projections, start=1):
        name = item.alias_or_name
        if not name:
            unnamed += 1
            name = f"column_{position}"
        # Keep a quoted name's case: RETURNING "CustomerID" is <result>.CustomerID (B9-02).
        written = item.args.get("alias") if isinstance(item, exp.Alias) else item
        if isinstance(written, exp.Column):
            written = written.this
        quoted = isinstance(written, exp.Identifier) and written.quoted
        columns.append(exp.to_identifier(name, quoted=quoted))

    if result_target:
        target = exp.to_table(result_target, dialect=dictionary.dialect)
    else:
        # Quoted so the angle brackets survive the round trip into `exp.Table.name`.
        target = exp.Table(this=exp.to_identifier(RESULT_RELATION, quoted=True))

    if unnamed:
        unresolved.append(
            Boundary(
                kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
                subject=target.name.upper(),
                detail=(
                    f"{origin.unit}: {unnamed} projection(s) of this query have no name, so "
                    f"their lineage is reported against a POSITIONAL one (column_N). The "
                    f"edges are real; the column names are this analyser's, not the query's"
                ),
            )
        )

    equivalent = exp.Insert(
        this=exp.Schema(this=target, expressions=columns),
        expression=select,
    )
    return _analyse_insert(equivalent, dictionary, band, unresolved, origin, summaries)


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

    resolved = dictionary.resolve(f"{target.db}.{name}" if target.db else name)
    target_name = _relation_name(resolved, fold(name, dictionary.dialect), dictionary)

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
                identifier(column, dictionary.dialect),
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
        resolved = dictionary.resolve(f"{table.db}.{table.name}" if table.db else table.name)
        real = _relation_name(resolved, identifier(table, dictionary.dialect), dictionary)
        tables[fold(table.alias or table.name, dictionary.dialect)] = real

    qualifier = fold(column.table or "", dictionary.dialect)
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

    name = identifier(column, dictionary.dialect)
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
    target_name = _relation_name(
        dictionary.resolve(f"{target.db}.{target.name}" if target.db else target.name),
        identifier(target, dictionary.dialect),
        dictionary,
    )

    using = statement.args.get("using")
    if using is None:
        return [], (RefusalCode.UNSUPPORTED_CONSTRUCT, "MERGE without a USING clause")

    # Build a scope over the USING source so its projections can be traced.
    wrapper = exp.Select().select(exp.Star()).from_(using)
    # `WITH src AS (...) MERGE INTO t USING src ...` hangs the WITH on the Merge. The
    # wrapper is what is analysed, so it carries the WITH or `src` is a relation nobody
    # defines - B1-01's defect, in the one write it had not been fixed for (finding B3-05).
    outer = statement.args.get("with_") or statement.args.get("with")
    if outer is not None:
        wrapper.set("with_", outer.copy())
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
    target_alias = fold(target.alias or target.name or "", dictionary.dialect)

    def _trace_in_merge(column: exp.Column) -> list[tuple[str, str, Transform]]:
        """Trace against the USING source, or against the MERGE target itself.

        The USING scope is consulted FIRST: an alias that exists there is that source's,
        whatever it is called, and preferring the target would silently redirect a real
        source reference at the table being written.
        """
        qualifier = fold(column.table or "", dictionary.dialect)
        if qualifier and qualifier not in scope.sources and qualifier == target_alias:
            name = identifier(column, dictionary.dialect)
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

    def _influence_edges(expression: Any, target_column: str) -> list[PredictedEdge]:
        """A MERGE's GROUP BY and window influence (stress finding S4-03).

        S3-03 gave a MERGE its FILTER edges and stopped there, so `_grouping_influence` and
        `_window_influence` were never reached from this statement type at all. A `USING`
        clause is a query like any other: it groups, it windows, and what those decide about
        an aggregated column is a dependency of the column the arm writes.

        Both walk outward from the setter's expression through the USING scope, so a
        `GROUP BY` four CTE levels inside the `USING` is found the same way it is under an
        `INSERT ... SELECT` - which is the point of reusing them rather than writing a
        MERGE-shaped version.
        """
        found: list[PredictedEdge] = []
        for resolver in (_window_influence, _grouping_influence):
            for source_table, source_column, _ in resolver(
                expression, scope, dictionary, unresolved
            ):
                found.append(
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
        return found

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
        # An arm's own condition - `WHEN MATCHED AND s.status = 'CLOSED'`, PostgreSQL 17's
        # `WHEN NOT MATCHED BY SOURCE AND t.is_current` - decides which rows that arm
        # writes or deletes: a filter, exactly like the arm's WHERE below. It gave nothing
        # (finding B3-09), so a MERGE that deletes closed customers reported no dependency
        # on `status` at all.
        # A condition on an aggregate of the USING source (`s.amt > 100`, amt a SUM) acts on
        # aggregated rows: post-aggregation, by D-6, as ON CONFLICT's DO UPDATE WHERE already
        # was (finding B10-06).
        condition = when.args.get("condition")
        if condition is not None:
            for column in predicate_columns(condition):
                for source_table, source_column, traced in _trace_in_merge(column):
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
                            phase=_filter_phase(FilterPhase.PRE_AGGREGATION, traced),
                        )
                    )
        action = when.args.get("then")
        if isinstance(action, exp.Update):
            # The arm's own WHERE - `WHEN MATCHED THEN UPDATE SET ... WHERE x.b > 0`. It
            # sits on the Update rather than on the WHEN, and it decides which matched rows
            # are actually written, so it filters this statement's effect as surely as the
            # USING predicate does.
            arm_where = action.args.get("where")
            if arm_where is not None:
                for column in predicate_columns(arm_where):
                    for source_table, source_column, traced in _trace_in_merge(column):
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
                                phase=_filter_phase(FilterPhase.PRE_AGGREGATION, traced),
                            )
                        )

            for setter in action.args.get("expressions") or []:
                if not isinstance(setter, exp.EQ):
                    continue
                target_column = identifier(setter.this, dictionary.dialect)
                edges.extend(_influence_edges(setter.expression, target_column))
                for column in setter.expression.find_all(exp.Column):
                    for src_table, src_column, traced in _trace_in_merge(column):
                        edges.append(
                            _edge(
                                src_table,
                                src_column,
                                target_name,
                                target_column,
                                _combine(
                                    _transform_of_path(setter.expression, column), traced
                                ),
                                band,
                                origin,
                            )
                        )
        elif isinstance(action, exp.Insert):
            # In a MERGE the insert arm carries a Tuple of columns and a Tuple of values,
            # not the Schema/Values pair a standalone INSERT uses.
            target_columns = [
                identifier(c, dictionary.dialect)
                for c in getattr(action.this, "expressions", []) or []
            ]
            values = action.expression
            if isinstance(values, exp.Values):
                items = list(values.expressions[0].expressions)
            else:
                items = list(getattr(values, "expressions", []) or [])
            # FH-02: no column list means the target's own columns in declared order - the
            # first N for N values, as for a plain INSERT. The arm was skipped instead, so
            # every column only the INSERT arm writes vanished with nothing declared.
            if not target_columns:
                try:
                    target_columns = list(dictionary.columns_of(target_name))[: len(items)]
                except UnknownObjectError as exc:
                    return [], (RefusalCode.NAME_NOT_RESOLVED, str(exc))
            if len(target_columns) != len(items):
                return [], (
                    RefusalCode.SHAPE_NOT_DECIDABLE,
                    f"MERGE insert arm writes {len(items)} values into "
                    f"{len(target_columns)} columns",
                )
            for target_column, item in zip(target_columns, items, strict=True):
                edges.extend(_influence_edges(item, target_column))
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
                                _combine(_transform_of_path(item, column), traced),
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
    result_target: str | None = None,
    join_keys: bool = False,
) -> AnalysisResult:
    """Analyse one PL/SQL source unit for band-0 lineage.

    `join_keys` adds `Flow.JOIN_KEY` edges to `edges` (D-57) for `procedure` to set apart;
    left False, none is computed.

    `result_target` names the relation a TARGETLESS query writes into (PG-09). Left unset,
    such a query's projections land on `<result>`, which is enough to read one query and
    not enough to tell two apart. Supplied - as a dbt or Databricks model's own table name
    - the projections land there instead, and lineage crosses the file boundary.

    Deliberately a parameter rather than an `AnalysisConfig` field: config is fingerprinted
    into every signed measurement, and this is a property of ONE invocation rather than a
    declared limit of the run.
    """
    settings = config or AnalysisConfig()
    known_calls = summaries or {}
    result = AnalysisResult()

    # Parsed by the DIALECT's front end, not by Oracle's. `Dialect.frontend` existed from
    # A3 and nothing called it: every entry point still reached for `parse_program`, which
    # is the Oracle ANTLR parser. A PostgreSQL script went through it, produced no units
    # and no statements, and analysed to zero edges in silence - the failure this seam was
    # built to make impossible, one call site short of being impossible.
    program = for_name(dictionary.dialect).frontend.parse(source)

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
    # Oracle only, and the gate is inside the function rather than written here - `$IF$` is
    # a legal PostgreSQL dollar-quote tag, so an unscoped span refuses readable statements
    # for an Oracle reason (finding PG-17).
    ccflag_spans = conditional_compilation_spans(source, program.dialect)

    path_token = SEARCH_PATH.set(None)
    keys_token = JOIN_KEYS.set(join_keys)
    # D-31: objects the script creates, visible to the statements after them (B7).
    catalog = ScriptCatalog(dictionary) if program.dialect == "postgres" else None
    for statement, statement_band in static + recovered:
        # D-23: `SET search_path` / `RESET search_path` change how every later unqualified
        # name binds, in script order. They carry no lineage of their own.
        change = search_path_change(statement.text, program.dialect)
        if is_path_change(change):
            SEARCH_PATH.set(change)
            continue
        effect = catalog.effect(statement.text) if catalog is not None else None
        if effect is not None:
            if effect.skip:
                continue
            if effect.rewrite is not None:
                kind = statement_kind(effect.rewrite, program.dialect)
                statement = dataclasses.replace(statement, text=effect.rewrite, kind=kind)
            elif effect.count_only:
                # DDL whose lineage the catalog states itself (D-33, D-34), or none at all.
                result.statements_seen += 1
                origin = Origin(unit=_enclosing_unit(program, statement), line=statement.line)
                for source_table, source_column, target, target_column, transform in effect.facts:
                    result.edges.append(
                        _edge(source_table, source_column, target, target_column, transform,
                              statement_band, origin)
                    )
                if effect.unknown is not None:
                    result.boundaries.append(
                        Boundary(
                            kind=BoundaryKind.SOURCE_UNAVAILABLE,
                            subject=effect.unknown,
                            detail=effect.unknown_detail,
                            unit=origin.unit,
                            line=statement.line,
                        )
                    )
                continue
        # B10-01: text SQLGlot reads as SEVERAL statements means the splitter cut in the
        # wrong place. It was skipped below with nothing said - sixteen statements of one
        # stress file, silently. Whatever the splitter gets wrong next, it is declared.
        if statement.kind == "block_statement":
            result.statements_seen += 1
            result.boundaries.append(
                Boundary(
                    kind=BoundaryKind.PARSE_FAILURE,
                    subject=f"{SCRIPT_UNIT}:{statement.line}",
                    detail=f"{SCRIPT_UNIT}: line {statement.line}: the text holds several "
                    "statements, so the script was split in the wrong place",
                    unit=_enclosing_unit(program, statement),
                    line=statement.line,
                )
            )
            continue
        working = catalog.dictionary if catalog is not None else dictionary
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

        # PG-09. A query standing alone at script level is ANALYSED, with its own
        # projections as the target columns - the shape of every dbt/Databricks model.
        # It was silent until 2026-09-24 and refused for part of that day; the refusal
        # was the honest interim while the target was a model decision, and the decision
        # has been taken.
        # A PostgreSQL UPDATE is analysed HERE, restated as the read it performs (B3,
        # `pg_dml`) - and so it is counted, which closes PG-01 for PostgreSQL. Oracle keeps
        # UPDATE in def-use, which can tell a PL/SQL variable from a column.
        postgres_update = (
            program.dialect == "postgres" and statement.kind == "update_statement"
        )
        # TT-4: a script statement SQLGlot cannot parse at all has no kind to dispatch on,
        # and was skipped here uncounted - a 2,963-line query read as an empty file
        # (statements seen 0). It goes on, so `_analyse_statement` counts it and refuses it
        # PARSE_FAILED like any other statement it cannot read.
        unparsed = (
            statement.kind == "unparsed_statement"
            and _enclosing_unit(program, statement).strip().lower() == SCRIPT_UNIT
        )
        # F0-01: a CREATE ... AS <query> that SQLGlot kept as an opaque Command (an option
        # `pg_grammar` does not restate) carries a whole object's lineage. It was skipped
        # here uncounted; it is counted and refused instead, never dropped.
        opaque_create = statement.kind == "command_statement" and _creates_from_query(
            statement.text
        )
        if (
            statement.kind not in SUPPORTED
            and not postgres_update
            and not unparsed
            and not opaque_create
            and not _is_standalone_query(program, statement)
        ):
            continue
        result.statements_seen += 1
        unit = _enclosing_unit(program, statement)
        origin = Origin(unit=unit, line=statement.line)
        if opaque_create:
            result.refusals.append(
                Refusal(
                    kind=statement.kind,
                    line=statement.line,
                    reason="SQLGlot kept this CREATE ... AS <query> as an unparsed command "
                    "(an option it cannot read), so the lineage of what it creates cannot be "
                    "named",
                    excerpt=" ".join(statement.text.split())[:80],
                    code=RefusalCode.PARSE_FAILED,
                    unit=unit,
                    end_line=last_line(statement),
                )
            )
            continue

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

        # A construct the analyser cannot resolve. Before refusing the STATEMENT, ask
        # whether the construct is confined to a named CTE - finding PG-20, where one
        # `JSON_TABLE` in one CTE out of eighty-five discarded the other eighty-four.
        # `cte_scoped_construct` returns None for everything it cannot prove confined, so
        # the fall-through below is the original behaviour unchanged.
        drop_ctes: frozenset[str] = frozenset()
        construct = classify_statement(statement.text, program.dialect)
        if construct is not None:
            scoped = cte_scoped_construct(statement.text, program.dialect)
            if scoped is None:
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

            _, poisoned = scoped
            # One refusal PER CTE, each addressed at its own body. A single refusal
            # spanning the first and last poisoned CTE would swallow every clean CTE
            # between them, and then `violations` - which asks whether any edge originates
            # inside a refusal's span - would report every recovered edge as a
            # contradiction. The narrow spans are what make that cross-check mean
            # something here rather than merely pass.
            for cte in poisoned:
                result.refusals.append(
                    Refusal(
                        kind=statement.kind,
                        line=statement.line + cte.line - 1,
                        reason=f"{construct.reason}; confined to CTE {cte.name}, whose "
                        f"columns are withheld while the rest of the statement is analysed",
                        excerpt=" ".join(statement.text.split())[:80],
                        code=construct.code,
                        unit=unit,
                        scope="cte",
                        end_line=statement.line + cte.end_line - 1,
                    )
                )
            result.partial_statements += 1
            result.scoped_statements.add((unit, statement.line))
            drop_ctes = frozenset(cte.name.upper() for cte in poisoned)

        edges, refusal, unresolved = _analyse_statement(
            statement,
            working,
            settings,
            statement_band,
            origin,
            known_calls,
            result_target,
            drop_ctes,
        )
        edges = _hardest_per_path(edges)
        if effect is not None and effect.finalize is not None:
            effect.finalize()  # a view becomes inlinable only after its own CREATE
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

    JOIN_KEYS.reset(keys_token)
    SEARCH_PATH.reset(path_token)
    return result


def _enclosing_unit(program: Program, statement: ParsedStatement) -> str:
    """Which program unit a statement sits inside.

    Origin is required on every IR edge: a fact whose origin cannot be stated is not
    evidence. Units are sorted by line, so the last one starting at or before the
    statement is its parent.
    """
    candidates = [unit for unit in program.units if unit.line <= statement.line]
    return fold(candidates[-1].name, program.dialect) if candidates else "<anonymous>"


def _drop_ctes(
    parsed: Any, names: frozenset[str]
) -> tuple[Any, list[Boundary], frozenset[str]]:
    """Remove the refused CTEs from the tree, declare each, and report which were found.

    The third return value is what was ACTUALLY dropped. The caller compares it with what
    it asked for, because "I dropped nothing" and "there was nothing to drop" must not be
    the same answer - see the scar below.

    **Removal, not avoidance, and the difference is the whole safety of PG-20's fix.** A
    refusal narrowed to a CTE has to stop that CTE from CONTRIBUTING as well as from being
    trusted, because a construct the classifier refuses is not thereby a construct SQLGlot
    declines to interpret. `CONNECT BY` is the recorded case: it parses cleanly and emits
    three `x -> x` self-edges, which look entirely ordinary beside a real projection.
    Leaving the body in the tree and merely declining to believe it would have re-created
    the guessed edge the classifier exists to prevent - and the edge would have carried the
    statement's origin, outside the CTE-scoped refusal's span, so `violations` would not
    have caught it either.

    What remains is a reference to a relation nobody defines, which is exactly what it is.
    Downstream columns taken from it resolve to nothing and are declared - measured, not
    assumed: the reduction in `tests/test_cte_scoped_refusal.py` yields a
    `dangling_reference` for the dropped CTE and NO edge for the column read out of it.

    An empty `WITH` list is removed from its parent rather than left behind, since a `WITH`
    with no CTEs is not valid SQL and later passes regenerate the tree.

    **The clause is located by TRAVERSAL, never by argument key**, and that is a scar. The
    first version read `parsed.args.get("with")`, which is `None` twice over: an INSERT's
    `WITH` hangs off the inner `Select`, and the key there is spelled `with_`. So it found
    nothing, dropped nothing, returned no boundary - and the reduction still produced the
    right four edges, because `JSON_TABLE` happens to dangle on its own. A silent no-op
    wearing a passing result. That is the same defect as `from_` vs `from` in `infer.py`
    and `with` vs `with_` in `bisect_chain.py`: the third time an argument key returned an
    empty answer where "not found" was meant.
    """
    targets = [
        clause
        for clause in parsed.find_all(exp.With)
        if any((cte.alias_or_name or "").upper() in names for cte in clause.expressions)
    ]
    if not targets:
        return parsed, [], frozenset()

    # The shallowest matching clause is the statement's own. A `WITH` nested inside a CTE
    # body is a different scope that may reuse a name, and `cte_bodies` only ever reports
    # top-level CTEs, so a deeper match is a coincidence of spelling rather than the thing
    # that was refused.
    with_clause = min(targets, key=_node_depth)
    poisoned = [
        cte for cte in with_clause.expressions if (cte.alias_or_name or "").upper() in names
    ]
    kept = [cte for cte in with_clause.expressions if cte not in poisoned]

    withheld = [
        Boundary(
            kind=BoundaryKind.DANGLING_REFERENCE,
            subject=cte.alias_or_name,
            detail=f"CTE {cte.alias_or_name} was refused, so columns taken from it have "
            f"no traceable source; the rest of the statement was analysed",
        )
        for cte in poisoned
    ]

    if kept:
        with_clause.set("expressions", kept)
    else:
        with_clause.pop()
    return parsed, withheld, frozenset((cte.alias_or_name or "").upper() for cte in poisoned)


def _node_depth(node: Any) -> int:
    depth = 0
    parent = node.parent
    while parent is not None:
        depth += 1
        parent = parent.parent
    return depth


_CREATE_AS_QUERY = re.compile(
    r"^\s*CREATE\b.*?\bAS\s*(?:\(|SELECT\b|WITH\b|VALUES\b|TABLE\b)", re.IGNORECASE | re.DOTALL
)


def _creates_from_query(text: str) -> bool:
    """True for `CREATE ... AS <query>` text, comments in front of it set aside (F0-01)."""
    index = 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if not text.startswith(("--", "/*"), index):
            break
        index = skip_lexeme(text, index) or len(text)
    return _CREATE_AS_QUERY.match(text[index:]) is not None


def _analyse_statement(
    statement: ParsedStatement,
    dictionary: Dictionary,
    config: AnalysisConfig,
    band: int,
    origin: Origin,
    summaries: dict[str, Any],
    result_target: str | None = None,
    drop_ctes: frozenset[str] = frozenset(),
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None, list[Boundary]]:
    unresolved: list[Boundary] = []
    try:
        parsed: Any = sqlglot.parse_one(
            strip_unparseable_clauses(statement.text), dialect=dictionary.dialect
        )
    except Exception as exc:
        return (
            [],
            (RefusalCode.PARSE_FAILED, f"SQLGlot could not parse: {str(exc).splitlines()[0]}"),
            unresolved,
        )

    if drop_ctes:
        parsed, withheld, dropped = _drop_ctes(parsed, drop_ctes)
        unresolved.extend(withheld)

        # Two guards, because the CTE spans were located by TEXT and the tree is the
        # authority. Either failing means the narrowed refusal cannot be justified, so the
        # statement falls back to being refused whole - the behaviour PG-20 improves on,
        # which is exactly the right place to land when the improvement cannot be proved.
        #
        # The first catches a name that is not in the tree at all, which would make
        # `_drop_ctes` a silent no-op and let the refused construct contribute edges under
        # cover of a refusal that claims it was withheld.
        if dropped != drop_ctes:
            return (
                [],
                (
                    RefusalCode.UNSUPPORTED_CONSTRUCT,
                    f"refused CTE(s) {', '.join(sorted(drop_ctes - dropped))} were located "
                    f"in the text but are not CTEs of the parsed statement, so the refusal "
                    f"could not be narrowed safely and the statement is refused whole",
                ),
                unresolved,
            )

        # The second catches a span that was too LONG: if blanking it hid a construct that
        # actually lives elsewhere, the confinement check would have passed while the
        # construct stayed in the tree. Measured need, not hypothetical - the torture
        # file's `E'O\\'Reilly'` desynchronised the noise-stripper and over-spanned a CTE by
        # 150 lines (finding PG-23). That cause is fixed; the guard stays, because the next
        # lexer gap the stripper has will look exactly the same from here. Honest
        # limitation: this re-reads GENERATED SQL, so a
        # construct SQLGlot normalises beyond recognition would slip past.
        try:
            regenerated = parsed.sql(dialect=dictionary.dialect)
        except Exception as exc:
            # Failing CLOSED. If the remaining tree cannot be re-read, the confinement
            # cannot be confirmed, and an unconfirmed narrowing is the one outcome this
            # guard exists to prevent.
            return (
                [],
                (
                    RefusalCode.UNSUPPORTED_CONSTRUCT,
                    f"could not re-read the statement after withholding its refused CTEs, "
                    f"so the narrowed refusal could not be confirmed: "
                    f"{str(exc).splitlines()[0]}",
                ),
                unresolved,
            )

        still_there = classify_statement(regenerated, dictionary.dialect)
        if still_there is not None:
            return (
                [],
                (
                    still_there.code,
                    f"{still_there.reason}; it survived the CTE-scoped refusal, so it is "
                    f"not confined to the CTEs that were withheld and the statement is "
                    f"refused whole",
                ),
                unresolved,
            )

    # PostgreSQL writes are restated as the reads they perform (batch B3, `pg_dml`): an
    # UPDATE, a DELETE, an ON CONFLICT DO UPDATE, a RETURNING and a data-modifying CTE each
    # become one or more statements the INSERT ... SELECT path already analyses fully.
    # Every other statement is its own single part.
    parts = expand_postgres_writes(parsed, dictionary)
    edges: list[PredictedEdge] = []
    for part in parts:
        try:
            part_edges, refusal = _analyse_parsed(
                part, dictionary, config, band, origin, summaries, result_target, unresolved
            )
        except RecursionError:
            # TE-1's backstop. The walkers stop at TRACE_DEPTH_CAP, far inside the stack;
            # if some other recursion runs out first, this statement is refused by name
            # rather than the exception taking every other statement in the file with it.
            return (
                [],
                (
                    RefusalCode.BUDGET_EXCEEDED,
                    "the statement nests deeper than the analyser's stack allows",
                ),
                unresolved,
            )
        if refusal is not None:
            return [], refusal, unresolved
        edges.extend(part_edges)
    return edges, None, unresolved


def _analyse_parsed(
    parsed: Any,
    dictionary: Dictionary,
    config: AnalysisConfig,
    band: int,
    origin: Origin,
    summaries: dict[str, Any],
    result_target: str | None,
    unresolved: list[Boundary],
) -> tuple[list[PredictedEdge], tuple[RefusalCode, str] | None]:
    """One parsed statement: inline views, resolve windows, qualify, dispatch by kind."""
    parsed, notes = _inline_views(parsed, dictionary, config.budgets.view_expansion_depth_cap)
    # After view inlining, so a named window inside an expanded view is resolved too.
    parsed = _resolve_named_windows(parsed)

    original = parsed.copy()
    retried: Boundary | None = None
    try:
        parsed = _qualify(parsed, dictionary)
    except Exception as exc:
        message = str(exc).splitlines()[0]
        # A qualified reference to a column its relation does not have - `fr.net_income`
        # where `fr` is a CTE that never projects it - used to refuse the WHOLE statement:
        # one bad name in a 49-column report cost all 49 columns their lineage. Retried
        # with partial qualification, the trace declares that one reference unresolved and
        # drops only it; every column that does not depend on it keeps its lineage.
        if not message.startswith("Unknown column:"):
            return [], (RefusalCode.NAME_NOT_RESOLVED, f"could not qualify names: {message}")
        try:
            parsed = _qualify(original, dictionary, partial=True)
        except Exception as retry:
            reason = str(retry).splitlines()[0]
            return [], (RefusalCode.NAME_NOT_RESOLVED, f"could not qualify names: {reason}")
        name = message.split(":", 1)[1].strip()
        retried = Boundary(
            kind=BoundaryKind.UNRESOLVED_IDENTIFIER,
            subject=name.upper(),
            detail=(
                f"{name} (not a column of the relation it names - analysed without it; "
                "the columns that depend on it lose that source)"
            ),
        )
        unresolved.append(retried)

    if isinstance(parsed, exp.Insert):
        edges, refusal = _analyse_insert(parsed, dictionary, band, unresolved, origin, summaries)
    elif isinstance(parsed, exp.Merge):
        edges, refusal = _analyse_merge(parsed, dictionary, band, unresolved, origin, summaries)
    elif isinstance(parsed, exp.Delete):
        edges, refusal = _analyse_delete(parsed, dictionary, band, unresolved, origin)
    elif isinstance(parsed, exp.MultitableInserts):
        edges, refusal = _analyse_multitable_insert(
            parsed, dictionary, band, unresolved, origin
        )
    elif isinstance(parsed, exp.Create):
        edges, refusal = _analyse_create_table_as(
            parsed, dictionary, band, unresolved, origin, summaries
        )
    elif isinstance(parsed, exp.Select | exp.SetOperation):
        # PG-09. Only reachable when the statement loop let a query through, which it does
        # only for one standing alone at script level - so an Oracle `SELECT ... INTO`,
        # which shares the kind `query_block`, never arrives here.
        edges, refusal = _analyse_standalone_query(
            parsed, dictionary, band, unresolved, origin, summaries, result_target
        )
    else:
        return (
            [],
            (
                RefusalCode.UNSUPPORTED_CONSTRUCT,
                f"unsupported statement type {type(parsed).__name__}",
            ),
        )

    if refusal is not None:
        return [], refusal

    # The trace usually declares the reference itself, with where it failed; the retry's
    # boundary then says it twice, so it stays only when the trace never reached the name.
    if retried is not None and any(
        b is not retried
        and b.kind == retried.kind
        and (b.subject == retried.subject or b.subject.endswith("." + retried.subject))
        for b in unresolved
    ):
        unresolved.remove(retried)

    # Notes from view inlining are boundaries too - a view left unexpanded means the
    # lineage below it was not reached.
    #
    # View-inlining notes arrive as prose from the resolver; classify them here, where the
    # reason they exist is still in scope. A view left unexpanded means the lineage below
    # it was never reached, which is a source that was unavailable to us.
    unresolved.extend(
        Boundary(
            kind=BoundaryKind.SOURCE_UNAVAILABLE,
            subject=str(note).split(" ")[1] if " " in str(note) else str(note),
            detail=str(note),
        )
        for note in notes
    )
    return edges, None

