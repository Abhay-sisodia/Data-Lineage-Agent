"""Scoring harness (T1.3).

The central test is `test_matches_hand_computed_scores`: a fixture small enough to work
out with a pencil, so the harness is verified against arithmetic rather than against
itself. Everything else pins a rule from ADR-0001.
"""

from __future__ import annotations

from typing import Any

from lineage.harness.labels import (
    Evidence,
    Flow,
    GroundTruth,
    Node,
    NodeKind,
    Transform,
)
from lineage.harness.scoring import (
    Mechanism,
    PredictedEdge,
    Tier,
    normalise_guard,
    render,
    score,
)


def _col(name: str) -> dict[str, str]:
    return {"kind": NodeKind.COLUMN.value, "name": name}


def _truth(edges: list[dict[str, Any]], boundaries: list[str] | None = None) -> GroundTruth:
    return GroundTruth.model_validate(
        {
            "package": "fixture/hand_computed.sql",
            "source_sha256": "0" * 64,
            "labelled_by": "fixture",
            "labelled_at": "2026-09-06",
            "edges": edges,
            "boundaries": boundaries or [],
        }
    )


def _label(src: str, dst: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "source": _col(src),
        "target": _col(dst),
        "flow": Flow.VALUE.value,
        "transform": Transform.IDENTITY.value,
        "band": 1,
        "origin": {"unit": "FIXTURE", "line": 1},
        "evidence": Evidence.OBSERVED.value,
    }
    payload.update(overrides)
    return payload


def _pred(src: str, dst: str, **overrides: Any) -> PredictedEdge:
    payload: dict[str, Any] = {
        "source": Node(kind=NodeKind.COLUMN, name=src),
        "target": Node(kind=NodeKind.COLUMN, name=dst),
        "flow": Flow.VALUE,
        "transform": Transform.IDENTITY,
        "band": 1,
        "mechanism": Mechanism.AST,
        "tier": Tier.A,
    }
    payload.update(overrides)
    return PredictedEdge.model_validate(payload)


def test_matches_hand_computed_scores() -> None:
    """Worked by hand, then asserted. The harness must agree with arithmetic.

    Truth:
      band 1 value : A->B identity, B->C DERIVED, C->D identity
      band 1 filter: X->R
      band 0 value : E->F identity

    Predicted:
      A->B identity  band1 value  -> hit
      B->C IDENTITY  band1 value  -> transform is wrong, so NOT a match (ADR-0001 4).
                                     Counts as a false positive AND leaves B->C missed.
      C->D identity  band1 value  -> hit
      X->R           band1 filter -> hit
      G->H identity  band0 value  -> pure invention

    Expected:
      band1/value  TP 2, FP 1, FN 1  -> precision 2/3, recall 2/3
      band1/filter TP 1, FP 0, FN 0  -> precision 1.0, recall 1.0
      band0/value  TP 0, FP 1, FN 1  -> precision 0.0, recall 0.0
    """
    truth = _truth(
        [
            _label("A", "B"),
            _label("B", "C", transform=Transform.DERIVED.value),
            _label("C", "D"),
            _label(
                "X",
                "R",
                flow=Flow.FILTER.value,
                target={"kind": NodeKind.RELATION.value, "name": "R"},
            ),
            _label("E", "F", band=0),
        ]
    )
    predicted = [
        _pred("A", "B"),
        _pred("B", "C"),  # transform mismatch - deliberately
        _pred("C", "D"),
        _pred("X", "R", flow=Flow.FILTER, target=Node(kind=NodeKind.RELATION, name="R")),
        _pred("G", "H", band=0),
    ]

    report = score(truth, predicted)

    value_b1 = report.cell(1, Flow.VALUE)
    assert (value_b1.true_positives, value_b1.false_positives, value_b1.false_negatives) == (
        2,
        1,
        1,
    )
    assert value_b1.precision == 2 / 3
    assert value_b1.recall == 2 / 3

    filter_b1 = report.cell(1, Flow.FILTER)
    assert (filter_b1.true_positives, filter_b1.false_positives, filter_b1.false_negatives) == (
        1,
        0,
        0,
    )
    assert filter_b1.precision == 1.0

    value_b0 = report.cell(0, Flow.VALUE)
    assert (value_b0.true_positives, value_b0.false_positives, value_b0.false_negatives) == (
        0,
        1,
        1,
    )
    assert value_b0.precision == 0.0
    assert value_b0.recall == 0.0

    # The gate is band-1 value precision specifically, not any aggregate.
    assert report.gate_precision == 2 / 3
    assert report.gate_recall == 2 / 3


