"""The committed ground-truth label sets must stay valid and in step with the corpus.

These run on every build. A label set that has drifted from the code it describes would
silently invalidate every score computed against it, with no symptom at all - which is
the worst failure mode this project has.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.harness.labels import Evidence, Flow, GroundTruth

GROUND_TRUTH_DIR = Path("ground_truth")
CORPUS = Path("corpus")

LABEL_FILES = sorted(GROUND_TRUTH_DIR.glob("*.yaml"))


def test_at_least_one_label_set_exists() -> None:
    assert LABEL_FILES, "no ground truth - the analyser cannot be scored against anything"


@pytest.mark.parametrize("path", LABEL_FILES, ids=lambda p: p.stem)
def test_label_set_is_valid(path: Path) -> None:
    truth = GroundTruth.load(path)
    assert truth.edges, f"{path.name} declares no edges"


@pytest.mark.parametrize("path", LABEL_FILES, ids=lambda p: p.stem)
def test_labels_match_the_source_they_describe(path: Path) -> None:
    """The hash check that stops labels and code drifting apart."""
    GroundTruth.load(path).verify_against(CORPUS)


@pytest.mark.parametrize("path", LABEL_FILES, ids=lambda p: p.stem)
def test_every_edge_records_its_provenance(path: Path) -> None:
    """Each label must say how it was established.

    A benchmark that cannot state its own provenance is not evidence, and 'observed'
    must never be claimed by default.
    """
    truth = GroundTruth.load(path)
    for edge in truth.edges:
        assert edge.evidence in set(Evidence), f"{edge.key()} has no usable evidence marker"


def test_b1_07_carries_the_package_state_filter_edge() -> None:
    """The edge this package exists to test.

    Package state written in one call governs which rows a later call loads. If this
    edge ever disappears from the answer key, the hardest band-1 construct is no longer
    being measured at all.
    """
    truth = GroundTruth.load(GROUND_TRUTH_DIR / "b1_07_package_variables.yaml")
    filters = {(str(edge.source), str(edge.target)) for edge in truth.by_flow(Flow.FILTER)}
    assert ("variable:PKG_POLICY_STATE.G_CUTOFF", "relation:TMP_RECENT") in filters


def test_b1_07_includes_the_trigger_side_effect() -> None:
    """Lineage that is real but absent from the package's own source.

    TRG_RECENT_AUDIT writes DIM_CUSTOMER.LIFETIME_VALUE on insert into TMP_RECENT.
    Observation found it; reading b1_07 alone never would. Omitting real lineage from an
    answer key is the silent hole this product exists to prevent.
    """
    truth = GroundTruth.load(GROUND_TRUTH_DIR / "b1_07_package_variables.yaml")
    origins = {edge.origin.unit for edge in truth.edges}
    assert "TRG_RECENT_AUDIT" in origins
