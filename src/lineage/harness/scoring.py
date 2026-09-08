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
from collections import defaultdict
from dataclasses import dataclass, field

from lineage.harness.labels import Flow, GroundTruth, LabelledEdge
from lineage.ir.model import BoundaryKind, Declared, IREdge, MatchKey, Mechanism, Tier

# ADR-0001 amendment 1, implemented at the scoring boundary.
#
# The match key is (source, target, flow, transform). It collapses edges that are
# genuinely different facts: the same write on the happy path and again inside an
# exception handler, a filter firing for EU and an identical one firing for APAC, an
# accumulation and a decay of the same variable. Scored on the match key alone a package
# can only ever be credited with ONE of each pair, and the other is a permanent miss that
# no analyser could fix.
#
# THE OBVIOUS FIX IS WRONG. Adding the guard to the key makes a wrong guard produce a miss
# and a false positive, which drags guard-comparison noise straight into precision - and
# ADR-0001 5 excludes guards from precision precisely because comparing them properly is
# a research problem. T2.4 measured that noise: the first guard scoring was 0/5 purely on
# phrasing, `P_REGION <> 'EU'` against `NOT (p_region = 'EU')`.
#
# So matching is TWO-STAGE. Group both sides by match key; where a group holds more than
# one edge, use the guard to pair them off. A lone edge with a mis-stated guard still
# matches and is counted wrong in the separate guard figure, exactly as before. Guards
# discriminate only where discrimination is actually needed.
#
# ORIGIN IS NOT USED, and that was measured rather than assumed. Across the corpus, label
# and analyser agree on the origin UNIT for 160 of 170 matched edges but on the LINE for
# only 12 - line numbers are hand-read while labelling, and requiring them would collapse
# recall for a reason unrelated to lineage being right. Unit agrees far better, but its ten
# disagreements are a live modelling question (when a view's predicate or a callee's
# expression is inlined, does the edge belong to the caller or to the unit that wrote it?)
# and putting an unsettled convention inside the gate would move the headline number on a
# decision nobody has taken.
#
# The residual cost is stated rather than hidden: three silent failures - s2, b2_05 and
# b2_02 - turn on origin alone and cannot be expressed here at all. They need direct
# assertions, not a scoring key.
#
# ---------------------------------------------------------------------------------------
# ORIGIN, RECONSIDERED - AND THE RECONSIDERATION MEASURED AND REJECTED (2026-09-09)
#
# The hypothesis. The paragraph above says origin cannot join the EQUALITY test, which is
# right, but it does not follow that origin has no place here at all. s2 looked like the
# counter-example:
#
#   line 19  STG_CUSTOMER.CUST_ID -> TMP_RECENT.CUST_ID   legitimate
#   line 26  STG_CUSTOMER.CUST_ID -> TMP_RECENT.CUST_ID   fabricated from a schema that
#                                                          was never granted
#
# Same source, target, flow, transform AND same guard, so two-stage pairing cannot separate
# them and the label validator cannot express the second as a forbidden edge - it
# contradicts a required one. Worse, the dedup below would collapse the pair into a single
# claim, so a fabricated edge could vanish into a legitimate one and still score 100%. A
# hole in PRECISION rather than recall, and invisible by construction.
#
# So origin unit was put into dedup behind `scoring.origin_in_dedup` and MEASURED against
# the full corpus. THE HYPOTHESIS DID NOT SURVIVE.
#
#   * It caught NO fabrication. s2 and b2_05 refuse the offending statement before it can
#     emit an edge, so there was never a second claim to separate. The hole is LATENT -
#     real, but not reachable by any input this corpus contains.
#   * It manufactured FIVE false positives out of correct analysis: 0/filter 100% -> 97.4%,
#     1/filter 100% -> 90.9%, 2/filter 100% -> 93.5%. Every one is a legitimate fact the
#     analyser reached by more than one route -
#         b1_08  STG_ORDERS.ORDER_ID -> STG_ORDERS, from FN_DISCOUNT_RATE, FN_NET_AMOUNT
#                and B1_NESTED_CALLS - one callee summarised at three call sites
#         b1_09  DIM_CUSTOMER.CUST_ID -> DIM_CUSTOMER, from the procedure and again from
#         s3     TRG_RECENT_AUDIT - a trigger edge inherited by the table's writer, which
#                is the whole T3.3 design
#
# Which is exactly what the dedup comment below already warned about, written a week
# earlier: counting the same true thing twice punishes precision. Multi-unit origin is the
# NORMAL case for interprocedural summaries and trigger inheritance, not the exceptional
# one, so origin cannot distinguish a second route from a second fact.
#
# A SAME-UNIT PAIRING PASS WAS ALSO TRIED, AND REMOVED AS INERT. It looked free - it only
# reorders which prediction is credited against which label inside a group, so the counts
# are arithmetically identical - but it turns out never to fire. `labels._reject_duplicates`
# makes (match key, guard) unique among LABELS, and dedup makes it unique among CLAIMS, so
# a group never holds two candidates for a unit test to choose between. Measured across the
# corpus: every cell, the guard figure and the execution axis byte-identical with it on.
# Config that cannot change an output is not a control, it is decoration - removed rather
# than kept as an option nobody can evaluate.
#
# THE STANDING CONCLUSION. Origin has no useful role in the scoring layer at all. The
# s2/b2_05 hole is real, still open, and needs what the label files have said all along: a
# DIRECT ORIGIN ASSERTION in the label format - "this edge must come from THIS unit" -
# scored beside the forbidden-edge rules rather than inside the match key. That is a label
# schema change, not a scoring change, and it is the honest next move.
#
# `origin_in_dedup` stays switchable, defaulted off, so this finding stays reproducible
# rather than becoming a story someone has to take on trust.
#
# UNIT, NEVER LINE, in any case. Label and analyser agree on the origin unit for 160 of 170
# matched edges and on the line for 12.
# ---------------------------------------------------------------------------------------
EdgeKey = tuple[str, str, str, str, str]

