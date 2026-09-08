"""Boundaries, scored (ADR-0001 amendment 1d).

Boundaries carry the regulatory claim - "here is what we could not see, counted and named"
- and until 2026-09-09 nothing checked them. Two independent failures, and either alone
would have been enough to make the axis useless:

1. ``run_measurement`` counted declarations and never compared them to expectations, so a
   silently omitted boundary produced no symptom anywhere.
2. Had it compared them, it would have reported **all 18 expected as undeclared while 35
   were declared**, because the two sides describe the same fact in different prose. The
   key says "database link target out of coverage"; the analyser says "(relation not in
   dictionary)".

A boundary now carries a ``kind`` and a ``subject`` - the thing it is about - so the two
vocabularies become comparable. These tests pin that, and pin the failure modes that made
the axis silently inert, because an unchecked check is worse than no check: it reads as
coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.harness.labels import GroundTruth
from lineage.harness.scoring import score
from lineage.ir.model import BoundaryKind, Declared
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")
GROUND_TRUTH = Path("ground_truth")


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def _analyse(stem: str, dictionary: Dictionary):
    truth = GroundTruth.load(GROUND_TRUTH / f"{stem}.yaml")
    result = analyse_source(
        (CORPUS / truth.package).read_text(encoding="utf-8"),
        dictionary,
        AnalysisConfig.load(),
        None,
    )
    return truth, result


# --- the carrier ------------------------------------------------------------------------


def test_a_declared_boundary_is_still_the_sentence_it_was() -> None:
    """Fifty-odd consumers treat boundaries as strings; that must keep working."""
    boundary = Declared(
        "insert_statement@24: REPORTING.STG_CUSTOMER.CUST_ID (relation not in dictionary)",
        kind=BoundaryKind.DANGLING_REFERENCE,
        subject="reporting.stg_customer",
    )
    assert boundary.startswith("insert_statement@24")
    assert "not in dictionary" in boundary
    assert boundary == str(boundary)


def test_the_subject_is_case_insensitive() -> None:
    """The key writes `reporting.stg_customer`; the analyser resolves to upper case."""
    boundary = Declared("x", kind=BoundaryKind.DANGLING_REFERENCE, subject="reporting.x")
    assert boundary.subject == "REPORTING.X"


def test_prefixing_keeps_the_classification() -> None:
    """An f-string over a Declared silently returns a plain str and drops the structure.

    That is how this axis would quietly become unscoreable again, one interpolation at a
    time, so decoration goes through `prefixed` instead.
    """
    boundary = Declared("line 26: unreadable", kind=BoundaryKind.PARSE_FAILURE, subject="26")
    prefixed = boundary.prefixed("MY_UNIT: ", subject_prefix="MY_UNIT:")

    assert prefixed == "MY_UNIT: line 26: unreadable"
    assert prefixed.kind is BoundaryKind.PARSE_FAILURE
    assert prefixed.subject == "MY_UNIT:26"
    assert Declared.classified(f"{boundary}") is None  # the bug this method prevents


def test_an_unclassified_boundary_is_reported_as_such_not_guessed() -> None:
    assert Declared.classified("some boundary nobody classified") is None


# --- the analyser classifies everything it declares --------------------------------------


def test_every_boundary_the_corpus_produces_is_classified(dictionary: Dictionary) -> None:
    """An unclassified boundary can never satisfy an expectation, so it is a silent hole.

    Asserted over the whole corpus rather than per package: a new emission site that
    forgets its kind is exactly the regression this guards.
    """
    unclassified: list[str] = []
    for path in sorted(GROUND_TRUTH.glob("*.yaml")):
        _, result = _analyse(path.stem, dictionary)
        unclassified += [b for b in result.boundaries if Declared.classified(b) is None]

    assert unclassified == []


# --- the comparison ----------------------------------------------------------------------


def test_prose_and_structure_disagree_and_structure_is_right(dictionary: Dictionary) -> None:
    """The measurement that motivated the change, kept as a test.

    b2_06 expects a DB link and an ungranted schema. It declares both - and the two sides
    share not one word of prose, so exact string comparison calls both undeclared.
    """
    truth, result = _analyse("b2_06_db_links", dictionary)
    report = score(truth, result.edges, result.boundaries)

    assert report.boundaries_undeclared == []
    assert len(report.boundaries_missed) == 2


def test_an_expectation_the_analyser_does_not_declare_is_reported(
    dictionary: Dictionary,
) -> None:
    """s2's context-dependent binding: expected for weeks, never declared, never noticed."""
    truth, result = _analyse("s2_schema_context", dictionary)
    report = score(truth, result.edges, result.boundaries)

    kinds = [kind for kind, _, _ in report.boundaries_undeclared]
    assert kinds == [BoundaryKind.CONTEXT_DEPENDENT_BINDING.value]


def test_the_ungranted_schema_is_declared_after_the_qualifier_fix(
    dictionary: Dictionary,
) -> None:
    """s2's whole reason for existing: a refused schema must not be absorbed silently."""
    truth, result = _analyse("s2_schema_context", dictionary)
    report = score(truth, result.edges, result.boundaries)

    assert ("dangling_reference", "REPORTING.STG_CUSTOMER") not in [
        (kind, subject) for kind, subject, _ in report.boundaries_undeclared
    ]
    assert any(
        Declared.classified(b) == (BoundaryKind.DANGLING_REFERENCE, "REPORTING.STG_CUSTOMER")
        for b in result.boundaries
    )


def test_the_corpus_wide_gap_is_four_and_they_are_named(dictionary: Dictionary) -> None:
    """The number this work exists to produce, pinned so it cannot drift unnoticed.

    Four expectations the analyser stays silent about. Each is a real gap, not a
    vocabulary mismatch, and each was invisible before the axis was scored at all.
    """
    undeclared: set[tuple[str, str]] = set()
    expected = 0
    for path in sorted(GROUND_TRUTH.glob("*.yaml")):
        truth, result = _analyse(path.stem, dictionary)
        report = score(truth, result.edges, result.boundaries)
        expected += len(report.boundaries_expected_structured)
        undeclared |= {(kind, subject) for kind, subject, _ in report.boundaries_undeclared}

    assert expected == 18
    assert undeclared == {
        ("dynamic_sql", "B2_DBMS_SQL"),
        ("suppressed_error", "B2_METADATA_DRIVEN_ETL:54"),
        ("context_dependent_binding", "S2_SCHEMA_CONTEXT:20"),
        ("source_unavailable", "U1_WRAPPED"),
    }
