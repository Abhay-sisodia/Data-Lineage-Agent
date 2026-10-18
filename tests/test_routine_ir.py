"""semantic-ir T2 / T5: routine statements lowered by meaning; one DYN for every position."""

from __future__ import annotations

import logging
import re
from itertools import pairwise
from pathlib import Path

import pytest

from lineage.analysis import routine_ir
from lineage.analysis.procedure import analyse_source
from lineage.analysis.routine_ir import Prim, dynamic_text, format_text, output_columns
from lineage.config import AnalysisConfig
from lineage.parsing import plpgsql as pl
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "stress" / "pg" / "grammar" / "dictionary.json"


@pytest.fixture(scope="module")
def book() -> Dictionary:
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    return Dictionary.load(BOOK)


def _pairs(result) -> set[tuple[str, str]]:
    return {(str(e.source), str(e.target)) for e in result.edges}


# --- the lowering table -----------------------------------------------------------------------


def test_every_form_the_reader_names_has_a_primitive() -> None:
    """A form the reader can produce and the table does not map would be refused - silently
    becoming the next holdout's gap. Read from the reader's source, so a new form fails here."""
    source = (ROOT / "src" / "lineage" / "parsing" / "plpgsql.py").read_text(encoding="utf-8")
    kinds = set(re.findall(r'Stmt\(\s*"(\w+)"', source))
    assert kinds, "no Stmt kinds found in the reader"
    assert kinds <= set(routine_ir.LOWERING), kinds - set(routine_ir.LOWERING)
    assert all(Prim.OPAQUE not in routine_ir.LOWERING[k] for k in kinds - {"opaque"})


# --- DYN ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "arguments", "expected"),
    [
        ("%1$I.%2$I", ["core", "fact_encounter"], "core.fact_encounter"),  # holdout4 H7
        ("%s-%s", ["a", "b"], "a-b"),
        ("%2$s %1$s %s", ["a", "b"], "b a b"),  # an unpositioned one continues after
        ("%I", ["Mixed"], '"Mixed"'),
        ("%L", ["o'k"], "'o''k'"),
        ("100%%", [], "100%"),
        ("%s %s", ["a"], None),  # an argument it does not have
        ("%s", [None], None),  # an argument that is not a constant
    ],
)
def test_format_text_is_postgres_format(template, arguments, expected) -> None:
    assert format_text(template, arguments) == expected


def test_dynamic_text_binds_placeholders_to_using_outside_literals() -> None:
    text = dynamic_text(
        "'SELECT a.v, ''$1'' AS tag FROM ' || 'src.a AS a WHERE a.k = $1'", {}, "p_k"
    )
    assert text == "SELECT a.v, '$1' AS tag FROM src.a AS a WHERE a.k = (p_k)"


def test_dynamic_text_without_using_reads_placeholders_as_external_values() -> None:
    assert dynamic_text("'SELECT a.v FROM src.a AS a WHERE a.k = $1'", {}) == (
        "SELECT a.v FROM src.a AS a WHERE a.k = NULL"
    )


def test_dynamic_text_of_a_variable_folds_through_constants() -> None:
    assert dynamic_text("v_sql", {"v_sql": "SELECT 1"}) == "SELECT 1"
    assert dynamic_text("v_sql || p_table", {"v_sql": "SELECT * FROM "}) is None


# --- EMIT: a routine's output shape -------------------------------------------------------------


def _routine(text: str) -> pl.Routine:
    routine = pl.read_routine(text, 0, len(text))
    assert routine is not None
    return routine


def test_output_columns_of_returns_table() -> None:
    routine = _routine(
        "CREATE FUNCTION s.f(p int) RETURNS TABLE (a int, b text) LANGUAGE sql "
        "AS $$ SELECT 1, 'x' $$"
    )
    assert output_columns(routine) == ["a", "b"]


