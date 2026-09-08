"""Origin assertions: the right edge reached the wrong way (ADR-0001 amendment 1c).

A forbidden edge names a wrong ANSWER. Two packages in this corpus invite a wrong
DERIVATION of the right answer, and no forbidden rule can express that - the rule would ban
the required edge too, and `_reject_self_contradiction` correctly refuses it.

* `s2_schema_context` fabricates `STG_CUSTOMER.CUST_ID -> TMP_RECENT.CUST_ID` by dropping an
  ungranted schema qualifier and binding to the local table. Character for character its own
  legitimate edge from the statement above; only the line differs.
* `b2_05_triggers` attaches a trigger's edges to the caller instead of the table, crediting a
  procedure with a write it never issued and losing the trigger for six other writers.

Amendment 1b established what does NOT work: putting origin in the match key. It caught no
fabrication and cost five false positives, because one true fact legitimately arrives from
several units. These tests pin the mechanism that does work, and - just as important - pin
that it stays quiet about the multi-route case that defeated the key change.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lineage.harness.labels import (
    AllowedOrigin,
    Evidence,
    Flow,
    GroundTruth,
    LabelledEdge,
    OriginAssertion,
)
from lineage.harness.scoring import score
from lineage.ir.model import IREdge, Mechanism, Node, NodeKind, Origin, Transform

GROUND_TRUTH = Path("ground_truth")


def _col(name: str) -> Node:
    return Node(kind=NodeKind.COLUMN, name=name)


def _claim(unit: str, line: int, source: str = "A.X", target: str = "B.Y") -> IREdge:
    return IREdge(
        source=_col(source),
        target=_col(target),
        flow=Flow.VALUE,
        transform=Transform.IDENTITY,
        band=1,
        mechanism=Mechanism.AST,
        origin=Origin(unit=unit, line=line),
    )


def _label(unit: str, line: int) -> LabelledEdge:
    return LabelledEdge(
        source=_col("A.X"),
        target=_col("B.Y"),
        flow=Flow.VALUE,
        transform=Transform.IDENTITY,
        band=1,
        origin=Origin(unit=unit, line=line),
        evidence=Evidence.SOURCE_READ,
    )


def _assertion(*allowed: AllowedOrigin) -> OriginAssertion:
    return OriginAssertion(
        source=_col("A.X"),
        target=_col("B.Y"),
        flow=Flow.VALUE,
        must_come_from=list(allowed),
        reason="test",
    )


def _truth(edges: list[LabelledEdge], assertions: list[OriginAssertion]) -> GroundTruth:
    return GroundTruth.model_validate(
        {
            "package": "adversarial/band0/b0_01_insert_select.sql",
            "source_sha256": "4fed27906d34830c7ca5324ad19fc1108bd5d2b7d361f03cdcd6650723474f00",
            "labelled_by": "test",
            "labelled_at": "2026-09-09",
            "edges": edges,
            "origin_assertions": assertions,
        }
    )


# --- the mechanism catches what it was built for ---------------------------------------


def test_an_edge_from_a_disallowed_line_is_reported() -> None:
    """The s2 shape: identical edge, same unit, wrong statement."""
    truth = _truth([_label("U", 19)], [_assertion(AllowedOrigin(unit="U", lines=(17, 22)))])
    report = score(truth, [_claim("U", 26)])

    assert len(report.origin_violations) == 1
    key, actual, allowed, _ = report.origin_violations[0]
    assert actual == "U:26"
    assert allowed == "U:17-22"
    assert key[:2] == ("column:A.X", "column:B.Y")


def test_an_edge_from_a_disallowed_unit_is_reported() -> None:
    """The b2_05 shape: the trigger's edge credited to the caller."""
    truth = _truth([_label("TRG", 3)], [_assertion(AllowedOrigin(unit="TRG"))])
    report = score(truth, [_claim("CALLER", 40)])

    assert [v[1] for v in report.origin_violations] == ["CALLER:40"]


def test_a_violation_does_not_touch_precision_or_recall() -> None:
    """Reported as a named defect, on its own axis - like forbidden edges.

    The wrongly-derived edge still MATCHES its label, because it is the right edge. Folding
    it into precision would say the analyser invented something, which is a different and
    weaker claim than the one being made.
    """
    truth = _truth([_label("U", 19)], [_assertion(AllowedOrigin(unit="U", lines=(17, 22)))])
    report = score(truth, [_claim("U", 26)])

    cell = report.cell(1, Flow.VALUE)
    assert (cell.true_positives, cell.false_positives, cell.false_negatives) == (1, 0, 0)
    assert report.origin_violations


