"""Scoring: how good is the analyser, per band (T1.3).

Implements ADR-0001. The rules that matter most here are the ones about what is *not*
combined:

* **Never blend bands.** A single number hides that band 0 is perfect and band 1 is
  broken, which is the only thing this phase exists to learn.
* **Never blend flow kinds.** A filter influence is a different claim from a value copy.
* **Guards are excluded from precision.** Reported separately, so the gate figure cannot
  move on string-comparison noise.

There is deliberately no headline number. The gate is *band-1 value-flow precision*, and
any report that lets a reader mistake something else for it is a defective report.

Bucketing rule: a matched edge is counted in its GROUND-TRUTH band; an unmatched
prediction (false positive) is counted in the band the analyser claimed. So an edge the
analyser mis-banded shows up as a miss in the true band and a false positive in the
claimed one — which is the honest reading of that mistake.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from lineage.harness.labels import Flow, GroundTruth, LabelledEdge, Node, Transform

EdgeKey = tuple[str, str, str, str]


class Mechanism(StrEnum):
    """How the analyser derived an edge. Separate from tier on purpose.

    Mechanism is *how it was found*; tier is *how well it was corroborated*. An
    AST-derived edge is still only Tier A if nothing contradicts it.
    """

    AST = "AST"
    DEF_USE = "DEF-USE"
    CFG = "CFG"
    LOG = "LOG"
    INFERRED = "INFERRED"


class Tier(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class PredictedEdge(BaseModel):
    """One edge the analyser claims.

    Mirrors LabelledEdge's identity so the two can be matched. Replaced by the real IR
    edge type in T2.1; kept minimal until the IR is designed against what the corpus
    actually contains rather than against a wishlist.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Node
    target: Node
    flow: Flow = Flow.VALUE
    transform: Transform = Transform.IDENTITY
    band: int = Field(ge=0, le=3)
    mechanism: Mechanism = Mechanism.AST
    tier: Tier = Tier.A
    guard: str | None = None
    unexercised: bool = False

    def key(self) -> EdgeKey:
        return (str(self.source), str(self.target), self.flow.value, self.transform.value)


def normalise_guard(guard: str | None) -> str | None:
    """Reduce a guard to a comparable form.

    `p_region = 'EU'` and `'EU' = p_region` are the same condition and different strings.
    Upper-cases, collapses whitespace, and orders the operands of a simple equality.

    Deliberately modest. Guards are excluded from precision precisely because comparing
    them properly is a research problem in itself, and a half-clever normaliser that
    silently mis-compares would be worse than an honest one that occasionally says
    "different".
    """
    if guard is None:
        return None
    text = re.sub(r"\s+", " ", guard.strip().upper())
    match = re.fullmatch(r"([^=<>!]+)=([^=<>!]+)", text)
    if match:
        left, right = (part.strip() for part in match.groups())
        left, right = sorted([left, right])
        return f"{left} = {right}"
    return text


@dataclass(frozen=True)
class Counts:
    """Confusion counts for one (band, flow) cell."""

    true_positives: int
    false_positives: int
    false_negatives: int

    @property
    def precision(self) -> float | None:
        claimed = self.true_positives + self.false_positives
        return self.true_positives / claimed if claimed else None

    @property
    def recall(self) -> float | None:
        actual = self.true_positives + self.false_negatives
        return self.true_positives / actual if actual else None


@dataclass(frozen=True)
class ScoreReport:
    """The full result. No blended headline figure exists, by design."""

    package: str
    cells: dict[tuple[int, str], Counts]
    guard_total: int
    guard_correct: int
    tier_distribution: dict[str, int]
    mechanism_distribution: dict[str, int]
    evidence_split: dict[str, int]
    unexercised_predicted: int
    boundaries_expected: list[str] = field(default_factory=list)
    boundaries_declared: list[str] = field(default_factory=list)
    missed_edges: list[EdgeKey] = field(default_factory=list)
    spurious_edges: list[EdgeKey] = field(default_factory=list)

    def cell(self, band: int, flow: Flow) -> Counts:
        return self.cells.get((band, flow.value), Counts(0, 0, 0))

    @property
    def gate_precision(self) -> float | None:
        """Band-1 value-flow precision — the number the phase gate is defined against."""
        return self.cell(1, Flow.VALUE).precision

    @property
    def gate_recall(self) -> float | None:
        return self.cell(1, Flow.VALUE).recall

    @property
    def guard_accuracy(self) -> float | None:
        return self.guard_correct / self.guard_total if self.guard_total else None

    @property
    def boundaries_missed(self) -> list[str]:
        """Known unknowns the analyser failed to declare.

        A silently omitted boundary is the specific failure the product exists to
        prevent, so it is reported even though it is not an edge.
        """
        return sorted(set(self.boundaries_expected) - set(self.boundaries_declared))


