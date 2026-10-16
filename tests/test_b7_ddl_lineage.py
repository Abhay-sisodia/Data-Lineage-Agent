"""Batch B7's fixes and decisions, pinned outside the stress files.

`stress/pg/b7_ddl_lineage/REPORT.md` has the evidence for each. Each test is the smallest
script that shows the rule - several statements, because the subject is what one statement
leaves for the next - with a control wherever the fix could over-reach.

* D-31 / B7-01 / B7-04  created objects are registered, schema-qualified, views inlined,
  temp tables in pg_temp and first on the path
* B7-02 / B7-05 / B7-06  SELECT INTO is CTAS; IF NOT EXISTS on an existing table and
  WITH NO DATA write nothing
* D-33  a plain CREATE TABLE is counted; generated columns derive
* D-34 / B7-08  partitions and children flow to the parent; ATTACH / DETACH / REFRESH counted
* D-32  REFRESH re-derives a known definition
* B7-07  OVERRIDING SYSTEM VALUE parses
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


def _values(sql: str) -> set[tuple[str, str]]:
    result = _result(sql)
    assert result.refusals == [], [r.reason for r in result.refusals]
    return {(str(e.source), str(e.target)) for e in result.edges if e.flow.value == "value"}


# --- D-31 / B7-01 / B7-04 -------------------------------------------------------------------


def test_a_created_table_is_schema_qualified_and_readable() -> None:
    values = _values(
        "CREATE TABLE mart.ch AS SELECT o.channel FROM staging.orders AS o;\n"
        "INSERT INTO mart.kpi_rollup (level) SELECT c.channel FROM mart.ch AS c;"
    )
    assert values == {
        ("column:staging.orders.channel", "column:mart.ch.channel"),
        ("column:mart.ch.channel", "column:mart.kpi_rollup.level"),
    }


def test_a_created_view_is_inlined_to_its_base_table() -> None:
    values = _values(
        "CREATE VIEW mart.v AS SELECT o.channel AS ch FROM staging.orders AS o;\n"
        "INSERT INTO mart.kpi_rollup (level) SELECT v.ch FROM mart.v AS v;"
    )
    assert ("column:staging.orders.channel", "column:mart.kpi_rollup.level") in values
    assert not any(source.startswith("column:mart.v.") for source, _ in values)


def test_a_temp_table_shadows_a_real_table_of_the_same_name() -> None:
    """B7-04: the INSERT went into staging.orders - the REAL table - by unique-name binding."""
    values = _values(
        "CREATE TEMP TABLE orders (id bigint, note text);\n"
        "INSERT INTO orders (id, note) SELECT p.id, p.method FROM staging.payments AS p;"
    )
    assert values == {
        ("column:staging.payments.id", "column:pg_temp.orders.id"),
        ("column:staging.payments.method", "column:pg_temp.orders.note"),
    }


def test_a_view_replaced_mid_script_is_read_by_its_new_definition() -> None:
    values = _values(
        "CREATE VIEW mart.v AS SELECT o.channel AS c FROM staging.orders AS o;\n"
        "CREATE OR REPLACE VIEW mart.v AS SELECT o.status AS c FROM staging.orders AS o;\n"
        "INSERT INTO mart.kpi_rollup (level) SELECT v.c FROM mart.v AS v;"
    )
    assert ("column:staging.orders.status", "column:mart.kpi_rollup.level") in values
    assert ("column:staging.orders.channel", "column:mart.kpi_rollup.level") not in values


# --- B7-02 / B7-05 / B7-06 ------------------------------------------------------------------


def test_select_into_creates_a_table_not_a_result() -> None:
    result = _result("SELECT c.id INTO TEMP TABLE t FROM staging.customers AS c;")
    assert {(str(e.source), str(e.target)) for e in result.edges} == {
        ("column:staging.customers.id", "column:pg_temp.t.id")
    }
    assert result.boundaries == []


def test_ctas_if_not_exists_on_an_existing_table_writes_nothing() -> None:
    result = _result(
        "CREATE TABLE IF NOT EXISTS mart.channel_mix AS SELECT o.channel FROM staging.orders AS o;"
    )
    assert result.edges == [] and result.refusals == [] and result.statements_seen == 1


def test_with_no_data_writes_nothing() -> None:
    result = _result(
        "CREATE TABLE mart.shape AS SELECT o.channel FROM staging.orders AS o WITH NO DATA;"
    )
    assert result.edges == [] and result.statements_seen == 1


# --- D-33 / D-34 / B7-08 / D-32 -------------------------------------------------------------


def test_a_generated_column_derives_and_the_table_is_counted() -> None:
    result = _result(
        "CREATE TABLE mart.g (a numeric, b numeric GENERATED ALWAYS AS (a * 2) STORED);"
    )
    assert result.refusals == [] and result.statements_seen == 1
    assert {(str(e.source), str(e.target), e.transform.value) for e in result.edges} == {
        ("column:mart.g.a", "column:mart.g.b", "derived")
    }


def test_attach_flows_child_to_parent_and_detach_is_counted() -> None:
    result = _result(
        "CREATE TABLE staging.w23 (LIKE staging.web_events);\n"
        "ALTER TABLE staging.web_events ATTACH PARTITION staging.w23 "
        "FOR VALUES FROM ('2023-01-01') TO ('2024-01-01');\n"
        "ALTER TABLE staging.web_events DETACH PARTITION staging.w23;"
    )
    assert ("column:staging.w23.event_ts", "column:staging.web_events.event_ts") in {
        (str(e.source), str(e.target)) for e in result.edges
    }
    assert result.statements_seen == 3


def test_refresh_re_derives_a_known_materialized_view() -> None:
    result = _result(
        "CREATE MATERIALIZED VIEW mart.mv AS SELECT s.region FROM core.dim_store AS s;\n"
        "REFRESH MATERIALIZED VIEW mart.mv;"
    )
    lines = {e.origin.line for e in result.edges}
    assert lines == {1, 2}, lines
    assert result.statements_seen == 2


def test_refresh_of_an_unknown_view_is_counted_and_declared() -> None:
    """The control: no definition, so no edge - and not silent either."""
    result = _result("REFRESH MATERIALIZED VIEW mart.never_created;")
    assert result.edges == [] and result.statements_seen == 1
    assert [b.kind.value for b in result.boundaries] == ["source_unavailable"]


# --- B7-07 ----------------------------------------------------------------------------------


def test_overriding_system_value_parses() -> None:
    values = _values(
        "CREATE TABLE mart.s (id bigint GENERATED ALWAYS AS IDENTITY, label text);\n"
        "INSERT INTO mart.s (id, label) OVERRIDING SYSTEM VALUE "
        "SELECT c.id, c.full_name FROM staging.customers AS c;"
    )
    assert values == {
        ("column:staging.customers.id", "column:mart.s.id"),
        ("column:staging.customers.full_name", "column:mart.s.label"),
    }
