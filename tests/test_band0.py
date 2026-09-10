"""Band 0 analyser, scored against ground truth (T1.5).

The T1.5 gate: band-0 precision 100% and recall >= 95% on the labelled packages, with
each of the five constructs passing its own adversarial case.

These run on every build. Band 0 is the baseline the whole spike rests on - if it
regresses, nothing measured above it means anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlglot

from lineage.analysis.band0 import _transform_of, analyse_source
from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow, GroundTruth, Transform
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
    """The abstention path: no edges, and a stated reason.

    This used to use `INSERT INTO tmp_recent VALUES (1, SYSDATE)`, which is no longer
    refused - see `test_a_literal_insert_values_is_analysed_not_refused` below and stress
    finding S1-04. The case is rewritten rather than deleted: what it tests is the
    abstention PATH, and that path still has to work, so it now uses a construct the
    register genuinely refuses.
    """
    dictionary = Dictionary.load(CORPUS / "dictionary.json")
    source = """
    CREATE OR REPLACE PROCEDURE t_refuse IS
    BEGIN
      INSERT INTO dim_customer_hier (cust_id, parent_cust_id)
      SELECT h.cust_id, h.parent_cust_id
        FROM dim_customer_hier h
       START WITH h.parent_cust_id IS NULL
     CONNECT BY PRIOR h.cust_id = h.parent_cust_id;
    END;
    """
    result = analyse_source(source, dictionary, AnalysisConfig())
    assert result.edges == []
    assert len(result.refusals) == 1
    assert result.parse_coverage == 0.0


def test_a_literal_insert_values_is_analysed_not_refused() -> None:
    """Stress finding S1-04, decision D-1. Records that the change of behaviour is meant.

    The assertion that moved: this statement used to produce one refusal and 0% parse
    coverage. It now produces no refusal and 100% coverage, with the same empty edge set -
    because a literal has no upstream to name, which is the ordinary literal rule and not
    an abstention. Refusing it was over-refusal (T3.1d) and it cost coverage.

    The edge set being unchanged is why the phase-0 CELLS did not move while parse
    coverage rose 4.3 points.
    """
    dictionary = Dictionary.load(CORPUS / "dictionary.json")
    source = """
    CREATE OR REPLACE PROCEDURE t_literals IS
    BEGIN
      INSERT INTO tmp_recent (cust_id, last_login) VALUES (1, SYSDATE);
    END;
    """
    result = analyse_source(source, dictionary, AnalysisConfig())
    assert result.edges == []
    assert result.refusals == []
    assert result.parse_coverage == 1.0


def test_two_units_writing_the_same_columns_both_survive(dictionary: Dictionary) -> None:
    """Band 0 used to deduplicate on match_key() alone, and it destroyed facts.

    Found by the stress corpus, 2026-09-09. `match_key()` is (source, target, flow,
    transform) - no guard, no origin - so a static INSERT in one procedure and a recovered
    dynamic INSERT in another, writing the same columns, collapsed into one edge. The
    survivor kept one origin, one band and one guard; the other fact was gone before it
    ever reached the IR. Not refused, not declared, not counted.

    That is strictly worse than the scoring-layer collision ADR-0001 amendment 1b
    measured, because the ledger never sees the second fact at all - and it made
    `procedure.py`'s "merge on ledger identity so two facts differing only by guard or
    origin both survive" a comment about something that had already happened.

    Deduplicating on identity() instead was free on the phase-0 corpus - every cell
    byte-identical - because its packages are small enough that two statements rarely
    write the same pair. Real packages are not.
    """
    source = """CREATE OR REPLACE PROCEDURE writer_one IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login FROM stg_customer WHERE status_code = 'A';
END writer_one;
/

CREATE OR REPLACE PROCEDURE writer_two IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login FROM stg_customer WHERE region = 'EU';
END writer_two;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    units = {
        edge.origin.unit for edge in result.edges if str(edge.target) == "column:TMP_RECENT.CUST_ID"
    }
    assert units == {"WRITER_ONE", "WRITER_TWO"}