def test_wrong_transform_is_not_partial_credit() -> None:
    """ADR-0001 4 - the s8 silent failure. SUM(x) is not a copy of x."""
    truth = _truth([_label("A", "B", transform=Transform.AGGREGATED.value)])
    report = score(truth, [_pred("A", "B", transform=Transform.IDENTITY)])
    assert report.cell(1, Flow.VALUE).true_positives == 0
    assert report.cell(1, Flow.VALUE).false_positives == 1
    assert report.cell(1, Flow.VALUE).false_negatives == 1


def test_bands_are_never_blended() -> None:
    """A perfect band 0 must not rescue a broken band 1."""
    truth = _truth([_label("A", "B", band=0), _label("C", "D", band=1)])
    report = score(truth, [_pred("A", "B", band=0)])
    assert report.cell(0, Flow.VALUE).precision == 1.0
    assert report.cell(1, Flow.VALUE).recall == 0.0
    assert report.gate_precision is None  # nothing claimed in band 1 at all


def test_mis_banded_edge_is_counted_honestly() -> None:
    """A matched edge counts in its TRUE band, not the claimed one."""
    truth = _truth([_label("A", "B", band=1)])
    report = score(truth, [_pred("A", "B", band=0)])
    assert report.cell(1, Flow.VALUE).true_positives == 1
    assert report.cell(0, Flow.VALUE).true_positives == 0


def test_guards_do_not_affect_precision_but_are_measured() -> None:
    """ADR-0001 5 - measured, reported, excluded from the gate."""
    truth = _truth([_label("A", "B", guard="p_region = 'EU'")])
    report = score(truth, [_pred("A", "B", guard="'EU'=P_REGION")])
    assert report.cell(1, Flow.VALUE).precision == 1.0  # guard text did not hurt it
    assert report.guard_total == 1
    assert report.guard_correct == 1
    assert report.guard_accuracy == 1.0


def test_guard_normalisation() -> None:
    assert normalise_guard("p_region = 'EU'") == normalise_guard("'EU'  =  P_REGION")
    assert normalise_guard(None) is None
    assert normalise_guard("x > 5") != normalise_guard("x < 5")


def test_undeclared_boundary_is_reported() -> None:
    """A silently omitted boundary is the failure the product exists to prevent."""
    truth = _truth([_label("A", "B")], boundaries=["remote_customer@crm_link"])
    report = score(truth, [_pred("A", "B")], declared_boundaries=[])
    assert report.boundaries_missed == ["remote_customer@crm_link"]


def test_render_is_deterministic() -> None:
    """Two runs on identical input must be byte-identical.

    Prefigures the phase-2 reproducibility gate; cheap to guarantee now, painful to
    retrofit once reports carry timestamps and dict ordering.
    """
    truth = _truth([_label("A", "B"), _label("C", "D", band=0)])
    predicted = [_pred("A", "B"), _pred("Z", "Y", band=2)]
    assert render(score(truth, predicted)) == render(score(truth, predicted))


def test_render_names_the_gate_explicitly() -> None:
    """The report must not let a reader mistake another number for the gate."""
    truth = _truth([_label("A", "B")])
    text = render(score(truth, [_pred("A", "B")]))
    assert "GATE  (band 1, value flow)" in text
    assert "threshold 95%" in text
