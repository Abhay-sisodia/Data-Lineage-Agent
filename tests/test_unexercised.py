"""`unexercised` as a first-class axis (T3.5).

The register: *"Not a tier — a separate axis. An edge can be Tier A (provably in the code)
and never observed running. That combination is itself a finding, and nobody else reports
it."*

The failure this prevents, in one sentence: the EU path ran 9,120 times and looks strong;
the APAC branch exists in the code, never ran in the window, and looks weak or absent.
**Demoting it by tier would say "we are unsure it is real". It is provably real - it has
simply never fired.**

Most of this file is about the THIRD state. Two states force a default, and both defaults
are lies: `False` claims every edge ran, `True` reports the estate as dead code. Absence of
a witness is not evidence of non-execution, and the type has to be able to say so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.evidence.witness import ExecutionWitness, StatementSighting
from lineage.harness.labels import GroundTruth
from lineage.harness.scoring import score
from lineage.ir.model import Flow, IREdge, Mechanism, Node, NodeKind, Origin, Tier
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")
S7 = CORPUS / "adversarial" / "silent" / "s7_unexercised_branch.sql"


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


@pytest.fixture(scope="module")
def eu_only() -> ExecutionWitness:
    """The witness s7 was designed around: run with `p_region = 'EU'` and nothing else.

    A TEST FIXTURE, NOT EVIDENCE. It stands in for `scripts/capture_witness.py` reading
    `V$SQL`, which needs a live database. Constructing one here proves the mechanism; it
    does not license any claim about what the corpus actually ran, and no witness for the
    corpus is committed for exactly that reason.
    """
    return ExecutionWitness(
        window="18 months to 2026-09-08",
        units=frozenset({"S7_UNEXERCISED_BRANCH"}),
        sightings=frozenset(
            {
                StatementSighting("S7_UNEXERCISED_BRANCH", 22),  # DBMS_APPLICATION_INFO
                StatementSighting("S7_UNEXERCISED_BRANCH", 26),  # the EU UPDATE
            }
        ),
        note="synthetic fixture",
    )


def _analyse(witness: ExecutionWitness | None, dictionary: Dictionary):
    return analyse_source(S7.read_text(encoding="utf-8"), dictionary, AnalysisConfig(), witness)


# --- the third state ------------------------------------------------------------------


def test_no_witness_means_no_claim_either_way(dictionary: Dictionary) -> None:
    """The default, and the one that has to be right.

    An analyser run against source alone knows nothing about execution. Saying `False`
    would assert that every edge ran, which is the precise claim this axis exists to
    avoid making.
    """
    result = _analyse(None, dictionary)

    assert result.edges
    assert all(edge.unexercised is None for edge in result.edges)


def test_a_witness_that_cannot_see_a_unit_says_nothing_about_it(
    dictionary: Dictionary,
) -> None:
    """No coverage, no verdict.

    A missing attribution here would produce a SILENT NEGATIVE - "this never ran", asserted
    about a unit the log simply could not see. That is a finding a migration team might act
    on by deleting code, which makes it the most expensive wrong answer in the system.
    """
    blind = ExecutionWitness(window="18 months", units=frozenset({"SOMETHING_ELSE"}))
    result = _analyse(blind, dictionary)

    assert all(edge.unexercised is None for edge in result.edges)


def test_the_witness_reports_unknown_rather_than_false_for_an_uncovered_unit() -> None:
    witness = ExecutionWitness(window="18 months", units=frozenset({"COVERED"}))

    assert witness.ran("UNCOVERED", 1) is None
    assert witness.ran("COVERED", 1) is False


# --- T3.5d: Tier A and unexercised at once --------------------------------------------


def test_the_apac_branch_is_tier_a_and_never_observed(
    eu_only: ExecutionWitness, dictionary: Dictionary
) -> None:
    """THE COMBINATION THIS TASK EXISTS FOR.

    The APAC edges are not weakly supported. They are Tier A - the strongest static
    evidence there is - and simply never ran. Both facts, on the same edge, at once.
    """
    result = _analyse(eu_only, dictionary)
    apac = [e for e in result.edges if e.guard and "APAC" in e.guard.upper()]

    assert apac, "the APAC branch produced no edges at all"
    assert all(e.tier is Tier.A for e in apac)
    assert all(e.unexercised is True for e in apac)


def test_the_exercised_branch_is_marked_exercised(
    eu_only: ExecutionWitness, dictionary: Dictionary
) -> None:
    """The other half of the same assertion. Without it, an analyser that marked
    everything unexercised would pass the test above."""
    result = _analyse(eu_only, dictionary)
    eu = [
        e for e in result.edges if e.origin.line == 26 and e.origin.unit == "S7_UNEXERCISED_BRANCH"
    ]

    assert eu
    assert all(e.unexercised is False for e in eu)


def test_the_axis_does_not_change_which_edges_exist(
    eu_only: ExecutionWitness, dictionary: Dictionary
) -> None:
    """Execution evidence is laid alongside an analysis, never folded into it.

    If a witness could add or remove an edge, the lineage would depend on how busy last
    month was - and the same code would produce different facts on two different Tuesdays.
    """
    without = {e.identity() for e in _analyse(None, dictionary).edges}
    with_witness = {e.identity() for e in _analyse(eu_only, dictionary).edges}

    assert without == with_witness


def test_it_is_orthogonal_to_tier() -> None:
    """Asserted on the model directly, because the two are easy to conflate in code that
    only ever sees them together."""
    edge = IREdge(
        source=Node(kind=NodeKind.COLUMN, name="A.X"),
        target=Node(kind=NodeKind.COLUMN, name="B.Y"),
        flow=Flow.VALUE,
        band=1,
        mechanism=Mechanism.DEF_USE,
        tier=Tier.A,
        origin=Origin(unit="U", line=1),
        unexercised=True,
    )

    assert edge.tier is Tier.A
    assert edge.unexercised is True


# --- T3.5c: the harness reports it ----------------------------------------------------


def test_the_harness_separates_disagreement_from_absence(
    eu_only: ExecutionWitness, dictionary: Dictionary
) -> None:
    """`unexercised_total` counts what could be compared; `unexercised_unknown` counts
    what could not. Collapsing them would turn a missing input into an analyser failure."""
    truth = GroundTruth.load(Path("ground_truth") / "s7_unexercised_branch.yaml")

    blind = score(truth, _analyse(None, dictionary).edges)
    assert blind.unexercised_total == 0
    assert blind.unexercised_unknown > 0
    assert blind.unexercised_accuracy is None  # NOT MEASURED, never 0%

    seeing = score(truth, _analyse(eu_only, dictionary).edges)
    assert seeing.unexercised_total > 0
    assert seeing.unexercised_accuracy == 1.0


def test_the_window_travels_with_the_verdict(
    eu_only: ExecutionWitness, dictionary: Dictionary
) -> None:
    """ "Never observed" without a window is not a statement about anything.

    A year-end path absent from 30 days says almost nothing; the same path absent from 18
    months is a real finding.
    """
    result = _analyse(eu_only, dictionary)
    assert any("18 months to 2026-09-08" in b for b in map(str, result.boundaries))


def test_a_witness_round_trips_through_its_file_form(tmp_path: Path) -> None:
    """It is an artefact that outlives the run that made it, so it has to survive being
    written down."""
    original = ExecutionWitness(
        window="18 months to 2026-09-08",
        units=frozenset({"A", "B"}),
        sightings=frozenset({StatementSighting("A", 3), StatementSighting("B", 9)}),
        note="round trip",
    )
    path = tmp_path / "witness.json"
    original.dump(path)

    assert ExecutionWitness.load(path) == original


# --- what the LIVE database taught this, and what protects against it (T3.5b) ----------
#
# Every rule below was written after a real capture produced a wrong answer. They are kept
# as tests rather than as comments because each one is a property of the evidence SOURCE,
# and a future source (AWR, a persisted capture job) has to be checked against the same
# list before it is trusted.

WITNESS = Path("evidence") / "witness.json"


def test_the_committed_witness_is_real_and_states_its_window() -> None:
    """Captured from `V$SQL` after `scripts/exercise_corpus.py`, not hand-written.

    The window is the caveat that travels with every negative it produces: `V$SQL` is the
    shared pool, so it empties at instance restart and ages out under memory pressure.
    Nothing older than the last restart exists to be found, whatever the retention setting
    says.
    """
    witness = ExecutionWitness.load(WITNESS)

    assert "V$SQL" in witness.window
    assert "instance up since" in witness.window
    assert witness.units


def test_the_real_witness_leaves_s7s_unrun_branches_unexercised() -> None:
    """T3.5d against real evidence rather than a fixture.

    `exercise_corpus.py` calls `s7_unexercised_branch('EU')` and deliberately never calls
    the APAC or YEAR_END arms - that restraint is the case.
    """
    witness = ExecutionWitness.load(WITNESS)

    assert witness.covers("S7_UNEXERCISED_BRANCH")
    assert witness.ran("S7_UNEXERCISED_BRANCH", 26) is True  # the EU UPDATE
    assert witness.ran("S7_UNEXERCISED_BRANCH", 32) is False  # APAC
    assert witness.ran("S7_UNEXERCISED_BRANCH", 40) is False  # YEAR_END


def test_a_unit_with_nothing_resident_gets_no_verdict() -> None:
    """MEASURED, AND IT OVERTURNED THE FIRST RULE.

    Every cursor tagged `s4_partition_exchange` turned out to belong to `s5`, `s6` or `s7`
    - statements that ran AFTER it in the same session and inherited its sticky action tag
    - while `s4`'s own INSERT had aged out of the pool seconds after running. Coverage by
    tag alone would have marked all four exchange edges unexercised: false, and false in
    the expensive direction.

    If nothing of a unit is resident, absence describes the SHARED POOL, not the estate.
    """
    witness = ExecutionWitness.load(WITNESS)

    assert not witness.covers("S4_PARTITION_EXCHANGE")
    assert witness.ran("S4_PARTITION_EXCHANGE", 51) is None


def test_ddl_gets_no_verdict_because_the_source_cannot_see_it() -> None:
    """`V$SQL` does not hold `ALTER TABLE ... EXCHANGE PARTITION` at all.

    Oracle parses DDL, runs it, and does not keep it as a shareable cursor. Two blind
    spots would otherwise land on the same statement: `s4` is a silent failure BECAUSE a
    DML-only reading misses the exchange, and the witness would then report the movement
    of an entire regulated dataset as dead code.
    """
    witness = ExecutionWitness(
        window="w",
        units=frozenset({"U"}),
        sightings=frozenset({StatementSighting("U", 10)}),
        unobservable=frozenset({StatementSighting("U", 20)}),
    )

    assert witness.ran("U", 10) is True
    assert witness.ran("U", 20) is None  # cannot be seen either way
    assert witness.ran("U", 30) is False  # observable, covered, absent


def test_shape_matching_keeps_literals_because_the_literal_is_the_difference() -> None:
    """The normaliser that erased literals equated two genuinely different statements:

        TRUNC(order_date, 'YYYY')   s7, YEAR_END branch - never called
        TRUNC(order_date, 'MM')     s8_aggregated_copy  - called

    and with the sticky action tag it reported s7's YEAR_END arm as exercised, destroying
    the only unexercised evidence in the corpus.
    """
    from scripts.capture_witness import shape

    assert shape("SELECT TRUNC(d, 'YYYY') FROM t") != shape("SELECT TRUNC(d, 'MM') FROM t")
    assert shape("select  a\n  from  t;") == shape("SELECT A FROM T")
    assert shape("WHERE r = 'EU'") != shape("WHERE r = 'eu'")
