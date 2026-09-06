"""Triggers: writes that no calling code shows (T3.3).

The register's instruction is one sentence and the whole design follows from it:
*parse separately and attach to the TABLE, not the caller. Any statement touching that
table inherits the trigger's edges.*

**Why attaching to the caller is wrong twice over.** `trg_recent_audit` is declared in
`b2_05_triggers.sql` and fires for the seven procedures in this corpus that insert into
`tmp_recent`, none of which mentions it. Bind the edge to `b2_triggers` and you credit one
procedure with a write it never issued *and* miss the other six. Neither error shows up as
a gap: `dim_customer.lifetime_value` simply appears to have a writer, or no writer, and
both read as findings.

**So triggers come from the dictionary, not from source files.** They are database objects
attached to tables, discovered at ingest, exactly like synonyms and views in T1.4. A source
file that inherits a trigger has no text in it to parse.

**The correlation names are the analysis problem.** In

    UPDATE dim_customer SET lifetime_value = ... WHERE cust_id = :NEW.cust_id

both operands of the predicate look like `dim_customer.cust_id`. One is; the other is a
column of `tmp_recent`, the table being inserted into, which this statement never names.
Resolving `:NEW` against the statement's own FROM clause is a silent failure - a plausible
self-referential edge, and the trigger's real input disappears. `:NEW` and `:OLD` are
therefore registered as scope *correlations*: qualifiers bound to a relation that is in
scope without appearing in any FROM clause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from lineage.analysis.cfg import Cfg, NodeKind, build_all
from lineage.analysis.defuse import (
    DefUseResult,
    analyse_statement,
    collect_scopes,
)
from lineage.ir.model import (
    Flow,
    IREdge,
    Mechanism,
    Node,
    Origin,
    Transform,
)
from lineage.ir.model import NodeKind as IRNodeKind
from lineage.parsing.plsql import parse_program
from lineage.resolution.dictionary import Dictionary, TriggerInfo, UnknownObjectError

__all__ = ["TriggerAnalysis", "analyse_trigger", "inherited_edges"]

DIALECT = "oracle"

# `:NEW.cust_id` / `:OLD.region`, in any case, with or without the colon Oracle strips
# inside a trigger body.
CORRELATION = re.compile(r":?\b(NEW|OLD)\s*\.\s*", re.IGNORECASE)

# The alias the correlation names are rewritten to. Deliberately not a real identifier a
# customer could collide with, and registered in scope.correlations so it resolves to the
# triggering relation rather than to whatever the statement happens to select from.
NEW_ALIAS = "TRIGGERING_ROW"


@dataclass
class TriggerAnalysis:
    """What one trigger contributes, independent of who fires it."""

    trigger: TriggerInfo
    edges: list[IREdge] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)


def _rewrite_correlations(body: str) -> str:
    """`:NEW.col` and `:OLD.col` both become `TRIGGERING_ROW.col`.

    NEW and OLD are collapsed on purpose. They are different ROWS of the same relation,
    and this model's node names carry no row identity - the same limitation recorded in
    `sq_06`'s self-join key before the analyser existed. Distinguishing them here would
    imply a precision the IR cannot express.
    """
    return CORRELATION.sub(f"{NEW_ALIAS}.", body)


def _assignment_edges(
    text: str, trigger: TriggerInfo, table: str, origin: Origin, guard: str | None
) -> list[IREdge]:
    """`:NEW.col := expr` — a column write with no SQL statement anywhere.

    Only BEFORE triggers can do this, and it is the only way a column's value is decided
    without a DML statement naming it. `trg_customer_default` derives
    `dim_customer.is_active` from `dim_customer.region` here and nowhere else in the
    corpus.
    """
    target_text, _, expression = text.partition(":=")
    target = target_text.strip().rstrip(";").upper()
    prefix = f"{NEW_ALIAS}."
    if not target.startswith(prefix):
        return []
    column = target[len(prefix) :]

    expression = expression.strip().rstrip(";")
    try:
        parsed = sqlglot.parse_one(expression, dialect=DIALECT)
    except Exception:
        return []

    transform = (
        Transform.CONDITIONAL
        if list(parsed.find_all(exp.Case, exp.If))
        else Transform.DERIVED
        if not isinstance(parsed, exp.Column)
        else Transform.IDENTITY
    )

    edges: list[IREdge] = []
    for reference in parsed.find_all(exp.Column):
        name = reference.name.upper()
        qualifier = (reference.table or "").upper()
        # A bare or :NEW-qualified name inside a trigger body is a column of the
        # triggering row either way.
        if qualifier not in ("", NEW_ALIAS):
            continue
        edges.append(
            IREdge(
                source=Node(kind=IRNodeKind.COLUMN, name=f"{table}.{name}"),
                target=Node(kind=IRNodeKind.COLUMN, name=f"{table}.{column}"),
                flow=Flow.VALUE,
                transform=transform,
                band=2,
                mechanism=Mechanism.AST,
                guard=guard,
                origin=origin,
            )
        )
    return edges


def _insert_values_edges(text: str, table: str, origin: Origin, guard: str | None) -> list[IREdge]:
    """`INSERT INTO t (a, b) VALUES (:NEW.x, USER)` — positional, and nothing else reads it.

    The set-based analyser handles `INSERT ... SELECT`; a VALUES list has no select list
    to trace and is normally all literals. Inside a trigger it is where the audit row is
    written, so it is the only place a correlation column reaches a second table.

    Binding is POSITIONAL, like a UNION arm and a FETCH INTO. `USER` and `SYSDATE` supply
    values from nowhere and produce no edge, which is the literal rule again.
    """
    try:
        parsed = sqlglot.parse_one(text, dialect=DIALECT)
    except Exception:
        return []
    if not isinstance(parsed, exp.Insert):
        return []

    schema = parsed.this
    if not isinstance(schema, exp.Schema):
        return []
    target_table = schema.this
    if not isinstance(target_table, exp.Table):
        return []
    columns = [c.name.upper() for c in schema.expressions]

    values = parsed.expression
    if not isinstance(values, exp.Values) or not values.expressions:
        return []
    items = list(values.expressions[0].expressions)

    edges: list[IREdge] = []
    for index, column in enumerate(columns):
        if index >= len(items):
            break
        item = items[index]
        for reference in item.find_all(exp.Column):
            qualifier = (reference.table or "").upper()
            if qualifier != NEW_ALIAS:
                continue
            edges.append(
                IREdge(
                    source=Node(kind=IRNodeKind.COLUMN, name=f"{table}.{reference.name.upper()}"),
                    target=Node(
                        kind=IRNodeKind.COLUMN,
                        name=f"{target_table.name.upper()}.{column}",
                    ),
                    flow=Flow.VALUE,
                    transform=(
                        Transform.IDENTITY if isinstance(item, exp.Column) else Transform.DERIVED
                    ),
                    band=2,
                    mechanism=Mechanism.AST,
                    guard=guard,
                    origin=origin,
                )
            )
    return edges


def analyse_trigger(trigger: TriggerInfo, dictionary: Dictionary) -> TriggerAnalysis:
    """Analyse one trigger body on its own terms.

    The body is wrapped in a synthetic procedure so the ordinary machinery applies: the
    same CFG, the same def-use, the same guard extraction. A trigger is a program, and
    inventing a second analysis path for it would be two places to get the same thing
    wrong.
    """
    analysis = TriggerAnalysis(trigger=trigger)
    simple_table = trigger.table.partition(".")[2] or trigger.table

    body = _rewrite_correlations(trigger.body.strip())
    if not body:
        analysis.boundaries.append(f"{trigger.qualified}: no trigger body captured")
        return analysis

    source = f"CREATE OR REPLACE PROCEDURE {trigger.name} IS\n{body}\n"
    program = parse_program(source)
    if program.error_count:
        analysis.boundaries.append(
            f"{trigger.qualified}: trigger body did not parse - "
            f"{program.errors[0] if program.errors else 'unknown error'}"
        )
        return analysis

    scopes = collect_scopes(program)
    graphs = build_all(program)

    for unit, cfg in graphs.items():
        scope = scopes.get(unit)
        if scope is None:
            continue
        # This is the whole point: :NEW resolves to the TRIGGERING relation, which the
        # statements below never name. The unqualified form is used so node names match
        # the rest of the engine - `TMP_RECENT.CUST_ID`, not `LINEAGE.TMP_RECENT.CUST_ID`.
        scope.correlations[NEW_ALIAS] = simple_table

        for node in cfg.nodes.values():
            if node.kind is not NodeKind.STATEMENT or node.ctx is None:
                continue
            origin = Origin(unit=trigger.name.upper(), line=node.line)
            guard = _guard_of(cfg, node.id)

            if node.statement_kind == "assignment_statement":
                analysis.edges.extend(
                    _assignment_edges(node.label, trigger, simple_table, origin, guard)
                )
                continue

            if node.statement_kind == "insert_statement":
                analysis.edges.extend(_insert_values_edges(node.label, simple_table, origin, guard))

            result = analyse_statement(node, cfg, scope, dictionary, trigger.name.upper())
            analysis.edges.extend(_as_band_2(result))
            analysis.boundaries.extend(f"{trigger.qualified}: {item}" for item in result.unresolved)

    # An INSTEAD OF trigger's :NEW row belongs to a VIEW. Left alone, every edge names an
    # object that stores nothing and the regulated base table looks unwritten.
    analysis.edges, unresolved = _resolve_views(analysis.edges, dictionary)
    analysis.boundaries.extend(f"{trigger.qualified}: {item}" for item in unresolved)

    return analysis


def _base_column(relation: str, column: str, dictionary: Dictionary) -> str | None:
    """Follow one column of a view down to the base column it projects from.

    An `INSTEAD OF` trigger's `:NEW` row is a row of the VIEW, so without this every edge
    it produces names an object that stores nothing - and the regulated table it really
    writes appears to have no writer. That is silent failure s6, arriving through the
    trigger rather than through the UPDATE.
    """
    resolved = dictionary.resolve(relation)
    if resolved.object_type != "VIEW" or resolved.qualified is None:
        return None
    text = dictionary.view_text.get(resolved.qualified)
    if not text:
        return None
    try:
        parsed = sqlglot.parse_one(text, dialect=DIALECT)
    except Exception:
        return None
    if not isinstance(parsed, exp.Select):
        return None

    sources = {
        (alias or table.name).upper(): table.name.upper()
        for table in parsed.find_all(exp.Table)
        for alias in [table.alias]
    }

    for projection in parsed.selects:
        if (projection.alias_or_name or "").upper() != column.upper():
            continue
        for reference in projection.find_all(exp.Column):
            qualifier = (reference.table or "").upper()
            base = sources.get(qualifier) or next(iter(sources.values()), None)
            if base is None:
                continue
            return f"{base}.{reference.name.upper()}"
    return None


def _resolve_views(edges: list[IREdge], dictionary: Dictionary) -> tuple[list[IREdge], list[str]]:
    """Rewrite any view column on either end of an edge to its base column."""
    rewritten: list[IREdge] = []
    unresolved: list[str] = []

    for edge in edges:
        update: dict[str, Node] = {}
        for side in ("source", "target"):
            node: Node = getattr(edge, side)
            if node.kind is not IRNodeKind.COLUMN or "." not in node.name:
                continue
            relation, _, column = node.name.rpartition(".")
            if dictionary.resolve(relation).object_type != "VIEW":
                continue
            base = _base_column(relation, column, dictionary)
            if base is None:
                unresolved.append(
                    f"{node.name} is a view column and the view's text could not be "
                    "expanded - the base table it writes is not known"
                )
                continue
            update[side] = Node(kind=IRNodeKind.COLUMN, name=base)
        rewritten.append(edge.model_copy(update=update) if update else edge)

    return rewritten, unresolved


def _guard_of(cfg: Cfg, node_id: int) -> str | None:
    guards = cfg.guards_reaching(node_id)
    return " AND ".join(guards) if guards else None


def _as_band_2(result: DefUseResult) -> list[IREdge]:
    """Every trigger-borne edge is band 2 (ADR-0001 §6).

    The path runs through a trigger, which is the hardest construct on it - the statement
    that caused the write does not mention the written table at all.
    """
    return [edge.model_copy(update={"band": 2}) for edge in result.edges]


def inherited_edges(
    written: dict[str, str],
    dictionary: Dictionary,
    cache: dict[str, TriggerAnalysis] | None = None,
) -> tuple[list[IREdge], list[str]]:
    """Edges inherited by a source that writes these relations.

    ``written`` maps the relation name as it appeared in code to the event that touched
    it (INSERT / UPDATE / DELETE), because a trigger fires on specific events and
    inheriting an AFTER INSERT trigger from an UPDATE would be an invented edge.
    """
    store = cache if cache is not None else {}
    edges: list[IREdge] = []
    boundaries: list[str] = []

    for relation, event in sorted(written.items()):
        try:
            triggers = dictionary.triggers_on(relation)
        except UnknownObjectError:
            continue
        for trigger in triggers:
            if not trigger.fires_on(event):
                continue
            analysis = store.get(trigger.qualified)
            if analysis is None:
                analysis = analyse_trigger(trigger, dictionary)
                store[trigger.qualified] = analysis
            edges.extend(analysis.edges)
            boundaries.extend(analysis.boundaries)

    return edges, boundaries