# --- S2-05: what counts as a conditional transform ---------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        # Oracle's own documentation defines DECODE as equivalent to CASE.
        ("DECODE(status_code, 'A', 1, 0)", Transform.CONDITIONAL),
        # Replaces the value with NULL on a test.
        ("NULLIF(region, 'XX')", Transform.CONDITIONAL),
        # Choose between two SOURCES by comparison.
        ("GREATEST(gross_amount, discount_amt)", Transform.CONDITIONAL),
        ("LEAST(gross_amount, discount_amt)", Transform.CONDITIONAL),
        # Tests one argument, returns one of two others.
        ("NVL2(region, 1, 0)", Transform.CONDITIONAL),
        ("CASE WHEN status_code = 'A' THEN 1 ELSE 0 END", Transform.CONDITIONAL),
        # Not conditional: the input flows through an expression.
        ("TRUNC(order_date, 'MM')", Transform.DERIVED),
        ("cust_id", Transform.IDENTITY),
        # Aggregation still outranks everything (ADR-0001 §4 transform ladder).
        ("NVL(SUM(gross_amount), 0)", Transform.AGGREGATED),
    ],
)
def test_conditional_transform_classification(expression: str, expected: Transform) -> None:
    """Found by stress 2. These were all `derived`, and a wrong transform is a MISS.

    ADR-0001 §4 makes transform part of the match key, so mis-classifying one costs a
    false positive AND a false negative - eight cells of damage from four expressions in
    stress 2. It is also the error that reads as correct in a report: the edge is there,
    the endpoints are right, and only the word describing what happened is wrong.
    """
    parsed = sqlglot.parse_one(f"SELECT {expression} FROM t", dialect="oracle").selects[0]
    assert _transform_of(parsed) is expected


def test_coalesce_is_conditional_only_when_two_columns_can_supply() -> None:
    """The one that needed a rule rather than a list, because NVL and COALESCE share a node.

    `COALESCE(a, b)` over two columns is a genuine choice of source. `NVL(x, 0)` has a
    constant floor: the column flows through unchanged whenever it exists, and the literal
    is null-safety rather than business logic. Calling that conditional would tell a reader
    there is a branch in the rule when the only branch is a null guard.

    Measured, not asserted: classifying every COALESCE as conditional costs one phase-0
    label — `sq_06`'s `NVL(parent.depth, 0) + 1` — and this rule leaves it alone.
    """
    two_columns = sqlglot.parse_one("SELECT COALESCE(email, region) FROM t", dialect="oracle")
    literal_floor = sqlglot.parse_one("SELECT NVL(lifetime_value, 0) + 1 FROM t", dialect="oracle")

    assert _transform_of(two_columns.selects[0]) is Transform.CONDITIONAL
    assert _transform_of(literal_floor.selects[0]) is Transform.DERIVED


# --- S2-07 / D-3: window-ness is not aggregation-ness ------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        # PICK a value out of the window. Nothing is computed over the set - the value
        # written appears verbatim in some row of the input, and the window only decides
        # WHICH row supplies it.
        (
            "FIRST_VALUE(product_name) OVER (PARTITION BY product_id ORDER BY order_date)",
            Transform.DERIVED,
        ),
        (
            "LAST_VALUE(unit_price) OVER (PARTITION BY product_id ORDER BY order_date)",
            Transform.DERIVED,
        ),
        # NTH_VALUE is FIRST_VALUE generalised and follows by the same rule.
        ("NTH_VALUE(unit_price, 2) OVER (PARTITION BY product_id)", Transform.DERIVED),
        # COMPUTE a value over the set. The OVER clause is not what makes an aggregation,
        # so these are unchanged - this is the half of the rule that is easy to break.
        (
            "SUM(line_amount) OVER (PARTITION BY product_id ORDER BY order_date)",
            Transform.AGGREGATED,
        ),
        ("COUNT(*) OVER (PARTITION BY product_id)", Transform.AGGREGATED),
        ("AVG(line_amount) OVER (PARTITION BY product_id)", Transform.AGGREGATED),
        # Plain aggregation, no window at all.
        ("SUM(line_amount)", Transform.AGGREGATED),
        ("MAX(product_name)", Transform.AGGREGATED),
    ],
)
def test_value_selecting_window_functions_are_derived(
    expression: str, expected: Transform
) -> None:
    """Stress finding S2-07, decision D-3. The key said `derived` and the analyser did not.

    sqlglot derives `FirstValue`/`LastValue`/`NthValue` from `exp.AggFunc`, so the
    catch-all in AGGREGATE_FUNCTIONS swept them up. A wrong transform is a MISS on both
    sides under ADR-0001 §4, so each instance cost a false positive AND a false negative.
    """
    parsed = sqlglot.parse_one(f"SELECT {expression} FROM t", dialect="oracle").selects[0]
    assert _transform_of(parsed) is expected


def test_an_aggregate_wrapping_a_value_selecting_window_still_aggregates() -> None:
    """The exclusion is per-node, not per-expression, so the ladder must still hold.

    If `_is_aggregate` returned False for the whole expression on finding a FIRST_VALUE
    anywhere in it, an enclosing SUM would be lost. ADR-0001 §4: the transform is the
    strongest on the path.
    """
    parsed = sqlglot.parse_one(
        "SELECT SUM(FIRST_VALUE(unit_price) OVER (PARTITION BY product_id)) FROM t",
        dialect="oracle",
    ).selects[0]
    assert _transform_of(parsed) is Transform.AGGREGATED


