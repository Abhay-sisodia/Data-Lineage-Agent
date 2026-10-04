"""Batch B8's fixes and decisions, pinned outside the stress files.

`stress/pg/b8_procedural/REPORT.md` has the evidence for each. Each test is the smallest
script that shows the rule, with a control wherever the fix could over-reach.

* B8-01  PL/pgSQL bodies are read (D-39): static SQL, variables, loops, cursors
* D-40   a RETURN QUERY function is inlined where it is read; RETURN NEXT is declared
* rule 6 a scalar function contributes its return value's columns, not its argument
* D-41   a trigger's edges come with its binding, band 2
* D-42   constant dynamic SQL is analysed, band 2; anything else is declared
* B8-02  CALL is counted
* the reader refuses what it does not know, whole: no half-read routine
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _facts(result) -> set[tuple[str, str, str, int]]:
    return {(str(e.source), str(e.target), e.flow.value, e.band) for e in result.edges}


def _routine(body: str, header: str = "CREATE FUNCTION mart.f() RETURNS void") -> str:
    return f"{header}\nLANGUAGE plpgsql AS $$\n{body}\n$$;"


# --- B8-01 / D-39 ---------------------------------------------------------------------------


def test_a_function_body_is_analysed_at_its_own_line_and_unit() -> None:
    result = _result(
        _routine(
            "DECLARE\n    v integer;\nBEGIN\n"
            "    INSERT INTO mart.kpi_rollup (level) SELECT o.channel FROM staging.orders AS o;\n"
            "END;"
        )
    )
    assert result.refusals == []
    [edge] = result.edges
    assert (str(edge.source), str(edge.target), edge.band) == (
        "column:staging.orders.channel",
        "column:mart.kpi_rollup.level",
        0,
    )
    assert (edge.origin.unit, edge.origin.line) == ("mart.f", 6)


def test_select_into_writes_a_variable_and_the_variable_feeds_a_column() -> None:
    facts = _facts(
        _result(
            _routine(
                "DECLARE\n    v_total numeric;\nBEGIN\n"
                "    SELECT SUM(p.amount) INTO v_total FROM staging.payments AS p "
                "WHERE p.status = 'OK';\n"
                "    INSERT INTO mart.kpi_rollup (revenue) VALUES (v_total);\nEND;"
            )
        )
    )
    assert facts == {
        ("column:staging.payments.amount", "variable:v_total", "value", 1),
        ("column:staging.payments.status", "relation:staging.payments", "filter", 1),
        ("variable:v_total", "column:mart.kpi_rollup.revenue", "value", 1),
    }


def test_a_loop_record_is_transparent_and_the_loop_query_filters_the_write() -> None:
    facts = _facts(
        _result(
            _routine(
                "DECLARE\n    r record;\nBEGIN\n"
                "    FOR r IN SELECT c.id, c.segment FROM staging.customers AS c\n"
                "             WHERE c.status = 'A'\n"
                "    LOOP\n"
                "        INSERT INTO mart.top_customers (customer_id, region)\n"
                "        VALUES (r.id, r.segment);\n"
                "    END LOOP;\nEND;"
            )
        )
    )
    assert facts == {
        ("column:staging.customers.id", "column:mart.top_customers.customer_id", "value", 1),
        ("column:staging.customers.segment", "column:mart.top_customers.region", "value", 1),
        ("column:staging.customers.status", "relation:mart.top_customers", "filter", 1),
    }


def test_call_is_counted() -> None:
    result = _result("CALL mart.pr_anything('x');")
    assert result.statements_seen == 1 and result.edges == [] and result.refusals == []


def test_a_routine_with_a_form_the_reader_does_not_know_stays_refused_whole() -> None:
    """The control: a CASE statement is not read, so nothing of the routine is claimed."""
    result = _result(
        _routine(
            "DECLARE\n    v integer;\nBEGIN\n"
            "    INSERT INTO mart.kpi_rollup (level) SELECT o.channel FROM staging.orders AS o;\n"
            "    CASE v WHEN 1 THEN NULL; END CASE;\nEND;"
        )
    )
    assert result.edges == []
    assert [r.code.value for r in result.refusals] == ["UNSUPPORTED_CONSTRUCT"]


def test_a_routines_own_search_path_binds_its_unqualified_names() -> None:
    result = _result(
        _routine(
            "DECLARE\n    v integer;\nBEGIN\n"
            "    INSERT INTO ledger (account) SELECT l.account FROM staging.ledger AS l;\nEND;",
            header="CREATE FUNCTION mart.f() RETURNS void SET search_path = core",
        )
    )
    assert ("column:staging.ledger.account", "column:core.ledger.account", "value", 0) in _facts(
        result
    )


# --- D-40 / rule 6 --------------------------------------------------------------------------


def test_a_return_query_function_is_inlined_where_it_is_read() -> None:
    facts = _facts(
        _result(
            _routine(
                "DECLARE\n    v integer;\nBEGIN\n"
                "    RETURN QUERY SELECT o.channel FROM staging.orders AS o\n"
                "        WHERE o.status = p;\nEND;",
                header="CREATE FUNCTION mart.rq(p text) RETURNS TABLE (ch text)",
            )
            + "\nINSERT INTO mart.kpi_rollup (level) SELECT f.ch FROM mart.rq('PAID') AS f;"
        )
    )
    assert facts == {
        ("column:staging.orders.channel", "column:mart.kpi_rollup.level", "value", 0),
        ("column:staging.orders.status", "relation:mart.kpi_rollup", "filter", 0),
    }


def test_a_return_next_function_read_is_declared() -> None:
    result = _result(
        _routine(
            "DECLARE\n    rr record;\nBEGIN\n"
            "    FOR rr IN SELECT s.region FROM core.dim_store AS s\n    LOOP\n"
            "        RETURN NEXT rr.region;\n    END LOOP;\nEND;",
            header="CREATE FUNCTION mart.gen() RETURNS SETOF text",
        )
        + "\nINSERT INTO mart.kpi_rollup (region) SELECT g.r FROM mart.gen() AS g(r);"
    )
    assert ("source_unavailable", "mart.gen") in {
        (b.kind.value, b.subject.lower()) for b in result.boundaries
    }


def test_a_scalar_function_gives_its_return_columns_and_the_rest_of_the_projection_stays() -> None:
    facts = _facts(
        _result(
            _routine(
                "DECLARE\n    v_cost numeric;\nBEGIN\n"
                "    SELECT pr.cost INTO v_cost FROM staging.products AS pr WHERE pr.id = p_prod;\n"
                "    RETURN v_cost;\nEND;",
                header="CREATE FUNCTION mart.unit_cost(p_prod bigint) RETURNS numeric",
            )
            + "\nINSERT INTO mart.product_performance (margin)\n"
            "SELECT ol.unit_price - mart.unit_cost(ol.product_id) FROM staging.order_lines AS ol;"
        )
    )
    values = {(s, t) for s, t, flow, _ in facts if flow == "value" and "margin" in t}
    assert values == {
        ("column:staging.order_lines.unit_price", "column:mart.product_performance.margin"),
        ("column:staging.products.cost", "column:mart.product_performance.margin"),
    }


# --- D-41 / D-42 ----------------------------------------------------------------------------

TRIGGER = _routine(
    "DECLARE\n    v integer;\nBEGIN\n"
    "    INSERT INTO mart.events_log (id) VALUES (NEW.id);\n    RETURN NEW;\nEND;",
    header="CREATE FUNCTION mart.trg() RETURNS trigger",
)


def test_a_bound_trigger_flows_from_its_table_in_band_2() -> None:
    result = _result(
        TRIGGER + "\nCREATE TRIGGER t AFTER INSERT ON staging.returns "
        "FOR EACH ROW EXECUTE FUNCTION mart.trg();"
    )
    assert _facts(result) == {
        ("column:staging.returns.id", "column:mart.events_log.id", "value", 2)
    }
    assert result.refusals == []


def test_an_unbound_trigger_function_gives_nothing() -> None:
    result = _result(TRIGGER)
    assert result.edges == [] and result.refusals == []


def test_constant_dynamic_sql_is_analysed_and_a_parameter_is_declared() -> None:
    constant = _result(
        "DO $$\nDECLARE\n    v_s text := 'mart';\nBEGIN\n"
        "    EXECUTE format('INSERT INTO %I.kpi_rollup (segment) SELECT c.segment "
        "FROM staging.customers AS c', v_s);\nEND;\n$$;"
    )
    assert _facts(constant) == {
        ("column:staging.customers.segment", "column:mart.kpi_rollup.segment", "value", 2)
    }
    undecidable = _result(
        _routine(
            "DECLARE\n    v integer;\nBEGIN\n    EXECUTE p_sql;\nEND;",
            header="CREATE PROCEDURE mart.run(p_sql text)",
        )
    )
    assert undecidable.edges == []
    assert [b.kind.value for b in undecidable.boundaries] == ["dynamic_sql"]


def test_when_others_then_null_is_declared() -> None:
    result = _result(
        _routine(
            "DECLARE\n    v integer;\nBEGIN\n"
            "    INSERT INTO mart.kpi_rollup (region) SELECT s.region FROM core.dim_store AS s;\n"
            "EXCEPTION WHEN OTHERS THEN\n    NULL;\nEND;"
        )
    )
    assert "suppressed_error" in {b.kind.value for b in result.boundaries}
    assert len(result.edges) == 1
