"""The statement ledger (plan step 3): every statement listed, with what became of it."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lineage.analysis.ledger import STATUSES
from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.export import from_document, to_document
from lineage.graph.model import build_batch
from lineage.parsing import psql
from lineage.parsing.sqlscript import split_statements
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
PG = ROOT / "stress" / "pg"
GRAMMAR = PG / "grammar" / "dictionary.json"

LOAD = """DROP TABLE IF EXISTS tgt.old;
CREATE TABLE tgt.c AS SELECT a.id, a.v FROM src.a AS a;
INSERT INTO tgt.t (v)
SELECT a.v FROM src.a AS a;
DELETE FROM tgt.t AS t USING src.b AS b WHERE b.id = t.id;
UPDATE tgt.t AS t SET b = a.b AND a.v < 1 FROM src.a AS a WHERE a.id = t.id;
ANALYZE tgt.t;
INSERT INTO tgt.t (k, n) VALUES (1, 'one');
DELETE FROM tgt.t;
CREATE RULE r AS ON INSERT TO src.a DO ALSO INSERT INTO tgt.t (v) VALUES (NEW.v);
INSERT INTO tgt.t (ts) SELECT TIMESTAMP('1900-01-01') FROM src.a AS a;
"""


def _result(sql: str, book: Dictionary):
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    return analyse_source(sql, book, AnalysisConfig(dialect="postgres"))


@pytest.fixture(scope="module")
def grammar_book() -> Dictionary:
    return Dictionary.load(GRAMMAR)


def test_every_statement_of_a_load_script_is_listed_with_its_status(grammar_book) -> None:
    ledger = _result(LOAD, grammar_book).ledger
    assert [(e.line, e.kind, e.status) for e in ledger] == [
        (1, "DropStmt", "effect"),
        (2, "CreateTableAsStmt", "column_level"),
        (3, "InsertStmt", "column_level"),
        (5, "DeleteStmt", "no_lineage"),  # key-only match (D-5)
        (6, "UpdateStmt", "refused"),  # holdout gap G1
        (7, "VacuumStmt", "no_lineage"),
        (8, "InsertStmt", "no_lineage"),  # constants only
        (9, "DeleteStmt", "effect"),  # every row
        (10, "RuleStmt", "not_analysed"),  # can move data, nothing came of it
        (11, "?", "not_valid"),  # not PostgreSQL (D-59)
    ]
    assert "D-5" in ledger[3].reason
    assert ledger[1].edges == 2


def _corpus() -> list[tuple[Path, Path]]:
    pairs = [(sql, PG / "dictionary.json") for sql in sorted(PG.glob("b*/*.sql"))]
    final = PG / "final"
    if (final / "dictionary.json").exists():  # the final suite is local only (gitignored)
        pairs += [(sql, final / "dictionary.json") for sql in sorted(final.rglob("*.sql"))]
    return pairs


@pytest.mark.parametrize("sql, dictionary", _corpus(), ids=lambda p: p.name)
def test_the_ledger_lists_every_statement_exactly_once(sql: Path, dictionary: Path) -> None:
    """The CI guarantee: as many entries as statements, one each, none silent."""
    source = sql.read_text(encoding="utf-8")
    statements = split_statements(psql.restate(source)[0])
    ledger = _result(source, Dictionary.load(dictionary)).ledger
    assert [e.line for e in ledger] == [line for line, _ in statements]
    assert {e.status for e in ledger} <= set(STATUSES)


def test_the_ledger_survives_export_and_reaches_the_graph(grammar_book) -> None:
    result = _result(LOAD, grammar_book)
    document = to_document(
        result,
        source_path="load.sql",
        source_text=LOAD,
        dialect="postgres",
        config_fingerprint="c",
        dictionary_fingerprint="d",
    )
    exported = from_document(document)
    assert [row["status"] for row in exported.ledger] == [e.status for e in result.ledger]
    statements = build_batch([exported]).nodes["Statement"]
    assert statements["load.sql#<script>:1"]["status"] == "effect"
    assert statements["load.sql#<script>:10"]["status"] == "not_analysed"
