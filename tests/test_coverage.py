"""The coverage statement (T3.6d, T3.6e).

Precision and recall answer *"of the things we claimed, how many were right"*. They are
structurally silent about *"what was never in front of us at all"* - a corpus can score
100% on both while a third of the estate sits outside the parser's reach.

Every test here is about a **gap that could be mistaken for a finding**. That is the only
kind worth counting separately: every other gap already shows up as a miss.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.harness.coverage import (
    WEAK_EVIDENCE_CEILING,
    build_coverage,
    render,
)
from lineage.ir.model import Flow, IREdge, Mechanism, Node, NodeKind, Origin, Tier
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def _edge(source: str, target: str, *, flow: Flow = Flow.VALUE, tier: Tier = Tier.A) -> IREdge:
    def node(name: str) -> Node:
        kind = NodeKind.COLUMN if "." in name else NodeKind.RELATION
        return Node(kind=kind, name=name)

    return IREdge(
        source=node(source),
        target=node(target),
        flow=flow,
        band=0,
        mechanism=Mechanism.AST,
        tier=tier,
        origin=Origin(unit="U", line=1),
    )


# --- the three signals ----------------------------------------------------------------


def test_a_dangling_reference_is_counted(dictionary: Dictionary) -> None:
    """An object the code names that we were never given.

    `remote_customer@crm_link` is real, load-bearing and unreadable from here. The failure
    this prevents is a report that simply omits it, leaving a reader to assume the upstream
    was checked.
    """
    coverage = build_coverage(
        [],
        ["insert_statement@20: REMOTE_CUSTOMER@CRM_LINK.CUST_ID (relation not in dictionary)"],
        dictionary,
    )

    assert coverage.dangling_references == ["REMOTE_CUSTOMER@CRM_LINK"]


def test_a_table_read_but_never_written_is_an_orphan_upstream(dictionary: Dictionary) -> None:
    """`s4` counted rather than described: "who populates this?" answered with silence.

    Silence reads as an answer, which is what makes it worse than a missing edge.
    """
    coverage = build_coverage([_edge("STG_ORDERS.CUST_ID", "FCT_REVENUE.CUST_ID")], [], dictionary)

    assert "STG_ORDERS" in coverage.orphan_upstream
    assert "FCT_REVENUE" not in coverage.orphan_upstream


def test_a_filter_edge_does_not_count_as_a_writer(dictionary: Dictionary) -> None:
    """A filter names the relation whose rows were selected, not a write.

    Counting it would report every table anyone read from as having a writer and quietly
    empty this entire section - the section passing while being useless.
    """
    coverage = build_coverage(
        [_edge("STG_ORDERS.CUST_ID", "FCT_REVENUE", flow=Flow.FILTER)], [], dictionary
    )

    assert "FCT_REVENUE" in coverage.orphan_upstream


def test_a_table_in_no_edge_at_all_is_reported_separately(dictionary: Dictionary) -> None:
    """The gap nobody notices.

    Nothing in a report about tables A and B suggests that table C was never looked at, so
    a trace-shaped output cannot surface this and a map-shaped one must.
    """
    coverage = build_coverage([_edge("STG_ORDERS.CUST_ID", "FCT_REVENUE.CUST_ID")], [], dictionary)

    assert "DIM_CUSTOMER" in coverage.untouched_relations
    assert "STG_ORDERS" not in coverage.untouched_relations


def test_the_three_signals_do_not_double_count(dictionary: Dictionary) -> None:
    """One gap under two headings doubles its apparent size.

    An earlier draft reported "no writer in scope" and "orphan upstream" as separate lists
    when every member of the second was a member of the first.
    """
    coverage = build_coverage(
        [_edge("STG_ORDERS.CUST_ID", "FCT_REVENUE.CUST_ID")],
        ["x: REMOTE_CUSTOMER@CRM_LINK.CUST_ID (relation not in dictionary)"],
        dictionary,
    )

    assert not set(coverage.orphan_upstream) & set(coverage.untouched_relations)
    assert not set(coverage.dangling_references) & set(coverage.orphan_upstream)


def test_views_are_never_reported_as_a_gap(dictionary: Dictionary) -> None:
    """A view is never written and its upstream is its own definition.

    A view absent from every edge was resolved THROUGH, deliberately (T3.4c). Listing it
    as a gap fills the statement with noise, and a coverage statement full of false gaps is
    one nobody reads - the same argument that excluded triggers from the fusion hazards.
    """
    coverage = build_coverage([], [], dictionary)

    assert not [name for name in coverage.orphan_upstream if name.startswith("V_")]
    assert not [name for name in coverage.untouched_relations if name.startswith("V_")]
    assert coverage.views_resolved_through == 5


# --- the evidence story (T3.6c) --------------------------------------------------------


def test_the_evidence_story_holds_when_edges_rest_on_parsing(dictionary: Dictionary) -> None:
    coverage = build_coverage(
        [_edge("STG_ORDERS.CUST_ID", "FCT_REVENUE.CUST_ID", tier=Tier.A)], [], dictionary
    )

    assert coverage.weak_evidence_share == 0.0
    assert coverage.evidence_story_holds


def test_a_run_built_on_inference_is_flagged(dictionary: Dictionary) -> None:
    """The register's own line: if 70% of edges land in Tier C or D, the lineage
    technically works and the evidence story does not.

    Not a percentage to optimise - below the ceiling the tool produces evidence, above it
    the tool produces opinions with a schema.
    """
    edges = [
        _edge(f"STG_ORDERS.C{index}", f"FCT_REVENUE.C{index}", tier=Tier.D) for index in range(8)
    ]
    edges += [_edge("STG_ORDERS.X", "FCT_REVENUE.X", tier=Tier.A)]
    coverage = build_coverage(edges, [], dictionary)

    assert coverage.weak_evidence_share is not None
    assert coverage.weak_evidence_share > WEAK_EVIDENCE_CEILING
    assert not coverage.evidence_story_holds
    assert "FLAGGED" in render(coverage)


# --- generated, not written (T3.6e) ----------------------------------------------------


def test_the_statement_is_generated_and_deterministic(dictionary: Dictionary) -> None:
    """Compared between runs, so a diff full of reordering is a diff nobody reads."""
    edges = [_edge("STG_ORDERS.CUST_ID", "FCT_REVENUE.CUST_ID")]

    first = render(build_coverage(edges, [], dictionary, packages=3))
    second = render(build_coverage(list(reversed(edges)), [], dictionary, packages=3))

    assert first == second
    assert "generated from the run" in first


def test_unattributed_writers_are_declared_impossible_rather_than_reported_empty(
    dictionary: Dictionary,
) -> None:
    """A write whose unit cannot be named cannot arise: `Origin` is mandatory on every edge.

    Reporting an always-empty list would imply a check that never runs, which is a quieter
    version of the same dishonesty the whole coverage statement exists to prevent.
    """
    statement = render(build_coverage([], [], dictionary))

    assert "unattributed writers   n/a" in statement
    assert "Origin is mandatory" in statement


def test_the_statement_reports_execution_evidence_as_absence_not_as_denial(
    dictionary: Dictionary,
) -> None:
    """`unexercised is None` is 226 edges about which nothing is known.

    Rendered as "no execution evidence", never as a count of edges that did not run.
    """
    coverage = build_coverage([_edge("STG_ORDERS.CUST_ID", "FCT_REVENUE.CUST_ID")], [], dictionary)
    statement = render(coverage)

    assert coverage.no_execution_evidence == 1
    assert "not a claim that they never ran" in statement
