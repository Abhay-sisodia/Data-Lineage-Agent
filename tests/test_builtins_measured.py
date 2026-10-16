"""Finding F0-03: PostgreSQL built-ins SQLGlot leaves untyped were declared unread user calls.

`to_tsvector(x)`, `jsonb_array_length(x)`, `pg_typeof(x)`, `cosd(x)` and about 160 others
arrive as `exp.Anonymous`, and D-53 declared each `source_unavailable` - a claim that a body
went unread, false for a built-in. The edges (arguments derived) were already right. Every
PostgreSQL 17 built-in measured to arrive untyped is now listed; a user routine still is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"

# The final suite's Step 0 probe list, each called over one column.
PROBED = [
    "to_tsvector('english', {c})",
    "to_tsquery({c})",
    "plainto_tsquery({c})",
    "setweight(to_tsvector({c}), 'A')",
    "ts_rank_cd(to_tsvector({c}), to_tsquery('a'))",
    "xmlelement(name x, xmlattributes({c} AS a))",
    "point({c}::float, 1)",
    "convert_to({c}, 'UTF8')",
    "pg_input_is_valid({c}, 'int')",
    "pg_input_error_info({c}, 'int')",
    "random_normal({c}::float)",
    "unistr({c})",
    "array_fill({c}, ARRAY[2])",
    "trim_array(ARRAY[{c}], 1)",
    "jsonb_array_length({c}::jsonb)",
    "atan2d({c}::float, 1)",
    "cosd({c}::float)",
    "current_setting({c})",
    "pg_typeof({c})",
    "format('%s', {c}) || txid_current_if_assigned()",
    "{c} || inet_server_addr()",
    "{c} || pg_backend_pid()",
    "{c} || statement_timestamp()",
    "{c} || transaction_timestamp()",
]


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


@pytest.mark.parametrize("call", PROBED)
def test_a_measured_built_in_is_not_declared_and_keeps_its_argument(call: str) -> None:
    sql = f"SELECT {call.format(c='o.status')} AS x FROM staging.orders AS o"
    result = _result(sql)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    assert "column:staging.orders.status" in {str(e.source) for e in result.edges}


def test_a_strict_aggregate_variant_aggregates() -> None:
    result = _result(
        "SELECT o.customer_id, jsonb_agg_strict(o.status) AS s FROM staging.orders AS o "
        "GROUP BY o.customer_id"
    )
    assert result.boundaries == []
    values = [e for e in result.edges if e.flow.value == "value" and str(e.target).endswith(".s")]
    assert {e.transform.value for e in values} == {"aggregated"}


def test_a_user_routine_is_still_declared() -> None:
    result = _result("SELECT calc_margin(o.amount) AS m FROM staging.orders AS o")
    assert [b.kind.value for b in result.boundaries] == ["source_unavailable"]