def test_the_legitimate_derivation_is_silent() -> None:
    truth = _truth([_label("U", 19)], [_assertion(AllowedOrigin(unit="U", lines=(17, 22)))])
    report = score(truth, [_claim("U", 18)])

    assert report.origin_violations == []
    assert report.origin_assertions_checked == 1


# --- and stays quiet about the case that defeated the match-key change -------------------


def test_one_fact_reached_from_several_allowed_units_is_not_a_violation() -> None:
    """The b1_08 / trigger-inheritance case, which amendment 1b measured at five FPs.

    This is why `must_come_from` is a list rather than a single origin: a callee summarised
    at three call sites and a trigger edge inherited by its table's writer are one true fact
    reached by several routes. The mechanism has to be able to say so.
    """
    truth = _truth(
        [_label("CALLEE", 14)],
        [_assertion(AllowedOrigin(unit="CALLEE"), AllowedOrigin(unit="CALLER"))],
    )
    report = score(truth, [_claim("CALLEE", 14), _claim("CALLER", 39)])

    assert report.origin_violations == []


def test_assertions_are_checked_before_dedup() -> None:
    """Dedup keeps one claim per key, and which one is decided by line order.

    So a fabricated edge could be the one dedup discards, and checking after it would let
    the defect through on an implementation detail. Here the legitimate claim sorts first
    and would survive dedup; the violation must still be reported.
    """
    truth = _truth([_label("U", 19)], [_assertion(AllowedOrigin(unit="U", lines=(17, 22)))])
    report = score(truth, [_claim("U", 18), _claim("U", 26)])

    assert [v[1] for v in report.origin_violations] == ["U:26"]


# --- the label format refuses assertions that would be traps ----------------------------


def test_an_assertion_contradicting_its_own_label_is_rejected() -> None:
    """Same class of error as a forbidden edge that names a required one."""
    with pytest.raises(ValidationError, match="does not admit the labelled origin"):
        _truth([_label("U", 40)], [_assertion(AllowedOrigin(unit="U", lines=(17, 22)))])


def test_an_inverted_line_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match="inverted"):
        AllowedOrigin(unit="U", lines=(22, 17))


def test_must_come_from_cannot_be_empty() -> None:
    """An assertion admitting nothing would fail every edge, including the correct one."""
    with pytest.raises(ValidationError):
        OriginAssertion(source=_col("A.X"), target=_col("B.Y"), must_come_from=[], reason="test")


# --- the two real packages --------------------------------------------------------------


def test_s2_asserts_statement_ones_lines() -> None:
    truth = GroundTruth.load(GROUND_TRUTH / "s2_schema_context.yaml")
    assert len(truth.origin_assertions) == 3
    for assertion in truth.origin_assertions:
        assert [str(a) for a in assertion.must_come_from] == ["S2_SCHEMA_CONTEXT:17-22"]


def test_s2_would_catch_the_fabrication_it_has_never_produced() -> None:
    """The hole is latent, so this is the test that proves the guard works at all.

    s2 currently emits nothing for statement 2 - it drops the ungranted schema silently,
    which is its own defect - so the assertion passes today for the wrong reason. Feeding it
    the fabrication directly is the only way to show the mechanism would catch it.
    """
    truth = GroundTruth.load(GROUND_TRUTH / "s2_schema_context.yaml")
    fabricated = _claim("S2_SCHEMA_CONTEXT", 26, "STG_CUSTOMER.CUST_ID", "TMP_RECENT.CUST_ID")

    report = score(truth, [fabricated])

    assert len(report.origin_violations) == 1
    assert report.origin_violations[0][1] == "S2_SCHEMA_CONTEXT:26"


def test_b2_05_asserts_the_trigger_owns_its_edges() -> None:
    truth = GroundTruth.load(GROUND_TRUTH / "b2_05_triggers.yaml")
    assert len(truth.origin_assertions) == 3
    for assertion in truth.origin_assertions:
        assert [str(a) for a in assertion.must_come_from] == ["TRG_RECENT_AUDIT"]


def test_b2_05_would_catch_the_trigger_credited_to_the_caller() -> None:
    truth = GroundTruth.load(GROUND_TRUTH / "b2_05_triggers.yaml")
    misattributed = _claim(
        "B2_TRIGGERS", 39, "DIM_CUSTOMER.LIFETIME_VALUE", "DIM_CUSTOMER.LIFETIME_VALUE"
    ).model_copy(update={"transform": Transform.DERIVED})

    report = score(truth, [misattributed])

    assert [v[1] for v in report.origin_violations] == ["B2_TRIGGERS:39"]
