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
from lineage.analysis.cfg import build_all
from lineage.analysis.defuse import analyse_unit, collect_scopes
from lineage.config import AnalysisConfig
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

    result = band0.analyse_source(source, dictionary, settings)

    program = parse_program(source)
    scopes = collect_scopes(program)

    for unit, cfg in build_all(program).items():
        scope = scopes.get(unit)
        if scope is None:
            continue
        dataflow = analyse_unit(cfg, scope, dictionary)
        result.edges.extend(dataflow.edges)
        for item in dataflow.unresolved:
            entry = f"{unit}: {item}"
            if entry not in result.boundaries:
                result.boundaries.append(entry)

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
    return result
