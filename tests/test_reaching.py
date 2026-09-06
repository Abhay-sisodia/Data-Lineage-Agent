"""Reaching definitions and loop convergence (T2.6).

Fixed-point iteration does not add edges in this IR — variables are first-class nodes, so
each statement contributes its own edges and the chain is complete after one pass. What it
answers is a different question: whether anything was assigned before a read, and whether
that holds on every path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.cfg import build_all
from lineage.analysis.defuse import collect_scopes, definitions_and_uses
from lineage.analysis.procedure import analyse_source
from lineage.analysis.reaching import (
    definitely_assigned,
    reaching_definitions,
)
from lineage.config import AnalysisConfig
from lineage.parsing.plsql import parse_program
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")
BAND1 = CORPUS / "adversarial" / "band1"


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def _facts(source: str, unit: str):
    program = parse_program(source)
    cfg = build_all(program)[unit]
    scope = collect_scopes(program)[unit]

    definitions: dict[int, set[str]] = {}
    uses: dict[int, set[str]] = {}
    for node in cfg.nodes.values():
        defined, used = definitions_and_uses(node, scope)
        if defined:
            definitions[node.id] = defined
        if used:
            uses[node.id] = used
    return cfg, scope, definitions, uses


# --- convergence and the cap ---------------------------------------------------------


@pytest.mark.parametrize("name", sorted(p.stem for p in BAND1.glob("*.sql")))
def test_converges_on_every_band1_package(name: str) -> None:
    """Loops must reach a stable state within the configured cap."""
    source = (BAND1 / f"{name}.sql").read_text(encoding="utf-8")
    program = parse_program(source)
    scopes = collect_scopes(program)

    for unit, cfg in build_all(program).items():
        scope = scopes.get(unit)
        if scope is None:
            continue
        definitions: dict[int, set[str]] = {}
        for node in cfg.nodes.values():
            defined, _ = definitions_and_uses(node, scope)
            if defined:
                definitions[node.id] = defined
        result = reaching_definitions(cfg, definitions, iteration_cap=10)
        assert result.converged, f"{unit} did not converge within the cap"


def test_non_convergence_is_reported_not_hidden() -> None:
    """A limit that silently truncates is a hidden defect.

    Forcing an impossible cap must produce a declared boundary, not a quietly partial
    answer that looks complete.
    """
    source = (BAND1 / "b1_03_loops.sql").read_text(encoding="utf-8")
    cfg, _, definitions, _ = _facts(source, "B1_LOOPS")

    result = reaching_definitions(cfg, definitions, iteration_cap=1)
    assert not result.converged
    assert result.iterations == 1


def test_configured_cap_is_the_one_used(dictionary: Dictionary) -> None:
    """The cap is config, and appears in the declared limits."""
    assert "loop_fixpoint_iteration_cap" in AnalysisConfig().declared_limits()


# --- loop-carried dependence ---------------------------------------------------------


def test_definition_later_in_a_loop_reaches_an_earlier_use() -> None:
    """The back edge is what makes this iterative.

    `v_prev := v_curr` reads a variable assigned BELOW it. On the second iteration that
    read sees the previous iteration's value; a single forward pass would miss it
    entirely and report a chain one iteration deep.
    """
    source = """
    CREATE OR REPLACE PROCEDURE t_carried IS
      v_prev NUMBER;
      v_curr NUMBER;
    BEGIN
      FOR rec IN (SELECT gross_amount FROM stg_orders) LOOP
        v_prev := v_curr;
        v_curr := rec.gross_amount;
      END LOOP;
    END;
    """
    cfg, _, definitions, _ = _facts(source, "T_CARRIED")

    read_node = next(n for n in cfg.statement_nodes() if "v_prev := v_curr" in n.label.lower())
    write_node = next(n for n in cfg.statement_nodes() if "v_curr := rec" in n.label.lower())

    result = reaching_definitions(cfg, definitions, iteration_cap=10)
    reaching = result.reaching(read_node.id, "V_CURR")

    assert write_node.id in reaching, (
        "the definition below the read did not reach it - the loop back edge was ignored"
    )


# --- uninitialised and partial reads --------------------------------------------------


def test_conditional_assignment_is_reported_as_partial(dictionary: Dictionary) -> None:
    """The spike document's worked example.

    `v_sql` is assigned only inside the branch. An edge sourced from it is conditional on
    a path that may never have run, and reporting it flat overstates what is known.
    """
    source = """
    CREATE OR REPLACE PROCEDURE t_conditional(p_region VARCHAR2) IS
      v_sql VARCHAR2(400);
    BEGIN
      IF p_region = 'EU' THEN
        v_sql := 'x';
      END IF;
      UPDATE dim_customer SET is_active = 1 WHERE region = v_sql;
    END;
    """
    boundaries = analyse_source(source, dictionary, AnalysisConfig()).boundaries
    partial = [b for b in boundaries if "only assigned on some paths" in b]
    assert partial, "a conditionally assigned variable was reported as unconditional"
    assert "V_SQL" in partial[0]


def test_declaration_default_counts_as_assigned(dictionary: Dictionary) -> None:
    """`v_total NUMBER := 0` is assigned before the first statement runs."""
    source = (BAND1 / "b1_03_loops.sql").read_text(encoding="utf-8")
    boundaries = analyse_source(source, dictionary, AnalysisConfig()).boundaries
    assert not [b for b in boundaries if "V_TOTAL" in b and "read at line" in b]


def test_for_loop_index_is_not_uninitialised(dictionary: Dictionary) -> None:
    """The index is defined by the loop header, not by any statement."""
    source = (BAND1 / "b1_03_loops.sql").read_text(encoding="utf-8")
    boundaries = analyse_source(source, dictionary, AnalysisConfig()).boundaries
    assert not [b for b in boundaries if b.strip().startswith("B1_LOOPS: I is read")]


def test_parameters_are_not_uninitialised(dictionary: Dictionary) -> None:
    """A parameter is assigned by the caller, outside this CFG."""
    source = (BAND1 / "b1_01_local_variables.sql").read_text(encoding="utf-8")
    boundaries = analyse_source(source, dictionary, AnalysisConfig()).boundaries
    assert not [b for b in boundaries if "P_REGION is read" in b]


def test_package_state_is_reported_as_cross_unit_not_wrong(dictionary: Dictionary) -> None:
    """`apply_policy` reads state that only `load_policy` assigns.

    Within this unit nothing assigns it, but the edge is real - what is conditional is
    CALL ORDER. Calling apply_policy without load_policy in the same session leaves the
    cutoff NULL and the filter matching nothing, which is exactly what the week-1
    observation harness hit before the calls were run in one session.
    """
    source = (BAND1 / "b1_07_package_variables.sql").read_text(encoding="utf-8")
    boundaries = analyse_source(source, dictionary, AnalysisConfig()).boundaries

    # Two boundaries mention this variable: the band-0 analyser declares it as an
    # identifier it could not resolve, and the reaching analysis explains why.
    cross_unit = [b for b in boundaries if "G_CUTOFF" in b and "read at line" in b]
    assert cross_unit, f"no reaching finding for G_CUTOFF in {boundaries}"
    assert "assigned in another program unit" in cross_unit[0]
    assert "call order" in cross_unit[0]
    assert "wrong" not in cross_unit[0], "a real edge was described as an error"


# --- must-analysis -------------------------------------------------------------------


def test_definitely_assigned_is_an_intersection() -> None:
    """A variable assigned on only one arm of a branch is not certainly assigned."""
    source = """
    CREATE OR REPLACE PROCEDURE t_must(p_flag NUMBER) IS
      v_a NUMBER;
      v_b NUMBER;
    BEGIN
      v_a := 1;
      IF p_flag = 1 THEN
        v_b := 2;
      END IF;
      UPDATE dim_customer SET is_active = v_a WHERE cust_id = v_b;
    END;
    """
    cfg, _, definitions, _ = _facts(source, "T_MUST")
    certain = definitely_assigned(cfg, definitions, iteration_cap=10)

    update = next(n for n in cfg.statement_nodes() if "UPDATE" in n.label.upper())
    assert "V_A" in certain[update.id], "assigned on every path"
    assert "V_B" not in certain[update.id], "assigned on only one arm"


def test_uninitialised_read_is_reported(dictionary: Dictionary) -> None:
    """No assignment anywhere: the value is NULL and any claimed source is wrong."""
    source = """
    CREATE OR REPLACE PROCEDURE t_never IS
      v_never VARCHAR2(10);
    BEGIN
      UPDATE dim_customer SET is_active = 1 WHERE region = v_never;
    END;
    """
    boundaries = analyse_source(source, dictionary, AnalysisConfig()).boundaries
    assert [b for b in boundaries if "V_NEVER" in b and "no assignment reaching it" in b]
