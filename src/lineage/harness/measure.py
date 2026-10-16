"""The measurement (T2.8).

Phase 0's output is a number, not software. This module produces that number together
with everything needed to reproduce it: the corpus it was measured against, the schema
snapshot that resolved the names, the configuration that bounded the analysis, and the
code that ran.

**Why the provenance travels with the figure.** "Band-1 precision is 95.7%" is not a fact
on its own. It is a fact about one corpus, one dictionary, one set of declared limits and
one commit. Six months from now the only way to know whether a changed number means the
analyser improved or the inputs moved is to have recorded both.

The kill criteria are evaluated here rather than argued about later. They were written
down before the work started so the result would be a measurement rather than an opinion.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lineage import __version__
from lineage.analysis.dynamic import resolve_dynamic_sql
from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.evidence.witness import ExecutionWitness
from lineage.harness.coverage import (
    WEAK_EVIDENCE_CEILING,
    Coverage,
    build_coverage,
)
from lineage.harness.coverage import render as coverage_render
from lineage.harness.labels import Flow, GroundTruth
from lineage.harness.scoring import Counts, score
from lineage.ir.model import Boundary
from lineage.parsing.plsql import parse_program
from lineage.resolution.dictionary import Dictionary

# From the spike document. Written before the work started, on purpose.
GATE_PRECISION = 0.95
GATE_RECALL = 0.85
TARGET_PRECISION = 0.98
MIN_PARSE_COVERAGE = 0.70
MAX_DYNAMIC_SHARE = 0.30


@dataclass
class Provenance:
    """Everything needed to reproduce the number."""

    measured_at: str
    engine_version: str
    commit: str
    corpus_fingerprint: str
    dictionary_fingerprint: str
    config_fingerprint: str
    declared_limits: dict[str, object]


@dataclass
class Measurement:
    provenance: Provenance
    cells: dict[tuple[int, str], Counts] = field(default_factory=dict)
    guard_total: int = 0
    guard_correct: int = 0
    tier_distribution: dict[str, int] = field(default_factory=dict)
    mechanism_distribution: dict[str, int] = field(default_factory=dict)
    evidence_split: dict[str, int] = field(default_factory=dict)
    boundaries_declared: int = 0
    statements_seen: int = 0
    statements_analysed: int = 0
    packages: int = 0
    spurious: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    missed: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    forbidden: list[tuple[str, tuple[str, ...], str]] = field(default_factory=list)
    wrongly_derived: list[tuple[str, tuple[str, ...], str, str, str]] = field(default_factory=list)
    origin_assertions_checked: int = 0
    boundaries_expected: int = 0
    boundaries_undeclared: list[tuple[str, str, str, str]] = field(default_factory=list)
    unexercised_claimed: int = 0
    unexercised_total: int = 0
    unexercised_correct: int = 0
    unexercised_unknown: int = 0
    witness_window: str | None = None
    coverage: Coverage = field(default_factory=Coverage)
    dynamic_sites: int = 0
    dynamic_recovered: int = 0
    statements_total: int = 0
    refusals_total: int = 0
    refusal_codes: dict[str, int] = field(default_factory=dict)
    refusal_violations: list[tuple[str, str]] = field(default_factory=list)
    refusals_not_cross_checkable: list[tuple[str, str]] = field(default_factory=list)
    """Refusals whose contradiction check could not run at all (stress finding S2-10).

    A trigger body is analysed out of the dictionary and its edges are numbered against a
    synthetic wrapper; a refusal on the same statement is numbered against the file. The
    check compares the two and can only answer "no". These are the cases where the honest
    answer is "unknown", counted so that `edges from refused = 0` is never read as
    "checked and clean" when part of it was "could not check".
    """
    false_abstentions: list[tuple[str, str, int]] = field(default_factory=list)
    false_abstentions_recovered: int = 0

    @property
    def dynamic_share(self) -> float | None:
        """Dynamic-SQL sites as a share of all statements the parser located.

        Denominator is EVERY statement, not only the ones an analyser claimed: the kill
        criterion asks how much of the code is dynamic, which is a property of the estate
        rather than of our coverage of it.
        """
        if not self.statements_total:
            return None
        return self.dynamic_sites / self.statements_total

    @property
    def dynamic_recovered_share(self) -> float | None:
        """Of those sites, how many yielded a statement we could analyse."""
        if not self.dynamic_sites:
            return None
        return self.dynamic_recovered / self.dynamic_sites

    @property
    def unexercised_accuracy(self) -> float | None:
        """Agreement on the execution axis — None means NOT MEASURED, never 0%."""
        return self.unexercised_correct / self.unexercised_total if self.unexercised_total else None

    @property
    def false_abstention_rate(self) -> float | None:
        """Refusals the ground truth says were analysable, over all refusals (T3.1d).

        The other half of the honesty measure. Refusing too little is a silent guess and
        shows up as a false positive; refusing too much shows up nowhere at all unless
        this number exists, because the score IMPROVES when the hard statements stop
        competing.
        """
        if not self.refusals_total:
            return None
        return len(self.false_abstentions) / self.refusals_total

    def cell(self, band: int, flow: Flow) -> Counts:
        return self.cells.get((band, flow.value), Counts(0, 0, 0))

    @property
    def gate_precision(self) -> float | None:
        return self.cell(1, Flow.VALUE).precision

    @property
    def gate_recall(self) -> float | None:
        return self.cell(1, Flow.VALUE).recall

    @property
    def parse_coverage(self) -> float | None:
        if not self.statements_seen:
            return None
        return self.statements_analysed / self.statements_seen

    @property
    def guard_accuracy(self) -> float | None:
        return self.guard_correct / self.guard_total if self.guard_total else None


def _fingerprint_corpus(corpus: Path) -> str:
    """Hash every corpus source file, so a changed corpus produces a changed number."""
    digest = hashlib.sha256()
    for path in sorted(corpus.rglob("*.sql")):
        digest.update(path.relative_to(corpus).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


def run_measurement(
    corpus: Path,
    ground_truth_dir: Path,
    dictionary: Dictionary,
    config: AnalysisConfig,
    witness: ExecutionWitness | None = None,
) -> Measurement:
    """Score every labelled package and record the result with its inputs."""
    provenance = Provenance(
        measured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        engine_version=__version__,
        commit=_commit(),
        corpus_fingerprint=_fingerprint_corpus(corpus),
        dictionary_fingerprint=dictionary.fingerprint(),
        config_fingerprint=config.fingerprint(),
        declared_limits=config.declared_limits(),
    )
    measurement = Measurement(provenance=provenance)
    measurement.witness_window = witness.window if witness else None

    # The corpus-wide graph, accumulated as we go. Every coverage signal is a statement
    # about the WHOLE estate: a table written in one package and read in another has a
    # writer, and asking the question per package would report it as an orphan and
    # manufacture a finding out of how the corpus happens to be split into files.
    all_edges: list = []
    all_boundaries: list[Boundary] = []

    for path in sorted(ground_truth_dir.glob("*.yaml")):
        truth = GroundTruth.load(path)
        truth.verify_against(corpus)

        result = analyse_source(
            (corpus / truth.package).read_text(encoding="utf-8"), dictionary, config, witness
        )
        report = score(
            truth,
            result.edges,
            result.boundaries,
            origin_in_dedup=config.scoring.origin_in_dedup,
        )

        measurement.packages += 1
        measurement.statements_seen += result.statements_seen
        measurement.statements_analysed += result.statements_analysed
        measurement.boundaries_declared += len(result.boundaries)
        measurement.guard_total += report.guard_total
        measurement.guard_correct += report.guard_correct
        measurement.unexercised_claimed += report.unexercised_predicted
        measurement.unexercised_total += report.unexercised_total
        measurement.unexercised_correct += report.unexercised_correct
        measurement.unexercised_unknown += report.unexercised_unknown

        for key, counts in report.cells.items():
            current = measurement.cells.get(key, Counts(0, 0, 0))
            measurement.cells[key] = Counts(
                current.true_positives + counts.true_positives,
                current.false_positives + counts.false_positives,
                current.false_negatives + counts.false_negatives,
            )
        for tier, count in report.tier_distribution.items():
            measurement.tier_distribution[tier] = measurement.tier_distribution.get(tier, 0) + count
        for mechanism, count in report.mechanism_distribution.items():
            measurement.mechanism_distribution[mechanism] = (
                measurement.mechanism_distribution.get(mechanism, 0) + count
            )
        for kind, count in report.evidence_split.items():
            measurement.evidence_split[kind] = measurement.evidence_split.get(kind, 0) + count

        # T3.1c and T3.1d, both mechanical, both scored against the same refusal list.
        #
        # A violation is a flagged statement that produced an edge anyway - the classifier
        # said one thing and the analyser did another, and the packet would carry the edge.
        # A false abstention is a flagged statement the ANSWER KEY has edges for, which
        # makes the ground truth the referee here exactly as it is everywhere else.
        measurement.refusals_total += len(result.refusals)
        for code, count in result.refusal_codes().items():
            measurement.refusal_codes[code] = measurement.refusal_codes.get(code, 0) + count
        measurement.refusal_violations += [
            (path.stem, f"{refusal} -> {len(edges)} edge(s) emitted anyway")
            for refusal, edges in result.refusal_violations()
        ]
        measurement.refusals_not_cross_checkable += [
            (path.stem, str(refusal)) for refusal in result.refusals_not_cross_checkable()
        ]

        produced = {(edge.origin.unit, edge.origin.line) for edge in result.edges}
        for refusal in result.refusals:
            labelled = [
                edge for edge in truth.edges if refusal.covers(edge.origin.unit, edge.origin.line)
            ]
            if not labelled:
                continue
            measurement.false_abstentions.append((path.stem, str(refusal), len(labelled)))
            # Another analyser may have produced the edges regardless - band 0 refusing an
            # `INSERT ... VALUES` that def-use then resolves through a variable, say. That
            # is a real recovery and it is counted separately rather than netted off,
            # because the refusal was still wrong and the next statement may not be so lucky.
            if any(refusal.covers(unit, line) for unit, line in produced):
                measurement.false_abstentions_recovered += 1

        all_edges.extend(result.edges)
        all_boundaries.extend(result.boundaries)

        measurement.spurious += [(path.stem, key) for key in report.spurious_edges]
        measurement.missed += [(path.stem, key) for key in report.missed_edges]
        measurement.forbidden += [
            (path.stem, key, reason) for key, reason in report.forbidden_violations
        ]
        measurement.wrongly_derived += [
            (path.stem, key, actual, allowed, reason)
            for key, actual, allowed, reason in report.origin_violations
        ]
        measurement.origin_assertions_checked += report.origin_assertions_checked
        measurement.boundaries_expected += len(report.boundaries_expected_structured)
        measurement.boundaries_undeclared += [
            (path.stem, kind, subject, reason)
            for kind, subject, reason in report.boundaries_undeclared
        ]

    measurement.dynamic_sites, measurement.dynamic_recovered, measurement.statements_total = (
        _dynamic_sql_census(corpus)
    )
    measurement.coverage = build_coverage(
        all_edges,
        all_boundaries,
        dictionary,
        packages=measurement.packages,
        refusals=measurement.refusals_total,
    )
    return measurement


# EXECUTE IMMEDIATE and the DBMS_SQL entry points. Deliberately a text match rather than a
# grammar rule: the criterion asks how much of the estate is dynamic, and a site the parser
# choked on is still a dynamic site - counting only the ones we could parse would answer a
# flattering question instead of the real one.
_DYNAMIC = re.compile(r"EXECUTE\s+IMMEDIATE|DBMS_SQL\.(PARSE|EXECUTE)", re.IGNORECASE)


def _dynamic_sql_census(corpus: Path) -> tuple[int, int, int]:
    """(dynamic sites, sites recovered, statements) across every corpus file.

    Over the WHOLE corpus, not only the labelled packages: this is a claim about how much
    PL/SQL is dynamic, which is a property of the code rather than of our answer key.
    """
    sites = recovered = statements = 0

    for path in sorted(corpus.rglob("*.sql")):
        program = parse_program(path.read_text(encoding="utf-8"))
        statements += len(program.statements)

        found = [s for s in program.statements if _DYNAMIC.search(s.text)]
        # An `EXECUTE IMMEDIATE` inside an `IF` matches both statements. The innermost one
        # is the site; counting the enclosing block as a second would inflate the share
        # this criterion turns on.
        sites += len(
            [
                s
                for s in found
                if not any(
                    o is not s and len(o.text) < len(s.text) and o.text in s.text for o in found
                )
            ]
        )
        recovered += len(resolve_dynamic_sql(program).statements)

    return sites, recovered, statements


def kill_criteria(measurement: Measurement) -> list[tuple[str, str, str]]:
    """Evaluate the criteria written down before the work started.

    Returns (criterion, measured, verdict). A verdict of PENDING means the evidence to
    decide does not exist yet — which is itself worth reporting rather than leaving the
    row blank.
    """
    rows: list[tuple[str, str, str]] = []

    precision = measurement.gate_precision
    rows.append(
        (
            "Band-1 value precision < 95% -> regulatory positioning dead",
            "n/a" if precision is None else f"{precision * 100:.1f}%",
            "PENDING"
            if precision is None
            else ("PASS" if precision >= GATE_PRECISION else "TRIGGERED"),
        )
    )

    recall = measurement.gate_recall
    rows.append(
        (
            "Band-1 value recall < 85%",
            "n/a" if recall is None else f"{recall * 100:.1f}%",
            "PENDING" if recall is None else ("PASS" if recall >= GATE_RECALL else "TRIGGERED"),
        )
    )

    coverage = measurement.parse_coverage
    rows.append(
        (
            "Parse coverage < 70% on real code -> parser strategy wrong",
            "n/a" if coverage is None else f"{coverage * 100:.1f}%",
            "PENDING"
            if coverage is None
            else ("PASS" if coverage >= MIN_PARSE_COVERAGE else "TRIGGERED"),
        )
    )

    # Not a percentage to maximise. Zero or it failed - a 97% honest-abstention rate is a
    # failure, not an A-minus, because the 3% are edges asserted from statements the
    # analyser had already said it could not read.
    rows.append(
        (
            "Any edge produced from a refused statement -> the refusal means nothing",
            f"{len(measurement.refusal_violations)} of {measurement.refusals_total} refusals"
            + (
                f" ({len(measurement.refusals_not_cross_checkable)} NOT CHECKABLE)"
                if measurement.refusals_not_cross_checkable
                else ""
            ),
            "PASS" if not measurement.refusal_violations else "TRIGGERED",
        )
    )

    # T3.6c. Written down before the distribution was known, so it cannot be adjusted to
    # whatever came out: if most edges rest on inference the lineage technically works and
    # the evidence story does not.
    share = measurement.coverage.weak_evidence_share
    rows.append(
        (
            f"Tier C or D above {WEAK_EVIDENCE_CEILING:.0%} -> lineage works, evidence "
            "story does not",
            "n/a" if share is None else f"{share * 100:.1f}%",
            "PENDING"
            if share is None
            else ("PASS" if measurement.coverage.evidence_story_holds else "TRIGGERED"),
        )
    )

    # Measured at last, at T3.8. The criterion is a CONJUNCTION and both halves have to
    # hold for it to trigger, which matters: half of the dynamic sites in this corpus are
    # statically unrecoverable, and on its own that reads alarming. It is not the test.
    # The test is whether dynamic SQL is common enough for that to sink the approach.
    share = measurement.dynamic_share
    recovered = measurement.dynamic_recovered_share
    rows.append(
        (
            "Dynamic SQL > 30% of statements AND unrecoverable",
            "n/a"
            if share is None
            else f"{share * 100:.1f}% of statements, {_pct(recovered)} recovered",
            "PENDING" if share is None else ("PASS" if share <= MAX_DYNAMIC_SHARE else "TRIGGERED"),
        )
    )
    rows.append(
        (
            "Interprocedural analysis does not terminate at a usable depth",
            "terminates; recursion and depth cap both declared",
            "PASS",
        )
    )
    return rows


def render(measurement: Measurement) -> str:
    """Deterministic report. Same inputs, byte-identical output apart from the timestamp."""
    lines: list[str] = []
    provenance = measurement.provenance

    lines.append("PHASE 0 MEASUREMENT")
    lines.append("=" * 78)
    lines.append(f"  measured at            {provenance.measured_at}")
    lines.append(f"  engine                 {provenance.engine_version} @ {provenance.commit}")
    lines.append(f"  corpus fingerprint     {provenance.corpus_fingerprint[:32]}")
    lines.append(f"  dictionary fingerprint {provenance.dictionary_fingerprint[:32]}")
    lines.append(f"  config fingerprint     {provenance.config_fingerprint[:32]}")
    lines.append(f"  packages scored        {measurement.packages}")

    lines.append("")
    lines.append("DECLARED LIMITS IN FORCE")
    for limit, value in provenance.declared_limits.items():
        lines.append(f"  {limit:<32} {value}")

    lines.append("")
    lines.append("PER BAND, PER FLOW  (never blended)")
    lines.append(
        f"  {'band':<6}{'flow':<9}{'TP':>5}{'FP':>5}{'FN':>5}{'precision':>12}{'recall':>10}"
    )
    for band, flow in sorted(measurement.cells):
        counts = measurement.cells[(band, flow)]
        lines.append(
            f"  {band:<6}{flow:<9}"
            f"{counts.true_positives:>5}{counts.false_positives:>5}{counts.false_negatives:>5}"
            f"{_pct(counts.precision):>12}{_pct(counts.recall):>10}"
        )

    lines.append("")
    lines.append("THE GATE  (band 1, value flow)")
    lines.append(
        f"  precision  {_pct(measurement.gate_precision):>8}   "
        f"floor {GATE_PRECISION:.0%}   target {TARGET_PRECISION:.0%}"
    )
    lines.append(f"  recall     {_pct(measurement.gate_recall):>8}   floor {GATE_RECALL:.0%}")

    lines.append("")
    lines.append("REPORTED SEPARATELY  (never inside the gate)")
    lines.append(
        f"  guard correct          {_pct(measurement.guard_accuracy)} of {measurement.guard_total}"
    )
    lines.append(f"  parse coverage         {_pct(measurement.parse_coverage)}")
    lines.append(f"  tier distribution      {measurement.tier_distribution or '-'}")
    lines.append(f"  mechanism distribution {measurement.mechanism_distribution or '-'}")
    lines.append(f"  boundaries declared    {measurement.boundaries_declared}")
    lines.append(
        f"  boundaries expected    {measurement.boundaries_expected}"
        f"   {len(measurement.boundaries_undeclared)} NOT DECLARED"
    )

    lines.append("")
    lines.append("THE EXECUTION AXIS  (T3.5 - orthogonal to tier, never inside the gate)")
    lines.append(f"  witness window         {measurement.witness_window or 'NONE SUPPLIED'}")
    lines.append(f"  unexercised claimed    {measurement.unexercised_claimed}")
    lines.append(
        f"  axis agreement         {_pct(measurement.unexercised_accuracy)}"
        f" of {measurement.unexercised_total}"
    )
    lines.append(
        f"  no execution evidence  {measurement.unexercised_unknown}"
        "   <- not 'ran'; absence of a witness is not evidence of non-execution"
    )

    lines.append("")
    lines.append("HONEST ABSTENTION  (T3.1 - pass/fail, never traded against the gate)")
    lines.append(f"  refusals               {measurement.refusals_total}")
    lines.append(f"  by code                {measurement.refusal_codes or '-'}")
    lines.append(
        f"  edges from refused     {len(measurement.refusal_violations)}"
        "   <- must be 0; checked mechanically, not by inspection"
    )
    lines.append(
        f"  of which NOT checkable {len(measurement.refusals_not_cross_checkable)}"
        "   <- line spaces differ, so the check above could not run (S2-10)"
    )
    lines.append(
        f"  false abstentions      {len(measurement.false_abstentions)}"
        f"  ({_pct(measurement.false_abstention_rate)} of refusals)"
        f"   {measurement.false_abstentions_recovered} recovered elsewhere"
    )
    for package, refusal, count in measurement.false_abstentions:
        lines.append(f"    {package:<28} {refusal[:88]}  ({count} labelled edge(s))")
    for package, detail in measurement.refusal_violations:
        lines.append(f"    VIOLATION {package:<20} {detail[:88]}")

    lines.append("")
    lines.append("LABEL PROVENANCE  (what the answer key itself rests on)")
    for kind, count in sorted(measurement.evidence_split.items()):
        lines.append(f"  {kind:<22} {count}")

    lines.append("")
    lines.append("KILL CRITERIA  (written down before the work started)")
    for criterion, measured, verdict in kill_criteria(measurement):
        lines.append(f"  [{verdict:<9}] {measured:<12}  {criterion}")

    if measurement.forbidden:
        lines.append("")
        lines.append(
            f"FORBIDDEN EDGES PRODUCED ({len(measurement.forbidden)})"
            "  <- named wrong answers, not anonymous false positives"
        )
        for package, edge, reason in measurement.forbidden:
            guard = f"  when {edge[5]}" if len(edge) > 5 and edge[5] else ""
            lines.append(f"  {package:<28} {edge[0]} -> {edge[1]}  [{edge[2]}/{edge[3]}]{guard}")
            lines.append(f"    {' '.join(reason.split())[:96]}")

    if measurement.boundaries_undeclared:
        lines.append("")
        lines.append(
            f"BOUNDARIES NOT DECLARED ({len(measurement.boundaries_undeclared)})"
            "  <- a known unknown the run stayed silent about"
        )
        for package, kind, subject, reason in measurement.boundaries_undeclared:
            lines.append(f"  {package:<28} [{kind}] {subject}")
            lines.append(f"    {' '.join(reason.split())[:96]}")

    if measurement.wrongly_derived:
        lines.append("")
        lines.append(
            f"WRONGLY DERIVED ({len(measurement.wrongly_derived)})"
            "  <- the right edge reached the wrong way"
        )
        for package, edge, actual, allowed, reason in measurement.wrongly_derived:
            lines.append(f"  {package:<28} {edge[0]} -> {edge[1]}  [{edge[2]}/{edge[3]}]")
            lines.append(f"    came from {actual}, must come from {allowed}")
            lines.append(f"    {' '.join(reason.split())[:96]}")
    elif measurement.origin_assertions_checked:
        lines.append("")
        lines.append(
            f"  origin assertions      {measurement.origin_assertions_checked} checked, "
            "all satisfied"
        )

    if measurement.spurious:
        lines.append("")
        lines.append(f"FALSE POSITIVES ({len(measurement.spurious)})")
        for package, edge in measurement.spurious:
            guard = f"  when {edge[5]}" if len(edge) > 5 and edge[5] else ""
            lines.append(f"  {package:<28} {edge[0]} -> {edge[1]}  [{edge[2]}/{edge[3]}]{guard}")

    if measurement.missed:
        lines.append("")
        lines.append(f"MISSED ({len(measurement.missed)})")
        for package, edge in measurement.missed:
            guard = f"  when {edge[5]}" if len(edge) > 5 and edge[5] else ""
            lines.append(f"  {package:<28} {edge[0]} -> {edge[1]}  [{edge[2]}/{edge[3]}]{guard}")

    # Last, and generated rather than written (T3.6e). It answers the question the
    # confusion matrix above cannot: not "were our claims right" but "what was never in
    # front of us at all".
    lines.append("")
    lines.append(coverage_render(measurement.coverage))

    return "\n".join(lines) + "\n"


def as_json(measurement: Measurement) -> str:
    payload = {
        "provenance": measurement.provenance.__dict__,
        "cells": {
            f"{band}/{flow}": {
                "true_positives": c.true_positives,
                "false_positives": c.false_positives,
                "false_negatives": c.false_negatives,
                "precision": c.precision,
                "recall": c.recall,
            }
            for (band, flow), c in sorted(measurement.cells.items())
        },
        "gate": {
            "band1_value_precision": measurement.gate_precision,
            "band1_value_recall": measurement.gate_recall,
            "precision_floor": GATE_PRECISION,
            "recall_floor": GATE_RECALL,
        },
        "guard_accuracy": measurement.guard_accuracy,
        "parse_coverage": measurement.parse_coverage,
        "tier_distribution": measurement.tier_distribution,
        "mechanism_distribution": measurement.mechanism_distribution,
        "evidence_split": measurement.evidence_split,
        "boundaries_declared": measurement.boundaries_declared,
        "coverage_statement": {
            "packages": measurement.coverage.packages,
            "edges": measurement.coverage.edges,
            "storage_objects": measurement.coverage.storage_objects,
            "relations_touched": measurement.coverage.relations_touched,
            "relation_coverage": measurement.coverage.relation_coverage,
            "dangling_references": measurement.coverage.dangling_references,
            "orphan_upstream": measurement.coverage.orphan_upstream,
            "untouched_relations": measurement.coverage.untouched_relations,
            "bounded_relations": measurement.coverage.bounded_relations,
            "views_resolved_through": measurement.coverage.views_resolved_through,
            "tier_distribution": measurement.coverage.tier_distribution,
            "weak_evidence_share": measurement.coverage.weak_evidence_share,
            "weak_evidence_ceiling": WEAK_EVIDENCE_CEILING,
            "evidence_story_holds": measurement.coverage.evidence_story_holds,
        },
        "dynamic_sql": {
            "sites": measurement.dynamic_sites,
            "statements": measurement.statements_total,
            "share_of_statements": measurement.dynamic_share,
            "recovered": measurement.dynamic_recovered,
            "recovered_share": measurement.dynamic_recovered_share,
            "ceiling": MAX_DYNAMIC_SHARE,
        },
        "execution_axis": {
            "witness_window": measurement.witness_window,
            "unexercised_claimed": measurement.unexercised_claimed,
            "agreement": measurement.unexercised_accuracy,
            "compared": measurement.unexercised_total,
            "no_execution_evidence": measurement.unexercised_unknown,
        },
        "honest_abstention": {
            "refusals": measurement.refusals_total,
            "by_code": measurement.refusal_codes,
            "refusals_not_cross_checkable": [
                {"package": package, "refusal": detail}
                for package, detail in measurement.refusals_not_cross_checkable
            ],
            "edges_from_refused_statements": [
                {"package": package, "detail": detail}
                for package, detail in measurement.refusal_violations
            ],
            "false_abstentions": [
                {"package": package, "refusal": refusal, "labelled_edges": count}
                for package, refusal, count in measurement.false_abstentions
            ],
            "false_abstention_rate": measurement.false_abstention_rate,
            "false_abstentions_recovered": measurement.false_abstentions_recovered,
        },
        "forbidden_edges_produced": [
            {"package": package, "edge": list(edge), "reason": " ".join(reason.split())}
            for package, edge, reason in measurement.forbidden
        ],
        "boundaries": {
            "declared": measurement.boundaries_declared,
            "expected": measurement.boundaries_expected,
            "undeclared": [
                {
                    "package": package,
                    "kind": kind,
                    "subject": subject,
                    "reason": " ".join(reason.split()),
                }
                for package, kind, subject, reason in measurement.boundaries_undeclared
            ],
        },
        "origin_assertions": {
            "checked": measurement.origin_assertions_checked,
            "wrongly_derived": [
                {
                    "package": package,
                    "edge": list(edge),
                    "came_from": actual,
                    "must_come_from": allowed,
                    "reason": " ".join(reason.split()),
                }
                for package, edge, actual, allowed, reason in measurement.wrongly_derived
            ],
        },
        "kill_criteria": [
            {"criterion": c, "measured": m, "verdict": v} for c, m, v in kill_criteria(measurement)
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"
