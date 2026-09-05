"""Control flow graph construction (T2.2).

Node and edge counts below were worked out by hand from the source before being
asserted, so the CFG is checked against a reading of the code rather than against
itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.cfg import Cfg, EdgeKind, NodeKind, build_all
from lineage.parsing.plsql import parse_program

BAND1 = Path("corpus/adversarial/band1")


def _cfgs(name: str) -> dict[str, Cfg]:
    return build_all(parse_program((BAND1 / f"{name}.sql").read_text(encoding="utf-8")))


def _cfg(name: str, unit: str) -> Cfg:
    return _cfgs(name)[unit]


# --- shape ---------------------------------------------------------------------------


def test_straight_line_procedure() -> None:
    """Four statements, entry and exit, five sequential edges. Counted by hand."""
    cfg = _cfg("b1_01_local_variables", "B1_LOCAL_VARIABLES")
    assert len(cfg.nodes) == 6
    assert len(cfg.edges) == 5
    assert all(edge.kind is EdgeKind.SEQUENTIAL for edge in cfg.edges)
    assert len(cfg.statement_nodes()) == 4


def test_branches_produce_branch_nodes_and_arms() -> None:
    """IF / ELSIF-with-nested-IF / ELSE: 8 nodes, 10 edges."""
    cfg = _cfg("b1_02_if_case", "B1_IF_CASE")
    assert len(cfg.nodes) == 8
    assert len(cfg.edges) == 10
    branches = [n for n in cfg.nodes.values() if n.kind is NodeKind.BRANCH]
    assert len(branches) == 2, "the outer IF and the nested IF"


def test_loops_produce_a_back_edge() -> None:
    """Without the back edge, def-use reports an accumulation one iteration deep."""
    cfg = _cfg("b1_03_loops", "B1_LOOPS")
    back_edges = [e for e in cfg.edges if e.kind is EdgeKind.LOOP_BACK]
    assert len(back_edges) == 2, "one per loop"
    for edge in back_edges:
        assert cfg.nodes[edge.target].kind is NodeKind.LOOP


def test_exception_handlers_are_edges_from_every_protected_statement() -> None:
    """An exception can be raised by any statement, so every one reaches every handler.

    b1_09 has three statements in the protected block and two handlers.
    """
    cfg = _cfg("b1_09_exception_handlers", "B1_EXCEPTION_HANDLERS")
    handlers = [n for n in cfg.nodes.values() if n.kind is NodeKind.HANDLER]
    assert len(handlers) == 2

    exceptional = cfg.exceptional_edges
    assert len(exceptional) == 6, "3 protected statements x 2 handlers"
    assert {cfg.nodes[e.target].kind for e in exceptional} == {NodeKind.HANDLER}


def test_cursor_constructs_build_without_error() -> None:
    """OPEN / FETCH / CLOSE and cursor FOR loops must not break construction."""
    for_loop = _cfg("b1_04_cursor_for_loop", "B1_CURSOR_FOR_LOOP")
    assert any(n.kind is NodeKind.LOOP for n in for_loop.nodes.values())

    explicit = _cfg("b1_05_explicit_cursor", "B1_EXPLICIT_CURSOR")
    kinds = {n.statement_kind for n in explicit.statement_nodes()}
    assert "open_statement" in kinds or "cursor_manipulation_statements" in kinds


def test_every_unit_in_a_package_gets_its_own_cfg() -> None:
    """Package bodies declare procedures that never nest inside a create_procedure_body."""
    graphs = _cfgs("b1_07_package_variables")
    assert {"LOAD_POLICY", "APPLY_POLICY"} <= set(graphs)


# --- guards --------------------------------------------------------------------------


def test_nested_branch_guards_are_conjoined() -> None:
    """The APAC arm is governed by two conditions.

    Reporting only the outer one claims the edge fires for every APAC run, which is
    false - and a regulator will ask exactly that.
    """
    cfg = _cfg("b1_02_if_case", "B1_IF_CASE")
    apac = next(n for n in cfg.statement_nodes() if "MAX(status_code)" in n.label)
    guards = cfg.guards_reaching(apac.id)

    assert any("NOT (p_region = 'EU')" in g for g in guards)
    assert any("p_region = 'APAC'" in g for g in guards)
    assert any("p_strict = 1" in g for g in guards)


def test_else_condition_is_reconstructed() -> None:
    """The ELSE arm's condition appears nowhere in the source."""
    cfg = _cfg("b1_02_if_case", "B1_IF_CASE")
    else_arm = next(n for n in cfg.statement_nodes() if "CASE WHEN" in n.label)
    guards = cfg.guards_reaching(else_arm.id)

    assert any("NOT (p_region = 'EU')" in g for g in guards)
    assert any("NOT (p_region = 'APAC')" in g for g in guards)


def test_merge_point_carries_no_branch_guard() -> None:
    """The COMMIT after the IF runs whichever arm was taken.

    Taking the first predecessor instead would claim it only runs for EU customers.
    """
    cfg = _cfg("b1_02_if_case", "B1_IF_CASE")
    commit = next(n for n in cfg.statement_nodes() if n.label.strip().upper() == "COMMIT")
    assert cfg.guards_reaching(commit.id) == []


def test_handler_body_is_guarded_by_its_exception() -> None:
    """An error-path write must not look unconditional."""
    cfg = _cfg("b1_09_exception_handlers", "B1_EXCEPTION_HANDLERS")
    fallback = next(n for n in cfg.statement_nodes() if "is_active = 0" in n.label)
    assert cfg.guards_reaching(fallback.id) == ["EXCEPTION NO_DATA_FOUND"]


def test_for_loop_exit_is_not_a_condition() -> None:
    """A FOR loop always completes.

    Negating its iteration spec would attach a meaningless guard to every statement
    after the loop.
    """
    cfg = _cfg("b1_03_loops", "B1_LOOPS")
    after_for = next(n for n in cfg.statement_nodes() if "lifetime_value = v_total" in n.label)
    assert cfg.guards_reaching(after_for.id) == []


def test_while_loop_exit_is_a_condition() -> None:
    """A WHILE loop's negated condition genuinely governs what follows it."""
    cfg = _cfg("b1_03_loops", "B1_LOOPS")
    commit = next(n for n in cfg.statement_nodes() if n.label.strip().upper() == "COMMIT")
    assert cfg.guards_reaching(commit.id) == ["NOT (v_total > 100)"]


# --- termination ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    sorted(p.stem for p in BAND1.glob("*.sql")),
)
def test_construction_terminates_on_every_band1_package(name: str) -> None:
    """Loops and nesting must not hang the analyser."""
    graphs = _cfgs(name)
    assert graphs, f"{name} produced no CFG"
    for cfg in graphs.values():
        assert cfg.entry in cfg.nodes
        assert cfg.exit in cfg.nodes
        # Guard computation walks predecessors; cycles must not recurse forever.
        for node_id in cfg.nodes:
            cfg.guards_reaching(node_id)
