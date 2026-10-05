"""Finding TT-5: PostgreSQL grammar SQLGlot cannot parse is restated, lineage unchanged.

The shipment torture query (local, `stress/pg/torture`) failed to parse whole on eighteen
constructs. `parsing.pg_grammar` restates each in the source. These tests pin, for each
construct, that the statement now parses and gives the lineage its plain spelling gives -
and, for the restatement itself, that lines and literals never move.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlglot

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.parsing.pg_grammar import restate
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _edges(sql: str) -> set[tuple[str, str, str, str]]:
    result = _result(sql)
    assert result.refusals == [], result.refusals
    return {(e.flow.value, str(e.source), str(e.target), e.transform.value) for e in result.edges}


# --- the restatement itself ---------------------------------------------------------------

UNREADABLE = [
    "SELECT o.id FROM staging.orders o ORDER BY o.id USING >",
    "SELECT o.id FROM staging.orders o JOIN staging.customers c USING (id) AS j",
    "SELECT JSON_VALUE(o.payload, '$.a' RETURNING int DEFAULT 5 ON EMPTY NULL ON ERROR) "
    "FROM staging.orders o",
    "SELECT JSON_ARRAY(o.id, o.amount NULL ON NULL RETURNING jsonb) FROM staging.orders o",
    "SELECT JSON_ARRAYAGG(o.id ORDER BY o.id NULL ON NULL RETURNING jsonb) FROM staging.orders o",
    "SELECT XMLPI(NAME audit, 'x') FROM staging.orders o",
    "SELECT XMLSERIALIZE(CONTENT o.payload AS text) FROM staging.orders o",
    "SELECT SUBSTRING(o.status SIMILAR '%#\"A#\"%' ESCAPE '#') FROM staging.orders o",
    "SELECT o.id FROM staging.orders o WHERE EXISTS (TABLE staging.customers)",
    "SELECT now() AT LOCAL",
    "SELECT CAST(o.status AS national character(5)) FROM staging.orders o",
    "SELECT box '((0,0),(1,1))' && box '((0,0),(2,2))'",
    "SELECT COLLATION FOR (o.status) FROM staging.orders o",
    "SELECT 3 OPERATOR(pg_catalog.+) 4, @ -42, pg_catalog.int4 '7'",
]


@pytest.mark.parametrize("sql", UNREADABLE)
def test_each_construct_parses_once_restated(sql: str) -> None:
    with pytest.raises(sqlglot.errors.ParseError):
        sqlglot.parse_one(sql, read="postgres")
    sqlglot.parse_one(restate(sql), read="postgres")


def test_radix_and_underscored_literals_become_plain_numbers() -> None:
    """SQLGlot does not reject these, it MISREADS them: `0o17` as `0 AS o17`."""
    assert restate("SELECT 0o17, 0x1F, 0b101, 1_000_000") == "SELECT 15, 31, 5, 1000000"


def test_lines_are_kept_and_literals_untouched() -> None:
    sql = (
        "SELECT JSON_VALUE(o.payload, '$.a'\n    RETURNING int\n    DEFAULT 5 ON EMPTY),\n"
        "       'ORDER BY x USING > 0o17 TABLE t' AS s -- AT LOCAL 0x1F\n"
        "FROM staging.orders o;"
    )
    restated = restate(sql)
    assert restated.count("\n") == sql.count("\n")
    assert "'ORDER BY x USING > 0o17 TABLE t'" in restated
    assert "-- AT LOCAL 0x1F" in restated


def test_restating_is_idempotent() -> None:
    once = restate(" ;\n".join(UNREADABLE))
    assert restate(once) == once


# --- lineage is the plain spelling's ------------------------------------------------------


def test_order_by_using_reads_as_a_plain_order_by() -> None:
    using = "SELECT o.id FROM staging.orders o ORDER BY o.amount USING > LIMIT 5;"
    plain = "SELECT o.id FROM staging.orders o ORDER BY o.amount DESC LIMIT 5;"
    assert _edges(using) == _edges(plain)


def test_a_join_alias_column_is_the_merged_column() -> None:
    aliased = "SELECT j.id, c.email FROM staging.orders o JOIN staging.customers c USING (id) AS j;"
    plain = "SELECT id, c.email FROM staging.orders o JOIN staging.customers c USING (id);"
    assert _edges(aliased) == _edges(plain)


def test_search_and_cycle_columns_come_from_their_columns() -> None:
    edges = _edges(
        "WITH RECURSIVE tree AS (\n"
        "    SELECT o.id, o.customer_id FROM staging.orders o\n"
        "    UNION ALL\n"
        "    SELECT c.id, c.customer_id FROM staging.orders c JOIN tree t ON c.customer_id = t.id\n"
        ") SEARCH DEPTH FIRST BY id SET seq\n"
        "  CYCLE id SET is_cycle TO 'Y' DEFAULT 'N' USING path\n"
        "SELECT tree.id, tree.seq, tree.is_cycle, tree.path FROM tree;"
    )
    for column in ("seq", "is_cycle", "path"):
        assert ("value", "column:staging.orders.id", f"column:<result>.{column}", "derived") in (
            edges
        ), column


def test_json_arrayagg_is_an_aggregate_with_its_order_as_influence() -> None:
    sqljson = (
        "SELECT o.customer_id, JSON_ARRAYAGG(o.amount ORDER BY o.order_ts RETURNING jsonb) AS a "
        "FROM staging.orders o GROUP BY o.customer_id;"
    )
    plain = (
        "SELECT o.customer_id, jsonb_agg(o.amount ORDER BY o.order_ts) AS a "
        "FROM staging.orders o GROUP BY o.customer_id;"
    )
    assert _edges(sqljson) == _edges(plain)
    assert (
        "value",
        "column:staging.orders.amount",
        "column:<result>.a",
        "aggregated",
    ) in _edges(sqljson)


def test_json_value_reads_its_document_and_its_default() -> None:
    edges = _edges(
        "SELECT JSON_VALUE(o.payload, '$.a' RETURNING numeric DEFAULT o.amount ON EMPTY) AS v "
        "FROM staging.orders o;"
    )
    assert edges == {
        ("value", "column:staging.orders.payload", "column:<result>.v", "derived"),
        ("value", "column:staging.orders.amount", "column:<result>.v", "derived"),
    }


def test_an_unreadable_operator_reads_both_operands() -> None:
    edges = _edges("SELECT o.amount <#> o.store_id AS d FROM staging.orders o;")
    assert edges == {
        ("value", "column:staging.orders.amount", "column:<result>.d", "derived"),
        ("value", "column:staging.orders.store_id", "column:<result>.d", "derived"),
    }


def test_table_command_is_its_select() -> None:
    table = "SELECT x.email FROM (TABLE staging.customers) AS x;"
    plain = "SELECT x.email FROM (SELECT * FROM staging.customers) AS x;"
    assert _edges(table) == _edges(plain)


def test_path_is_a_typed_literal_only_where_a_value_starts() -> None:
    """`@-@ path '...'` is a geometric literal; `bigint PATH '$.x'` is JSON_TABLE's keyword."""
    assert "CAST('[(0,0),(3,4)]' AS path)" in restate("SELECT @-@ path '[(0,0),(3,4)]'")
    columns = "COLUMNS (pid bigint PATH '$.id', w numeric(12,3) PATH '$.w')"
    assert restate(columns) == columns