def test_output_columns_of_out_and_inout_parameters() -> None:
    """holdout4 H5: `core.fn_staff_rate(p_role, ..., INOUT p_is_agency, OUT o_hourly, OUT
    o_uplift)` - no RETURNS; its row is the OUT / INOUT parameters, in order."""
    routine = _routine(
        "CREATE FUNCTION s.f(p_role text, INOUT p_is_agency boolean, OUT o_hourly numeric, "
        "OUT o_uplift numeric) LANGUAGE plpgsql AS $$ BEGIN NULL; END $$"
    )
    assert routine.returns == "record"
    assert output_columns(routine) == ["p_is_agency", "o_hourly", "o_uplift"]
    assert routine_ir.yields_rows(routine)


def test_returns_record_with_out_parameters_is_a_record() -> None:
    routine = _routine(
        "CREATE FUNCTION s.f(OUT a int, OUT b int) RETURNS record LANGUAGE plpgsql "
        "AS $$ BEGIN a := 1; b := 2; END $$"
    )
    assert routine.returns == "record" and output_columns(routine) == ["a", "b"]


def test_output_columns_of_setof_a_table(book) -> None:
    routine = _routine(
        "CREATE FUNCTION s.f() RETURNS SETOF src.b LANGUAGE sql AS $$ SELECT * FROM src.b $$"
    )
    assert routine.setof == "src.b"
    assert output_columns(routine, book) == ["id", "k", "v", "n", "d"]


# --- end to end: holdout4 H7's shape --------------------------------------------------------------

H7 = """CREATE FUNCTION tgt.f_rows(p_k integer, p_id integer)
RETURNS TABLE (o_id integer, o_v integer) LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_sql text;
BEGIN
  v_sql := format($q$
      SELECT a.id, a.v FROM %1$I.%2$I AS a WHERE a.k = $1 AND a.id > $2
  $q$, 'src', 'a');
  RETURN QUERY EXECUTE v_sql USING p_k, p_id;
END $$;

INSERT INTO tgt.t (id, v)
SELECT r.o_id, r.o_v
FROM src.b AS b
CROSS JOIN LATERAL tgt.f_rows(p_id => b.id, p_k => b.k) AS r;
"""


def test_return_query_execute_of_constant_text_is_traced_through_the_call(book) -> None:
    """holdout4 H7: RETURN QUERY EXECUTE of `format()` with constant (positional) arguments
    was refused, the function had no output and `mart.encounter_cost` lost 52 columns. Its
    rows are those of the folded query, read through the call like RETURN QUERY's."""
    result = analyse_source(H7, book, AnalysisConfig(dialect="postgres"))
    assert result.refusals == []
    pairs = _pairs(result)
    assert ("column:src.a.id", "column:tgt.t.id") in pairs
    assert ("column:src.a.v", "column:tgt.t.v") in pairs
    # D-42: the USING values are external, so the caller's arguments filter nothing here.
    filters = {str(e.source) for e in result.edges if e.flow.value == "filter"}
    assert not {"column:src.b.k", "column:src.b.id"} & filters


def test_binding_using_traces_the_callers_arguments_into_the_dynamic_where(
    book, monkeypatch
) -> None:
    """With `BIND_USING` (pending decision, semantic-ir T0) `$n` is the n-th USING
    expression, and the call's named arguments bind the parameters to the caller's columns."""
    from lineage.analysis import plpgsql

    monkeypatch.setattr(plpgsql, "BIND_USING", True)
    result = analyse_source(H7, book, AnalysisConfig(dialect="postgres"))
    filters = {str(e.source) for e in result.edges if e.flow.value == "filter"}
    assert {"column:src.b.k", "column:src.b.id"} <= filters


# --- EMIT into the routine's output (semantic-ir T3) ----------------------------------------------

OUT_PARAMS = """CREATE FUNCTION tgt.f_rate(p_k integer, INOUT p_flag boolean, OUT o_v integer,
                           OUT o_n integer)
LANGUAGE plpgsql AS $$
BEGIN
  SELECT a.v, a.n INTO STRICT o_v, o_n FROM src.a AS a WHERE a.k = p_k;
END $$;

INSERT INTO tgt.t (id, v, n)
SELECT b.id, r.o_v, r.o_n FROM src.b AS b
CROSS JOIN LATERAL tgt.f_rate(b.k, true) AS r;
"""

