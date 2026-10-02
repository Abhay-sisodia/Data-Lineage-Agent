"""Batch B5's fixes and decisions, pinned outside the stress files.

`stress/pg/b5_semi_structured/REPORT.md` has the evidence for each. One statement per
test, the smallest that shows the rule, and a control wherever the fix could over-reach.

* B5-03  an untyped ordered aggregate's ORDER BY column is influence, not a value
* B5-01  JSON / hstore set-returning functions in FROM read the document
* B5-02  SQL/JSON, jsonpath, xpath and array_to_json are built-ins
* D-30 / B5-04  JSON_TABLE and XMLTABLE are analysed, PASSING included
* D-29  lineage is column-level: a literal key names no column
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"
PAYLOAD = "column:staging.orders.payload"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _facts(sql: str) -> set[tuple[str, str, str, str]]:
    result = _result(sql)
    assert result.refusals == [], [r.reason for r in result.refusals]
    assert result.boundaries == [], [str(b) for b in result.boundaries]
    return {(str(e.source), str(e.target), e.flow.value, e.transform.value) for e in result.edges}


# --- B5-03 ----------------------------------------------------------------------------------


def test_jsonb_agg_order_by_is_influence_not_a_value() -> None:
    facts = _facts(
        "INSERT INTO mart.carrier_scorecard (carrier_name) "
        "SELECT jsonb_agg(s.tracking ORDER BY s.shipped_at)::text FROM staging.shipments AS s"
    )
    target = "column:mart.carrier_scorecard.carrier_name"
    assert facts == {
        ("column:staging.shipments.tracking", target, "value", "aggregated"),
        ("column:staging.shipments.shipped_at", target, "influence", "identity"),
    }


# --- B5-01 ----------------------------------------------------------------------------------


def test_jsonb_each_in_from_gives_key_and_value_from_the_document() -> None:
    assert _facts(
        "INSERT INTO mart.kpi_rollup (level, segment) SELECT kv.key, kv.value "
        "FROM staging.orders AS o, jsonb_each_text(o.payload) AS kv"
    ) == {
        (PAYLOAD, "column:mart.kpi_rollup.level", "value", "derived"),
        (PAYLOAD, "column:mart.kpi_rollup.segment", "value", "derived"),
    }


def test_jsonb_to_recordset_columns_come_from_the_definition_list() -> None:
    assert _facts(
        "INSERT INTO mart.sales_daily (units) SELECT r.qty "
        "FROM staging.orders AS o, jsonb_to_recordset(o.payload -> 'items') AS r(sku text, qty int)"
    ) == {(PAYLOAD, "column:mart.sales_daily.units", "value", "derived")}


def test_an_srf_over_a_literal_document_is_silent() -> None:
    """The control: no column, so no edge - and no 'row source is a function' either."""
    result = _result(
        "INSERT INTO mart.kpi_rollup (segment) SELECT e.value "
        'FROM jsonb_each_text(\'{"x": "1"}\'::jsonb) AS e'
    )
    assert result.edges == [] and result.boundaries == [] and result.refusals == []


# --- B5-02 ----------------------------------------------------------------------------------


def test_sql_json_and_jsonpath_functions_read_the_document() -> None:
    assert _facts(
        "INSERT INTO mart.kpi_rollup (level, revenue) "
        "SELECT JSON_VALUE(o.payload, '$.k'), jsonb_path_query_first(o.payload, '$.t')::numeric "
        "FROM staging.orders AS o"
    ) == {
        (PAYLOAD, "column:mart.kpi_rollup.level", "value", "derived"),
        (PAYLOAD, "column:mart.kpi_rollup.revenue", "value", "derived"),
    }


def test_xpath_reads_the_xml_column() -> None:
    assert _facts(
        "INSERT INTO mart.kpi_rollup (level) "
        "SELECT (xpath('/a/@b', f.doc))[1]::text FROM staging.feed_xml AS f"
    ) == {("column:staging.feed_xml.doc", "column:mart.kpi_rollup.level", "value", "derived")}


# --- D-30 / B5-04 ---------------------------------------------------------------------------


def test_json_table_with_ordinality_is_analysed() -> None:
    """It was refused SHAPE_NOT_DECIDABLE; the COLUMNS clause states the shape (D-30)."""
    assert _facts(
        "INSERT INTO mart.product_performance (units, growth_pct) SELECT jt.qty, jt.n "
        "FROM staging.orders AS o, JSON_TABLE(o.payload, '$.items[*]' "
        "COLUMNS (n FOR ORDINALITY, qty bigint PATH '$.qty')) AS jt"
    ) == {
        (PAYLOAD, "column:mart.product_performance.units", "value", "derived"),
        (PAYLOAD, "column:mart.product_performance.growth_pct", "influence", "identity"),
    }


def test_json_table_passing_is_parsed_and_read() -> None:
    """SQLGlot cannot parse PASSING at all (B5-04); the source restatement reads it."""
    assert _facts(
        "INSERT INTO mart.sales_daily (region) SELECT jt.sku "
        "FROM staging.orders AS o, JSON_TABLE(o.payload, '$.i[*] ? (@.s == $s)' "
        "PASSING o.discount_code AS s COLUMNS (sku text PATH '$.s')) AS jt"
    ) == {
        (PAYLOAD, "column:mart.sales_daily.region", "value", "derived"),
        (
            "column:staging.orders.discount_code",
            "column:mart.sales_daily.region",
            "value",
            "derived",
        ),
    }


def test_xmltable_is_analysed() -> None:
    assert _facts(
        "INSERT INTO mart.top_customers (revenue) SELECT x.amt "
        "FROM staging.feed_xml AS f, XMLTABLE('/r' PASSING f.doc COLUMNS amt numeric PATH 'a') AS x"
    ) == {("column:staging.feed_xml.doc", "column:mart.top_customers.revenue", "value", "derived")}


def test_a_json_table_in_a_string_literal_is_left_alone() -> None:
    """The control: the text restatement must not rewrite a literal that mentions it."""
    assert _facts(
        "INSERT INTO mart.kpi_rollup (level) SELECT o.channel FROM staging.orders AS o "
        "WHERE o.status <> 'JSON_TABLE(x, ''$'' COLUMNS (a int)) AS t'"
    ) == {
        ("column:staging.orders.channel", "column:mart.kpi_rollup.level", "value", "identity")
    } | {("column:staging.orders.status", "relation:mart.kpi_rollup", "filter", "identity")}


# --- D-29 -----------------------------------------------------------------------------------


def test_a_literal_key_names_no_column() -> None:
    """Column-level: 'amount' is text inside the document, not the column amount."""
    assert _facts(
        "INSERT INTO mart.channel_mix (revenue) SELECT (o.payload ->> 'amount')::numeric "
        "FROM staging.orders AS o"
    ) == {(PAYLOAD, "column:mart.channel_mix.revenue", "value", "derived")}
