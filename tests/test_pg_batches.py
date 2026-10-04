"""The PostgreSQL batch programme (`stress/pg/`), pinned once each batch report is agreed.

`stress/pg/triage.py` prints these numbers; nothing fails if one moves. This file makes them
assertions, so a regression is a red test and not a figure someone has to notice.

**A pin is added only after the batch's REPORT.md is agreed**, never at first measurement:
before that the batch is a measurement, and pinning a first run would freeze its defects as
if they were the answer. When a fix moves a number here, edit the pin deliberately and say
why beside it - the move is the fix's receipt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow, GroundTruth
from lineage.harness.scoring import score
from lineage.resolution.dictionary import Dictionary

STRESS = Path(__file__).resolve().parent.parent / "stress"
PG = STRESS / "pg"

# (batch/test, flow) -> (true positives, false positives, false negatives), band 0.
EXPECTED = {
    # B1 core SELECT - agreed 2026-10-01 after every fix in REPORT.md section 8.
    # The only false positives are GROUPING() (B1-Q1, open by decision): 4 in T1 S10a,
    # 2 in T2 S5. They move when B1-Q1 is decided, and that move is expected.
    ("b1_core_select/t1_breadth", Flow.VALUE): (109, 2, 0),
    ("b1_core_select/t1_breadth", Flow.FILTER): (21, 0, 0),
    ("b1_core_select/t1_breadth", Flow.INFLUENCE): (47, 2, 0),
    ("b1_core_select/t2_depth", Flow.VALUE): (42, 0, 0),
    ("b1_core_select/t2_depth", Flow.FILTER): (9, 2, 0),
    ("b1_core_select/t2_depth", Flow.INFLUENCE): (26, 0, 0),
    ("b1_core_select/t3_interaction", Flow.VALUE): (51, 0, 0),
    ("b1_core_select/t3_interaction", Flow.FILTER): (10, 0, 0),
    ("b1_core_select/t3_interaction", Flow.INFLUENCE): (45, 0, 0),
    ("b1_core_select/t4_adversarial", Flow.VALUE): (43, 0, 0),
    ("b1_core_select/t4_adversarial", Flow.FILTER): (16, 0, 0),
    ("b1_core_select/t4_adversarial", Flow.INFLUENCE): (11, 0, 0),
    ("b1_core_select/t5_metamorphic", Flow.VALUE): (34, 0, 0),
    ("b1_core_select/t5_metamorphic", Flow.FILTER): (8, 0, 0),
    ("b1_core_select/t5_metamorphic", Flow.INFLUENCE): (29, 0, 0),
    # B2 subqueries, CTEs, set operations - agreed 2026-10-02 after the fixes in REPORT.md
    # section 6, under D-12, D-13 and D-14. Every cell 100%. Three T5 pairs (P5, P6, P8's
    # match key) still DIFFER by decision - the semi-join / anti-join seam left open - and
    # their keys hold the union of both spellings, so the grid does not see it.
    ("b2_subqueries_ctes_setops/t1_breadth", Flow.VALUE): (58, 0, 0),
    ("b2_subqueries_ctes_setops/t1_breadth", Flow.FILTER): (34, 0, 0),
    ("b2_subqueries_ctes_setops/t1_breadth", Flow.INFLUENCE): (5, 0, 0),
    ("b2_subqueries_ctes_setops/t2_depth", Flow.VALUE): (41, 0, 0),
    ("b2_subqueries_ctes_setops/t2_depth", Flow.FILTER): (21, 0, 0),
    ("b2_subqueries_ctes_setops/t2_depth", Flow.INFLUENCE): (10, 0, 0),
    ("b2_subqueries_ctes_setops/t3_interaction", Flow.VALUE): (50, 0, 0),
    ("b2_subqueries_ctes_setops/t3_interaction", Flow.FILTER): (16, 0, 0),
    ("b2_subqueries_ctes_setops/t3_interaction", Flow.INFLUENCE): (16, 0, 0),
    ("b2_subqueries_ctes_setops/t4_adversarial", Flow.VALUE): (32, 0, 0),
    ("b2_subqueries_ctes_setops/t4_adversarial", Flow.FILTER): (17, 0, 0),
    ("b2_subqueries_ctes_setops/t4_adversarial", Flow.INFLUENCE): (4, 0, 0),
    ("b2_subqueries_ctes_setops/t5_metamorphic", Flow.VALUE): (28, 0, 0),
    ("b2_subqueries_ctes_setops/t5_metamorphic", Flow.FILTER): (21, 0, 0),
    ("b2_subqueries_ctes_setops/t5_metamorphic", Flow.INFLUENCE): (8, 0, 0),
    # B3 DML writes - agreed 2026-10-02 after every fix in REPORT.md section 6. No false
    # positive and no miss. T4 has no influence fact. T5's P6 differs by the open
    # semi-join seam, which no key scores.
    ("b3_dml_writes/t1_breadth", Flow.VALUE): (38, 0, 0),
    ("b3_dml_writes/t1_breadth", Flow.FILTER): (23, 0, 0),
    ("b3_dml_writes/t1_breadth", Flow.INFLUENCE): (4, 0, 0),
    ("b3_dml_writes/t2_depth", Flow.VALUE): (16, 0, 0),
    ("b3_dml_writes/t2_depth", Flow.FILTER): (28, 0, 0),
    ("b3_dml_writes/t2_depth", Flow.INFLUENCE): (10, 0, 0),
    ("b3_dml_writes/t3_interaction", Flow.VALUE): (25, 0, 0),
    ("b3_dml_writes/t3_interaction", Flow.FILTER): (19, 0, 0),
    ("b3_dml_writes/t3_interaction", Flow.INFLUENCE): (17, 0, 0),
    ("b3_dml_writes/t4_adversarial", Flow.VALUE): (17, 0, 0),
    ("b3_dml_writes/t4_adversarial", Flow.FILTER): (11, 0, 0),
    ("b3_dml_writes/t5_metamorphic", Flow.VALUE): (18, 0, 0),
    ("b3_dml_writes/t5_metamorphic", Flow.FILTER): (13, 0, 0),
    ("b3_dml_writes/t5_metamorphic", Flow.INFLUENCE): (7, 0, 0),
    # B4 expressions and types - agreed 2026-10-02 after every fix in REPORT.md section 6.
    # No false positive, no miss, and none hidden per statement (triage.py). T4 has no
    # influence fact. All ten T5 pairs are equal.
    ("b4_expressions_types/t1_breadth", Flow.VALUE): (85, 0, 0),
    ("b4_expressions_types/t1_breadth", Flow.FILTER): (17, 0, 0),
    ("b4_expressions_types/t1_breadth", Flow.INFLUENCE): (1, 0, 0),
    ("b4_expressions_types/t2_depth", Flow.VALUE): (49, 0, 0),
    ("b4_expressions_types/t2_depth", Flow.FILTER): (10, 0, 0),
    ("b4_expressions_types/t2_depth", Flow.INFLUENCE): (16, 0, 0),
    ("b4_expressions_types/t3_interaction", Flow.VALUE): (48, 0, 0),
    ("b4_expressions_types/t3_interaction", Flow.FILTER): (10, 0, 0),
    ("b4_expressions_types/t3_interaction", Flow.INFLUENCE): (30, 0, 0),
    ("b4_expressions_types/t4_adversarial", Flow.VALUE): (48, 0, 0),
    ("b4_expressions_types/t4_adversarial", Flow.FILTER): (13, 0, 0),
    ("b4_expressions_types/t5_metamorphic", Flow.VALUE): (42, 0, 0),
    ("b4_expressions_types/t5_metamorphic", Flow.FILTER): (7, 0, 0),
    ("b4_expressions_types/t5_metamorphic", Flow.INFLUENCE): (18, 0, 0),
    # B9 names and resolution - agreed 2026-10-03 after every fix in REPORT.md section 6,
    # scored strictly against schema-qualified keys (D-25). No false positive, no miss,
    # none hidden per statement; all ten T5 pairs equal.
    ("b9_names_resolution/t1_breadth", Flow.VALUE): (33, 0, 0),
    ("b9_names_resolution/t1_breadth", Flow.FILTER): (6, 0, 0),
    ("b9_names_resolution/t1_breadth", Flow.INFLUENCE): (1, 0, 0),
    ("b9_names_resolution/t2_depth", Flow.VALUE): (16, 0, 0),
    ("b9_names_resolution/t2_depth", Flow.FILTER): (8, 0, 0),
    ("b9_names_resolution/t2_depth", Flow.INFLUENCE): (3, 0, 0),
    ("b9_names_resolution/t3_interaction", Flow.VALUE): (21, 0, 0),
    ("b9_names_resolution/t3_interaction", Flow.FILTER): (5, 0, 0),
    ("b9_names_resolution/t3_interaction", Flow.INFLUENCE): (5, 0, 0),
    ("b9_names_resolution/t4_adversarial", Flow.VALUE): (18, 0, 0),
    ("b9_names_resolution/t4_adversarial", Flow.FILTER): (5, 0, 0),
    ("b9_names_resolution/t4_adversarial", Flow.INFLUENCE): (1, 0, 0),
    ("b9_names_resolution/t5_metamorphic", Flow.VALUE): (24, 0, 0),
    ("b9_names_resolution/t5_metamorphic", Flow.FILTER): (3, 0, 0),
    ("b9_names_resolution/t5_metamorphic", Flow.INFLUENCE): (1, 0, 0),
    # B6 set-returning and table functions - agreed 2026-10-03 after every fix in
    # REPORT.md section 6. No false positive, no miss, none hidden; all six pairs equal.
    ("b6_srf_table_functions/t1_breadth", Flow.VALUE): (24, 0, 0),
    ("b6_srf_table_functions/t1_breadth", Flow.FILTER): (2, 0, 0),
    ("b6_srf_table_functions/t1_breadth", Flow.INFLUENCE): (3, 0, 0),
    ("b6_srf_table_functions/t2_depth", Flow.VALUE): (14, 0, 0),
    ("b6_srf_table_functions/t2_depth", Flow.FILTER): (2, 0, 0),
    ("b6_srf_table_functions/t2_depth", Flow.INFLUENCE): (9, 0, 0),
    ("b6_srf_table_functions/t3_interaction", Flow.VALUE): (14, 0, 0),
    ("b6_srf_table_functions/t3_interaction", Flow.FILTER): (5, 0, 0),
    ("b6_srf_table_functions/t3_interaction", Flow.INFLUENCE): (5, 0, 0),
    ("b6_srf_table_functions/t4_adversarial", Flow.VALUE): (18, 0, 0),
    ("b6_srf_table_functions/t4_adversarial", Flow.FILTER): (2, 0, 0),
    ("b6_srf_table_functions/t4_adversarial", Flow.INFLUENCE): (1, 0, 0),
    ("b6_srf_table_functions/t5_metamorphic", Flow.VALUE): (10, 0, 0),
    ("b6_srf_table_functions/t5_metamorphic", Flow.FILTER): (2, 0, 0),
    ("b6_srf_table_functions/t5_metamorphic", Flow.INFLUENCE): (2, 0, 0),
    # B5 semi-structured - agreed 2026-10-03 after every fix in REPORT.md section 6. No
    # false positive, no miss, none hidden; all eight pairs equal. T4 has no influence.
    ("b5_semi_structured/t1_breadth", Flow.VALUE): (44, 0, 0),
    ("b5_semi_structured/t1_breadth", Flow.FILTER): (5, 0, 0),
    ("b5_semi_structured/t1_breadth", Flow.INFLUENCE): (6, 0, 0),
    ("b5_semi_structured/t2_depth", Flow.VALUE): (11, 0, 0),
    ("b5_semi_structured/t2_depth", Flow.FILTER): (2, 0, 0),
    ("b5_semi_structured/t2_depth", Flow.INFLUENCE): (4, 0, 0),
    ("b5_semi_structured/t3_interaction", Flow.VALUE): (17, 0, 0),
    ("b5_semi_structured/t3_interaction", Flow.FILTER): (4, 0, 0),
    ("b5_semi_structured/t3_interaction", Flow.INFLUENCE): (3, 0, 0),
    ("b5_semi_structured/t4_adversarial", Flow.VALUE): (14, 0, 0),
    ("b5_semi_structured/t4_adversarial", Flow.FILTER): (4, 0, 0),
    ("b5_semi_structured/t5_metamorphic", Flow.VALUE): (14, 0, 0),
    ("b5_semi_structured/t5_metamorphic", Flow.FILTER): (3, 0, 0),
    ("b5_semi_structured/t5_metamorphic", Flow.INFLUENCE): (7, 0, 0),
    # B7 DDL lineage - agreed 2026-10-04 after every fix in REPORT.md section 6. No false
    # positive, no miss, none hidden; all five pairs equal. T4 has only value facts.
    ("b7_ddl_lineage/t1_breadth", Flow.VALUE): (56, 0, 0),
    ("b7_ddl_lineage/t1_breadth", Flow.FILTER): (4, 0, 0),
    ("b7_ddl_lineage/t1_breadth", Flow.INFLUENCE): (3, 0, 0),
    ("b7_ddl_lineage/t2_depth", Flow.VALUE): (18, 0, 0),
    ("b7_ddl_lineage/t2_depth", Flow.FILTER): (10, 0, 0),
    ("b7_ddl_lineage/t2_depth", Flow.INFLUENCE): (5, 0, 0),
    ("b7_ddl_lineage/t3_interaction", Flow.VALUE): (20, 0, 0),
    ("b7_ddl_lineage/t3_interaction", Flow.FILTER): (3, 0, 0),
    ("b7_ddl_lineage/t3_interaction", Flow.INFLUENCE): (2, 0, 0),
    ("b7_ddl_lineage/t4_adversarial", Flow.VALUE): (17, 0, 0),
    ("b7_ddl_lineage/t5_metamorphic", Flow.VALUE): (29, 0, 0),
    ("b7_ddl_lineage/t5_metamorphic", Flow.FILTER): (3, 0, 0),
    ("b7_ddl_lineage/t5_metamorphic", Flow.INFLUENCE): (6, 0, 0),
    ("b10_lexical/t1_breadth", Flow.VALUE): (29, 0, 0),
    ("b10_lexical/t1_breadth", Flow.FILTER): (10, 0, 0),
    ("b10_lexical/t2_depth", Flow.VALUE): (26, 0, 0),
    ("b10_lexical/t2_depth", Flow.FILTER): (4, 0, 0),
    ("b10_lexical/t2_depth", Flow.INFLUENCE): (2, 0, 0),
    ("b10_lexical/t3_interaction", Flow.VALUE): (17, 0, 0),
    ("b10_lexical/t3_interaction", Flow.FILTER): (11, 0, 0),
    ("b10_lexical/t3_interaction", Flow.INFLUENCE): (1, 0, 0),
    ("b10_lexical/t4_adversarial", Flow.VALUE): (15, 0, 0),
    ("b10_lexical/t4_adversarial", Flow.FILTER): (8, 0, 0),
    ("b10_lexical/t5_metamorphic", Flow.VALUE): (20, 0, 0),
    ("b10_lexical/t5_metamorphic", Flow.FILTER): (8, 0, 0),
    ("b10_lexical/t5_metamorphic", Flow.INFLUENCE): (8, 0, 0),
}

PINNED = sorted({name for name, _ in EXPECTED})

# Refusals a test makes on purpose, by code. Anything else refused is a regression.
REFUSED_ON_PURPOSE = {
    # TRUNCATE (no column has a source) and COPY (rows from outside the database).
    "b3_dml_writes/t1_breadth": ["SOURCE_UNAVAILABLE", "UNSUPPORTED_CONSTRUCT"],
    # psql \copy, refused by name as COPY is (D-37).
    "b10_lexical/t1_breadth": ["SOURCE_UNAVAILABLE"],
}


@pytest.fixture(scope="module")
def runs() -> dict[str, tuple[object, object]]:
    book = Dictionary.load(PG / "dictionary.json")
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    out: dict[str, tuple[object, object]] = {}
    for name in PINNED:
        truth = GroundTruth.load(PG / f"{name}.yaml")
        truth.verify_against(STRESS)
        result = analyse_source(
            (STRESS / truth.package).read_text(encoding="utf-8"), book, config, None
        )
        out[name] = (result, score(truth, result.edges, result.boundaries))
    return out


@pytest.mark.parametrize(("name", "flow"), sorted(EXPECTED, key=lambda k: (k[0], k[1].value)))
def test_grid_has_not_moved(name: str, flow: Flow, runs: dict) -> None:
    cell = runs[name][1].cells[(0, flow.value)]
    actual = (cell.true_positives, cell.false_positives, cell.false_negatives)
    assert actual == EXPECTED[(name, flow)], f"{name} {flow.value} moved"


@pytest.mark.parametrize("name", PINNED)
def test_no_forbidden_edge_and_no_refusal(name: str, runs: dict) -> None:
    result, report = runs[name]
    assert report.forbidden_violations == []
    refused = sorted(r.code.value for r in result.refusals)
    assert refused == REFUSED_ON_PURPOSE.get(name, [])
