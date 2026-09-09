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
from lineage.harness.labels import ExpectedBoundary, GroundTruth
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
    """The check itself, pinned against a synthetic expectation.

    It used to assert s2's context-dependent binding was undeclared - which was true, and
    is the gap that got built. Pointing this at a real package again would mean the test
    passes only while some gap remains open, and starts failing the moment the analyser
    improves. The mechanism is what needs pinning, not the estate's current shortfall.
    """
    truth, result = _analyse("s2_schema_context", dictionary)
    invented = truth.model_copy(
        update={
            "expected_boundaries": [
                ExpectedBoundary(
                    kind=BoundaryKind.DEPTH_CAP,
                    subject="NOTHING_DECLARES_THIS",
                    reason="a known unknown no analyser in this corpus produces",
                )
            ]
        }
    )
    report = score(invented, result.edges, result.boundaries)

    assert [(kind, subject) for kind, subject, _ in report.boundaries_undeclared] == [
        ("depth_cap", "NOTHING_DECLARES_THIS")
    ]


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


def test_no_expectation_is_left_undeclared(dictionary: Dictionary) -> None:
    """The gap is closed, and closing it went two ways.

    Scoring the axis produced four undeclared expectations. Two were real analyser gaps and
    were built: `WHEN OTHERS THEN NULL` (b2_04) and context-dependent binding (s2). **Two
    were errors in the expectations themselves**, withdrawn with the reason recorded in
    each key - b2_03 claimed no static route to a statement that constant propagation folds
    from four literals, and u1 duplicated a refusal it had already declared.

    Pinned at zero so a new expectation cannot be added without either the analyser
    learning to declare it or someone stating why it does not stand.
    """
    undeclared: set[tuple[str, str]] = set()
    expected = 0
    for path in sorted(GROUND_TRUTH.glob("*.yaml")):
        truth, result = _analyse(path.stem, dictionary)
        report = score(truth, result.edges, result.boundaries)
        expected += len(report.boundaries_expected_structured)
        undeclared |= {(kind, subject) for kind, subject, _ in report.boundaries_undeclared}

    assert expected == 16
    assert undeclared == set()


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


# --- the two gaps the scored axis exposed, now closed -------------------------------------


def test_when_others_then_null_is_declared(dictionary: Dictionary) -> None:
    """A handler that swallows everything makes absence of an edge stop being evidence.

    b2_04 enables mappings from a config table and catches every failure with `WHEN OTHERS
    THEN NULL`. A mapping that ran and was rejected looks exactly like one that never ran,
    so nothing below this handler can be read as "did not happen".
    """
    _, result = _analyse("b2_04_metadata_driven_etl", dictionary)

    assert ("suppressed_error", "B2_METADATA_DRIVEN_ETL:54") in {
        b.identity() for b in result.boundaries
    }


def test_a_bare_null_handler_is_matched_structurally_not_by_text(
    dictionary: Dictionary,
) -> None:
    """`WHEN OTHERS THEN NULL;` on one line and across three are the same fact."""
    source = """CREATE OR REPLACE PROCEDURE spread_out IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login FROM stg_customer;
EXCEPTION
    WHEN OTHERS
    THEN
        NULL;
END spread_out;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig.load(), None)

    assert any(b.kind is BoundaryKind.SUPPRESSED_ERROR for b in result.boundaries)


def test_a_handler_that_does_something_is_not_a_suppressed_error(
    dictionary: Dictionary,
) -> None:
    """The check must not fire on every exception handler, or it says nothing.

    b1_09's handler writes on the error path - that is lineage, not suppression, and
    flagging it would bury the real signal under one boundary per TRY block in the estate.
    """
    _, result = _analyse("b1_09_exception_handlers", dictionary)

    assert not [b for b in result.boundaries if b.kind is BoundaryKind.SUPPRESSED_ERROR]


def test_unqualified_names_declare_their_execution_schema_dependency(
    dictionary: Dictionary,
) -> None:
    """s2's surviving half: a correct analyser still owes the caller this condition.

    The lineage is right for the schema it was bound against and is a different answer
    under another. That is a property of the run, and a condition nobody states is a
    condition nobody knows about.
    """
    _, result = _analyse("s2_schema_context", dictionary)

    assert ("context_dependent_binding", "S2_SCHEMA_CONTEXT:18") in {
        b.identity() for b in result.boundaries
    }


def test_context_dependent_binding_is_declared_once_per_unit(dictionary: Dictionary) -> None:
    """Per reference would be accurate and unreadable.

    Same reasoning as fusion hazards being declared per relation: a coverage statement full
    of the same sentence is one nobody reads, and unreadable is indistinguishable from
    undeclared for the person who needed to know.
    """
    _, result = _analyse("b2_06_db_links", dictionary)

    contextual = [b for b in result.boundaries if b.kind is BoundaryKind.CONTEXT_DEPENDENT_BINDING]
    assert len(contextual) == len({b.subject for b in contextual})
