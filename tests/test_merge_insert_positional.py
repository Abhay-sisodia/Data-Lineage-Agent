"""Finding FH-02: `MERGE ... WHEN NOT MATCHED THEN INSERT VALUES (...)` with no column list.

The insert arm needed a column list to pair values with target columns. Without one it was
skipped (`continue`) - no edge, no refusal, no boundary - although PostgreSQL defines the
pairing: all the target's columns in declared order, or the first N for N values, as for a
plain INSERT. The arm now takes the target's columns from the dictionary, and an arm that
cannot be paired is refused rather than dropped.
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"

MERGE = (
    "MERGE INTO mart.events_log AS t\n"
    "USING staging.events_log AS s ON t.id = s.id\n"
    "WHEN MATCHED THEN UPDATE SET kind = s.kind\n"
    "WHEN NOT MATCHED THEN INSERT {columns} VALUES ({values})"
)


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _facts(sql: str) -> set[tuple[str, str, str, str]]:
    result = _result(sql)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {(e.flow.value, str(e.source), str(e.target), e.transform.value) for e in result.edges}


def test_no_column_list_pairs_values_with_the_targets_columns_in_order() -> None:
    values = "s.id, s.kind, s.payload_size"
    written = _facts(MERGE.format(columns="", values=values))
    listed = _facts(MERGE.format(columns="(id, kind, rolled_up)", values=values))
    assert written == listed
    assert (
        "value",
        "column:staging.events_log.payload_size",
        "column:mart.events_log.rolled_up",
        "identity",
    ) in written


def test_fewer_values_fill_the_first_columns() -> None:
    values = "s.id, s.kind"
    written = _facts(MERGE.format(columns="", values=values))
    assert written == _facts(MERGE.format(columns="(id, kind)", values=values))


def test_an_arm_that_cannot_be_paired_is_refused_not_dropped() -> None:
    result = _result(MERGE.format(columns="", values="s.id, s.kind, s.payload_size, s.logged_at"))
    assert result.statements_seen == 1
    assert [r.code.value for r in result.refusals] == ["SHAPE_NOT_DECIDABLE"]
