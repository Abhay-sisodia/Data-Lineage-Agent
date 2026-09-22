"""The procedural front-end protocol, and the Oracle implementation of it.

A3 moved fourteen grammar-class lookups out of five analysis modules and behind
`lineage.parsing.frontend.Frontend`. Its only real requirement - that the signed
measurement does not move - is asserted by phase 0 and the stress grids, not here.

What this file pins is the protocol's contract from the analysis side: names come back AS
WRITTEN (the caller folds), the three `FOR ... IN` forms are told apart, nested units do
not leak their declarations into the enclosing one, and the Oracle front end actually
satisfies the Protocol rather than merely resembling it.
"""

from __future__ import annotations

from lineage.parsing.frontend import Frontend, IfShape, LoopParam, LoopShape
from lineage.parsing.plsql import ORACLE_FRONTEND, parse_program

SOURCE = """CREATE OR REPLACE PACKAGE BODY pkg_x IS
  g_state NUMBER := 0;

  PROCEDURE inner_p(p_arg VARCHAR2) IS
    v_local DATE;
    CURSOR c_rows IS SELECT o.cust_id FROM stg_orders o;
  BEGIN
    v_local := SYSDATE;
    FOR rec IN c_rows LOOP
      NULL;
    END LOOP;
    FOR r2 IN (SELECT 1 AS x FROM dual) LOOP
      NULL;
    END LOOP;
    FOR i IN 1 .. 10 LOOP
      NULL;
    END LOOP;
    FETCH c_rows INTO v_local;
  END inner_p;

  FUNCTION f_get RETURN NUMBER IS
  BEGIN
    RETURN g_state + 1;
  END f_get;
END pkg_x;
/

CREATE OR REPLACE PROCEDURE standalone(P_Mixed NUMBER) IS
  V_Outer NUMBER;
  PROCEDURE nested_helper IS
    v_hidden NUMBER;
  BEGIN
    NULL;
  END nested_helper;
BEGIN
  NULL;
EXCEPTION
  WHEN OTHERS THEN NULL;
END standalone;
/
"""


def test_the_oracle_front_end_satisfies_the_protocol() -> None:
    """`Frontend` is runtime-checkable, so this is a structural assertion, not a type hint."""
    assert isinstance(ORACLE_FRONTEND, Frontend)


def test_a_parsed_program_carries_the_front_end_that_produced_it() -> None:
    """The analysis asks `program.frontend`, never a grammar class.

    That is what makes a second dialect's program answer the same questions with its own
    parser - and what makes it impossible to ask an Oracle question of a PostgreSQL tree.
    """
    program = parse_program(SOURCE)
    assert program.frontend is ORACLE_FRONTEND


def test_units_are_reported_with_their_kind_and_as_written() -> None:
    """Packaged and standalone units are different kinds, because what state they can
    see differs. Names are NOT folded here - `standalone` comes back as written and the
    caller applies the dialect's rule, exactly as for every other identifier.

    `nested_helper` is reported as `packaged_procedure` too. That is the grammar's word,
    not the front end's: Oracle's `procedure_body` rule matches a procedure declared
    inside a package body AND one declared inside another unit, and the analysis has
    always collected both under that kind. The first draft of this test expected the
    nested one to be absent - the byte-identical measurement is what said the expectation
    was wrong rather than the code.
    """
    program = parse_program(SOURCE)
    units = {u.name: u.kind for u in ORACLE_FRONTEND.units(program.tree)}

    assert units == {
        "inner_p": "packaged_procedure",
        "f_get": "packaged_function",
        "standalone": "procedure",
        "nested_helper": "packaged_procedure",
    }


def test_names_are_not_folded_by_the_front_end() -> None:
    """Mixed case survives. Folding is the dialect's decision, made once, by the caller.

    If the front end folded, it would need to know the dialect's rule - a second place for
    that rule to live, and the S2-08 shape (one rule, two homes, drift) for a third time.
    """
    program = parse_program(SOURCE)
    standalone = next(u for u in ORACLE_FRONTEND.units(program.tree) if u.name == "standalone")

    assert [p.name for p in ORACLE_FRONTEND.parameters(standalone.ctx)] == ["P_Mixed"]
    assert [v.name for v in ORACLE_FRONTEND.variables(standalone.ctx)] == ["V_Outer"]


