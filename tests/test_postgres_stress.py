"""The PostgreSQL stress packages, pinned.

`stress/run_stress.py` prints these grids; nothing failed if a number moved. This file makes
the numbers assertions, so a regression is a red test rather than a figure a human has to
notice in a wall of output.

**What each package is for**, since three packages is only useful if they ask different
questions:

  stress_pg_01  BREADTH inside the scope. Seven statements, one or two constructs each, and
                two of them close ports of Oracle packages that already score 100% - so a
                difference there is a DIALECT difference and nothing else.
  stress_pg_02  DEPTH. Five statements nested deeply enough that a value must survive
                several rewrites. Found PG-03, PG-04 and PG-05, none of which a wide package
                can reach.
  stress_pg_03  CONTAINMENT. Refused and in-scope statements alternating, to test that a
                refusal stays local. The Oracle side learned this the hard way twice.
  stress_pg_04  QUERY COMPLEXITY, and nothing else - no routines, no DDL. Three statements
                whose entire difficulty is inside the SELECT. Its three disagreements are
                the three its key FLAGGED IN ADVANCE, and are pinned as such below.
  stress_pg_05  LENGTH. ONE statement, 461 lines, sixteen CTEs, seven base relations, five
                changes of grain. Asks whether lineage survives a PIPELINE rather than a
                construct - the question production warehouse SQL actually poses. Value and
                influence are perfect; its only three disagreements are PG-10.

**The misses in stress_pg_02 are pinned as misses**, with the finding that owns each. That
is deliberate: a test that asserted 100% would have to be written by deleting the labels the
analyser cannot satisfy, and a key edited to agree with the code has stopped measuring
anything. When PG-03 is fixed this test fails, and that failure is the fix's receipt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow, GroundTruth
from lineage.harness.scoring import score
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
STRESS = ROOT / "stress"

PACKAGES = [
    "stress_pg_01_wide_coverage",
    "stress_pg_02_deep_nesting",
    "stress_pg_03_boundary",
    "stress_pg_04_query_complexity",
    "stress_pg_05_single_query",
]


@pytest.fixture(scope="module")
def book() -> Dictionary:
    return Dictionary.load(STRESS / "dictionary_postgres.json")


def run(name: str, book: Dictionary):
    truth = GroundTruth.load(STRESS / f"{name}.yaml")
    truth.verify_against(STRESS)
    config = AnalysisConfig.load().model_copy(update={"dialect": truth.dialect})
    source = (STRESS / truth.package).read_text(encoding="utf-8")
    result = analyse_source(source, book, config, None)
    return truth, result, score(truth, result.edges, result.boundaries)


# (package, flow) -> (true positives, false positives, false negatives), band 0.
# Measured 2026-09-23. Every entry is a claim that can fail.
EXPECTED = {
    ("stress_pg_01_wide_coverage", Flow.VALUE): (23, 0, 0),
    ("stress_pg_01_wide_coverage", Flow.FILTER): (16, 0, 0),
    ("stress_pg_01_wide_coverage", Flow.INFLUENCE): (19, 0, 0),
    # The five value misses are PG-03 (LATERAL x1), PG-04 (jsonb_build_object x2) and
    # PG-05 (unnest x1), plus PG-03's second LATERAL column. The filter miss and the
    # influence miss are both PG-03.
    ("stress_pg_02_deep_nesting", Flow.VALUE): (17, 0, 5),
    ("stress_pg_02_deep_nesting", Flow.FILTER): (14, 0, 1),
    ("stress_pg_02_deep_nesting", Flow.INFLUENCE): (21, 0, 1),
    ("stress_pg_03_boundary", Flow.VALUE): (9, 0, 0),
    ("stress_pg_03_boundary", Flow.FILTER): (5, 0, 0),
    ("stress_pg_03_boundary", Flow.INFLUENCE): (6, 0, 0),
    # The one filter and two value disagreements are PG-10 and PG-11, both of them
    # DECISIONS rather than defects, and both flagged in the key before the run. Each costs
    # a false positive AND a false negative because transform and phase are in the match
    # key (ADR-0001 §4). When either is settled this line moves - deliberately, with the
    # decision recorded, which is the only way it should ever move.
    ("stress_pg_04_query_complexity", Flow.VALUE): (21, 2, 2),
    ("stress_pg_04_query_complexity", Flow.FILTER): (21, 1, 1),
    ("stress_pg_04_query_complexity", Flow.INFLUENCE): (34, 0, 0),
    # 25/25 value and 44/44 influence through sixteen CTEs. The three filter
    # disagreements are ALL PG-10, and the key priced them before the run: settle that
    # decision the key's way and this package is 100% on all three flows.
    ("stress_pg_05_single_query", Flow.VALUE): (25, 0, 0),
    ("stress_pg_05_single_query", Flow.FILTER): (14, 3, 2),
    ("stress_pg_05_single_query", Flow.INFLUENCE): (44, 0, 0),
}


@pytest.mark.parametrize("name", PACKAGES)
@pytest.mark.parametrize("flow", [Flow.VALUE, Flow.FILTER, Flow.INFLUENCE])
def test_band0_grid_has_not_moved(name: str, flow: Flow, book: Dictionary) -> None:
    _truth, _result, report = run(name, book)
    cell = report.cells.get((0, flow.value))
    assert cell is not None, f"{name}: no band-0 {flow.value} cell"
    assert (cell.true_positives, cell.false_positives, cell.false_negatives) == EXPECTED[
        (name, flow)
    ], f"{name} band 0 {flow.value} moved"


_GUESS_EXEMPT = {"stress_pg_04_query_complexity", "stress_pg_05_single_query"}


@pytest.mark.parametrize("name", [p for p in PACKAGES if p not in _GUESS_EXEMPT])
def test_nothing_is_ever_guessed(name: str, book: Dictionary) -> None:
    """Zero false positives in every package and every flow, and this one is not negotiable.

    stress_pg_04 and stress_pg_05 are excluded and the exclusion is narrow: every false
    positive in both is PG-10 or PG-11, where the analyser emits the RIGHT ENDPOINTS with a
    phase or transform the key disputes. That is a disagreement about classification, not
    an invented edge - the endpoints are in the key either way.
    `test_no_forbidden_edge_appears` still covers them, and so do their pinned grids above.

    A miss is a gap and is survivable; an invented edge is a wrong answer delivered with the
    same confidence as a right one. All three packages score zero here, including the deep
    one that misses seven labels - the analyser declined rather than guessed, which is the
    property the whole design is organised around.
    """
    _truth, _result, report = run(name, book)
    guessed = [
        (band, flow, counts.false_positives)
        for (band, flow), counts in sorted(report.cells.items())
        if counts.false_positives
    ]
    assert guessed == [], f"{name}: edges emitted that no label claims: {guessed}"


@pytest.mark.parametrize("name", PACKAGES)
def test_no_forbidden_edge_appears(name: str, book: Dictionary) -> None:
    """The named wrong answers - the plausible mistake each package was written to elicit."""
    _truth, _result, report = run(name, book)
    assert report.forbidden_violations == [], f"{name}: {report.forbidden_violations}"


def test_in_scope_packages_are_refused_nothing(book: Dictionary) -> None:
    """The scope contract, as a test rather than as a paragraph.

    stress_pg_01 and stress_pg_02 use only constructs `docs/postgres_scope.md` lists as in
    scope. A refusal in either means the scope claims something it cannot do, or a construct
    has retreated out of it unnoticed - and a refusal is the comfortable place to retreat to
    when something breaks, which is exactly why it is pinned.
    """
    for name in (
        "stress_pg_01_wide_coverage",
        "stress_pg_02_deep_nesting",
        "stress_pg_04_query_complexity",
        "stress_pg_05_single_query",
    ):
        _truth, result, _report = run(name, book)
        assert result.refusals == [], f"{name} refused: {[r.reason for r in result.refusals]}"


def test_the_boundary_package_refuses_exactly_what_it_predicted(book: Dictionary) -> None:
    """Five refusals, and the CODES matter as much as the count.

    Four are UNSUPPORTED_CONSTRUCT and one is SOURCE_UNAVAILABLE, and the split is
    load-bearing: choosing a PL/pgSQL grammar would recover all four and would never recover
    the COPY, whose rows come from outside the database. Collapsing them onto one code would
    misstate what is fixable, which is the question a refusal exists to answer.
    """
    _truth, result, report = run("stress_pg_03_boundary", book)

    codes = sorted(r.code.value for r in result.refusals)
    assert codes == [
        "SOURCE_UNAVAILABLE",
        "UNSUPPORTED_CONSTRUCT",
        "UNSUPPORTED_CONSTRUCT",
        "UNSUPPORTED_CONSTRUCT",
        "UNSUPPORTED_CONSTRUCT",
    ], codes
    assert report.boundaries_undeclared == [], report.boundaries_undeclared


def test_a_refusal_stays_local(book: Dictionary) -> None:
    """Containment: the in-scope statements next to refusals score exactly as they would alone.

    This has failed before. During the port the routine-body refusal pattern was found
    OVER-BROAD - it spanned 2561 characters to reach a `$IF $PLSQL_UNIT` and took Oracle's
    parse coverage from 81.6% to 80.0% before it was anchored on `AS $tag`. On the Oracle
    side `$IF` poisons the statements AROUND it rather than itself, which is the whole reason
    `conditional_compilation_spans` exists.

    Asserted as full recall rather than as an edge count, because the failure it guards
    against is a statement DISAPPEARING, not an edge changing class.
    """
    _truth, _result, report = run("stress_pg_03_boundary", book)
    for (band, flow), counts in sorted(report.cells.items()):
        assert counts.false_negatives == 0, (
            f"a statement next to a refusal lost {counts.false_negatives} "
            f"band-{band} {flow} edges - containment failed"
        )


def test_every_label_is_band_zero(book: Dictionary) -> None:
    """PostgreSQL has no band 1 and no band 2, and a key must not claim otherwise.

    A PL/pgSQL body is refused (ADR-0002 §5), so there is no variable def-use, no cursor
    analysis and no trigger inheritance. A band-1 label in a PostgreSQL key would be
    labelling a capability `docs/postgres_scope.md` says does not exist - and it would score
    as a miss forever, which reads as a defect rather than as a scope boundary.
    """
    for name in PACKAGES:
        truth = GroundTruth.load(STRESS / f"{name}.yaml")
        bands = sorted({edge.band for edge in truth.edges})
        assert bands == [0], f"{name} claims bands {bands}; PostgreSQL has only band 0"


def test_every_relation_in_the_long_query_contributes_something(book: Dictionary) -> None:
    """All seven base relations of stress_pg_05 reach the output. Structural, not a count.

    A 461-line query has branches, and a branch can be lost WHOLE - a join dropped, a CTE
    never walked - while the grid still looks respectable because the surviving branches
    carry most of the edges. The pinned grid would catch it, but only as three numbers
    moving; this names the relation.

    stg_products is the one that matters most: its single contribution to the entire query
    is `COUNT(DISTINCT e.category_id)`, and every other mention of it is a join key.
    """
    _truth, result, _report = run("stress_pg_05_single_query", book)

    sources = {str(edge.source) for edge in result.edges}
    for relation in (
        "stg_orders",
        "stg_order_lines",
        "stg_products",
        "stg_returns",
        "stg_customer",
        "stg_categories",
        "ref_policy",
    ):
        assert any(s.startswith(f"column:{relation}.") for s in sources), (
            f"{relation} contributes NOTHING to stress_pg_05 - a whole branch of the "
            "pipeline was lost, not an edge"
        )
