"""Batch B10's fixes and decisions, pinned outside the stress files.

`stress/pg/b10_lexical/REPORT.md` has the evidence for each. Each test is the smallest
script that shows the rule, with a control wherever the fix could over-reach.

* B10-01 / B10-02  the splitter knows E'' escapes and nested comments; a glued fragment is
  declared, never skipped
* B10-03 / D-37  psql meta-commands are skipped; \\i declared, \\copy refused alone, \\gset
  gives nothing
* D-36  psql variables are external values
* B10-04 / D-35  PREPARE / EXECUTE / DEALLOCATE
* B10-05  U&"" identifiers
* B10-06  a MERGE arm condition on an aggregate is post-aggregation
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.parsing import sqlscript
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _values(sql: str) -> set[tuple[str, str]]:
    result = _result(sql)
    assert result.refusals == [], [r.reason for r in result.refusals]
    return {(str(e.source), str(e.target)) for e in result.edges if e.flow.value == "value"}


CHANNEL = ("column:staging.orders.channel", "column:mart.kpi_rollup.level")
SEGMENT = ("column:staging.customers.segment", "column:mart.kpi_rollup.segment")
CHANNEL_SQL = "INSERT INTO mart.kpi_rollup (level) SELECT o.channel FROM staging.orders AS o;"


# --- B10-01 / B10-02 ------------------------------------------------------------------------


def test_an_escaped_quote_in_an_e_string_does_not_swallow_the_next_statement() -> None:
    values = _values(
        "INSERT INTO mart.kpi_rollup (segment) SELECT E'it\\'s ' || c.segment "
        "FROM staging.customers AS c;\n" + CHANNEL_SQL
    )
    assert values == {SEGMENT, CHANNEL}


def test_a_semicolon_in_a_nested_comment_does_not_split_the_statement() -> None:
    values = _values(
        "INSERT INTO mart.kpi_rollup (segment)\n"
        "SELECT /* a /* b; */ still a comment; */ c.segment FROM staging.customers AS c;"
    )
    assert values == {SEGMENT}


def test_a_fragment_holding_several_statements_is_declared(monkeypatch) -> None:
    """The guard: whatever the splitter gets wrong next, it is not silent."""
    glued = CHANNEL_SQL + " " + CHANNEL_SQL.replace("level", "segment")
    monkeypatch.setattr(sqlscript, "split_statements", lambda source: [(1, glued)])
    result = _result(glued)
    assert result.statements_seen == 1
    assert [b.kind.value for b in result.boundaries] == ["parse_failure"]


# --- B10-03 / D-37 / D-36 -------------------------------------------------------------------


def test_meta_commands_are_skipped_and_a_psql_variable_is_a_value() -> None:
    values = _values(
        "\\set st 'PAID'\n\\timing on\n"
        "INSERT INTO mart.kpi_rollup (level) SELECT o.channel FROM staging.orders AS o "
        "WHERE o.status = :'st';"
    )
    assert values == {CHANNEL}


def test_echoed_sql_is_not_lineage() -> None:
    result = _result(
        "\\echo 'INSERT INTO mart.kpi_rollup (segment) SELECT c.segment FROM "
        "staging.customers AS c; done'\n" + CHANNEL_SQL
    )
    assert {(str(e.source), str(e.target)) for e in result.edges} == {CHANNEL}


def test_an_include_is_declared_and_a_copy_is_refused_alone() -> None:
    result = _result("\\i deploy/views.sql\n\\copy staging.orders FROM 'o.csv'\n" + CHANNEL_SQL)
    assert {(str(e.source), str(e.target)) for e in result.edges} == {CHANNEL}
    assert [r.line for r in result.refusals] == [2]
    assert ("source_unavailable", "deploy/views.sql") in {
        (b.kind.value, b.subject.lower()) for b in result.boundaries
    }


def test_a_query_sent_with_gset_writes_nothing() -> None:
    result = _result("SELECT max(o.amount) AS top FROM staging.orders AS o \\gset\n" + CHANNEL_SQL)
    assert {(str(e.source), str(e.target)) for e in result.edges} == {CHANNEL}


def test_casts_slices_and_quoted_colons_are_not_psql_variables() -> None:
    """Control: `::`, `[1:2]` and a `:'v'` inside a literal stay as they are."""
    values = _values(
        "INSERT INTO mart.kpi_rollup (level) "
        "SELECT array_to_string(o.tags[1:2], ':''x''') || o.amount::text "
        "FROM staging.orders AS o;"
    )
    assert values == {
        ("column:staging.orders.tags", "column:mart.kpi_rollup.level"),
        ("column:staging.orders.amount", "column:mart.kpi_rollup.level"),
    }


# --- B10-04 / D-35 --------------------------------------------------------------------------

PREPARE = (
    "PREPARE p(text) AS\nINSERT INTO mart.kpi_rollup (level)\n"
    "SELECT o.channel FROM staging.orders AS o WHERE o.status = $1;\n"
)


def test_execute_emits_the_prepared_lineage_at_its_own_line_each_time() -> None:
    result = _result(PREPARE + "EXECUTE p('PAID');\nEXECUTE p('SHIPPED');")
    assert result.statements_seen == 3
    by_line = {}
    for edge in result.edges:
        by_line.setdefault(edge.origin.line, set()).add((str(edge.source), str(edge.target)))
    assert sorted(by_line) == [4, 5]
    assert by_line[4] == by_line[5]
    assert CHANNEL in by_line[4]


def test_create_table_as_execute_is_ctas_over_the_prepared_query() -> None:
    values = _values(
        "PREPARE q AS SELECT c.segment FROM staging.customers AS c;\n"
        "CREATE TABLE mart.seg AS EXECUTE q;"
    )
    assert values == {("column:staging.customers.segment", "column:mart.seg.segment")}


def test_execute_after_deallocate_is_counted_and_declared() -> None:
    result = _result(PREPARE + "DEALLOCATE p;\nEXECUTE p('PAID');")
    assert result.edges == [] and result.statements_seen == 3
    assert [(b.kind.value, b.subject.lower()) for b in result.boundaries] == [
        ("source_unavailable", "p")
    ]


def test_a_prepared_update_is_analysed_as_an_update() -> None:
    values = _values(
        "PREPARE u(text) AS UPDATE mart.customer_summary AS cs SET segment = c.segment "
        "FROM staging.customers AS c WHERE c.id = cs.customer_id AND c.status = $1;\n"
        "EXECUTE u('A');"
    )
    assert values == {("column:staging.customers.segment", "column:mart.customer_summary.segment")}


# --- B10-05 / B10-06 ------------------------------------------------------------------------


def test_a_unicode_escaped_identifier_is_the_column_it_spells() -> None:
    values = _values(
        'INSERT INTO mart.kpi_rollup (customers) SELECT c.U&"\\0069d" FROM staging.customers AS c;'
    )
    assert values == {("column:staging.customers.id", "column:mart.kpi_rollup.customers")}


def test_a_merge_arm_condition_on_an_aggregate_is_post_aggregation() -> None:
    result = _result(
        "MERGE INTO mart.top_customers AS t\n"
        "USING (SELECT p.order_id, SUM(p.amount) AS amt FROM staging.payments AS p "
        "GROUP BY p.order_id) AS s\n"
        "ON t.customer_id = s.order_id\n"
        "WHEN MATCHED AND s.amt > 100 THEN UPDATE SET revenue = s.amt;"
    )
    filters = {(str(e.source), e.phase.value) for e in result.edges if e.flow.value == "filter"}
    assert filters == {("column:staging.payments.amount", "post-aggregation")}
