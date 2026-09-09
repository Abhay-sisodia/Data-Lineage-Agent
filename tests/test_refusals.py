"""The unanalysable-construct classifier (T3.1).

Every assertion here is about an ABSENCE, which is the awkward part of testing a refusal:
an analyser that has silently crashed produces the same empty edge list as one that
correctly declined. So the tests come in pairs. One side asserts the refusal exists, is
coded, and names the construct; the other asserts the control statement in the same file
still produces its edges. Neither half is worth much alone.

The cross-check is deliberately not written as "look at the output" - it is
`violations()`, which addresses every flagged statement by (unit, line span) and asserts
the edge set produced from it is empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.analysis.refusal import (
    CONSTRUCTS,
    RefusalCode,
    classify_program,
    classify_statement,
    conditional_compilation_spans,
    violations,
)
from lineage.config import AnalysisConfig
from lineage.parsing.plsql import parse_program
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")
LOUD = CORPUS / "adversarial" / "unanalysable" / "u1_loud_constructs.sql"


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


@pytest.fixture(scope="module")
def loud(dictionary: Dictionary):
    return analyse_source(LOUD.read_text(encoding="utf-8"), dictionary, AnalysisConfig())


def _analyse(relative: str, dictionary: Dictionary):
    return analyse_source(
        (CORPUS / "adversarial" / relative).read_text(encoding="utf-8"),
        dictionary,
        AnalysisConfig(),
    )


# --- the taxonomy ---------------------------------------------------------------------


def test_the_taxonomy_is_closed_and_every_code_is_documented() -> None:
    """A free-text reason cannot be counted and drifts into paraphrases of itself.

    The docstring requirement is not decoration: a code whose meaning is not written down
    gets used for whatever is nearest to hand, and then the distribution means nothing.
    """
    assert len(RefusalCode) == 9
    for code in RefusalCode:
        assert code.__doc__, f"{code.value} has no stated meaning"


def test_every_construct_maps_to_a_code_in_the_taxonomy() -> None:
    for construct in CONSTRUCTS:
        assert isinstance(construct.code, RefusalCode)
        assert construct.reason.strip()


def test_no_construct_refuses_a_database_link() -> None:
    """`b2_06` refuted the first draft of this list.

    The local half of `INSERT INTO dim_customer ... FROM remote_customer@crm_link` is
    fully provable - target columns, projection and filter are all in the local text.
    Refusing the statement would have destroyed seven labelled edges to buy nothing. A
    remote reference is a boundary node, not a refused statement.
    """
    assert classify_statement("SELECT r.cust_id FROM remote_customer@crm_link r") is None


# --- T3.1e: the loud-failure register gets explicit refusals, not crashes --------------


@pytest.mark.parametrize(
    ("fragment", "code"),
    [
        (
            "SELECT a FROM t MODEL DIMENSION BY (d) MEASURES (a) RULES (a[ANY] = 1)",
            RefusalCode.UNSUPPORTED_CONSTRUCT,
        ),
        (
            "SELECT * FROM t MATCH_RECOGNIZE (PATTERN (a b) DEFINE b AS b.x > a.x)",
            RefusalCode.UNSUPPORTED_CONSTRUCT,
        ),
        (
            "SELECT * FROM t PIVOT (SUM(x) FOR c IN (SELECT c FROM u))",
            RefusalCode.SHAPE_NOT_DECIDABLE,
        ),
        (
            "SELECT x FROM t START WITH p IS NULL CONNECT BY PRIOR id = p",
            RefusalCode.UNSUPPORTED_CONSTRUCT,
        ),
        (
            "SELECT x.a FROM t, XMLTABLE('/r' PASSING t.doc COLUMNS a NUMBER PATH 'a') x",
            RefusalCode.SHAPE_NOT_DECIDABLE,
        ),
        ("$IF $$flag $THEN NULL; $END", RefusalCode.CONTEXT_DEPENDENT),
        ("CREATE PROCEDURE p WRAPPED\na000000\n", RefusalCode.SOURCE_UNAVAILABLE),
    ],
)
def test_each_loud_construct_is_refused_by_name(fragment: str, code: RefusalCode) -> None:
    construct = classify_statement(fragment)
    assert construct is not None, f"not flagged: {fragment}"
    assert construct.code is code


def test_all_seven_constructs_in_the_corpus_are_flagged(loud) -> None:
    """100% of the injected known-unresolvable constructs, which is the T3.1e bar."""
    refused_units = {refusal.unit for refusal in loud.refusals}

    assert refused_units == {
        "U1_MODEL_CLAUSE",
        "U1_MATCH_RECOGNIZE",
        "U1_PIVOT_SUBQUERY",
        "U1_HIERARCHICAL",
        "U1_CONDITIONAL_COMPILATION",
        "U1_XMLTABLE",
        "U1_WRAPPED",
    }


def test_a_construct_the_grammar_cannot_parse_is_still_refused(loud) -> None:
    """Wrapped PL/SQL produces no statement at all, so a statement-level pass sees nothing.

    Which is backwards: failing to parse is MORE reason to refuse, not less. The
    source-level sweep exists for exactly this, and this is the only construct in the
    corpus that needs it.
    """
    wrapped = [r for r in loud.refusals if r.code is RefusalCode.SOURCE_UNAVAILABLE]

    assert len(wrapped) == 1
    assert wrapped[0].kind == "<unparsed>"
    assert wrapped[0].unit == "U1_WRAPPED"


# --- T3.1c: the mechanical cross-check -------------------------------------------------


def test_no_edge_survives_from_a_refused_statement(loud) -> None:
    """THE VERIFICATION METHOD IS THE POINT OF T3.1.

    "I inspected the output and it looked right" is exactly how a broken refusal passes
    review. Every flagged statement is addressed by (unit, line span) and its edge set
    must be empty.
    """
    assert loud.refusal_violations() == []


@pytest.mark.parametrize(
    "package",
    [
        "band0/b0_01_insert_select.sql",
        "band1/b1_03_loops.sql",
        "band2/b2_05_triggers.sql",
        "silent/s6_updatable_view.sql",
    ],
)
def test_the_cross_check_holds_across_the_rest_of_the_corpus(
    package: str, dictionary: Dictionary
) -> None:
    """The classifier gates band 0, but def-use and the trigger analyser run afterwards.

    Checking only the file full of refusals would leave the interesting case untested: an
    analyser downstream of the gate emitting an edge for a statement already refused.
    """
    result = _analyse(package, dictionary)
    assert result.refusal_violations() == []


def test_connect_by_produced_self_edges_before_the_gate_existed(dictionary: Dictionary) -> None:
    """MEASURED, and the reason the classifier gates rather than audits.

    `CONNECT BY` parses cleanly, so the analyser walked the select list and emitted three
    `x -> x` self-edges. Next to a real projection they look entirely ordinary; the row
    they copy from is at an arbitrary depth in a hierarchy walked at runtime.
    """
    result = analyse_source(LOUD.read_text(encoding="utf-8"), dictionary, AnalysisConfig())
    hierarchy = [e for e in result.edges if "DIM_CUSTOMER_HIER" in str(e.target)]

    assert hierarchy == []


# --- refusing too much: the failure that looks like discipline -------------------------


def test_the_control_statement_survives_every_refusal_in_the_file(loud) -> None:
    """A classifier that refused the FILE would pass every other assertion here.

    It would then cost recall everywhere else, and nothing in the report would attribute
    the loss to the classifier. That is why u1 carries a control at all.
    """
    pairs = {(str(e.source), str(e.target)) for e in loud.edges}

    assert ("column:STG_CUSTOMER.CUST_ID", "column:TMP_RECENT.CUST_ID") in pairs
    assert ("column:STG_CUSTOMER.LAST_LOGIN", "column:TMP_RECENT.LAST_LOGIN") in pairs
    assert ("column:STG_CUSTOMER.LAST_LOGIN", "relation:TMP_RECENT") in pairs


def test_the_control_still_inherits_its_trigger(loud) -> None:
    """u1_control writes tmp_recent, so trg_recent_audit fires exactly as it does elsewhere.

    Included because a file this full of refusals is where an over-eager gate would first
    show up as a missing trigger rather than as a missing statement.
    """
    pairs = {(str(e.source), str(e.target)) for e in loud.edges}
    assert (
        "column:DIM_CUSTOMER.LIFETIME_VALUE",
        "column:DIM_CUSTOMER.LIFETIME_VALUE",
    ) in pairs


def test_a_comment_mentioning_a_construct_does_not_trip_the_classifier() -> None:
    """`-- we do not use CONNECT BY here` is not a hierarchical query."""
    assert classify_statement("SELECT a FROM t -- CONNECT BY was removed in 2019") is None
    assert classify_statement("/* MODEL DIMENSION BY */ SELECT a FROM t") is None


def test_sql_text_inside_a_string_literal_is_not_this_statement() -> None:
    """`b2_02` builds SQL by concatenation, and that text is not the statement holding it.

    Recovered dynamic statements are classified on their own once T3.2 resolves them, at
    which point the construct is refused against the text that actually runs.
    """
    assert classify_statement("v_sql := 'SELECT x FROM t CONNECT BY PRIOR a = b'") is None


def test_the_innermost_statement_owns_the_construct() -> None:
    """An `IF`'s source span includes the statements inside it.

    Flagging the outer statement too would refuse one that carries genuine lineage of its
    own - refusing too much, the failure mode that reads as discipline.
    """
    source = """
    CREATE OR REPLACE PROCEDURE p IS
    BEGIN
        IF x = 1 THEN
            INSERT INTO tmp_recent (cust_id, last_login)
            SELECT cust_id, last_login FROM stg_customer CONNECT BY PRIOR cust_id = cust_id;
        END IF;
    END p;
    """
    refusals = classify_program(parse_program(source))
    lines = [
        refusal.line for refusal in refusals if refusal.code is RefusalCode.UNSUPPORTED_CONSTRUCT
    ]

    assert len(lines) == 1


# --- conditional compilation poisons its neighbours, not itself ------------------------


def test_a_conditional_compilation_block_refuses_both_arms(loud) -> None:
    """MEASURED. The analyser emitted BOTH arms before spans were refused.

    Two contradictory answers, each stated as fact, and the file cannot say which one is
    in the compiled unit because `PLSQL_CCFLAGS` decides that and it is not in the file.
    """
    pairs = {(str(e.source), str(e.target)) for e in loud.edges}

    assert ("column:STG_CUSTOMER.CUST_ID", "column:DW_AUDIT_LOG.CUST_ID") not in pairs
    context = [r for r in loud.refusals if r.code is RefusalCode.CONTEXT_DEPENDENT]
    assert len(context) == 3  # the directive itself plus one statement per arm


def test_the_span_ends_at_the_directive_and_not_at_the_file() -> None:
    """u1_control sits well below the `$END` and must not be caught by it."""
    spans = conditional_compilation_spans(LOUD.read_text(encoding="utf-8"))

    assert len(spans) == 1
    first, last = spans[0]
    assert last - first < 12


# --- the refusal addresses a statement, not a line ------------------------------------


def test_a_refusal_covers_the_whole_statement_it_refuses(loud) -> None:
    """`b2_06` labels projections at lines 21, 22 and 24 of a statement starting at 20.

    Addressing a refusal by its first line alone made the false-abstention rate read 0.0%
    while a refused statement had seven labelled edges against it.
    """
    model = next(r for r in loud.refusals if r.unit == "U1_MODEL_CLAUSE")

    assert model.end_line is not None
    assert model.end_line > model.line
    assert model.covers("U1_MODEL_CLAUSE", model.line + 1)
    assert not model.covers("U1_CONTROL", model.line + 1)


def test_violations_ignores_an_edge_from_a_different_unit(loud) -> None:
    """Line numbers repeat across units; the unit is half the address."""
    model = next(r for r in loud.refusals if r.unit == "U1_MODEL_CLAUSE")
    elsewhere = [e for e in loud.edges if e.origin.unit != model.unit]

    assert violations([model], elsewhere) == []


# --- S2-04: collections were SILENT, which is the one thing they must not be -------------


def test_fetch_bulk_collect_is_refused_not_ignored(dictionary: Dictionary) -> None:
    """Found by stress 2. The statement produced no edges AND no refusal.

    Neither analyser claimed it: `forall_statement` and the collection form of FETCH are
    absent from band 0's SUPPORTED set, and a collection of records is a memory location
    def-use does not model. So both statements were skipped by both passes, five labelled
    facts vanished, and nothing in the report said so.

    A missing edge is survivable and a declared boundary is the product. An undeclared
    absence is neither - it is indistinguishable from having correctly found nothing.
    """
    source = """CREATE OR REPLACE PROCEDURE bulk_reader IS
    TYPE t_row IS RECORD (cust_id NUMBER, last_login DATE);
    TYPE t_rows IS TABLE OF t_row;
    v_batch t_rows;
    CURSOR c_src IS SELECT s.cust_id, s.last_login FROM stg_customer s;