def test_a_nested_units_declarations_stay_inside_it() -> None:
    """`v_hidden` belongs to `nested_helper`, not to `standalone`.

    Leaking it outward would let a name declared in an inner block resolve in the outer
    one, which binds a read to the wrong variable silently.
    """
    program = parse_program(SOURCE)
    standalone = next(u for u in ORACLE_FRONTEND.units(program.tree) if u.name == "standalone")

    assert "v_hidden" not in {v.name for v in ORACLE_FRONTEND.variables(standalone.ctx)}


def test_the_three_loop_forms_are_told_apart() -> None:
    """Named cursor, inline query, numeric index - each sets exactly one field.

    The named-cursor form is stress finding S4-02: only the inline form was handled, so a
    `FOR rec IN c` loop registered no row source and `rec.field` bound to the target table.
    The front end has to distinguish them for that fix to have anything to work with.
    """
    program = parse_program(SOURCE)
    inner = next(u for u in ORACLE_FRONTEND.units(program.tree) if u.name == "inner_p")
    params = ORACLE_FRONTEND.loop_params(inner.ctx)

    by_line = {p.line: p for p in params}
    forms = sorted(
        ("cursor" if p.cursor else "query" if p.query else "index" if p.index else "?")
        for p in by_line.values()
    )
    assert forms == ["cursor", "index", "query"]

    named = next(p for p in params if p.cursor)
    assert named == LoopParam(line=named.line, record="rec", cursor="c_rows")


def test_cursors_assignments_fetches_and_returns() -> None:
    """The statement-level questions, each on the statement the analysis would hand over."""
    program = parse_program(SOURCE)
    inner = next(u for u in ORACLE_FRONTEND.units(program.tree) if u.name == "inner_p")

    cursors = ORACLE_FRONTEND.cursors(inner.ctx)
    assert [(c.name, c.query) for c in cursors] == [
        ("c_rows", "SELECT o.cust_id FROM stg_orders o")
    ]

    # Statements are addressed through the program's statement list, as the CFG does.
    assignment_ctx = next(
        ctx
        for ctx in _statement_contexts(program.tree)
        if "v_local := SYSDATE" in _text(ctx)
    )
    assignment = ORACLE_FRONTEND.assignment(assignment_ctx)
    assert assignment is not None
    assert (assignment.target, assignment.expression) == ("v_local", "SYSDATE")

    fetch_ctx = next(ctx for ctx in _statement_contexts(program.tree) if "FETCH" in _text(ctx))
    fetch = ORACLE_FRONTEND.fetch(fetch_ctx)
    assert fetch is not None
    assert (fetch.cursor, fetch.targets) == ("c_rows", ("v_local",))

    f_get = next(u for u in ORACLE_FRONTEND.units(program.tree) if u.name == "f_get")
    assert ORACLE_FRONTEND.returns(f_get.ctx) == ["g_state + 1"]


def test_package_state_is_reachable_from_its_units() -> None:
    """`inner_p` sits inside `pkg_x`, so it can see `g_state`. `standalone` does not."""
    program = parse_program(SOURCE)
    bodies = ORACLE_FRONTEND.package_bodies(program.tree)
    assert [name for name, _ in bodies] == ["pkg_x"]

    units = {u.name: u for u in ORACLE_FRONTEND.units(program.tree)}
    assert ORACLE_FRONTEND.enclosing_package(units["inner_p"].ctx) == "pkg_x"
    assert ORACLE_FRONTEND.enclosing_package(units["standalone"].ctx) is None


def test_exception_handlers_are_found() -> None:
    program = parse_program(SOURCE)
    handlers = ORACLE_FRONTEND.exception_handlers(program.tree)
    assert len(handlers) == 1
    assert "OTHERS" in _text(handlers[0])


# --- control flow (A3b) --------------------------------------------------------------

