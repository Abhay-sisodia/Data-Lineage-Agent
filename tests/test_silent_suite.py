"""The silent-failure suite, scored end to end (T3.4).

The register's instruction for the whole corpus: *"Build the adversarial corpus around the
silent list, not the loud one. Loud failures announce themselves during testing. Silent
ones surface when a customer catches you."*

Eight packages, and each one is defined by a plausible WRONG answer rather than by a hard
right one. So this file asserts two things per case and neither is optional: the true edges
appear, and the wrong answer does not. A test that only checked the first would pass
against an analyser that emitted both.

**Every case becomes a permanent regression test the day it is written** (T3.4e). That is
the point of the file existing separately from the per-feature test modules: the defences
here were added by five different tasks across three weeks, and nothing else would notice
if one of them quietly stopped working.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.harness.labels import GroundTruth
from lineage.harness.scoring import score
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")
GROUND_TRUTH = Path("ground_truth")

SILENT = [
    "s1_synonym_redirect",
    "s2_schema_context",
    "s3_shared_temp_table",
    "s4_partition_exchange",
    "s5_positional_union",
    "s6_updatable_view",
    "s7_unexercised_branch",
    "s8_aggregation_semantics",
]

# Cases that do not yet score clean, with the reason. DECLARED, not skipped: a declared
# failure is an acceptable outcome for this suite and a silent one is not, so the list is
# asserted to be exactly this and a case leaving it must be removed here deliberately.
KNOWN_INCOMPLETE = {
    "s1_synonym_redirect": "the filter edge through the synonym is not emitted - the "
    "value edges resolve correctly, so the synonym itself is defended",
    "s8_aggregation_semantics": "the GROUP BY key is not emitted as a filter influence; "
    "the aggregation transform class, which is what this case tests, is correct",
}


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def _score(stem: str, dictionary: Dictionary):
    truth = GroundTruth.load(GROUND_TRUTH / f"{stem}.yaml")
    truth.verify_against(CORPUS)
    result = analyse_source(
        (CORPUS / truth.package).read_text(encoding="utf-8"), dictionary, AnalysisConfig()
    )
    return truth, result, score(truth, result.edges, result.boundaries)


# --- T3.4a: all eight are scored, and nothing is quietly unscoreable -------------------


@pytest.mark.parametrize("stem", SILENT)
def test_every_silent_case_is_scored(stem: str, dictionary: Dictionary) -> None:
    """The suite's first job is to exist. A package with no key is invisible to every
    metric, which is how a silent failure stays silent inside the tool built to find it."""
    truth, _, report = _score(stem, dictionary)

    assert truth.edges, f"{stem} has no labelled edges"
    assert report.cells, f"{stem} scored nothing at all"


@pytest.mark.parametrize("stem", SILENT)
def test_no_silent_case_produces_its_named_wrong_answer(stem: str, dictionary: Dictionary) -> None:
    """The forbidden edge is the whole definition of a silent failure.

    Scored by PRESENCE, not by absence: an analyser can score well on recall and still emit
    the exact edge the package exists to trap.
    """
    _, _, report = _score(stem, dictionary)
    assert report.forbidden_violations == []


@pytest.mark.parametrize("stem", SILENT)
def test_each_case_either_passes_or_is_declared(stem: str, dictionary: Dictionary) -> None:
    """A declared failure is acceptable here. A silent one is not.

    This is the assertion that keeps `KNOWN_INCOMPLETE` honest in both directions: a case
    that regresses fails, and a case that starts passing also fails until someone removes
    it from the list and says so.
    """
    _, _, report = _score(stem, dictionary)
    clean = not report.missed_edges and not report.spurious_edges

    if stem in KNOWN_INCOMPLETE:
        assert not clean, f"{stem} now scores clean - remove it from KNOWN_INCOMPLETE"
    else:
        assert clean, f"{stem}: missed={report.missed_edges} spurious={report.spurious_edges}"


# --- s4: DDL that moves data (T3.4b) --------------------------------------------------


def test_partition_exchange_is_lineage_bearing(dictionary: Dictionary) -> None:
    """`fct_revenue_part` acquires its entire contents with no INSERT anywhere.

    "No writer" is worse than a missing edge: it reads as a positive finding, and an
    auditor asking who populates this table gets a silence that looks like an answer.
    """
    _, result, _ = _score("s4_partition_exchange", dictionary)
    pairs = {(str(e.source), str(e.target)) for e in result.edges}

    assert (
        "column:FCT_REVENUE_STAGE.NET_AMOUNT",
        "column:FCT_REVENUE_PART.NET_AMOUNT",
    ) in pairs
    assert len([e for e in result.edges if "FCT_REVENUE_PART" in str(e.target)]) == 4


def test_the_exchange_is_band_2_not_band_0(dictionary: Dictionary) -> None:
    """It is DDL, and in this corpus it is also inside EXECUTE IMMEDIATE.

    Crediting band 0 with it would let that band's score claim work it did not do
    (ADR-0001 §6): the hardest construct on the path is dynamic DDL.
    """
    _, result, _ = _score("s4_partition_exchange", dictionary)
    exchange = [e for e in result.edges if "FCT_REVENUE_PART" in str(e.target)]

    assert exchange
    assert all(e.band == 2 for e in exchange)


def test_the_exchange_does_not_compose_the_two_hops(dictionary: Dictionary) -> None:
    """`stg_orders.gross_amount -> fct_revenue_part.net_amount` is arguably true end to
    end, and is forbidden here.

    Emitting it directly skips the exchange and so reports the wrong mechanism, the wrong
    origin and the wrong evidence tier for a hop no INSERT performed. Composition is a
    query-time operation over hops that each stand on their own evidence.
    """
    _, result, _ = _score("s4_partition_exchange", dictionary)
    pairs = {(str(e.source), str(e.target)) for e in result.edges}

    assert (
        "column:STG_ORDERS.GROSS_AMOUNT",
        "column:FCT_REVENUE_PART.NET_AMOUNT",
    ) not in pairs


def test_the_reverse_direction_of_the_swap_is_declared(dictionary: Dictionary) -> None:
    """An exchange swaps segments BOTH ways.

    Here the partition was empty so the reverse carries nothing, and emitting it would be
    inventing rows. On a non-empty partition it is a real flow, so it is declared rather
    than forgotten.
    """
    _, result, _ = _score("s4_partition_exchange", dictionary)
    assert any("swaps segments both ways" in b for b in map(str, result.boundaries))


def test_a_shape_mismatch_refuses_rather_than_zipping(dictionary: Dictionary) -> None:
    """Oracle rejects an exchange between mismatched shapes, so a mismatch here means the
    DICTIONARY is stale - and zipping two different shapes produces a full set of
    confident, positional, wrong edges."""
    from lineage.analysis.ddl import exchange_partition_edges
    from lineage.ir.model import Origin

    edges, notes = exchange_partition_edges(
        "ALTER TABLE fct_revenue_part EXCHANGE PARTITION p_2026 WITH TABLE tmp_recent",
        Origin(unit="X", line=1),
        dictionary,
    )

    assert edges == []
    assert notes and "cannot be bound positionally" in str(notes[0])


# --- s6: updatable views and INSTEAD OF triggers (T3.4c) -------------------------------


def test_an_ordinary_update_through_a_view_lands_on_the_base_table(
    dictionary: Dictionary,
) -> None:
    """The last place s6 was still open.

    Band 0 inlines view text and the trigger analyser rewrites `:NEW`, but a plain
    `UPDATE v_customer_editable ... WHERE region = 'EU'` went through neither and kept the
    view on both ends of its filter edge - naming an object that stores nothing.
    """
    _, result, _ = _score("s6_updatable_view", dictionary)
    pairs = {(str(e.source), str(e.target)) for e in result.edges}

    assert ("column:DIM_CUSTOMER.REGION", "relation:DIM_CUSTOMER") in pairs
    assert not [e for e in result.edges if "V_CUSTOMER_EDITABLE" in str(e.source)]
    assert not [e for e in result.edges if "V_CUSTOMER_EDITABLE" in str(e.target)]


def test_the_join_view_declares_what_a_per_column_mapping_cannot_carry(
    dictionary: Dictionary,
) -> None:
    """Column by column the mapping is exact; the ROW correspondence is the join predicate.

    Resolving each column independently is correct and incomplete, and the incompleteness
    is stated rather than assumed away.
    """
    _, result, _ = _score("s6_updatable_view", dictionary)
    assert any("row correspondence" in b for b in map(str, result.boundaries))


def test_the_view_resolver_is_idempotent(dictionary: Dictionary) -> None:
    """It runs over edges band 0 has already inlined views out of.

    A resolution rule that changed its answer on a second pass would make the edge set
    depend on how many analysers happened to touch it.
    """
    from lineage.resolution.views import resolve_views

    _, result, _ = _score("s6_updatable_view", dictionary)
    once, _ = resolve_views(result.edges, dictionary)
    twice, _ = resolve_views(once, dictionary)

    assert [e.identity() for e in once] == [e.identity() for e in twice]


# --- the three that were already defended, re-checked ---------------------------------


def test_s1_does_not_redirect_through_the_synonym(dictionary: Dictionary) -> None:
    """`customer_target` is a synonym. Bind it by name and the edge lands on an object
    that is not the one written."""
    _, result, _ = _score("s1_synonym_redirect", dictionary)
    assert not [e for e in result.edges if "CUSTOMER_TARGET" in str(e.target)]


def test_s3_fuses_nothing_across_units(dictionary: Dictionary) -> None:
    """Forty procedures write `tmp_recent`. Composing through it invents lineage."""
    _, result, _ = _score("s3_shared_temp_table", dictionary)
    pairs = {(str(e.source), str(e.target)) for e in result.edges}

    assert ("column:STG_ORDERS.CUST_ID", "relation:DIM_CUSTOMER") not in pairs
    assert ("column:STG_CUSTOMER.CUST_ID", "column:FCT_REVENUE.CUST_ID") not in pairs


def test_s5_binds_union_arms_positionally(dictionary: Dictionary) -> None:
    """A UNION binds by position, not by name, and the arms here are deliberately
    misordered relative to their names."""
    _, _, report = _score("s5_positional_union", dictionary)
    assert not report.missed_edges
    assert not report.spurious_edges


def test_s8_separates_three_edges_that_share_every_endpoint(dictionary: Dictionary) -> None:
    """THE ONE PLACE IN THE CORPUS WHERE TRANSFORM ALONE CARRIES EDGE IDENTITY.

    Three procedures write `fct_revenue.net_amount` from `stg_orders.gross_amount`: a copy,
    a `SUM`, and a `CASE`. Source, target and flow are character for character identical
    across all three, so the transform class is the entire difference between them - and
    under ADR-0001 §4 getting it wrong is a MISS, not a near miss.

    Observation could not settle this one. The seed data puts one row in every GROUP BY
    group, so `SUM(x) = x` and the identity and aggregated runs are indistinguishable in a
    before/after diff. That is a limit of the referee, recorded in the key.
    """
    _, result, _ = _score("s8_aggregation_semantics", dictionary)
    classes = {
        e.transform.value
        for e in result.edges
        if (str(e.source), str(e.target))
        == ("column:STG_ORDERS.GROSS_AMOUNT", "column:FCT_REVENUE.NET_AMOUNT")
        and e.flow.value == "value"
    }

    assert classes == {"identity", "aggregated", "conditional"}