# The analyser emits IR edges directly (T2.1). The alias is kept because "predicted" is
# the right word at a scoring boundary - these are claims being tested, not facts yet.
PredictedEdge = IREdge

__all__ = [
    "Counts",
    "EdgeKey",
    "MatchKey",
    "Mechanism",
    "PredictedEdge",
    "ScoreReport",
    "Tier",
    "dedup_key",
    "normalise_guard",
    "render",
    "score",
    "scoring_key",
]


def scoring_key(edge: IREdge | LabelledEdge) -> EdgeKey:
    """Match key plus normalised guard — the reported identity of one scored fact.

    Used for *reporting* a miss or a false positive unambiguously, and for pairing edges
    within a match-key group. It is not the equality test on its own; see ``_pair``.

    Takes either side deliberately: a label and a prediction must be reduced by exactly
    the same function, or the comparison is between two different notions of identity.
    """
    match_key = edge.key() if isinstance(edge, LabelledEdge) else edge.match_key()
    return (*match_key, normalise_guard(edge.guard) or "")


def dedup_key(edge: IREdge | LabelledEdge, *, origin_in_dedup: bool = False) -> tuple[str, ...]:
    """The identity used to collapse predictions that state the same fact twice.

    With ``origin_in_dedup`` the unit is carried, so a legitimate edge and a fabricated one
    that agree in every other field survive as two claims instead of one. Without it, the
    fabricated one is absorbed and never counted - see the ORIGIN, RECONSIDERED note above.
    """
    key = scoring_key(edge)
    return (*key, edge.origin.unit) if origin_in_dedup else key


