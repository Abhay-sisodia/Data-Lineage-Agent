"""Dynamic SQL: recover what is decidable, refuse the rest loudly (T3.2).

Two failure directions, and they are not symmetric.

Refusing a statement that is fully knowable is expensive but visible - it shows up as a
declared boundary and as lost recall. Folding the constant fragments of a statement that
is NOT fully knowable produces a confident wrong answer with no symptom, which is the
failure mode this whole project exists to avoid. So constant-ness is a must property, and
most of these tests are about the refusals.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.dynamic import fold_constant, resolve_dynamic_sql
from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow
from lineage.parsing.plsql import parse_program
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def _resolve(relative: str):
    return resolve_dynamic_sql(
        parse_program((CORPUS / "adversarial" / relative).read_text(encoding="utf-8"))
    )


def _analyse(relative: str, dictionary: Dictionary):
    return analyse_source(
        (CORPUS / "adversarial" / relative).read_text(encoding="utf-8"),
        dictionary,
        AnalysisConfig(),
    )


def _pairs(result) -> set[tuple[str, str]]:
    return {(str(e.source), str(e.target)) for e in result.edges}


# --- folding ---------------------------------------------------------------------------


def test_a_literal_is_its_own_value() -> None:
    assert fold_constant("'SELECT 1 FROM dual'", {}) == "SELECT 1 FROM dual"


def test_literals_concatenate() -> None:
    assert fold_constant("'INSERT INTO t ' || 'SELECT 1'", {}) == "INSERT INTO t SELECT 1"


def test_a_doubled_quote_is_one_quote() -> None:
    """`region = ''EU''` is a quoted literal inside SQL inside a PL/SQL literal.

    Getting this wrong truncates the recovered statement at the inner quote and produces
    something that still parses — a wrong statement, not an error.
    """
    assert fold_constant("'WHERE region = ''EU'''", {}) == "WHERE region = 'EU'"


def test_a_concatenation_operator_inside_a_literal_is_not_an_operator() -> None:
    assert fold_constant("'a || b'", {}) == "a || b"


def test_one_unknown_operand_makes_the_whole_expression_unknown() -> None:
    """MUST, not may. Half a statement resolves to a different statement."""
    assert fold_constant("'UPDATE ' || p_column || ' SET x = 1'", {}) is None


def test_a_known_variable_is_substituted() -> None:
    assert fold_constant("v_head || ' WHERE x = 1'", {"V_HEAD": "SELECT *"}) == (
        "SELECT * WHERE x = 1"
    )


def test_an_unterminated_literal_is_refused_rather_than_guessed() -> None:
    assert fold_constant("'SELECT", {}) is None


# --- b2_01: the cheap win --------------------------------------------------------------


def test_a_constant_assigned_then_executed_is_recovered() -> None:
    resolution = _resolve("band2/b2_01_dynamic_constant.sql")
    recovered = [site.recovered for site in resolution.sites]

    assert "UPDATE dim_customer SET is_active = 1 WHERE region = 'EU'" in recovered


def test_a_constant_assembled_from_constant_parts_is_recovered() -> None:
    resolution = _resolve("band2/b2_01_dynamic_constant.sql")
    assert any(
        text and text.startswith("INSERT INTO tmp_recent")
        for text in (site.recovered for site in resolution.sites)
    )


def test_recovered_edges_are_real_edges(dictionary: Dictionary) -> None:
    """Indistinguishable from static SQL once recovered — which is the point."""
    result = _analyse("band2/b2_01_dynamic_constant.sql", dictionary)
    pairs = _pairs(result)

    assert ("column:STG_CUSTOMER.CUST_ID", "column:TMP_RECENT.CUST_ID") in pairs
    assert ("column:STG_CUSTOMER.LAST_LOGIN", "column:TMP_RECENT.LAST_LOGIN") in pairs
    assert ("column:DIM_CUSTOMER.REGION", "relation:DIM_CUSTOMER") in pairs


def test_a_recovered_edge_stays_band_2(dictionary: Dictionary) -> None:
    """ADR-0001 §6 bands by the hardest construct on the PATH.

    The path runs through EXECUTE IMMEDIATE whether or not we could see through it.
    Crediting band 0 would let that band's score claim work it did not do.
    """
    result = _analyse("band2/b2_01_dynamic_constant.sql", dictionary)
    recovered = [
        e
        for e in result.edges
        if (str(e.source), str(e.target))
        == ("column:STG_CUSTOMER.CUST_ID", "column:TMP_RECENT.CUST_ID")
    ]
    assert recovered and all(e.band == 2 for e in recovered)


# --- b2_02: the refusal ----------------------------------------------------------------


def test_a_runtime_assembled_statement_is_refused_with_a_reason() -> None:
    resolution = _resolve("band2/b2_02_dynamic_concatenated.sql")

    assert resolution.sites
    assert all(site.recovered is None for site in resolution.sites)
    assert all("not known until runtime" in (site.refusal or "") for site in resolution.sites)


def test_a_refusal_becomes_a_declared_boundary(dictionary: Dictionary) -> None:
    result = _analyse("band2/b2_02_dynamic_concatenated.sql", dictionary)
    assert any("EXECUTE IMMEDIATE" in str(entry) for entry in result.boundaries)


def test_no_edge_is_invented_from_a_refused_statement(dictionary: Dictionary) -> None:
    """THE FAILURE THIS TASK EXISTS TO PREVENT.

    `'UPDATE dim_customer SET ' || p_column || ' = 1'` names a real table and a real
    column. Folding what is available and guessing the rest yields an edge that reads
    perfectly and is false.
    """
    result = _analyse("band2/b2_02_dynamic_concatenated.sql", dictionary)
    sources = {str(e.source) for e in result.edges}

    assert "variable:P_COLUMN" not in sources
    assert "variable:P_SUFFIX" not in sources
    assert "variable:V_SQL" not in sources


# --- statement carriers are not data ---------------------------------------------------


def test_a_statement_carrier_produces_no_def_use_edge(dictionary: Dictionary) -> None:
    """`p_column -> v_sql` is a true def-use fact and not a lineage fact.

    The variable carries statement TEXT. Nothing downstream carries p_column's value into
    a column, so the edge asserts something that never happens.
    """
    result = _analyse("band2/b2_02_dynamic_concatenated.sql", dictionary)
    assert not [e for e in result.edges if str(e.target) == "variable:V_SQL"]


def test_data_carrying_variables_are_untouched(dictionary: Dictionary) -> None:
    """The distinction is data versus text, NOT 'variables are noise'.

    `v_cutoff` decides which rows load and must survive; suppressing variables wholesale
    would delete the chain the whole spike exists to trace.
    """
    result = _analyse("band1/b1_01_local_variables.sql", dictionary)
    targets = {str(e.target) for e in result.edges}

    assert "variable:V_DAYS" in targets
    assert "variable:V_CUTOFF" in targets


def test_a_dbms_sql_cursor_handle_is_not_data(dictionary: Dictionary) -> None:
    """`v_rows := DBMS_SQL.EXECUTE(v_cursor)` — a handle is API plumbing.

    The row count genuinely derives from the statement's effect, which no static edge can
    express. Claiming the handle supplied it is a def-use chain with no meaning.
    """
    result = _analyse("band2/b2_03_dbms_sql.sql", dictionary)
    assert not [e for e in result.edges if str(e.source) == "variable:V_CURSOR"]


# --- constant is constant, whatever API submits it -------------------------------------


def test_a_constant_dbms_sql_statement_is_still_recovered() -> None:
    """b2_03 assembles its text across four assignments, all literal.

    The ladder calls DBMS_SQL "log recovery only", and that is right about the general
    case — piecewise parsing and dynamic binding defeat static analysis. It is not right
    about THIS case: the text is provably constant, so refusing it would be abstention
    for the sake of the API's reputation rather than for a reason.
    """
    resolution = _resolve("band2/b2_03_dbms_sql.sql")
    recovered = [site.recovered for site in resolution.sites if site.recovered]

    assert recovered
    assert "UPDATE dim_customer SET lifetime_value" in recovered[0]
    assert ":b_region" in recovered[0], (
        "the bind variable survives; it hides a value, not structure"
    )


# --- a conditional definition is not a constant ----------------------------------------


def test_an_assignment_inside_a_branch_poisons_the_constant() -> None:
    """b2_04 appends a WHERE clause only when the config row has one.

    Even if every fragment were literal, the statement would differ by path — and a
    constant that only sometimes holds is not a constant.
    """
    resolution = _resolve("band2/b2_04_metadata_driven_etl.sql")
    assert all(site.recovered is None for site in resolution.sites)


def test_the_metadata_driven_pipeline_invents_nothing(dictionary: Dictionary) -> None:
    """The worst case in the corpus must produce a boundary, not a guess."""
    result = _analyse("band2/b2_04_metadata_driven_etl.sql", dictionary)
    assert not [e for e in result.edges if str(e.target) == "variable:V_SQL"]
    assert any("EXECUTE IMMEDIATE" in str(entry) for entry in result.boundaries)


# --- s4: dynamic DDL -------------------------------------------------------------------


def test_dynamic_ddl_is_recovered_even_though_it_is_not_dml() -> None:
    """The exchange is a constant string; recovering it is the first half of s4.

    Turning the recovered ALTER TABLE into an edge is the second half and belongs to
    T3.4b — treating DDL in the log as lineage-bearing.
    """
    resolution = _resolve("silent/s4_partition_exchange.sql")
    recovered = [site.recovered for site in resolution.sites if site.recovered]

    assert recovered
    assert "EXCHANGE PARTITION" in recovered[0]


# --- band 2 is no longer zero ----------------------------------------------------------


def test_band_2_produces_its_first_edges(dictionary: Dictionary) -> None:
    result = _analyse("band2/b2_01_dynamic_constant.sql", dictionary)
    band2 = [e for e in result.edges if e.band == 2]

    assert [e for e in band2 if e.flow is Flow.VALUE]
    assert [e for e in band2 if e.flow is Flow.FILTER]