BEGIN
    OPEN c_src;
    LOOP
        FETCH c_src BULK COLLECT INTO v_batch LIMIT 100;
        EXIT WHEN v_batch.COUNT = 0;
    END LOOP;
    CLOSE c_src;
END bulk_reader;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    assert [r.code for r in result.refusals] == [RefusalCode.UNSUPPORTED_CONSTRUCT]
    assert "BULK COLLECT" in result.refusals[0].reason


def test_forall_is_refused_not_ignored(dictionary: Dictionary) -> None:
    """The other half: the DML is driven by a collection subscript."""
    source = """CREATE OR REPLACE PROCEDURE bulk_writer IS
    TYPE t_ids IS TABLE OF NUMBER;
    v_ids t_ids;
BEGIN
    FORALL i IN 1 .. v_ids.COUNT
        INSERT INTO tmp_recent (cust_id, last_login)
        VALUES (v_ids(i), SYSDATE);
END bulk_writer;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    assert any(r.code is RefusalCode.UNSUPPORTED_CONSTRUCT for r in result.refusals)
    assert any("FORALL" in r.reason for r in result.refusals)


def test_select_bulk_collect_still_works(dictionary: Dictionary) -> None:
    """The over-refusal guard, and the reason the FETCH pattern is FETCH-specific.

    `SELECT ... BULK COLLECT INTO` is analysed correctly today - stress 1 traces
    `stg_customer.cust_id -> v_ids` through it. A pattern matching BULK COLLECT generally
    would have refused a statement the analyser already gets right, which is the failure
    the register's DB-link note warns about: refusing too much looks like discipline.
    """
    source = """CREATE OR REPLACE PROCEDURE bulk_select IS
    TYPE t_ids IS TABLE OF NUMBER;
    v_ids t_ids;
BEGIN
    SELECT cust_id BULK COLLECT INTO v_ids
      FROM stg_customer
     WHERE status_code = 'A';
END bulk_select;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    assert result.refusals == []
    assert any(str(e.target) == "variable:V_IDS" for e in result.edges)


def test_a_literal_list_pivot_is_no_longer_refused() -> None:
    """The register used to refuse every PIVOT. Stress finding S2-03 removed half of it.

    This case sat in `test_each_loud_construct_is_refused_by_name` asserting
    `UNSUPPORTED_CONSTRUCT`, and it is moved here rather than deleted, because the change
    of behaviour is the point: a literal IN-list fixes the output columns at parse time, so
    refusing it was a false abstention (T3.1d) that also cost parse coverage.

    The subquery form stays in that list. There the output columns ARE the data.
    """
    assert classify_statement("SELECT * FROM t PIVOT (SUM(x) FOR c IN ('A', 'B'))") is None
    assert (
        classify_statement("SELECT * FROM t PIVOT (SUM(x) FOR c IN (SELECT c FROM u))") is not None
    )
