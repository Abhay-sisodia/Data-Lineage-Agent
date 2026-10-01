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
}

PINNED = sorted({name for name, _ in EXPECTED})


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
    assert result.refusals == []