def _pair(
    truths: list[LabelledEdge], predictions: list[PredictedEdge]
) -> tuple[list[tuple[LabelledEdge, PredictedEdge]], list[LabelledEdge], list[PredictedEdge]]:
    """Pair labels with predictions inside one match-key group.

    Guard-equal pairs are taken first so that when a group holds an EU edge and an APAC
    edge, each is credited against its own counterpart rather than at random. Whatever
    remains is paired positionally: those are edges the analyser found with the wrong
    guard, which must still count as matched - the guard figure is what reports them.

    A same-UNIT pass was tried here (2026-09-09) and removed as provably inert rather than
    merely harmless: ``labels._reject_duplicates`` makes (match key, guard) unique among
    labels, and dedup makes it unique among claims, so a group never holds two candidates
    for a unit test to choose between. See ORIGIN, RECONSIDERED above.
    """
    remaining = list(predictions)
    pairs: list[tuple[LabelledEdge, PredictedEdge]] = []
    unpaired: list[LabelledEdge] = []

    for truth in truths:
        want = normalise_guard(truth.guard)
        exact = next((p for p in remaining if normalise_guard(p.guard) == want), None)
        if exact is None:
            unpaired.append(truth)
            continue
        remaining.remove(exact)
        pairs.append((truth, exact))

    # Second pass: a mis-stated guard is still a match. Only genuine surplus is a miss.
    still_unpaired: list[LabelledEdge] = []
    for truth in unpaired:
        if remaining:
            pairs.append((truth, remaining.pop(0)))
        else:
            still_unpaired.append(truth)

    return pairs, still_unpaired, remaining


def normalise_guard(guard: str | None) -> str | None:
    """Reduce a guard to a comparable form.

    Four things vary without changing meaning, and all four are normalised away:

    * case — `p_region` and `P_REGION`
    * operand order — `p_region = 'EU'` and `'EU' = p_region`
    * conjunct order — a guard is a set of conditions, not a sequence
    * negated equality — `NOT (x = y)` and `x <> y`

    Deliberately stops there. Guards are excluded from precision precisely because
    comparing them properly is a research problem, and a half-clever normaliser that
    silently equates two different conditions would be far worse than an honest one that
    occasionally reports a difference. Anything beyond these four rules needs a solver,
    not a regex.
    """
    if guard is None:
        return None

    atoms = sorted({_normalise_atom(part) for part in _split_conjuncts(guard)})
    return " AND ".join(a for a in atoms if a)


def _split_conjuncts(guard: str) -> list[str]:
    """Split on top-level AND, ignoring ANDs inside parentheses."""
    parts: list[str] = []
    depth = 0
    current = ""
    for token in re.split(r"(\(|\)|\bAND\b)", guard, flags=re.IGNORECASE):
        if token is None:
            continue
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif token.upper() == "AND" and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += token
    parts.append(current)
    return [p for p in parts if p.strip()]


