"""C1: the analyser's result as a versioned JSON document, round-trip exact.

`lineage analyse --output` crashed on the first boundary (raw `Boundary` objects handed to
`json.dumps`) and never wrote refusals. Everything downstream - the knowledge graph first -
reads this document, so it must carry the whole result and read back unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lineage.analysis.procedure import analyse_source
from lineage.cli import app
from lineage.config import AnalysisConfig
from lineage.export import FORMAT, from_document, to_document
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "stress" / "pg" / "dictionary.json"

# Edges, a boundary (an unknown function, D-53) and a refusal (COPY FROM a file) in one
# script. It was a TRUNCATE until D-62 made TRUNCATE an effect, not a refusal.
SCRIPT = (
    "INSERT INTO core.ledger (id, balance)\n"
    "SELECT o.id, calc_margin(o.amount) FROM staging.orders AS o WHERE o.status = 'PAID';\n"
    "COPY mart.load_audit FROM '/data/audit.csv';\n"
)


def _config() -> AnalysisConfig:
    return AnalysisConfig.load().model_copy(update={"dialect": "postgres"})


def _document(source: str, path: str = "script.sql") -> dict:
    book = Dictionary.load(BOOK)
    config = _config()
    result = analyse_source(source, book, config, None)
    return to_document(
        result,
        source_path=path,
        source_text=source,
        dialect="postgres",
        config_fingerprint=config.fingerprint(),
        dictionary_fingerprint=book.fingerprint(),
    )


def test_the_document_carries_edges_boundaries_and_refusals() -> None:
    document = _document(SCRIPT)
    assert document["format"] == FORMAT
    assert document["edges"] and document["boundaries"] and document["refusals"]
    assert document["statements"]["seen"] == 2
    assert document["refusals"][0]["code"] == "SOURCE_UNAVAILABLE"


def test_round_trip_is_exact() -> None:
    result = analyse_source(SCRIPT, Dictionary.load(BOOK), _config(), None)
    back = from_document(json.loads(json.dumps(_document(SCRIPT))))
    assert back.edges == result.edges
    assert back.boundaries == result.boundaries
    assert back.refusals == result.refusals
    assert back.statements_seen == result.statements_seen
    assert back.source.dialect == "postgres"


def test_an_unknown_format_is_refused() -> None:
    with pytest.raises(ValueError, match="lineage-result/1"):
        from_document({"format": "something-else", "edges": []})


def test_the_cli_writes_the_document_when_boundaries_exist(tmp_path: Path) -> None:
    """The old crash: `Object of type Boundary is not JSON serializable`."""
    sql = tmp_path / "script.sql"
    sql.write_bytes(SCRIPT.encode("utf-8"))
    out = tmp_path / "result.json"
    run = CliRunner().invoke(
        app,
        [
            "analyse",
            str(sql),
            "--dictionary",
            str(BOOK),
            "--dialect",
            "postgres",
            "--output",
            str(out),
        ],
    )
    assert run.exit_code == 0, run.output
    back = from_document(json.loads(out.read_text(encoding="utf-8")))
    assert back.boundaries and back.refusals and back.edges


PG_BATCH_SQL = sorted((ROOT / "stress" / "pg").glob("b*/*.sql"))


@pytest.mark.parametrize("path", PG_BATCH_SQL, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_committed_postgres_test_file_round_trips(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    result = analyse_source(source, Dictionary.load(BOOK), _config(), None)
    back = from_document(json.loads(json.dumps(_document(source, str(path)))))
    assert (back.edges, back.boundaries, back.refusals) == (
        result.edges,
        result.boundaries,
        result.refusals,
    )
