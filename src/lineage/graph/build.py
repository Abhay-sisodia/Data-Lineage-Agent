"""Build the lineage knowledge graph: analyse SQL files, load the results into Neo4j (C2).

`analyse_folder` runs the deterministic parser over every `.sql` file and keeps each result
as a `lineage-result/1` document; `load` writes the rows `model.build_batch` derives from
them. Nothing here interprets lineage - the graph holds exactly what the parser said.

**Re-loading is safe.** Every node and relationship is written with `MERGE` on its id, so
loading the same input twice changes nothing. `replace=True` (the default for a build)
first empties the graph, so a file removed from the folder does not leave stale lineage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.export import ExportedResult, from_document, sha256_of, to_document
from lineage.graph.model import (
    SCHEMA_VERSION,
    GraphBatch,
    add_refusal_mentions,
    build_batch,
    snapshot_id,
)
from lineage.resolution.dictionary import Dictionary

_BATCH = 500
"""Rows per UNWIND statement."""

_UNIQUE_LABELS = (
    "Table",
    "Column",
    "Variable",
    "File",
    "Statement",
    "Boundary",
    "Refusal",
    "Procedure",
    "Literal",
    "Snapshot",
)


@dataclass(frozen=True)
class BuildReport:
    snapshot: str
    files: int
    counts: dict[str, int]


def analyse_folder(
    folder: Path, dictionary: Dictionary, config: AnalysisConfig
) -> list[ExportedResult]:
    """Every `.sql` file under `folder`, analysed. Paths are relative to `folder`."""
    results = []
    for path in sorted(folder.rglob("*.sql")):
        source = path.read_text(encoding="utf-8")
        document = to_document(
            analyse_source(source, dictionary, config),
            source_path=path.relative_to(folder),
            source_text=source,
            dialect=config.dialect,
            config_fingerprint=config.fingerprint(),
            dictionary_fingerprint=dictionary.fingerprint(),
        )
        # Through JSON and back: the graph is built from exactly what a consumer of the
        # exported document would read, never from in-memory objects it could not see.
        results.append(from_document(json.loads(json.dumps(document))))
    return results


def folder_sources(folder: Path) -> dict[str, str]:
    """Each `.sql` file's text, keyed by the relative path `analyse_folder` records."""
    return {
        path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(folder.rglob("*.sql"))
    }


def load(
    driver: Any,
    results: list[ExportedResult],
    *,
    database: str = "neo4j",
    replace: bool = True,
    sources: dict[str, str] | None = None,
    default_schema: str | None = None,
) -> BuildReport:
    """Write the results to Neo4j. Returns the snapshot id and the row counts written.

    `sources` (path -> SQL text) is stored on each `File` node, so a tool can show the SQL
    behind an edge. The text is checked against the hash the result recorded: SQL that
    changed since it was analysed is refused rather than shown beside lineage it no longer
    produces.
    """
    batch = build_batch(results)
    for result in results:
        text = (sources or {}).get(result.source.path)
        if text is None:
            continue
        if sha256_of(text) != result.source.sha256:
            raise ValueError(f"{result.source.path} changed since it was analysed")
        batch.node("File", result.source.path, text=text)
    if sources:
        add_refusal_mentions(batch, results, sources, default_schema)
    snapshot = snapshot_id(results)
    with driver.session(database=database) as session:
        for label in _UNIQUE_LABELS:
            session.run(
                f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            ).consume()
        if replace:
            session.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS").consume()
        _write(session, batch)
        session.run(
            "MERGE (s:Snapshot {id: $id}) SET s.schema = $schema, s.files = $files",
            id=snapshot,
            schema=SCHEMA_VERSION,
            files=len(results),
        ).consume()
    return BuildReport(snapshot=snapshot, files=len(results), counts=batch.counts())


def graph_counts(driver: Any, *, database: str = "neo4j") -> dict[str, int]:
    """What is actually in Neo4j, by node label and relationship type (Snapshot excluded)."""
    found: dict[str, int] = {}
    with driver.session(database=database) as session:
        for row in session.run("MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n"):
            if row["label"] != "Snapshot":
                found[row["label"]] = row["n"]
        for row in session.run("MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n"):
            found[row["rel"]] = row["n"]
    return dict(sorted(found.items()))


def _write(session: Any, batch: GraphBatch) -> None:
    for label, rows in sorted(batch.nodes.items()):
        for chunk in _chunks(list(rows.values())):
            session.run(
                f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row", rows=chunk
            ).consume()
    for rel_type, rows in sorted(batch.relationships.items()):
        by_shape: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows.values():
            by_shape.setdefault((row["start_label"], row["end_label"]), []).append(row)
        for (start, end), shaped in sorted(by_shape.items()):
            for chunk in _chunks(shaped):
                session.run(
                    f"UNWIND $rows AS row "
                    f"MATCH (a:{start} {{id: row.start}}) MATCH (b:{end} {{id: row.end}}) "
                    f"MERGE (a)-[r:{rel_type} {{id: row.id}}]->(b) SET r += row.props",
                    rows=chunk,
                ).consume()


def _chunks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [rows[i : i + _BATCH] for i in range(0, len(rows), _BATCH)]