def _normalise_atom(text: str) -> str:
    """Canonicalise one condition."""
    atom = re.sub(r"\s+", " ", text.strip().upper())

    # NOT (x = y) is the same condition as x <> y.
    negated = re.fullmatch(r"NOT\s*\((.+)\)", atom)
    if negated:
        inner = negated.group(1).strip()
        equality = re.fullmatch(r"([^=<>!]+)=([^=<>!]+)", inner)
        if equality:
            left, right = sorted(part.strip() for part in equality.groups())
            return f"{left} <> {right}"
        return f"NOT ({inner})"

    # Equality is symmetric, so order the operands.
    equality = re.fullmatch(r"([^=<>!]+)=([^=<>!]+)", atom)
    if equality:
        left, right = sorted(part.strip() for part in equality.groups())
        return f"{left} = {right}"

    inequality = re.fullmatch(r"([^=<>!]+)<>([^=<>!]+)", atom)
    if inequality:
        left, right = sorted(part.strip() for part in inequality.groups())
        return f"{left} <> {right}"

    return atom


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
    unexercised_total: int = 0
    unexercised_correct: int = 0
    unexercised_unknown: int = 0
    boundaries_expected: list[str] = field(default_factory=list)
    boundaries_declared: list[str] = field(default_factory=list)
    missed_edges: list[EdgeKey] = field(default_factory=list)
    spurious_edges: list[EdgeKey] = field(default_factory=list)
    forbidden_violations: list[tuple[EdgeKey, str]] = field(default_factory=list)
    origin_violations: list[tuple[EdgeKey, str, str, str]] = field(default_factory=list)
    origin_assertions_checked: int = 0
    boundaries_expected_structured: list[tuple[str, str, str]] = field(default_factory=list)
    boundaries_undeclared: list[tuple[str, str, str]] = field(default_factory=list)

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
    def unexercised_accuracy(self) -> float | None:
        """Agreement on the execution axis, over matched edges where BOTH sides state one.

        Returns None when nothing can be compared, which with no execution witness is
        every edge - and `None` here has to read as *not measured*, never as 0%. Scoring
        an axis the analyser was given no evidence for would turn a missing input into an
        analyser failure, and the whole point of the tri-state is to keep those apart.

        Kept out of precision for the same reason guards are (ADR-0001 §5): whether a
        statement ran is a fact about the estate, not about the analysis, and letting it
        move the gate would make the headline number depend on how busy last month was.
        """
        return self.unexercised_correct / self.unexercised_total if self.unexercised_total else None

    @property
    def boundaries_missed(self) -> list[str]:
        """Known unknowns the analyser failed to declare, compared as PROSE.

        Kept because the older label sets state boundaries only as sentences, and a
        package with no structured expectation still deserves the weaker check.

        **It is nearly useless on its own and was measured to be so**: with the answer key
        writing "database link target out of coverage" where the analyser writes "(relation
        not in dictionary)", exact string equality reported all 18 expected boundaries as
        undeclared while 35 were declared. `boundaries_undeclared` is the real check;
        this one is the fallback for packages that have not been converted.
        """
        return sorted(set(self.boundaries_expected) - set(self.boundaries_declared))


