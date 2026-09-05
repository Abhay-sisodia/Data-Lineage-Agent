"""Band 0 analyser, scored against ground truth (T1.5).

The T1.5 gate: band-0 precision 100% and recall >= 95% on the labelled packages, with
each of the five constructs passing its own adversarial case.

These run on every build. Band 0 is the baseline the whole spike rests on - if it
regresses, nothing measured above it means anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.band0 import analyse_source
from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow, GroundTruth
from lineage.harness.scoring import ScoreReport, score
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")
GROUND_TRUTH = Path("ground_truth")

# construct -> (package, ground truth)
BAND0_CASES = {
    "insert_select": "b0_01_insert_select",
    "merge": "b0_02_merge",
    "select_star": "b0_03_select_star",
    "nested_views": "b0_04_nested_views",
    "alias_chains": "b0_05_alias_chains",
}


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def _score_case(name: str, dictionary: Dictionary) -> ScoreReport:
    truth = GroundTruth.load(GROUND_TRUTH / f"{name}.yaml")
    truth.verify_against(CORPUS)
    source = (CORPUS / truth.package).read_text(encoding="utf-8")
    result = analyse_source(source, dictionary, AnalysisConfig())
    return score(truth, result.edges, result.boundaries)


@pytest.mark.parametrize("construct", sorted(BAND0_CASES))
def test_band0_construct_is_perfectly_precise(construct: str, dictionary: Dictionary) -> None:
    """No false edges in band 0.

    A false edge inside a filing is far worse than a missing one, so precision is the
    number that must be perfect here.
    """
    report = _score_case(BAND0_CASES[construct], dictionary)
    counts = report.cell(0, Flow.VALUE)
    assert counts.false_positives == 0, f"invented edges: {report.spurious_edges}"
    if counts.true_positives:
        assert counts.precision == 1.0


@pytest.mark.parametrize("construct", sorted(BAND0_CASES))
def test_band0_construct_recall(construct: str, dictionary: Dictionary) -> None:
    """Every band-0 value edge in the answer key must be found."""
    report = _score_case(BAND0_CASES[construct], dictionary)
    counts = report.cell(0, Flow.VALUE)
    assert counts.recall == 1.0, f"missed: {report.missed_edges}"


def test_band0_aggregate_meets_the_gate(dictionary: Dictionary) -> None:
    """The T1.5 gate across all labelled band-0 packages at once."""
    true_positives = false_positives = false_negatives = 0
    for name in BAND0_CASES.values():
        counts = _score_case(name, dictionary).cell(0, Flow.VALUE)
        true_positives += counts.true_positives
        false_positives += counts.false_positives
        false_negatives += counts.false_negatives

    precision = true_positives / (true_positives + false_positives)
    recall = true_positives / (true_positives + false_negatives)

    assert precision == 1.0, "band-0 precision must be perfect"
    assert recall >= 0.95, f"band-0 recall {recall:.1%} below the 95% gate"


def test_transform_class_is_derived_not_assumed(dictionary: Dictionary) -> None:
    """SUM over a subtraction is AGGREGATED, and TRUNC of a date is DERIVED.

    Getting the endpoints right and the class wrong is a miss (ADR-0001 4), so the
    strongest transform on the path has to win.
    """
    report = _score_case("b0_05_alias_chains", dictionary)
    assert report.cell(0, Flow.VALUE).false_positives == 0
    assert report.cell(0, Flow.VALUE).recall == 1.0


def test_filter_edges_are_found_inside_views(dictionary: Dictionary) -> None:
    """A predicate hidden inside a view still decides which rows land."""
    report = _score_case("b0_03_select_star", dictionary)
    assert report.cell(0, Flow.FILTER).recall == 1.0


def test_lineage_reaches_base_tables_through_three_views(dictionary: Dictionary) -> None:
    """Reporting the outermost view as the source is true and useless."""
    truth = GroundTruth.load(GROUND_TRUTH / "b0_04_nested_views.yaml")
    source = (CORPUS / truth.package).read_text(encoding="utf-8")
    result = analyse_source(source, Dictionary.load(CORPUS / "dictionary.json"), AnalysisConfig())

    sources = {edge.source.name for edge in result.edges}
    assert any(name.startswith("STG_CUSTOMER.") for name in sources)
    assert not any(name.startswith("V_CUST_L") for name in sources), (
        "lineage stopped at a view instead of resolving to the base table"
    )


def test_parse_coverage_is_reported(dictionary: Dictionary) -> None:
    """Refusals are acceptable; silence is not.

    An analyser that produces nothing for a statement must be distinguishable from one
    that correctly found nothing.
    """
    source = (CORPUS / "adversarial/band0/b0_01_insert_select.sql").read_text(encoding="utf-8")
    result = analyse_source(source, dictionary, AnalysisConfig())
    assert result.statements_seen == 1
    assert result.parse_coverage == 1.0


def test_unsupported_statement_is_refused_not_guessed() -> None:
    """The abstention path: no edges, and a stated reason."""
    dictionary = Dictionary.load(CORPUS / "dictionary.json")
    source = """
    CREATE OR REPLACE PROCEDURE t_refuse IS
    BEGIN
      INSERT INTO tmp_recent (cust_id, last_login) VALUES (1, SYSDATE);
    END;
    """
    result = analyse_source(source, dictionary, AnalysisConfig())
    assert result.edges == []
    assert len(result.refusals) == 1
    assert result.parse_coverage == 0.0
