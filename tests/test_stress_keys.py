"""The stress keys must stay valid, and must account for every unit they describe.

`tests/test_ground_truth.py` covers `ground_truth/*.yaml` against `corpus/`. The stress
packages are scored by `stress/run_stress.py` and were covered by nothing, which is how
finding S2-06 happened: ten of stress 2's twenty-eight program units carried no labels at
all, and the correct edges those units produced scored as false positives against my own
omission. In the report that reads as an analyser defect.

The rule pinned here is the weakest one that would have caught it: EVERY program unit in a
stress package is either labelled, or explicitly declared to contribute no labels. A unit
that genuinely has nothing to say is a real answer - `run_all` only passes a parameter
through - but it has to be SAID, because silence and "nothing here" look identical.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lineage.harness.labels import GroundTruth

STRESS = Path("stress")
KEY_FILES = sorted(STRESS.glob("*.yaml"))

# `CREATE OR REPLACE PROCEDURE x`, and procedures declared inside a package BODY. The
# package SPEC's forward declarations are deliberately not units: they hold no statements.
_TOP_LEVEL = re.compile(
    r"^CREATE\s+OR\s+REPLACE\s+(?:PROCEDURE|FUNCTION|TRIGGER)\s+([A-Za-z0-9_]+)",
    re.IGNORECASE | re.MULTILINE,
)
_NESTED = re.compile(r"^\s+(?:PROCEDURE|FUNCTION)\s+([A-Za-z0-9_]+)[^;]*?\sIS\b", re.IGNORECASE | re.MULTILINE)
_DECLARED_EMPTY = re.compile(r"#\s*NO LABELS:\s*([A-Za-z0-9_]+)")


def units_in(source: str) -> set[str]:
    return {m.upper() for m in _TOP_LEVEL.findall(source)} | {m.upper() for m in _NESTED.findall(source)}


@pytest.mark.parametrize("path", KEY_FILES, ids=lambda p: p.stem)
def test_stress_key_is_valid_and_matches_its_source(path: Path) -> None:
    """The same hash check the corpus keys get. A drifted key is worse than no key."""
    truth = GroundTruth.load(path)
    truth.verify_against(STRESS)
    assert truth.edges


@pytest.mark.parametrize("path", KEY_FILES, ids=lambda p: p.stem)
def test_every_program_unit_is_labelled_or_declared_empty(path: Path) -> None:
    truth = GroundTruth.load(path)
    source = (STRESS / truth.package).read_text(encoding="utf-8")

    labelled = {edge.origin.unit.upper() for edge in truth.edges}
    declared_empty = {name.upper() for name in _DECLARED_EMPTY.findall(path.read_text(encoding="utf-8"))}

    unaccounted = sorted(units_in(source) - labelled - declared_empty)
    assert not unaccounted, (
        f"{path.name} says nothing about {len(unaccounted)} program unit(s): "
        f"{', '.join(unaccounted)}.\n"
        "Label them, or declare the omission with a `# NO LABELS: <UNIT>` line saying why. "
        "An unlabelled unit's correct edges score as false positives against the key, which "
        "reads as an analyser defect (finding S2-06)."
    )


def test_the_unit_scanner_actually_finds_units() -> None:
    """A regex that silently matched nothing would make the test above vacuously pass."""
    source = (STRESS / "stress_02_deep_coverage.sql").read_text(encoding="utf-8")
    found = units_in(source)
    assert len(found) >= 28
    assert "S2_LOCKING" in found  # a top-level procedure
    assert "PUBLISH" in found  # declared inside the package body
    assert "RUN_ALL" in found  # the one declared empty rather than labelled