CONTROL_FLOW = """CREATE OR REPLACE PROCEDURE p_flow(p_n NUMBER) IS
  v NUMBER := p_n;
BEGIN
  IF v > 10 THEN
    v := 10;
  ELSIF v > 5 THEN
    v := 5;
  ELSE
    v := 0;
  END IF;
  WHILE v > 0 LOOP
    v := v - 1;
  END LOOP;
  FOR i IN 1 .. 3 LOOP
    v := v + i;
  END LOOP;
  EXECUTE IMMEDIATE 'TRUNCATE TABLE gtt_stage';
EXCEPTION
  WHEN NO_DATA_FOUND OR TOO_MANY_ROWS THEN v := -1;
END p_flow;
/
"""


def test_a_branch_comes_back_as_a_shape_with_every_arm() -> None:
    """The CFG builder never learns how the grammar spelled IF/ELSIF/ELSE - only what the
    conditions are and which sequence each arm holds. That is what lets PL/pgSQL, which has
    the same shapes with different spellings, reuse the builder unchanged."""
    program = parse_program(CONTROL_FLOW)
    unit = ORACLE_FRONTEND.units(program.tree)[0]
    body = ORACLE_FRONTEND.body(unit.ctx)
    assert body is not None

    shapes = [ORACLE_FRONTEND.shape(s) for s in ORACLE_FRONTEND.statements_of(body.sequence)]
    branch = next(s for s in shapes if isinstance(s, IfShape))
    assert branch.condition == "v > 10"
    assert [c for c, _ in branch.elsifs] == ["v > 5"]
    assert branch.else_sequence is not None
    then_kinds = [
        ORACLE_FRONTEND.statement_kind(s)
        for s in ORACLE_FRONTEND.statements_of(branch.then_sequence)
    ]
    assert then_kinds == ["assignment_statement"]


def test_while_carries_a_condition_and_for_carries_only_a_label() -> None:
    """A guard is a condition under which an edge fires. A FOR body always runs once per
    iteration, so its iteration spec is a label and not a guard - treating it as one would
    say the write inside is conditional when it is not."""
    program = parse_program(CONTROL_FLOW)
    unit = ORACLE_FRONTEND.units(program.tree)[0]
    body = ORACLE_FRONTEND.body(unit.ctx)
    assert body is not None

    loops = [
        s
        for s in (ORACLE_FRONTEND.shape(x) for x in ORACLE_FRONTEND.statements_of(body.sequence))
        if isinstance(s, LoopShape)
    ]
    assert [(loop.condition, loop.label) for loop in loops] == [
        ("v > 0", "v > 0"),
        (None, "i IN 1 .. 3"),
    ]


def test_handlers_and_body_end_are_on_the_body_shape() -> None:
    program = parse_program(CONTROL_FLOW)
    unit = ORACLE_FRONTEND.units(program.tree)[0]
    body = ORACLE_FRONTEND.body(unit.ctx)
    assert body is not None
    assert [h.names for h in body.handlers] == ["NO_DATA_FOUND OR TOO_MANY_ROWS"]
    assert body.end_line > ORACLE_FRONTEND.line(unit.ctx)


def test_conditionality_and_dynamic_sites() -> None:
    """`dynamic.py` refuses to fold a constant from an assignment inside a branch or loop,
    so the front end has to say which ones those are; and it needs every EXECUTE IMMEDIATE
    with its argument text."""
    program = parse_program(CONTROL_FLOW)
    assignments = ORACLE_FRONTEND.assignments(program.tree)
    flags = [ORACLE_FRONTEND.is_conditional(a) for a in assignments]

    # Six statement-level assignments: three IF arms, the WHILE body, the FOR body - all
    # conditional - and one in the exception handler, which is NOT. A handler is reached
    # by an exception edge, not by a branch, and the original `_is_conditional` only ever
    # looked for IF and LOOP ancestors; this pins that reading rather than widening it.
    assert flags.count(True) == 5
    assert flags.count(False) == 1

    sites = ORACLE_FRONTEND.execute_immediates(program.tree)
    assert [arg for _, arg in sites] == ["'TRUNCATE TABLE gtt_stage'"]


# --- helpers: reach statements the way cfg.py does, without naming a grammar class here --


def _statement_contexts(tree: object) -> list[object]:
    from lineage.parsing.generated.PlSqlParser import PlSqlParser
    from lineage.parsing.plsql import iter_contexts

    return iter_contexts(tree, PlSqlParser.StatementContext)


def _text(ctx: object) -> str:
    from lineage.parsing.plsql import source_slice

    return source_slice(ctx)
