"""Forbidden edges: the wrong answer a package was written to elicit (T3.0).

Recall punishes a missing true edge. Nothing punished the PRESENCE of the specific false
one - it landed as an anonymous false positive among others, and a regression that
reintroduced a silent failure moved a decimal rather than naming itself.

These tests pin the mechanism, and pin that the corpus actually uses it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lineage.harness.labels import Evidence, Flow, ForbiddenEdge, GroundTruth, LabelledEdge
from lineage.harness.scoring import score
from lineage.ir.model import IREdge, Mechanism, Node, NodeKind, Origin, Transform

GROUND_TRUTH = Path("ground_truth")
CORPUS = Path("corpus")


def _node(name: str, kind: NodeKind = NodeKind.COLUMN) -> Node:
    return Node(kind=kind, name=name)


def _truth(**overrides) -> GroundTruth:
    base = {
        "package": "adversarial/band0/b0_01_insert_select.sql",
        "source_sha256": "4fed27906d34830c7ca5324ad19fc1108bd5d2b7d361f03cdcd6650723474f00",
        "labelled_by": "test",
        "labelled_at": "2026-09-06",
        "edges": [],
    }
    return GroundTruth.model_validate(base | overrides)


def _predicted(source: str, target: str, transform: Transform = Transform.IDENTITY) -> IREdge:
    return IREdge(
        source=_node(source),
        target=_node(target),
        flow=Flow.VALUE,
        transform=transform,
        band=0,
        mechanism=Mechanism.AST,
        origin=Origin(unit="T", line=1),
    )


# --- the mechanism -------------------------------------------------------------------


def test_a_forbidden_edge_is_reported_by_name_with_its_reason() -> None:
    """The point of the feature: not a number moving, a named defect."""
    truth = _truth(
        forbidden=[
            ForbiddenEdge(
                source=_node("STG_ORDERS.ORDER_DATE"),
                target=_node("TMP_RECENT.CUST_ID"),
                reason="the name-matched UNION binding",
            )
        ]
    )
    report = score(truth, [_predicted("STG_ORDERS.ORDER_DATE", "TMP_RECENT.CUST_ID")])

    assert len(report.forbidden_violations) == 1
    _, reason = report.forbidden_violations[0]
    assert "name-matched" in reason


def test_omitting_transform_forbids_every_transform_class() -> None:
    """A wrong binding is wrong however it was computed.

    Requiring the label to guess the transform class would let the exact same false edge
    slip through by arriving as `derived` instead of `identity`.
    """
    truth = _truth(
        forbidden=[
            ForbiddenEdge(
                source=_node("A.X"), target=_node("B.Y"), reason="wrong under any transform"
            )
        ]
    )
    for transform in (Transform.IDENTITY, Transform.DERIVED, Transform.AGGREGATED):
        report = score(truth, [_predicted("A.X", "B.Y", transform)])
        assert report.forbidden_violations, transform


def test_naming_a_transform_narrows_the_rule() -> None:
    truth = _truth(
        forbidden=[
            ForbiddenEdge(
                source=_node("A.X"),
                target=_node("B.Y"),
                transform=Transform.AGGREGATED,
                reason="only the aggregated reading is wrong",
            )
        ]
    )
    assert not score(truth, [_predicted("A.X", "B.Y", Transform.IDENTITY)]).forbidden_violations
    assert score(truth, [_predicted("A.X", "B.Y", Transform.AGGREGATED)]).forbidden_violations


def test_a_clean_run_reports_nothing() -> None:
    truth = _truth(
        forbidden=[ForbiddenEdge(source=_node("A.X"), target=_node("B.Y"), reason="nope")]
    )
    assert score(truth, [_predicted("C.X", "D.Y")]).forbidden_violations == []


def test_a_key_cannot_both_require_and_forbid_the_same_edge() -> None:
    """Cheap to write by accident, and it makes a package unscoreable with no symptom."""
    with pytest.raises(ValueError, match="both required and forbidden"):
        _truth(
            edges=[
                LabelledEdge(
                    source=_node("A.X"),
                    target=_node("B.Y"),
                    band=0,
                    origin=Origin(unit="T", line=1),
                    evidence=Evidence.SOURCE_READ,
                )
            ],
            forbidden=[
                ForbiddenEdge(source=_node("A.X"), target=_node("B.Y"), reason="contradiction")
            ],
        )


# --- the corpus actually uses it ------------------------------------------------------


def _all_truths() -> list[tuple[str, GroundTruth]]:
    return [(p.stem, GroundTruth.load(p)) for p in sorted(GROUND_TRUTH.glob("*.yaml"))]


SILENT = [
    "s1_synonym_redirect",
    "s3_shared_temp_table",
    "s4_partition_exchange",
    "s5_positional_union",
    "s6_updatable_view",
    "s7_unexercised_branch",
    "s8_aggregation_semantics",
]


@pytest.mark.parametrize("name", SILENT)
def test_every_silent_failure_names_its_wrong_answer(name: str) -> None:
    """A silent-failure package is DEFINED by the mistake it invites.

    s2 is deliberately absent: its wrong answer has the same match key as its right one,
    so it cannot be expressed here. That gap is recorded in the key itself.
    """
    truth = GroundTruth.load(GROUND_TRUTH / f"{name}.yaml")
    assert truth.forbidden, f"{name} has no forbidden edge"
    for rule in truth.forbidden:
        assert len(rule.reason.split()) >= 8, f"{name}: a reason must explain, not label"


def test_no_forbidden_rule_contradicts_its_own_key() -> None:
    """The validator enforces this on load; this proves every committed key passes it."""
    for name, truth in _all_truths():
        for rule in truth.forbidden:
            for edge in truth.edges:
                assert not rule.matches(edge.key()), f"{name}: {edge.key()}"


def test_every_package_in_the_corpus_has_a_label_set() -> None:
    """T3.0's closing condition. Every .sql under adversarial/ is scored or it is invisible."""
    packages = {p.relative_to(CORPUS).as_posix() for p in (CORPUS / "adversarial").rglob("*.sql")}
    labelled = {truth.package for _, truth in _all_truths()}
    assert packages - labelled == set()


def test_every_label_set_hash_checks_against_its_source() -> None:
    for _name, truth in _all_truths():
        truth.verify_against(CORPUS)


def test_labels_are_valid_yaml_with_no_duplicate_keys() -> None:
    """Guards against a silently dropped edge - YAML keeps the last of a duplicated key."""
    for path in sorted(GROUND_TRUTH.glob("*.yaml")):
        assert yaml.safe_load(path.read_text(encoding="utf-8")) is not None, path
