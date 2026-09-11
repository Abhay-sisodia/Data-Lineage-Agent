"""The band-1 analyser: set-based lineage plus dataflow (T2.3).

Combines two analyses that see different things and must not be confused with each other:

* **Set-based** (`band0`) reads one statement at a time. It resolves column-to-column
  lineage through projections, views and alias chains, and it cannot see a variable.
* **Def-use** (`defuse`) reads the program. It follows values through variables, across
  statements, and across procedure calls in a package body.

Neither subsumes the other. A statement's projections are band-0 work even inside a
procedure; a variable governing which rows that statement loads is band-1 work. Keeping
them separate is what lets the score say which one broke.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from lineage.analysis import band0
from lineage.analysis.band0 import AnalysisResult, Refusal
from lineage.analysis.cfg import Cfg, build_all
from lineage.analysis.cfg import NodeKind as CfgNodeKind
from lineage.analysis.defuse import (
    UnitScope,
    analyse_unit,
    collect_scopes,
    definitions_and_uses,
)
from lineage.analysis.dynamic import Resolution, resolve_dynamic_sql
from lineage.analysis.interproc import build_summaries
from lineage.analysis.reaching import reaching_definitions, uninitialised_uses
from lineage.analysis.refusal import classify_program, enclosing_unit_at
from lineage.analysis.scratch import find_fusion_hazards
from lineage.analysis.triggers import inherited_edges
from lineage.config import AnalysisConfig
from lineage.evidence.witness import ExecutionWitness
from lineage.ir.model import Boundary, BoundaryKind, IREdge, Node, NodeKind
from lineage.parsing.generated.PlSqlParser import PlSqlParser
from lineage.parsing.plsql import Program, iter_contexts, parse_program, source_slice
from lineage.resolution.dictionary import Dictionary
from lineage.resolution.views import resolve_views

__all__ = ["AnalysisResult", "Refusal", "analyse_source"]


def analyse_source(
    source: str,
    dictionary: Dictionary,
    config: AnalysisConfig | None = None,
    witness: ExecutionWitness | None = None,
) -> AnalysisResult:
    """Analyse one PL/SQL source unit for band-0 and band-1 lineage.

    ``witness`` supplies execution evidence (T3.5). Omit it and every edge carries
    ``unexercised=None`` - nothing was observed, so nothing is claimed either way.
    """
    settings = config or AnalysisConfig()

    program = parse_program(source)

    # Callees are summarised before the call sites are analysed, so a scalar UDF inside a
    # SELECT contributes the columns its return value depends on rather than the columns
    # in its arguments.
    summaries = build_summaries(program, dictionary, settings.budgets.interprocedural_depth_cap)

    result = band0.analyse_source(source, dictionary, settings, summaries=summaries)

    # The unanalysable-construct classifier (T3.1), run over the whole program rather than
    # over one module's statement list. A `MODEL` clause is refused whether it appears in a
    # band-0 INSERT or inside a cursor a band-1 loop drives, and a refusal that depended on
    # which analyser happened to look first would not be a property of the statement.
    #
    # Only flagged statements are counted, and each one costs exactly one statement of
    # parse coverage. Refusing is never free: a classifier that abstained from everything
    # would report 0% coverage, not 100% precision.
    already_refused = {(refusal.unit, refusal.line) for refusal in result.refusals}
    for refusal in classify_program(program):
        if (refusal.unit, refusal.line) not in already_refused:
            result.statements_seen += 1
            result.refusals.append(refusal)
            already_refused.add((refusal.unit, refusal.line))
    for refusal in result.refusals:
        refused = Boundary(
            kind=BoundaryKind.REFUSAL,
            subject=f"{refusal.unit}:{refusal.line}",
            detail=f"{refusal.unit}: line {refusal.line} refused "
            f"[{refusal.code.value}] - {refusal.reason}",
            unit=refusal.unit,
            line=refusal.line,
        )
        if refused not in result.boundaries:
            result.boundaries.append(refused)

    scopes = collect_scopes(program)
    graphs = build_all(program)

    # Resolved once for the whole source, not per unit: a carrier variable is a carrier
    # everywhere, and the constant environment is built in source order.
    dynamic = resolve_dynamic_sql(program)

    for summary in summaries.values():
        boundary = summary.boundary()
        if boundary is None:
            continue
        capped = Boundary(
            kind=BoundaryKind.DEPTH_CAP,
            subject=summary.unit,
            detail=boundary,
            unit=summary.unit,
        )
        if capped not in result.boundaries:
            result.boundaries.append(capped)

    for unit, cfg in graphs.items():
        scope = scopes.get(unit)
        if scope is None:
            continue
        dataflow = analyse_unit(cfg, scope, dictionary, dynamic)
        result.edges.extend(dataflow.edges)
        for item in dataflow.unresolved:
            unresolved = _in_unit(unit, item, BoundaryKind.UNRESOLVED_IDENTIFIER)
            if unresolved not in result.boundaries:
                result.boundaries.append(unresolved)

        for text in _reaching_findings(cfg, scope, settings):
            finding = _in_unit(unit, text, BoundaryKind.CROSS_UNIT_STATE)
            if finding not in result.boundaries:
                result.boundaries.append(finding)

    # Triggers fire invisibly. Nothing in the source mentions them, so their edges are
    # inherited from the DICTIONARY by whichever relations this source writes - the
    # register's rule, and the reason a trigger belongs to its table rather than to any
    # caller. Added before the merge below so they are deduplicated and ordered like any
    # other edge.
    trigger_edges, trigger_boundaries = inherited_edges(
        _written_relations(program, dynamic), dictionary
    )
    result.edges.extend(trigger_edges)

    # S2-10: a trigger body is analysed out of the DICTIONARY, wrapped in a synthetic
    # procedure, so its edges carry lines relative to that wrapper while a refusal on the
    # same statement carries the line in the FILE. Recording the units here is what lets
    # `refusals_not_cross_checkable` say "unknown" instead of letting `covers()` answer
    # "no" for a reason that has nothing to do with the truth.
    result.incomparable_units.update(
        qualified.rpartition(".")[2].upper() for qualified in dictionary.triggers
    )
    for note in trigger_boundaries:
        trigger_note = Boundary(
            kind=BoundaryKind.SOURCE_UNAVAILABLE,
            subject=str(note).split(":")[0],
            detail=str(note),
        )
        if trigger_note not in result.boundaries:
            result.boundaries.append(trigger_note)

    for suppressed in _suppressed_errors(program):
        if suppressed not in result.boundaries:
            result.boundaries.append(suppressed)

    for contextual in _context_dependent_names(program, result):
        if contextual not in result.boundaries:
            result.boundaries.append(contextual)

    # Silent failure s6, closed corpus-wide rather than per analyser (T3.4c). Band 0
    # inlines view text before analysing a projection and the trigger analyser rewrites an
    # INSTEAD OF trigger's :NEW row, but an ordinary `UPDATE v_customer_editable` went
    # through neither and kept the view on both ends of its filter edge - naming an object
    # that stores nothing, while dim_customer appeared to have no writer.
    #
    # Run over every edge from every analyser, because a view is a naming fact and the
    # third copy of a naming rule is the one that disagrees with the other two.
    result.edges, view_notes = resolve_views(result.edges, dictionary)
    for note in view_notes:
        view_note = Boundary(
            kind=BoundaryKind.ROW_CORRESPONDENCE,
            subject=str(note).split(" ")[0],
            detail=str(note),
        )
        if view_note not in result.boundaries:
            result.boundaries.append(view_note)

    # The set-based analyser works one statement at a time and never sees control flow,
    # so its edges arrive unguarded even when the statement sits inside a branch or a
    # loop. An unguarded edge claims the write always happens, which for a conditional
    # write is a wrong answer rather than an incomplete one - so guards are applied here,
    # from the CFG, once both analyses have run.
    result.edges = [_with_guard(edge, graphs) for edge in result.edges]

    # Merge on ledger identity so two facts differing only by guard or origin both
    # survive, while the same fact found by both analyses appears once.
    unique: dict[tuple[str, ...], object] = {}
    merged = []
    for edge in result.edges:
        key = edge.identity()
        if key in unique:
            continue
        unique[key] = edge
        merged.append(edge)

    # `unexercised` is applied last, over the finished edge set (T3.5). It is not an
    # analysis result - it is a fact about the running system laid alongside one - so it
    # must not be able to change which edges exist or what they say. An edge whose unit the
    # witness cannot attribute keeps None, and the harness counts those separately: a
    # statement the log could not tie to a unit is UNCOVERED, not exercised.
    if witness is not None:
        result.edges = [
            edge.model_copy(update={"unexercised": _unexercised(edge, witness)}) for edge in merged
        ]
        result.boundaries.append(
            Boundary(
                kind=BoundaryKind.SOURCE_UNAVAILABLE,
                subject="EXECUTION WITNESS",
                detail=f"execution witness covers {len(witness.units)} unit(s) over "
                f"{witness.window}; edges outside those units carry no execution "
                f"evidence either way",
            )
        )
    else:
        result.edges = merged

    result.edges = sorted(
        result.edges, key=lambda e: (e.band, e.flow.value, str(e.source), str(e.target))
    )

    # Declared, not silently resolved. Nothing composes paths yet, so nothing is fused -
    # but the hazard is recorded now so the constraint exists before the code that would
    # violate it.
    for hazard in find_fusion_hazards(result.edges, dictionary):
        entry = Boundary(
            kind=BoundaryKind.FUSION_HAZARD,
            subject=hazard.relation,
            detail=f"fusion hazard: {hazard.describe()}",
            attaches_to=Node(kind=NodeKind.RELATION, name=hazard.relation),
        )
        if entry not in result.boundaries:
            result.boundaries.append(entry)

    return result


def _in_unit(unit: str, item: Boundary | str, default: BoundaryKind) -> Boundary:
    """Prefix a boundary with its unit without losing how it was classified.

    An f-string over a ``Declared`` returns a plain ``str`` and drops kind and subject
    silently, which would make the axis unscoreable again one interpolation at a time.
    """
    prefix = f"{unit}: "
    if isinstance(item, Boundary):
        # A parse failure knows its line and not its unit, and a bare line number is not an
        # identity - so the unit qualifies the subject here, where it is known.
        qualify = f"{unit}:" if item.kind is BoundaryKind.PARSE_FAILURE else ""
        return item.prefixed(prefix, subject_prefix=qualify).model_copy(
            update={"unit": item.unit or unit}
        )
    return Boundary(kind=default, subject=unit, detail=f"{prefix}{item}", unit=unit)


def _unexercised(edge: IREdge, witness: ExecutionWitness) -> bool | None:
    """Whether this edge's statement was seen running, from the witness's point of view.

    Inverted from `ran()` because the axis is named for the interesting case: an edge that
    is provably in the code and has never fired is a finding, and an edge that fired is
    just an ordinary edge.
    """
    ran = witness.ran(edge.origin.unit, edge.origin.line)
    return None if ran is None else not ran


def _written_relations(program: Program, dynamic: Resolution) -> dict[str, str]:
    """Which relations this source writes, and by what event.

    The event matters: a trigger fires on `INSERT` or on `UPDATE`, and inheriting an
    `AFTER INSERT` trigger from an `UPDATE` statement would be an invented edge - the
    trigger genuinely does not run.

    Recovered dynamic statements are included. `b2_01` inserts into `tmp_recent` through
    `EXECUTE IMMEDIATE`, and the trigger fires just the same; a source that only looked at
    written SQL would miss it, which is two invisible mechanisms compounding.
    """
    written: dict[str, str] = {}
    texts = [statement.text for statement in program.statements]
    texts += [statement.text for statement in dynamic.statements]

    for text in texts:
        try:
            parsed = sqlglot.parse_one(text, dialect="oracle")
        except Exception:
            continue
        if isinstance(parsed, exp.Insert):
            event = "INSERT"
        elif isinstance(parsed, exp.Update):
            event = "UPDATE"
        elif isinstance(parsed, exp.Delete):
            event = "DELETE"
        elif isinstance(parsed, exp.Merge):
            # A MERGE both inserts and updates, and which one a given row takes is not
            # statically decidable. Both events are claimed, because a trigger that might
            # fire is a real edge and dropping it would be a silent miss.
            event = "INSERT UPDATE"
        else:
            continue

        target = parsed.this
        if isinstance(target, exp.Schema):
            target = target.this
        if isinstance(target, exp.Table):
            name = target.name.upper()
            written[name] = event if name not in written else f"{written[name]} {event}"

    return written


def _suppressed_errors(program: Program) -> list[Boundary]:
    """Exception handlers that swallow the failure whole.

    `WHEN OTHERS THEN NULL` is not a style complaint here, it is a coverage fact. The
    handler catches every error and does nothing, so a mapping that was enabled and failed
    leaves the estate looking exactly like one that ran and wrote nothing. **Absence of an
    edge stops being evidence** anywhere downstream of it - which is precisely the claim a
    coverage statement exists to qualify.

    Detected structurally rather than by matching source text, because `WHEN OTHERS THEN
    NULL;` and a handler whose body is a bare NULL across three lines are the same fact.
    """
    handler_ctx = getattr(PlSqlParser, "Exception_handlerContext", None)
    if handler_ctx is None:  # pragma: no cover - grammar always has it
        return []

    found: list[Boundary] = []
    for ctx in iter_contexts(program.tree, handler_ctx):
        body = " ".join(source_slice(ctx).split()).upper()
        if "OTHERS" not in body.split("THEN")[0]:
            continue
        statements = body.split("THEN", 1)[1] if "THEN" in body else ""
        if re.fullmatch(r"\s*NULL\s*;?\s*", statements) is None:
            continue
        line = ctx.start.line
        unit = enclosing_unit_at(program, line)
        found.append(
            Boundary(
                kind=BoundaryKind.SUPPRESSED_ERROR,
                subject=f"{unit}:{line}",
                detail=f"{unit}: WHEN OTHERS THEN NULL at line {line} swallows every "
                f"failure - a write that was attempted and rejected is indistinguishable "
                f"from one that never ran, so a missing edge below here proves nothing",
                unit=unit,
                line=line,
            )
        )
    return found


def _context_dependent_names(program: Program, result: AnalysisResult) -> list[Boundary]:
    """Units whose lineage is only valid for the schema that executed them.

    Silent failure s2, the half that survives a correct analyser. An unqualified name binds
    through the EXECUTING user's schema, so two jobs running identical code against
    different schemas touch different tables - and a single observation shows one of them
    and looks definitive. The resolution is not a defect to fix; it is a condition on the
    answer, and a condition nobody states is a condition nobody knows about.

    One boundary per unit, at the first unqualified reference. Per-reference would be
    accurate and unreadable - the same reason fusion hazards are declared per relation.
    """
    seen: dict[str, int] = {}
    for statement in program.statements:
        if statement.kind not in band0.SUPPORTED:
            continue
        try:
            parsed = sqlglot.parse_one(statement.text, dialect=band0.DIALECT)
        except Exception:
            continue
        for table in parsed.find_all(exp.Table):
            if table.text("db") or not table.name:
                continue
            unit = enclosing_unit_at(program, statement.line)
            seen.setdefault(unit, statement.line)

    return [
        Boundary(
            kind=BoundaryKind.CONTEXT_DEPENDENT_BINDING,
            subject=f"{unit}:{line}",
            detail=f"{unit}: unqualified names from line {line} resolve through whichever "
            f"schema executes this unit, so the lineage below is valid for the schema it "
            f"was bound against and is a different answer under another",
            unit=unit,
            line=line,
        )
        for unit, line in sorted(seen.items())
    ]


def _reaching_findings(cfg: Cfg, scope: UnitScope, config: AnalysisConfig) -> list[str]:
    """Variables read before anything assigns them, and non-convergence if it happens."""
    definitions: dict[int, set[str]] = {}
    uses: dict[int, set[str]] = {}

    for node in cfg.nodes.values():
        if node.kind is CfgNodeKind.LOOP:
            # A FOR loop's index or record is defined by the loop header, not by any
            # statement. Without this it looks like a variable read before assignment.
            for name, row in scope.row_sources.items():
                if row.line == node.line:
                    definitions.setdefault(node.id, set()).add(name)
            for name, declaration in scope.variables.items():
                if declaration.line == node.line and declaration.scope == "local":
                    definitions.setdefault(node.id, set()).add(name)
            continue
        if node.kind is not CfgNodeKind.STATEMENT:
            continue
        defined, used = definitions_and_uses(node, scope)
        if defined:
            definitions[node.id] = defined
        if used:
            uses[node.id] = used

    # `v_total NUMBER := 0` is assigned before the first statement runs. Treating the
    # declaration as a definition at entry stops every later read looking uninitialised.
    initialised = {name for name, declaration in scope.variables.items() if declaration.has_default}
    if initialised:
        definitions.setdefault(cfg.entry, set()).update(initialised)

    cap = config.budgets.loop_fixpoint_iteration_cap
    result = reaching_definitions(cfg, definitions, cap)

    findings: list[str] = []
    if not result.converged:
        # A limit that silently truncates is a hidden defect; declared, it is a
        # specification.
        findings.append(
            f"reaching definitions did not converge within the configured cap of {cap} "
            f"iterations - results below that point are incomplete"
        )

    parameters = {name for name, decl in scope.variables.items() if decl.scope == "parameter"}
    package_state = {name for name, decl in scope.variables.items() if decl.scope == "package"}

    for finding in uninitialised_uses(
        cfg,
        definitions,
        uses,
        result,
        parameters=parameters,
        external=package_state,
        iteration_cap=cap,
    ):
        findings.append(finding.describe())

    return findings


def _with_guard(edge: IREdge, graphs: dict[str, Cfg]) -> IREdge:
    """Attach the governing condition to an edge that does not already carry one."""
    if edge.guard is not None:
        return edge

    cfg = graphs.get(edge.origin.unit)
    if cfg is None:
        return edge

    for node in cfg.statement_nodes():
        if node.line != edge.origin.line:
            continue
        guards = cfg.guards_reaching(node.id)
        if guards:
            return edge.model_copy(update={"guard": " AND ".join(guards)})
        return edge
    return edge