RETURN_NEXT = """CREATE FUNCTION tgt.f_alloc(p_k integer) RETURNS SETOF src.b LANGUAGE plpgsql AS $$
DECLARE
  v_row src.b%ROWTYPE;
  r record;
BEGIN
  RETURN QUERY SELECT a.id, a.k, a.v, a.n, a.d FROM src.a AS a WHERE a.k = p_k;
  FOR r IN SELECT a.id, a.w FROM src.a AS a LOOP
    v_row.id := r.id;
    v_row.v := r.w * 2;
    RETURN NEXT v_row;
  END LOOP;
  RETURN;
END $$;

INSERT INTO tgt.t (id, v)
SELECT x.id, x.v FROM tgt.f_alloc(3) AS x;
"""


def _path(result, *nodes: str) -> bool:
    """Every consecutive pair of `nodes` is an edge of `result`."""
    pairs = _pairs(result)
    return all((a, b) in pairs for a, b in pairwise(nodes))


def test_an_out_parameter_routine_read_in_lateral_is_traced(book) -> None:
    """holdout4 H5: OUT / INOUT parameters and no RETURNS - its row is its OUT values, read
    through its output relation; the caller's argument reaches the routine's WHERE."""
    result = analyse_source(OUT_PARAMS, book, AnalysisConfig(dialect="postgres"))
    assert result.refusals == [] and not result.boundaries
    assert _path(result, "column:src.a.v", "variable:tgt.f_rate.o_v", "column:tgt.f_rate.o_v",
                 "column:tgt.t.v")  # fmt: skip
    assert ("column:src.b.k", "variable:tgt.f_rate.p_k") in _pairs(result)
    assert result.ledger[0].status == "no_lineage"  # lineage is where it is called


def test_return_next_of_a_record_built_field_by_field_is_traced(book) -> None:
    """holdout4 H6: RETURN NEXT of a %ROWTYPE record assigned field by field, beside RETURN
    QUERY, was "built procedurally" and refused; both now emit into its output."""
    result = analyse_source(RETURN_NEXT, book, AnalysisConfig(dialect="postgres"))
    assert result.refusals == [] and not result.boundaries
    assert _path(result, "column:src.a.w", "column:tgt.f_alloc.v", "column:tgt.t.v")
    assert _path(result, "column:src.a.v", "column:tgt.f_alloc.v", "column:tgt.t.v")
    assert result.statements_seen == 2  # the routine and the INSERT; nothing synthetic counted


# --- ASSIGN / BIND to NEW fields (semantic-ir T4) ----------------------------------------------

TRIGGER = """CREATE FUNCTION tgt.trg_enrich() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.w := lower(NEW.v::text);
  SELECT b.n, b.d INTO NEW.n, NEW.d
  FROM src.b AS b
  WHERE NEW.k = b.k
  ORDER BY b.id DESC
  LIMIT 1;
  IF NOT FOUND THEN
    NEW.n := 0;
  END IF;
  OLD.v := NEW.v;
  RETURN NEW;
END $$;

CREATE TRIGGER t_enrich BEFORE INSERT OR UPDATE ON tgt.t
  FOR EACH ROW EXECUTE FUNCTION tgt.trg_enrich();

INSERT INTO tgt.t (id, k, v) SELECT a.id, a.k, a.v FROM src.a AS a;
"""


def test_new_fields_set_in_a_before_trigger_are_writes_of_its_table(book) -> None:
    """holdout4 H8: `NEW.f := e` was refused and `SELECT .. INTO NEW.f` was read as a plain
    variable `f`. Both write the trigger table's column; NEW.k in the lookup's WHERE chooses
    the row read - a filter of the table, not a value."""
    result = analyse_source(TRIGGER, book, AnalysisConfig(dialect="postgres"))
    assert result.refusals == []
    pairs = _pairs(result)
    assert ("column:src.b.n", "column:tgt.t.n") in pairs
    assert ("column:src.b.d", "column:tgt.t.d") in pairs
    assert ("column:tgt.t.v", "column:tgt.t.w") in pairs
    filters = {(str(e.source), str(e.target)) for e in result.edges if e.flow.value == "filter"}
    assert ("column:tgt.t.k", "relation:tgt.t") in filters
    assert ("column:tgt.t.k", "column:tgt.t.n") not in pairs  # not a value of the lookup
    assert not any(str(e.target) == "variable:n" for e in result.edges)  # no stray variable