def score(
    truth: GroundTruth,
    predicted: list[PredictedEdge],
    declared_boundaries: list[str] | None = None,
) -> ScoreReport:
    """Score analyser output against a ground-truth label set."""
    truth_by_key: dict[EdgeKey, LabelledEdge] = {edge.key(): edge for edge in truth.edges}
    predicted_by_key: dict[EdgeKey, PredictedEdge] = {edge.key(): edge for edge in predicted}

    matched_keys = sorted(set(truth_by_key) & set(predicted_by_key))
    missed_keys = sorted(set(truth_by_key) - set(predicted_by_key))
    spurious_keys = sorted(set(predicted_by_key) - set(truth_by_key))

    cells: dict[tuple[int, str], Counts] = {}

    def _bump(band: int, flow: str, tp: int = 0, fp: int = 0, fn: int = 0) -> None:
        current = cells.get((band, flow), Counts(0, 0, 0))
        cells[(band, flow)] = Counts(
            current.true_positives + tp,
            current.false_positives + fp,
            current.false_negatives + fn,
        )

    # Matched and missed count against the TRUE band.
    for key in matched_keys:
        edge = truth_by_key[key]
        _bump(edge.band, edge.flow.value, tp=1)
    for key in missed_keys:
        edge = truth_by_key[key]
        _bump(edge.band, edge.flow.value, fn=1)
    # A false positive counts against the band the analyser CLAIMED.
    for key in spurious_keys:
        invented = predicted_by_key[key]
        _bump(invented.band, invented.flow.value, fp=1)

    # Guards: measured only over edges that matched, and only where truth states one.
    guard_total = 0
    guard_correct = 0
    for key in matched_keys:
        expected = normalise_guard(truth_by_key[key].guard)
        if expected is None:
            continue
        guard_total += 1
        if normalise_guard(predicted_by_key[key].guard) == expected:
            guard_correct += 1

    tier_distribution: dict[str, int] = {}
    mechanism_distribution: dict[str, int] = {}
    for claim in predicted:
        tier_distribution[claim.tier.value] = tier_distribution.get(claim.tier.value, 0) + 1
        mechanism_distribution[claim.mechanism.value] = (
            mechanism_distribution.get(claim.mechanism.value, 0) + 1
        )

    return ScoreReport(
        package=truth.package,
        cells=cells,
        guard_total=guard_total,
        guard_correct=guard_correct,
        tier_distribution=dict(sorted(tier_distribution.items())),
        mechanism_distribution=dict(sorted(mechanism_distribution.items())),
        evidence_split=truth.evidence_split(),
        unexercised_predicted=sum(1 for edge in predicted if edge.unexercised),
        boundaries_expected=sorted(truth.boundaries),
        boundaries_declared=sorted(declared_boundaries or []),
        missed_edges=missed_keys,
        spurious_edges=spurious_keys,
    )


def render(report: ScoreReport) -> str:
    """Plain-text report. Deterministic: same inputs, byte-identical output.

    No timestamp and no ordering that depends on dict insertion, because the
    reproducibility test compares two runs byte for byte.
    """
    lines: list[str] = []
    lines.append(f"package: {report.package}")
    lines.append("")
    lines.append("PER BAND, PER FLOW  (never blended - ADR-0001)")
    lines.append(
        f"  {'band':<6}{'flow':<9}{'TP':>4}{'FP':>4}{'FN':>4}{'precision':>12}{'recall':>10}"
    )

    for band, flow in sorted(report.cells):
        counts = report.cells[(band, flow)]
        precision = counts.precision
        recall = counts.recall
        lines.append(
            f"  {band:<6}{flow:<9}"
            f"{counts.true_positives:>4}{counts.false_positives:>4}{counts.false_negatives:>4}"
            f"{_pct(precision):>12}{_pct(recall):>10}"
        )

    lines.append("")
    lines.append("GATE  (band 1, value flow)")
    lines.append(f"  precision  {_pct(report.gate_precision)}   threshold 95%")
    lines.append(f"  recall     {_pct(report.gate_recall)}   threshold 85%")

    lines.append("")
    lines.append("REPORTED SEPARATELY")
    lines.append(f"  guard correct        {_pct(report.guard_accuracy)} of {report.guard_total}")
    lines.append(f"  tier distribution    {report.tier_distribution or '-'}")
    lines.append(f"  mechanism            {report.mechanism_distribution or '-'}")
    lines.append(f"  unexercised claimed  {report.unexercised_predicted}")

    lines.append("")
    lines.append("LABEL PROVENANCE  (what the answer key itself rests on)")
    for kind, count in sorted(report.evidence_split.items()):
        lines.append(f"  {kind:<20} {count}")

    if report.boundaries_expected or report.boundaries_declared:
        lines.append("")
        lines.append("BOUNDARIES")
        lines.append(f"  expected   {len(report.boundaries_expected)}")
        lines.append(f"  declared   {len(report.boundaries_declared)}")
        for item in report.boundaries_missed:
            lines.append(f"  NOT DECLARED  {item}")

    if report.missed_edges:
        lines.append("")
        lines.append(f"MISSED ({len(report.missed_edges)})")
        for key in report.missed_edges:
            lines.append(f"  {key[0]} -> {key[1]}  [{key[2]}/{key[3]}]")

    if report.spurious_edges:
        lines.append("")
        lines.append(
            f"SPURIOUS ({len(report.spurious_edges)})  <- these are what precision punishes"
        )
        for key in report.spurious_edges:
            lines.append(f"  {key[0]} -> {key[1]}  [{key[2]}/{key[3]}]")

    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"
