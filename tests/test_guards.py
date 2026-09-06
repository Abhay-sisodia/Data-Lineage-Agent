"""Branch guards (T2.4).

An edge that only fires for EU customers is a different fact from an unconditional one,
and a regulator will ask exactly that. Guards are measured, reported, and deliberately
kept out of the precision gate so it cannot move on string-comparison noise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.harness.scoring import normalise_guard
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def _edges(package: str, dictionary: Dictionary) -> list:
    source = (CORPUS / "adversarial" / package).read_text(encoding="utf-8")
    return analyse_source(source, dictionary, AnalysisConfig()).edges


# --- normalisation -------------------------------------------------------------------


def test_operand_order_does_not_matter() -> None:
    assert normalise_guard("p_region = 'EU'") == normalise_guard("'EU' = P_REGION")


def test_conjunct_order_does_not_matter() -> None:
    """A guard is a set of conditions, not a sequence."""
    assert normalise_guard("A = 1 AND B = 2") == normalise_guard("B = 2 AND A = 1")


def test_negated_equality_matches_inequality() -> None:
    """`NOT (x = y)` and `x <> y` are the same condition written two ways.

    The CFG reconstructs an ELSE arm as the negation of preceding arms; a human writes
    the inequality directly. Neither is wrong.
    """
    assert normalise_guard("NOT (p_region = 'EU')") == normalise_guard("P_REGION <> 'EU'")


def test_different_conditions_stay_different() -> None:
    """The normaliser must not equate conditions that genuinely differ.

    Silently collapsing two different guards would be far worse than reporting a
    difference that turns out to be cosmetic.
    """
    assert normalise_guard("x > 5") != normalise_guard("x < 5")
    assert normalise_guard("A = 1 AND B = 2") != normalise_guard("A = 1")
    assert normalise_guard("p_region = 'EU'") != normalise_guard("p_region = 'APAC'")


def test_none_is_preserved() -> None:
    """An unconditional edge is a distinct claim from a guarded one."""
    assert normalise_guard(None) is None


# --- attachment ----------------------------------------------------------------------


def test_branch_edges_carry_their_guard(dictionary: Dictionary) -> None:
    guarded = [e for e in _edges("band1/b1_02_if_case.sql", dictionary) if e.guard]
    assert guarded, "every edge in b1_02 sits inside a branch"


def test_nested_guards_are_conjoined(dictionary: Dictionary) -> None:
    """The APAC arm is governed by the outer ELSIF and the inner IF.

    Reporting only the outer condition claims the edge fires for every APAC run.
    """
    edges = _edges("band1/b1_02_if_case.sql", dictionary)
    apac = [e for e in edges if e.guard and "P_STRICT" in e.guard.upper()]
    assert apac

    # Written the way a human would write it; the analyser reconstructs the ELSIF
    # negation from the CFG. Both must normalise to the same condition.
    expected = normalise_guard("P_REGION <> 'EU' AND P_REGION = 'APAC' AND P_STRICT = 1")
    for edge in apac:
        assert normalise_guard(edge.guard) == expected


def test_set_based_edges_inside_a_loop_are_guarded(dictionary: Dictionary) -> None:
    """The set-based analyser never sees control flow, so guards are applied afterwards.

    Without this, an INSERT inside `WHILE v_total > 100` claims the write always happens
    - a wrong answer rather than an incomplete one.
    """
    edges = _edges("band1/b1_03_loops.sql", dictionary)
    # Origin is the statement's first line, which is where the INSERT begins.
    inside_while = [e for e in edges if e.origin.line == 27]
    assert inside_while, "no edges found for the INSERT inside the WHILE loop"

    expected = normalise_guard("v_total > 100")
    for edge in inside_while:
        assert edge.guard is not None, f"{edge.source} -> {edge.target} lost its loop guard"
        assert normalise_guard(edge.guard) == expected


def test_for_loop_contributes_no_guard(dictionary: Dictionary) -> None:
    """A FOR loop's body always runs; its iteration spec is not a condition.

    The loop variable's real influence - deciding WHICH rows are read - is carried as a
    filter edge instead, which is where it belongs.
    """
    edges = _edges("band1/b1_03_loops.sql", dictionary)
    inside_for = [e for e in edges if e.origin.line == 15]
    assert inside_for
    for edge in inside_for:
        assert edge.guard is None, "a FOR iteration spec was reported as a guard"

    loop_variable = [
        e for e in edges if str(e.source) == "variable:I" and str(e.target) == "relation:STG_ORDERS"
    ]
    assert loop_variable, "the loop counter's row-selection influence was lost"


def test_unconditional_edges_carry_no_guard(dictionary: Dictionary) -> None:
    edges = _edges("band1/b1_01_local_variables.sql", dictionary)
    assert edges
    assert all(e.guard is None for e in edges), "a straight-line procedure has no guards"


def test_exception_path_edges_are_guarded_by_the_exception(dictionary: Dictionary) -> None:
    """An error-path write must not look unconditional."""
    edges = _edges("band1/b1_09_exception_handlers.sql", dictionary)
    handler = [e for e in edges if e.guard and "EXCEPTION" in e.guard.upper()]
    assert handler, "the NO_DATA_FOUND write was reported as unconditional"