# --- BEGIN ATOMIC bodies (semantic-ir T6) ------------------------------------------------------

ATOMIC = """CREATE FUNCTION tgt.fn_label(p_id integer)
RETURNS text
LANGUAGE sql
STABLE
BEGIN ATOMIC
    SELECT CASE WHEN b.k > 0 THEN b.v::text ELSE 'none' END || ';' || b.d::text
    FROM src.b AS b
    WHERE b.id = p_id;
END;

INSERT INTO tgt.t (id, w) SELECT a.id, tgt.fn_label(a.id) FROM src.a AS a;
"""


def test_a_begin_atomic_body_is_one_statement_and_its_value_is_traced(book) -> None:
    """holdout4 H2: the body was split at its inner `;` (and at the one in the literal is
    not a risk) - the CREATE FUNCTION was not valid and the call lost its lineage."""
    from lineage.parsing.sqlscript import split_statements

    assert [line for line, _ in split_statements(ATOMIC)] == [1, 11]
    result = analyse_source(ATOMIC, book, AnalysisConfig(dialect="postgres"))
    assert result.refusals == []
    assert {e.status for e in result.ledger} <= {"no_lineage", "column_level"}
    pairs = _pairs(result)
    assert ("column:src.b.v", "column:tgt.t.w") in pairs
    assert ("column:src.b.d", "column:tgt.t.w") in pairs


# --- overloads (holdout5 B) ----------------------------------------------------------------------

OVERLOADS = """CREATE FUNCTION tgt.to_usd(p_amount numeric, p_k integer, p_on date)
RETURNS numeric LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_rate numeric;
BEGIN
  SELECT b.n INTO v_rate FROM src.b AS b WHERE b.k = p_k AND b.d <= p_on;
  RETURN p_amount * v_rate;
END $$;

CREATE FUNCTION tgt.to_usd(p_amount numeric, p_k integer)
RETURNS numeric LANGUAGE sql STABLE AS $$
  SELECT tgt.to_usd(p_amount, p_k, a.d) FROM src.a AS a WHERE a.id = 1
$$;

INSERT INTO tgt.t (v, w)
SELECT tgt.to_usd(a.v, a.k, a.d), tgt.to_usd(a.w, a.k) FROM src.a AS a;
"""


def test_overloads_are_two_routines_and_each_call_runs_its_own(book) -> None:
    """holdout5 B: `fx_to_usd(amount, ccy, on)` then `fx_to_usd(amount, ccy)` - the second
    replaced the first, so every 3-argument call lost the rate it reads. Each overload is
    now its own routine (`<name>__<n>`), and a call runs the one its arguments pick."""
    result = analyse_source(OVERLOADS, book, AnalysisConfig(dialect="postgres"))
    assert result.refusals == []
    pairs = _pairs(result)
    assert ("column:src.a.v", "column:tgt.t.v") in pairs  # the 3-argument call
    assert ("column:src.b.n", "column:tgt.t.v") in pairs  # ... reads the rate
    assert ("column:src.a.w", "column:tgt.t.w") in pairs  # the 2-argument call
    assert ("column:src.b.n", "column:tgt.t.w") in pairs  # ... through the 3-argument one


RENAMED = """INSERT INTO tgt.t (id, v) SELECT a.id, a.v FROM src.a AS a;
ALTER TABLE tgt.t RENAME COLUMN v TO v_usd;
INSERT INTO src.b (id, v) SELECT t.id, t.v_usd FROM tgt.t AS t;
"""


def test_a_rename_after_a_load_carries_the_columns_contents(book) -> None:
    """holdout5: `carrier_cost` was loaded, then renamed `carrier_cost_usd`, and every later
    reader read the new name - which had no lineage. A rename is an identity edge from the
    old name to the new; the statement is still an effect in the ledger."""
    result = analyse_source(RENAMED, book, AnalysisConfig(dialect="postgres"))
    pairs = _pairs(result)
    assert ("column:tgt.t.v", "column:tgt.t.v_usd") in pairs
    assert ("column:tgt.t.v_usd", "column:src.b.v") in pairs
    assert [e.status for e in result.ledger][1] == "effect"
