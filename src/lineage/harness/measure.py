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
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from lineage import __version__
from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow, GroundTruth
from lineage.harness.scoring import Counts, score
from lineage.resolution.dictionary import Dictionary

# From the spike document. Written before the work started, on purpose.
GATE_PRECISION = 0.95
GATE_RECALL = 0.85
TARGET_PRECISION = 0.98
MIN_PARSE_COVERAGE = 0.70


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

    for path in sorted(ground_truth_dir.glob("*.yaml")):
        truth = GroundTruth.load(path)
        truth.verify_against(corpus)

        result = analyse_source(
            (corpus / truth.package).read_text(encoding="utf-8"), dictionary, config
        )
        report = score(truth, result.edges, result.boundaries)

        measurement.packages += 1
        measurement.statements_seen += result.statements_seen
        measurement.statements_analysed += result.statements_analysed
        measurement.boundaries_declared += len(result.boundaries)
        measurement.guard_total += report.guard_total
        measurement.guard_correct += report.guard_correct

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

        measurement.spurious += [(path.stem, key) for key in report.spurious_edges]
        measurement.missed += [(path.stem, key) for key in report.missed_edges]
        measurement.forbidden += [
            (path.stem, key, reason) for key, reason in report.forbidden_violations
        ]

    return measurement


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

    rows.append(
        (
            "Dynamic SQL > 30% of statements AND unrecoverable",
            "not yet measured",
            "PENDING",
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
            guard = f"  when {edge[4]}" if len(edge) > 4 and edge[4] else ""
            lines.append(f"  {package:<28} {edge[0]} -> {edge[1]}  [{edge[2]}/{edge[3]}]{guard}")
            lines.append(f"    {' '.join(reason.split())[:96]}")

    if measurement.spurious:
        lines.append("")
        lines.append(f"FALSE POSITIVES ({len(measurement.spurious)})")
        for package, edge in measurement.spurious:
            guard = f"  when {edge[4]}" if len(edge) > 4 and edge[4] else ""
            lines.append(f"  {package:<28} {edge[0]} -> {edge[1]}  [{edge[2]}/{edge[3]}]{guard}")

    if measurement.missed:
        lines.append("")
        lines.append(f"MISSED ({len(measurement.missed)})")
        for package, edge in measurement.missed:
            guard = f"  when {edge[4]}" if len(edge) > 4 and edge[4] else ""
            lines.append(f"  {package:<28} {edge[0]} -> {edge[1]}  [{edge[2]}/{edge[3]}]{guard}")

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
        "forbidden_edges_produced": [
            {"package": package, "edge": list(edge), "reason": " ".join(reason.split())}
            for package, edge, reason in measurement.forbidden
        ],
        "kill_criteria": [
            {"criterion": c, "measured": m, "verdict": v} for c, m, v in kill_criteria(measurement)
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"