def test_lag_is_still_aggregated_and_that_is_recorded_as_inconsistent() -> None:
    """LAG selects an existing value too, and every key in the corpus calls it `aggregated`.

    This test pins the CURRENT state rather than endorsing it. D-3 named FIRST_VALUE and
    LAST_VALUE; moving LAG would change `sq_03_window_functions` and stress 1, which is a
    phase-0 change and a separate measurement. Recorded as finding S2-08 — if that finding
    is ever decided the other way, this test is the thing that should fail.
    """
    parsed = sqlglot.parse_one(
        "SELECT LAG(total) OVER (PARTITION BY product_id ORDER BY period_month) FROM t",
        dialect="oracle",
    ).selects[0]
    assert _transform_of(parsed) is Transform.AGGREGATED


# --- S2-12 / D-4: window influence is neither value nor filter ---------------------------


def test_a_window_removes_no_rows_so_its_ordering_is_not_a_filter(dictionary: Dictionary) -> None:
    """The assertion that carries the decision: no filter edge against the relation.

    `filter` means "these columns decided WHICH ROWS landed". A window function removes no
    rows - every input row survives it - so an edge saying `period_month` decided which
    rows landed in `fct_product_sales` is false. It was emitted for two stress runs, and
    scored as correct against `sq_03`'s key and as a false positive against both stress
    keys, because the two key sets held opposite conventions and nothing compared them.
    """
    source = """CREATE OR REPLACE PROCEDURE ranker IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, rank_in_month)
    SELECT TRUNC(o.order_date, 'MM'),
           l.product_id,
           ROW_NUMBER() OVER (PARTITION BY TRUNC(o.order_date, 'MM')
                                  ORDER BY l.line_amount DESC)
      FROM stg_orders o
      JOIN stg_order_lines l ON l.order_id = o.order_id;
END ranker;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    assert not [
        e for e in result.edges if e.flow is Flow.FILTER
    ], "a statement with no WHERE produced filter edges"

    influence = {(str(e.source), str(e.target)) for e in result.edges if e.flow is Flow.INFLUENCE}
    assert influence == {
        ("column:STG_ORDERS.ORDER_DATE", "column:FCT_PRODUCT_SALES.RANK_IN_MONTH"),
        ("column:STG_ORDER_LINES.LINE_AMOUNT", "column:FCT_PRODUCT_SALES.RANK_IN_MONTH"),
    }


def test_window_influence_names_the_column_it_decides(dictionary: Dictionary) -> None:
    """Two windows over the same partition column feed two different output columns.

    Under the old relation-targeted form these collapsed onto ONE four-tuple and one of
    the two facts was simply lost - S1-05, hiding a real distinction rather than a
    duplicate. Naming the column separates them, and the difference is not cosmetic: one
    says which row gets which rank, the other says which row counts as "previous".
    """
    source = """CREATE OR REPLACE PROCEDURE two_windows IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, rank_in_month, prior_month)
    SELECT TRUNC(o.order_date, 'MM'),
           l.product_id,
           ROW_NUMBER() OVER (PARTITION BY l.product_id ORDER BY o.order_date),
           LAG(l.line_amount) OVER (PARTITION BY l.product_id ORDER BY o.order_date)
      FROM stg_orders o
      JOIN stg_order_lines l ON l.order_id = o.order_id;
END two_windows;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    influence = {(str(e.source), str(e.target)) for e in result.edges if e.flow is Flow.INFLUENCE}
    assert ("column:STG_ORDER_LINES.PRODUCT_ID", "column:FCT_PRODUCT_SALES.RANK_IN_MONTH") in influence
    assert ("column:STG_ORDER_LINES.PRODUCT_ID", "column:FCT_PRODUCT_SALES.PRIOR_MONTH") in influence

    # LAG's ARGUMENT is still a value source. The distinction the corpus has held since
    # sq_03 - argument yes, partition and order no - is untouched by D-4.
    values = {(str(e.source), str(e.target)) for e in result.edges if e.flow is Flow.VALUE}
    assert ("column:STG_ORDER_LINES.LINE_AMOUNT", "column:FCT_PRODUCT_SALES.PRIOR_MONTH") in values


def test_a_real_where_is_still_a_filter_against_the_relation(dictionary: Dictionary) -> None:
    """The guard on D-4: splitting the two influences must not move ordinary predicates.

    `_influence_columns` used to return window columns and correlated-predicate columns
    together. Only the window half moved; a WHERE still decides which rows land and still
    points at the relation.
    """
    source = """CREATE OR REPLACE PROCEDURE filterer IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, rank_in_month)
    SELECT TRUNC(o.order_date, 'MM'),
           l.product_id,
           ROW_NUMBER() OVER (ORDER BY l.line_amount)
      FROM stg_orders o
      JOIN stg_order_lines l ON l.order_id = o.order_id
     WHERE o.currency = 'GBP';
END filterer;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    filters = {(str(e.source), str(e.target)) for e in result.edges if e.flow is Flow.FILTER}
    assert filters == {("column:STG_ORDERS.CURRENCY", "relation:FCT_PRODUCT_SALES")}
