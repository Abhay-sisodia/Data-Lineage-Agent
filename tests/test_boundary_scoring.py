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
from lineage.ir.model import Boundary, BoundaryKind, Node, NodeKind
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


def test_a_boundary_still_reads_as_the_sentence_it_was() -> None:
    """Reports print boundaries; the prose has to survive the promotion to a node."""
    boundary = Boundary(
        kind=BoundaryKind.DANGLING_REFERENCE,
        subject="reporting.stg_customer",
        detail="insert_statement@24: REPORTING.STG_CUSTOMER.CUST_ID (relation not in dictionary)",
    )
    assert str(boundary).startswith("insert_statement@24")
    assert "not in dictionary" in str(boundary)


def test_a_boundary_is_a_graph_endpoint() -> None:
    """The point of the promotion: lineage can terminate ON a boundary.

    ir-v0 item 1 - the concept carried the regulatory pitch while being unable to be one
    end of an edge, so "this column is fed by something we were never given" could not be
    stated in the graph at all.
    """
    boundary = Boundary(
        kind=BoundaryKind.DANGLING_REFERENCE,
        subject="remote_customer@crm_link",
        detail="out of coverage",
    )
    node = boundary.as_node()
    assert node.kind is NodeKind.BOUNDARY
    assert node.name == "DANGLING_REFERENCE:REMOTE_CUSTOMER@CRM_LINK"


def test_a_boundary_can_name_the_lineage_it_bounds() -> None:
    """The other half: queryable beside the thing whose knowledge stops."""
    bounded = Node(kind=NodeKind.RELATION, name="fct_revenue_part")
    boundary = Boundary(kind=BoundaryKind.DDL_SEMANTICS, subject="x", detail="y").attached_to(
        bounded
    )
    assert boundary.attaches_to == bounded
    # Never overwritten - the first caller that knows is the one that knows.
    assert boundary.attached_to(Node(kind=NodeKind.RELATION, name="other")).attaches_to == bounded


def test_the_subject_is_case_insensitive() -> None:
    """The key writes `reporting.stg_customer`; the analyser resolves to upper case."""
    boundary = Boundary(kind=BoundaryKind.DANGLING_REFERENCE, subject="reporting.x", detail="x")
    assert boundary.subject == "REPORTING.X"
    assert boundary.identity() == ("dangling_reference", "REPORTING.X")


def test_prefixing_keeps_the_classification() -> None:
    """A boundary raised deep in the analysis knows its line and not its unit.

    A bare line number is not an identity, so the caller that knows the unit qualifies the
    subject - and the kind must survive that.
    """
    boundary = Boundary(
        kind=BoundaryKind.PARSE_FAILURE, subject="26", detail="line 26: unreadable", line=26
    )
    prefixed = boundary.prefixed("MY_UNIT: ", subject_prefix="MY_UNIT:")

    assert str(prefixed) == "MY_UNIT: line 26: unreadable"
    assert prefixed.kind is BoundaryKind.PARSE_FAILURE
    assert prefixed.subject == "MY_UNIT:26"


# --- the analyser classifies everything it declares --------------------------------------


def test_every_boundary_the_corpus_produces_is_classified(dictionary: Dictionary) -> None:
    """An unclassified boundary can never satisfy an expectation, so it is a silent hole.

    Asserted over the whole corpus rather than per package: a new emission site that
    forgets its kind is exactly the regression this guards.
    """
    unclassified: list[str] = []
    for path in sorted(GROUND_TRUTH.glob("*.yaml")):
        _, result = _analyse(path.stem, dictionary)
        unclassified += [str(b) for b in result.boundaries if not isinstance(b, Boundary)]

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
        b.identity() == ("dangling_reference", "REPORTING.STG_CUSTOMER") for b in result.boundaries
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


# --- what the promotion to a node actually bought ----------------------------------------


def test_boundaries_name_the_relations_whose_knowledge_stops(dictionary: Dictionary) -> None:
    """`orphan_upstream` says nothing in scope writes this, and cannot say why.

    A staging table loaded by an unseen ingestion job and a table fed through a partition
    exchange the analyser declined to trace look identical from the edge set. Only a
    boundary that names the relation it bounds separates them - which is the difference
    between "we did not find a writer" and "we found where the writer went".

    This is the query a string could not answer, and the reason `Boundary` is a node.
    """
    from lineage.harness.coverage import build_coverage

    edges = []
    boundaries = []
    for path in sorted(GROUND_TRUTH.glob("*.yaml")):
        _, result = _analyse(path.stem, dictionary)
        edges += result.edges
        boundaries += result.boundaries

    coverage = build_coverage(edges, boundaries, dictionary)

    # The partition exchange (s4) and the shared scratch table (s3): both have a boundary
    # that names them, and both would otherwise be indistinguishable from a staging table.
    assert coverage.bounded_relations == ["FCT_REVENUE_PART", "TMP_RECENT"]
