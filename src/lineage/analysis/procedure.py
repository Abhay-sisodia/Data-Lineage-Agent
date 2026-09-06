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
from lineage.analysis.interproc import build_summaries
from lineage.analysis.reaching import reaching_definitions, uninitialised_uses
from lineage.analysis.scratch import find_fusion_hazards
from lineage.config import AnalysisConfig
from lineage.ir.model import IREdge
from lineage.parsing.plsql import parse_program
from lineage.resolution.dictionary import Dictionary

__all__ = ["AnalysisResult", "Refusal", "analyse_source"]


def analyse_source(
    source: str,
    dictionary: Dictionary,
    config: AnalysisConfig | None = None,
) -> AnalysisResult:
    """Analyse one PL/SQL source unit for band-0 and band-1 lineage."""
    settings = config or AnalysisConfig()

    program = parse_program(source)

    # Callees are summarised before the call sites are analysed, so a scalar UDF inside a
    # SELECT contributes the columns its return value depends on rather than the columns
    # in its arguments.
    summaries = build_summaries(program, dictionary, settings.budgets.interprocedural_depth_cap)

    result = band0.analyse_source(source, dictionary, settings, summaries=summaries)

    scopes = collect_scopes(program)
    graphs = build_all(program)

    for summary in summaries.values():
        boundary = summary.boundary()
        if boundary and boundary not in result.boundaries:
            result.boundaries.append(boundary)

    for unit, cfg in graphs.items():
        scope = scopes.get(unit)
        if scope is None:
            continue
        dataflow = analyse_unit(cfg, scope, dictionary)
        result.edges.extend(dataflow.edges)
        for item in dataflow.unresolved:
            entry = f"{unit}: {item}"
            if entry not in result.boundaries:
                result.boundaries.append(entry)

        for item in _reaching_findings(cfg, scope, settings):
            entry = f"{unit}: {item}"
            if entry not in result.boundaries:
                result.boundaries.append(entry)

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

    result.edges = sorted(
        merged, key=lambda e: (e.band, e.flow.value, str(e.source), str(e.target))
    )

    # Declared, not silently resolved. Nothing composes paths yet, so nothing is fused -
    # but the hazard is recorded now so the constraint exists before the code that would
    # violate it.
    for hazard in find_fusion_hazards(result.edges, dictionary):
        entry = f"fusion hazard: {hazard.describe()}"
        if entry not in result.boundaries:
            result.boundaries.append(entry)

    return result


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