def score(
    truth: GroundTruth,
    predicted: list[PredictedEdge],
    declared_boundaries: list[str] | None = None,
    *,
    origin_in_dedup: bool = False,
) -> ScoreReport:
    """Score analyser output against a ground-truth label set.

    ``origin_in_dedup`` changes what counts as one claim. Default off: it was measured
    harmful on this corpus. See ORIGIN, RECONSIDERED above.
    """
    truth_groups: dict[MatchKey, list[LabelledEdge]] = defaultdict(list)
    for edge in truth.edges:
        truth_groups[edge.key()].append(edge)

    # Collapse predictions that are the same fact stated twice.
    #
    # The analyser merges on IREdge.identity(), which includes origin, so one fact reached
    # by two routes - a predicate inlined from a view and again from the caller, a callee
    # summarised at two call sites - survives as two edges. Under the scoring key those
    # are one claim, and counting them twice would punish precision for saying the same
    # true thing twice. Deduplicating here rather than in the analyser is deliberate: the
    # ledger genuinely wants both, each with its own origin.
    #
    # With `origin_in_dedup` the unit joins that key, so two claims the analyser reached in
    # two different UNITS are two claims. That is the s2 case: absorbing the second one is
    # how a fabricated edge disappears into a legitimate one.
    seen: dict[tuple[str, ...], PredictedEdge] = {}
    for claim in sorted(predicted, key=lambda e: (e.origin.unit, e.origin.line)):
        seen.setdefault(dedup_key(claim, origin_in_dedup=origin_in_dedup), claim)

    predicted_groups: dict[MatchKey, list[PredictedEdge]] = defaultdict(list)
    for claim in seen.values():
        predicted_groups[claim.match_key()].append(claim)

    matched: list[tuple[LabelledEdge, PredictedEdge]] = []
    missed: list[LabelledEdge] = []
    spurious: list[PredictedEdge] = []

    for match_key in truth_groups.keys() | predicted_groups.keys():
        pairs, unmatched_truth, surplus = _pair(
            truth_groups.get(match_key, []),
            predicted_groups.get(match_key, []),
        )
        matched += pairs
        missed += unmatched_truth
        spurious += surplus

    missed_keys = sorted(scoring_key(edge) for edge in missed)
    spurious_keys = sorted(scoring_key(claim) for claim in spurious)

    cells: dict[tuple[int, str], Counts] = {}

    def _bump(band: int, flow: str, tp: int = 0, fp: int = 0, fn: int = 0) -> None:
        current = cells.get((band, flow), Counts(0, 0, 0))
        cells[(band, flow)] = Counts(
            current.true_positives + tp,
            current.false_positives + fp,
            current.false_negatives + fn,
        )

    # Matched and missed count against the TRUE band.
    for edge, _ in matched:
        _bump(edge.band, edge.flow.value, tp=1)
    for edge in missed:
        _bump(edge.band, edge.flow.value, fn=1)
    # A false positive counts against the band the analyser CLAIMED.
    for invented in spurious:
        _bump(invented.band, invented.flow.value, fp=1)

    # Guards: measured only over edges that matched, and only where truth states one.
    # Still entirely outside precision (ADR-0001 §5) - a guard that disagrees produces a
    # matched pair here and a wrong entry there.
    guard_total = 0
    guard_correct = 0
    for edge, claim in matched:
        expected = normalise_guard(edge.guard)
        if expected is None:
            continue
        guard_total += 1
        if normalise_guard(claim.guard) == expected:
            guard_correct += 1

    # The execution axis (T3.5), measured only where both sides say something. A label
    # states `unexercised: true` as a positive assertion by the labeller; a prediction
    # carrying None was given no execution window and is counted as unknown rather than
    # wrong. Labels are two-state and predictions are three-state on purpose - a labeller
    # writing the key HAS looked, and a run without a witness has not.
    unexercised_total = 0
    unexercised_correct = 0
    unexercised_unknown = 0
    for edge, claim in matched:
        if claim.unexercised is None:
            unexercised_unknown += 1
            continue
        unexercised_total += 1
        if claim.unexercised == edge.unexercised:
            unexercised_correct += 1

    # Forbidden edges are checked against everything emitted, not only against the
    # spurious set. An edge could in principle be forbidden here and legitimate
    # elsewhere; what matters is whether THIS package produced it.
    #
    # Reported by scoring key, never by the dedup key: the dedup key's shape depends on a
    # config flag, and a violation must read the same however the run was configured.
    violations: list[tuple[EdgeKey, str]] = []
    for key in sorted(scoring_key(claim) for claim in seen.values()):
        for rule in truth.forbidden:
            if rule.matches(key[:4]):
                violations.append((key, rule.reason))

    # Boundaries, compared on kind and subject rather than on prose (ADR-0001 amendment
    # 1d). An unclassified declaration cannot satisfy anything - it is counted as declared
    # for the human report and simply cannot be matched, which is honest: nobody has said
    # what it is about.
    declared_identities = {
        classified
        for entry in (declared_boundaries or [])
        if (classified := Declared.classified(entry)) is not None
    }
    expected_structured = [
        (expected.kind.value, expected.subject.upper(), expected.reason)
        for expected in truth.expected_boundaries
    ]
    boundaries_undeclared = [
        item
        for item in expected_structured
        if (BoundaryKind(item[0]), item[1]) not in declared_identities
    ]

    # Origin assertions (ADR-0001 amendment 1c). Checked against EVERY emitted edge, before
    # dedup, because dedup keeps one claim per key and the whole question here is which of
    # several same-key claims the analyser derived. A wrongly-derived edge that dedup
    # happened to discard would otherwise pass.
    #
    # Universally quantified on purpose: every matching edge must have an allowed origin.
    # Asking whether SOME edge does would pass with a fabricated one sitting beside it.
    origin_violations: list[tuple[EdgeKey, str, str, str]] = []
    for claim in sorted(predicted, key=lambda e: (e.origin.unit, e.origin.line)):
        key = scoring_key(claim)
        for assertion in truth.origin_assertions:
            if not assertion.matches(key[:4]):
                continue
            if not assertion.admits(claim.origin.unit, claim.origin.line):
                origin_violations.append(
                    (key, str(claim.origin), assertion.allowed_description(), assertion.reason)
                )

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
        unexercised_total=unexercised_total,
        unexercised_correct=unexercised_correct,
        unexercised_unknown=unexercised_unknown,
        boundaries_expected=sorted(truth.boundaries),
        boundaries_declared=sorted(declared_boundaries or []),
        missed_edges=missed_keys,
        spurious_edges=spurious_keys,
        forbidden_violations=violations,
        origin_violations=origin_violations,
        origin_assertions_checked=len(truth.origin_assertions),
        boundaries_expected_structured=expected_structured,
        boundaries_undeclared=boundaries_undeclared,
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
    lines.append(
        f"  execution axis       {_pct(report.unexercised_accuracy)} of "
        f"{report.unexercised_total}   {report.unexercised_unknown} with no witness"
    )

    lines.append("")
    lines.append("LABEL PROVENANCE  (what the answer key itself rests on)")
    for kind, count in sorted(report.evidence_split.items()):
        lines.append(f"  {kind:<20} {count}")

    if report.boundaries_expected or report.boundaries_declared:
        lines.append("")
        lines.append("BOUNDARIES")
        lines.append(f"  expected   {len(report.boundaries_expected)}")
        lines.append(f"  declared   {len(report.boundaries_declared)}")
        if report.boundaries_expected_structured:
            lines.append(
                f"  checked    {len(report.boundaries_expected_structured)} by kind+subject, "
                f"{len(report.boundaries_undeclared)} NOT DECLARED"
            )
            for kind, subject, reason in report.boundaries_undeclared:
                lines.append(f"  NOT DECLARED  [{kind}] {subject}")
                lines.append(f"      {' '.join(reason.split())[:96]}")
        else:
            for item in report.boundaries_missed:
                lines.append(f"  NOT DECLARED (prose match)  {item}")

    if report.forbidden_violations:
        lines.append("")
        lines.append(
            f"FORBIDDEN EDGES PRODUCED ({len(report.forbidden_violations)})"
            "  <- the exact wrong answer this package tests for"
        )
        for key, reason in report.forbidden_violations:
            lines.append(f"  {key[0]} -> {key[1]}  [{key[2]}/{key[3]}]{_guard_suffix(key)}")
            lines.append(f"      {reason}")

    if report.origin_violations:
        lines.append("")
        lines.append(
            f"WRONGLY DERIVED ({len(report.origin_violations)})"
            "  <- the right edge reached the wrong way"
        )
        for key, actual, allowed, reason in report.origin_violations:
            lines.append(f"  {key[0]} -> {key[1]}  [{key[2]}/{key[3]}]{_guard_suffix(key)}")
            lines.append(f"      came from {actual}, must come from {allowed}")
            lines.append(f"      {reason}")
    elif report.origin_assertions_checked:
        lines.append("")
        lines.append(
            f"origin assertions   {report.origin_assertions_checked} checked, all satisfied"
        )

    if report.missed_edges:
        lines.append("")
        lines.append(f"MISSED ({len(report.missed_edges)})")
        for key in report.missed_edges:
            lines.append(f"  {key[0]} -> {key[1]}  [{key[2]}/{key[3]}]{_guard_suffix(key)}")

    if report.spurious_edges:
        lines.append("")
        lines.append(
            f"SPURIOUS ({len(report.spurious_edges)})  <- these are what precision punishes"
        )
        for key in report.spurious_edges:
            lines.append(f"  {key[0]} -> {key[1]}  [{key[2]}/{key[3]}]{_guard_suffix(key)}")

    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _guard_suffix(key: EdgeKey) -> str:
    """Show the guard when there is one — two edges can now differ by nothing else."""
    return f"  when {key[4]}" if len(key) > 4 and key[4] else ""
