"""Batch B4's fixes and decisions, pinned outside the stress files.

`stress/pg/b4_expressions_types/REPORT.md` has the evidence for each. One statement per
test, the smallest that shows the rule, and a control wherever the fix could over-reach.

* B4-01 / D-20  whole-row references and `(t).f` are traced, never dropped
* B4-02 / D-19  a conditional form is conditional only when two columns decide it
* B4-03  row, regex and range built-ins pass their columns as values
* B4-04  a keyspec `repeat` must restate a fact the file states
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "stress" / "pg" / "dictionary.json"
CUSTOMER_COLUMNS = {
    "id", "email", "full_name", "country_code", "segment", "status", "signup_ts",
    "attributes", "created_at",
}


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _facts(sql: str) -> set[tuple[str, str, str, str]]:
    result = _result(sql)
    assert result.refusals == []
    assert result.boundaries == [], [str(b) for b in result.boundaries]
    return {(str(e.source), str(e.target), e.flow.value, e.transform.value) for e in result.edges}


# --- B4-01 / D-20: whole rows ------------------------------------------------------------


def test_field_access_is_the_column_itself() -> None:
    """`(c).full_name` produced nothing at all - no edge, no boundary."""
    assert _facts(
        "INSERT INTO mart.top_customers (full_name) SELECT (c).full_name "
        "FROM staging.customers AS c"
    ) == {("column:staging.customers.full_name", "column:mart.top_customers.full_name", "value", "identity")}


def test_a_whole_row_function_reads_every_column() -> None:
    facts = _facts(
        "INSERT INTO mart.top_customers (region) SELECT md5(row_to_json(c)::text) "
        "FROM staging.customers AS c"
    )
    assert {f[0].rsplit(".", 1)[1] for f in facts} == CUSTOMER_COLUMNS
    assert {f[3] for f in facts} == {"derived"}


def test_a_whole_row_test_in_where_filters_on_every_column() -> None:
    facts = _facts(
        "INSERT INTO mart.top_customers (customer_id) SELECT c.id FROM staging.customers AS c "
        "WHERE c IS NOT NULL"
    )
    assert {f[0].rsplit(".", 1)[1] for f in facts if f[2] == "filter"} == CUSTOMER_COLUMNS


def test_field_access_through_a_derived_table_stays_field_exact() -> None:
    """`(x.c).id` reads customers.id only - not the whole row the derived table carries."""
    facts = _facts(
        "INSERT INTO mart.top_customers (customer_id) SELECT (x.c).id "
        "FROM (SELECT c FROM staging.customers AS c) AS x"
    )
    assert facts == {
        ("column:staging.customers.id", "column:mart.top_customers.customer_id", "value", "identity")
    }


def test_an_alias_named_like_a_column_is_still_the_column() -> None:
    """The control: PostgreSQL resolves a bare name to a column before a whole row."""
    assert _facts(
        "INSERT INTO mart.kpi_rollup (orders) SELECT year FROM core.dim_date AS year"
    ) == {("column:core.dim_date.year", "column:mart.kpi_rollup.orders", "value", "identity")}


# --- B4-02 / D-19 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("GREATEST(f.net_amount, 0)", "derived"),
        ("NULLIF(f.net_amount, 0)", "derived"),
        ("CASE WHEN f.net_amount IS NULL THEN 0 ELSE f.net_amount END", "derived"),
        ("CASE WHEN false THEN f.net_amount END", "derived"),
        ("COALESCE(f.net_amount, f.net_amount)", "derived"),
        ("COALESCE(f.net_amount, f.gross_amount)", "conditional"),
        ("CASE WHEN f.status = 'X' THEN 0 ELSE f.net_amount END", "conditional"),
        ("COALESCE(NULLIF(f.net_amount, 0), f.unit_price)", "conditional"),
    ],
)
def test_two_columns_make_a_conditional(expression: str, expected: str) -> None:
    """Equivalent spellings of one guard disagreed: COALESCE derived, the rest conditional."""
    facts = _facts(
        f"INSERT INTO mart.sales_daily (net_amount) SELECT {expression} "
        "FROM core.fct_order_line AS f"
    )
    net = {f[3] for f in facts if f[0] == "column:core.fct_order_line.net_amount"}
    assert net == {expected}


def test_a_self_join_reads_two_columns() -> None:
    facts = _facts(
        "INSERT INTO mart.product_performance (revenue) "
        "SELECT GREATEST(a.list_price, b.list_price) "
        "FROM core.dim_product AS a JOIN core.dim_product AS b ON b.product_id = a.product_id"
    )
    assert facts == {
        (
            "column:core.dim_product.list_price",
            "column:mart.product_performance.revenue",
            "value",
            "conditional",
        )
    }


# --- B4-03 ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expression",
    [
        "ROW(o.channel, o.currency)::text",
        "(regexp_match(o.channel || o.currency, '([a-z]+)'))[1]",
        "upper(tstzrange(o.order_ts, o.updated_at))::text || o.channel || o.currency",
    ],
)
def test_row_regex_and_range_builtins_pass_their_columns(expression: str) -> None:
    """Each was declared source_unavailable and lost its columns at the top level."""
    sources = {f[0] for f in _facts(
        f"INSERT INTO mart.channel_mix (channel) SELECT {expression} FROM staging.orders AS o"
    )}
    assert {"column:staging.orders.channel", "column:staging.orders.currency"} <= sources


def test_a_user_routine_is_still_declared() -> None:
    """The control: listing built-ins must not swallow a call whose body is not visible."""
    result = _result(
        "INSERT INTO mart.channel_mix (revenue) SELECT myschema.calc(o.amount) "
        "FROM staging.orders AS o"
    )
    assert [b.kind.value for b in result.boundaries] == ["source_unavailable"]


# --- B4-04 ---------------------------------------------------------------------------------


def test_a_repeat_must_restate_a_stated_fact(tmp_path: Path) -> None:
    sys.path.insert(0, str(ROOT / "stress" / "pg"))
    from keygen import compile_spec, repeats_of

    (tmp_path / "t.sql").write_text("SELECT 1;\nSELECT 2;\n", encoding="utf-8")
    spec = tmp_path / "t.keyspec"
    spec.write_text(
        "@ 1 S1\nvalue a.x -> b.y derived\n@ 2 S2\nrepeat value a.x -> b.y derived\n",
        encoding="utf-8",
    )
    compile_spec(spec)
    assert repeats_of(spec) == {
        2: [("a.x", "b.y", "value", "derived", "pre-aggregation", "column")]
    }

    spec.write_text("@ 1 S1\nvalue a.x -> b.y derived\n@ 2 S2\nrepeat value a.x -> b.z derived\n")
    with pytest.raises(SystemExit, match="repeat of a fact no statement states"):
        compile_spec(spec)
