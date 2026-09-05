"""Label format invariants (T1.1).

These tests pin the six decisions in ADR-0001. If one of them is ever loosened by
accident, a test fails rather than the precision figure quietly changing meaning.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from lineage.harness.labels import (
    Evidence,
    Flow,
    GroundTruth,
    LabelledEdge,
    Node,
    NodeKind,
    Origin,
    Transform,
)


def _edge(**overrides: Any) -> LabelledEdge:
    payload: dict[str, Any] = {
        "source": Node(kind=NodeKind.COLUMN, name="REF_POLICY.WINDOW_DAYS"),
        "target": Node(kind=NodeKind.VARIABLE, name="V_DAYS"),
        "flow": Flow.VALUE,
        "transform": Transform.IDENTITY,
        "band": 1,
        "origin": Origin(unit="B1_LOCAL_VARIABLES", line=17),
        "evidence": Evidence.SOURCE_READ,
    }
    payload.update(overrides)
    return LabelledEdge.model_validate(payload)


def test_identifiers_are_case_normalised() -> None:
    """Oracle identifiers are case-insensitive; comparing raw case would produce
    mismatches that say nothing about the analyser."""
    assert Node(kind=NodeKind.COLUMN, name="ref_policy.window_days").name == (
        "REF_POLICY.WINDOW_DAYS"
    )


def test_transform_is_part_of_edge_identity() -> None:
    """ADR-0001 §4 - right endpoints, wrong transform class is a MISS.

    This is the s8 silent failure: SUM(x) and a copy of x share endpoints and mean
    entirely different things.
    """
    identity = _edge(transform=Transform.IDENTITY)
    aggregated = _edge(transform=Transform.AGGREGATED)
    assert identity.key() != aggregated.key()


def test_flow_is_part_of_edge_identity() -> None:
    """ADR-0001 §2 - a filter influence is a different claim from a value copy."""
    assert _edge(flow=Flow.VALUE).key() != _edge(flow=Flow.FILTER).key()


def test_guard_is_not_part_of_edge_identity() -> None:
    """ADR-0001 §5 - guards are measured separately, not inside precision.

    Otherwise the gate number would move on string-comparison noise.
    """
    assert _edge(guard=None).key() == _edge(guard="P_REGION = 'EU'").key()


def test_band_and_origin_are_not_part_of_edge_identity() -> None:
    """Carried and reported, but they do not decide whether an edge matches."""
    assert _edge(band=0).key() == _edge(band=2).key()
    assert _edge(origin=Origin(unit="A", line=1)).key() == (
        _edge(origin=Origin(unit="B", line=99)).key()
    )


def test_variables_are_first_class_endpoints() -> None:
    """ADR-0001 §3 - a def-use chain is scored hop by hop, not collapsed."""
    edge = _edge(target=Node(kind=NodeKind.VARIABLE, name="V_CUTOFF"))
    assert edge.target.kind is NodeKind.VARIABLE
    assert str(edge.target) == "variable:V_CUTOFF"


def test_duplicate_edges_are_rejected(tmp_path: Path) -> None:
    """Two identical edges would inflate recall against a padded key."""
    with pytest.raises(ValidationError, match="duplicate edges"):
        GroundTruth.model_validate(
            {
                "package": "adversarial/band1/x.sql",
                "source_sha256": "0" * 64,
                "labelled_by": "test",
                "labelled_at": "2026-09-06",
                "edges": [_edge().model_dump(), _edge().model_dump()],
            }
        )


def test_unknown_fields_are_rejected() -> None:
    """A mistyped field that silently does nothing is worse than an error."""
    with pytest.raises(ValidationError):
        _edge(evidnce=Evidence.OBSERVED)


def test_source_hash_mismatch_is_detected(tmp_path: Path) -> None:
    """A label set that no longer matches the code it describes is worse than none.

    Drift here would silently invalidate every score computed afterwards.
    """
    corpus = tmp_path / "corpus"
    package = corpus / "adversarial" / "band1"
    package.mkdir(parents=True)
    source = package / "x.sql"
    source.write_text("CREATE PROCEDURE x IS BEGIN NULL; END;", encoding="utf-8")

    truth = GroundTruth.model_validate(
        {
            "package": "adversarial/band1/x.sql",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "labelled_by": "test",
            "labelled_at": "2026-09-06",
            "edges": [],
        }
    )
    truth.verify_against(corpus)  # matches - no raise

    source.write_text("CREATE PROCEDURE x IS BEGIN COMMIT; END;", encoding="utf-8")
    with pytest.raises(ValueError, match="has changed since labelling"):
        truth.verify_against(corpus)


def test_evidence_split_is_reportable() -> None:
    """Every score is published alongside the provenance of the labels behind it."""
    truth = GroundTruth.model_validate(
        {
            "package": "adversarial/band1/x.sql",
            "source_sha256": "0" * 64,
            "labelled_by": "test",
            "labelled_at": "2026-09-06",
            "edges": [
                _edge(evidence=Evidence.OBSERVED).model_dump(),
                _edge(
                    evidence=Evidence.SOURCE_READ,
                    target=Node(kind=NodeKind.VARIABLE, name="V_CUTOFF"),
                ).model_dump(),
            ],
        }
    )
    assert truth.evidence_split() == {"observed": 1, "source_read": 1}


def test_round_trips_through_yaml(tmp_path: Path) -> None:
    """Labels are hand-edited YAML, so they must survive a write/read cycle."""
    truth = GroundTruth.model_validate(
        {
            "package": "adversarial/band1/x.sql",
            "source_sha256": "0" * 64,
            "labelled_by": "test",
            "labelled_at": "2026-09-06",
            "edges": [_edge().model_dump()],
            "boundaries": ["remote_customer@crm_link"],
        }
    )
    path = tmp_path / "truth.yaml"
    path.write_text(yaml.safe_dump(truth.model_dump(mode="json")), encoding="utf-8")
    assert GroundTruth.load(path) == truth
