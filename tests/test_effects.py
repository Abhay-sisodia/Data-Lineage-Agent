"""Effects (plan step 6, D-62): what a statement does to a whole relation is recorded.

TRUNCATE, DELETE with no WHERE, DROP, a rename, a dropped or retyped column, an attached or
detached partition - not lineage edges, but they decide what a table holds. Each is an
`Operation` at its file:line, top level or inside a routine, and never a refusal.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.export import from_document, to_document
from lineage.graph.model import build_batch
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "grammar" / "dictionary.json"


@pytest.fixture(scope="module")
def book() -> Dictionary:
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    return Dictionary.load(BOOK)


def _run(sql: str, book: Dictionary):
    return analyse_source(sql, book, AnalysisConfig(dialect="postgres"))


def _operations(result) -> list[tuple[str, str, int, str]]:
    return [(o.kind.value, o.relation, o.line, o.detail) for o in result.operations]


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("TRUNCATE tgt.t", ("resets", "tgt.t", 1, "emptied (TRUNCATE)")),
        ("DELETE FROM tgt.t", ("resets", "tgt.t", 1, "every row deleted (DELETE, no WHERE)")),
        ("DROP TABLE IF EXISTS tgt.t", ("drops", "tgt.t", 1, "dropped (TABLE)")),
        ("ALTER TABLE tgt.t RENAME COLUMN v TO amount", ("alters", "tgt.t", 1,
                                                          "renames column v to amount")),
        ("ALTER TABLE tgt.t RENAME TO t_old", ("alters", "tgt.t", 1, "renamed to t_old")),
        ("ALTER TABLE tgt.t DROP COLUMN w", ("alters", "tgt.t", 1, "drops column w")),
        ("ALTER TABLE tgt.t SET SCHEMA src", ("alters", "tgt.t", 1, "moved to schema src")),
        (
            "ALTER TABLE tgt.t ATTACH PARTITION tgt.t_2026 FOR VALUES FROM ('2026-01-01') "
            "TO ('2027-01-01')",
            ("alters", "tgt.t", 1, "attaches partition t_2026"),
        ),
    ],
    ids=["truncate", "delete-all", "drop", "rename-column", "rename", "drop-column",
         "set-schema", "attach"],
)  # fmt: skip
def test_each_effect_is_an_operation_and_never_a_refusal(sql, expected, book) -> None:
    result = _run(f"{sql};", book)
    assert _operations(result) == [expected]
    assert result.refusals == []
    assert [e.status for e in result.ledger] == ["effect"]


def test_a_truncate_inside_a_procedure_is_an_effect_and_the_reload_is_lineage(book) -> None:
    """The case behind plan step 6: TRUNCATE, then the reload - the table holds only that."""
    result = _run(
        """CREATE PROCEDURE tgt.reload() LANGUAGE plpgsql AS $$
BEGIN
  TRUNCATE tgt.t;
  INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a;
  CREATE TEMP TABLE snap AS SELECT a.id, a.n FROM src.a AS a;
  DROP TABLE IF EXISTS snap;
END $$;
""",
        book,
    )
    assert result.refusals == []
    assert ("column:src.a.v", "column:tgt.t.v") in {
        (str(e.source), str(e.target)) for e in result.edges
    }
    assert _operations(result) == [
        ("resets", "tgt.t", 3, "emptied (TRUNCATE)"),
        ("drops", "snap", 6, "dropped (TABLE)"),
    ]
    assert [e.status for e in result.ledger] == ["column_level"]


def test_operations_reach_the_graph_as_statement_to_table(book) -> None:
    sql = "TRUNCATE tgt.t;\nINSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a;\n"
    document = to_document(
        _run(sql, book),
        source_path="load.sql",
        source_text=sql,
        dialect="postgres",
        config_fingerprint="c",
        dictionary_fingerprint="d",
    )
    batch = build_batch([from_document(document)])
    (reset,) = batch.relationships["RESETS"].values()
    assert (reset["start"], reset["end"], reset["props"]["line"]) == (
        "load.sql#<script>:1",
        "tgt.t",
        1,
    )
