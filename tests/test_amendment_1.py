"""ADR-0001 amendment 1: guard separates facts, without entering precision.

Two decisions are in tension and both have to survive.

* §4 says edge identity for matching is (source, target, flow, transform). Under that key
  alone, a write on the happy path and the same write inside an exception handler are one
  target, so one of them is a miss no analyser can ever fix.
* §5 says guards stay out of precision, because comparing them is a research problem and
  the gate must not move on string-comparison noise. T2.4 measured that noise directly:
  the first guard score was 0/5, entirely on phrasing.

The resolution is a two-stage match, and these tests pin both halves. The interesting
failure is the easy fix - putting the guard in the key - which satisfies §4 by breaking §5.
"""

from __future__ import annotations

from pathlib import Path

from lineage.harness.labels import Evidence, Flow, GroundTruth, LabelledEdge
from lineage.harness.scoring import score
from lineage.ir.model import IREdge, Mechanism, Node, NodeKind, Origin, Transform

GROUND_TRUTH = Path("ground_truth")


def _node(name: str) -> Node:
    return Node(kind=NodeKind.COLUMN, name=name)


def _label(guard: str | None, band: int = 1, line: int = 1) -> LabelledEdge:
    return LabelledEdge(
        source=_node("A.X"),
        target=_node("B.Y"),
        flow=Flow.VALUE,
        transform=Transform.IDENTITY,
        band=band,
        guard=guard,
        origin=Origin(unit="U", line=line),
        evidence=Evidence.SOURCE_READ,
    )


def _claim(guard: str | None, line: int = 1) -> IREdge:
    return IREdge(
        source=_node("A.X"),
        target=_node("B.Y"),
        flow=Flow.VALUE,
        transform=Transform.IDENTITY,
        band=1,
        guard=guard,
        mechanism=Mechanism.AST,
        origin=Origin(unit="U", line=line),
    )


def _truth(edges: list[LabelledEdge]) -> GroundTruth:
    return GroundTruth.model_validate(
        {
            "package": "adversarial/band0/b0_01_insert_select.sql",
            "source_sha256": ("4fed27906d34830c7ca5324ad19fc1108bd5d2b7d361f03cdcd6650723474f00"),
            "labelled_by": "test",
            "labelled_at": "2026-09-06",
            "edges": edges,
        }
    )


# --- §4: guard-distinguished facts are both scoreable ---------------------------------


def test_two_facts_sharing_a_match_key_are_both_credited() -> None:
    """The b1_09 case: the same write on the happy path and in an exception handler."""
    truth = _truth([_label(None, line=1), _label("EXCEPTION NO_DATA_FOUND", line=9)])
    report = score(truth, [_claim(None), _claim("EXCEPTION NO_DATA_FOUND", line=9)])

    counts = report.cell(1, Flow.VALUE)
    assert (counts.true_positives, counts.false_positives, counts.false_negatives) == (2, 0, 0)


def test_finding_only_one_of_the_pair_is_one_hit_and_one_miss() -> None:
    """Before the amendment this scored 1/0/0 — the second fact was invisible."""
    truth = _truth([_label(None), _label("EXCEPTION NO_DATA_FOUND", line=9)])
    report = score(truth, [_claim(None)])

    counts = report.cell(1, Flow.VALUE)
    assert (counts.true_positives, counts.false_negatives) == (1, 1)


def test_edges_are_paired_with_their_own_guard_not_at_random() -> None:
    """An EU edge must be credited against the EU claim, not the APAC one.

    Pairing arbitrarily inside a group still yields 2 TP, so precision would look
    identical — and the guard figure would then report a failure that did not happen.
    """
    truth = _truth([_label("P_REGION = 'EU'"), _label("P_REGION = 'APAC'", line=9)])
    report = score(truth, [_claim("P_REGION = 'APAC'", line=9), _claim("P_REGION = 'EU'")])

    assert report.guard_total == 2
    assert report.guard_correct == 2


# --- §5: a wrong guard is still a match -----------------------------------------------


def test_a_mis_stated_guard_still_matches_and_costs_no_precision() -> None:
    """THE REGRESSION THIS FILE EXISTS FOR.

    Putting the guard into the match key would make this one miss plus one false positive
    — guard noise landing directly on the gate, which §5 forbids. It must stay a match,
    reported wrong only in the separate guard figure.
    """
    truth = _truth([_label("P_REGION = 'EU'")])
    report = score(truth, [_claim("P_REGION = 'APAC'")])

    counts = report.cell(1, Flow.VALUE)
    assert (counts.true_positives, counts.false_positives, counts.false_negatives) == (1, 0, 0)
    assert (report.guard_total, report.guard_correct) == (1, 0)


def test_cosmetic_guard_differences_are_normalised_not_punished() -> None:
    truth = _truth([_label("NOT (p_region = 'EU') AND p_strict = 1")])
    report = score(truth, [_claim("P_STRICT = 1 AND P_REGION <> 'EU'")])

    assert report.guard_correct == 1
    assert report.cell(1, Flow.VALUE).false_positives == 0


# --- one fact stated twice is one claim -----------------------------------------------


def test_duplicate_predictions_are_one_claim_not_two() -> None:
    """The analyser merges on identity(), which includes origin.

    A predicate reached both through a view and from the caller survives as two edges.
    Counting the second against precision would punish saying the same true thing twice.
    """
    truth = _truth([_label(None)])
    report = score(truth, [_claim(None, line=1), _claim(None, line=7)])

    counts = report.cell(1, Flow.VALUE)
    assert (counts.true_positives, counts.false_positives) == (1, 0)


def test_a_duplicated_false_edge_is_still_only_one_false_positive() -> None:
    report = score(_truth([]), [_claim(None, line=1), _claim(None, line=7)])
    assert report.cell(1, Flow.VALUE).false_positives == 1


# --- the corpus uses it ---------------------------------------------------------------


def test_the_restored_edges_are_in_the_committed_keys() -> None:
    """Four packages could not state a fact under the v0 key. They can now."""
    expected = {
        "b1_03_loops": ("variable:V_TOTAL", "variable:V_TOTAL"),
        "b1_09_exception_handlers": ("variable:P_REGION", "relation:DIM_CUSTOMER"),
        "b1_02_if_case": ("column:DIM_CUSTOMER.CUST_ID", "relation:DIM_CUSTOMER"),
        "s7_unexercised_branch": ("column:DIM_CUSTOMER.REGION", "relation:DIM_CUSTOMER"),
    }
    for package, (source, target) in expected.items():
        truth = GroundTruth.load(GROUND_TRUTH / f"{package}.yaml")
        pairs = [e for e in truth.edges if str(e.source) == source and str(e.target) == target]
        assert len(pairs) >= 2, f"{package}: expected a guard-distinguished pair"
        guards = {e.guard for e in pairs}
        assert len(guards) == len(pairs), f"{package}: the pair must differ by guard, got {guards}"
