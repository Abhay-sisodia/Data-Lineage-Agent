"""Origin in the scoring key: what it may and may not touch (2026-09-09).

ADR-0001 amendment 1 settled that GUARD separates facts without entering precision. This
module pins the follow-up question it left open: whether ORIGIN should do the same.

The answer measured out asymmetric, and both halves need pinning because the wrong half is
the tempting one:

* **Dedup** may not use origin. Two claims sharing a key but reached in two units are the
  NORMAL output of interprocedural summarisation and trigger inheritance - one true fact
  reached twice, not two facts. Separating them manufactures false positives out of
  correct analysis, and it catches no fabrication, because the fabrications this corpus
  contains are refused before they can emit an edge.
* **Pairing** cannot use origin either, for a different reason: there is never a choice to
  make. `labels._reject_duplicates` makes (match key, guard) unique among labels and dedup
  makes it unique among claims, so a same-unit pass never fires. It was built, measured
  byte-identical, and removed.

The corpus-level evidence is in `test_corpus_evidence_for_the_rejection` and in
`docs/decisions/0001-scoring-model.md`. These unit tests state the rule; that test states
the measurement that produced it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lineage.config import AnalysisConfig, Scoring
from lineage.harness.labels import Evidence, Flow, GroundTruth, LabelledEdge
from lineage.harness.scoring import dedup_key, score, scoring_key
from lineage.ir.model import IREdge, Mechanism, Node, NodeKind, Origin, Transform


def _node(name: str) -> Node:
    return Node(kind=NodeKind.COLUMN, name=name)


def _label(guard: str | None = None, unit: str = "CALLER", line: int = 1) -> LabelledEdge:
    return LabelledEdge(
        source=_node("A.X"),
        target=_node("B.Y"),
        flow=Flow.VALUE,
        transform=Transform.IDENTITY,
        band=1,
        guard=guard,
        origin=Origin(unit=unit, line=line),
        evidence=Evidence.SOURCE_READ,
    )


def _claim(guard: str | None = None, unit: str = "CALLER", line: int = 1) -> IREdge:
    return IREdge(
        source=_node("A.X"),
        target=_node("B.Y"),
        flow=Flow.VALUE,
        transform=Transform.IDENTITY,
        band=1,
        guard=guard,
        mechanism=Mechanism.AST,
        origin=Origin(unit=unit, line=line),
    )


def _truth(edges: list[LabelledEdge]) -> GroundTruth:
    return GroundTruth.model_validate(
        {
            "package": "adversarial/band0/b0_01_insert_select.sql",
            "source_sha256": "4fed27906d34830c7ca5324ad19fc1108bd5d2b7d361f03cdcd6650723474f00",
            "labelled_by": "test",
            "labelled_at": "2026-09-09",
            "edges": edges,
        }
    )


def _counts(report: object) -> tuple[int, int, int]:
    cell = report.cell(1, Flow.VALUE)  # type: ignore[attr-defined]
    return cell.true_positives, cell.false_positives, cell.false_negatives


# --- the default is unchanged behaviour ------------------------------------------------


def test_flag_defaults_off() -> None:
    """A measurement taken today must mean what a measurement taken last week meant."""
    assert AnalysisConfig().scoring.origin_in_dedup is False


def test_flag_is_a_declared_limit() -> None:
    """Anything that can change an answer appears beside the answer (config principle 2)."""
    assert AnalysisConfig().declared_limits()["scoring_origin_in_dedup"] is False


def test_turning_a_flag_on_changes_the_config_fingerprint() -> None:
    """Otherwise two incomparable scores would pin to the same configuration."""
    base = AnalysisConfig()
    changed = base.model_copy(update={"scoring": Scoring(origin_in_dedup=True)})
    assert base.fingerprint() != changed.fingerprint()


# --- dedup: the half that was rejected -------------------------------------------------


def test_one_fact_reached_in_two_units_is_one_claim_by_default() -> None:
    """The b1_08 case: a callee summarised at two call sites is not two facts.

    This is the behaviour the rejected flag would break, so it is stated first.
    """
    truth = _truth([_label()])
    report = score(truth, [_claim(unit="CALLEE"), _claim(unit="CALLER")])
    assert _counts(report) == (1, 0, 0)


def test_origin_in_dedup_manufactures_a_false_positive_from_correct_analysis() -> None:
    """The measured reason the flag is off: the second route becomes a second claim.

    Nothing here is wrong. One true edge, reached twice - once inside the callee and once
    inlined at the call site - and enabling the flag punishes precision for it.
    """
    truth = _truth([_label()])
    report = score(
        truth,
        [_claim(unit="CALLEE"), _claim(unit="CALLER")],
        origin_in_dedup=True,
    )
    assert _counts(report) == (1, 1, 0)


def test_dedup_key_carries_the_unit_only_when_asked() -> None:
    claim = _claim(unit="TRG_RECENT_AUDIT")
    assert dedup_key(claim) == scoring_key(claim)
    assert dedup_key(claim, origin_in_dedup=True) == (*scoring_key(claim), "TRG_RECENT_AUDIT")


def test_dedup_key_never_carries_the_line() -> None:
    """Unit agrees 160/170 with the labels; line agrees 12/170.

    Keying on line would collapse recall for a reason with nothing to do with lineage
    being right, so the line must not reach the key by either flag.
    """
    early = _claim(unit="U", line=3)
    late = _claim(unit="U", line=99)
    assert dedup_key(early, origin_in_dedup=True) == dedup_key(late, origin_in_dedup=True)


# --- pairing: the half that turned out inert -------------------------------------------


def test_two_labels_cannot_share_a_match_key_and_guard() -> None:
    """Why a same-unit pairing pass can never fire, stated where it is easy to check.

    This validator is the reason origin has no work to do in pairing: the case a unit test
    would disambiguate - two labels alike in every scored field, differing only by origin -
    is rejected before scoring ever sees it. Anyone reaching for origin in `_pair` again
    should start here.
    """
    with pytest.raises(ValidationError, match="duplicate edges"):
        _truth([_label(unit="A", line=1), _label(unit="B", line=2)])


# --- the corpus-level finding, so it cannot silently reverse ----------------------------


def test_corpus_evidence_for_the_rejection() -> None:
    """The fabrications the flag was built for are refused before they emit an edge.

    s2 declares a boundary for the ungranted schema rather than inventing the edge, so
    there is no second claim for origin to separate. The hole origin_in_dedup was meant to
    close is real but LATENT - unreachable by any input this corpus contains - which is
    why the flag pays only its cost and none of its benefit.

    Stated as a test because the argument for turning the flag on is persuasive on paper
    and only the measurement refutes it.
    """
    truth = GroundTruth.load(Path("ground_truth/s2_schema_context.yaml"))
    assert any("schema not granted" in boundary for boundary in truth.boundaries)
