"""C2 gate: the knowledge graph holds exactly what the parser said, once, linked across files.

The pure tests check the mapping (`graph.model`) with no database. The `requires_neo4j`
tests load into the live Neo4j - **they replace the graph's contents**; rebuild afterwards
with `lineage graph build`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lineage.config import AnalysisConfig
from lineage.graph.build import analyse_folder, graph_counts, load
from lineage.graph.model import FLOW_TYPES, build_batch, snapshot_id
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "stress" / "pg" / "dictionary.json"
LINEAGE = set(FLOW_TYPES.values())

FILE_A = (  # writes mart.top_customers from staging.orders
    "INSERT INTO mart.top_customers (customer_id, revenue)\n"
    "SELECT o.customer_id, SUM(o.amount) FROM staging.orders AS o\n"
    "WHERE o.status = 'PAID' GROUP BY o.customer_id;\n"
)
FILE_B = (  # reads mart.top_customers - the link between the two files
    "SELECT t.customer_id, t.revenue * 2 AS doubled FROM mart.top_customers AS t;\n"
    "COPY mart.events_log FROM '/data/events.csv';\n"
)
FILE_C = "SELECT o.id, calc_margin(o.amount) AS m FROM staging.orders AS o;\n"


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> list[Any]:
    folder = tmp_path_factory.mktemp("sql")
    for name, text in (("a.sql", FILE_A), ("sub/b.sql", FILE_B), ("c.sql", FILE_C)):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_folder(folder, Dictionary.load(BOOK), config)


def _lineage_by_file(batch: Any) -> dict[str, int]:
    found: dict[str, int] = {}
    for rel_type in LINEAGE:
        for row in batch.relationships.get(rel_type, {}).values():
            found[row["props"]["file"]] = found.get(row["props"]["file"], 0) + 1
    return found


def test_one_lineage_relationship_per_parser_edge_per_file(results: list[Any]) -> None:
    batch = build_batch(results)
    assert _lineage_by_file(batch) == {
        r.source.path: len(r.edges) + len(r.join_keys) + len(r.fallback)
        for r in results
        if r.edges or r.fallback
    }


def test_every_boundary_and_refusal_is_in_the_graph(results: list[Any]) -> None:
    batch = build_batch(results)
    assert len(batch.relationships["DECLARES"]) == sum(len(r.boundaries) for r in results)
    assert len(batch.nodes["Refusal"]) == sum(len(r.refusals) for r in results) == 1


def test_a_bare_select_result_is_per_file(results: list[Any]) -> None:
    tables = set(build_batch(results).nodes["Table"])
    assert {"<result:c.sql>", "<result:sub/b.sql>"} <= tables
    assert "<result>" not in tables


def test_the_snapshot_id_depends_on_content_not_order(results: list[Any]) -> None:
    assert snapshot_id(results) == snapshot_id(list(reversed(results)))
    assert snapshot_id(results) != snapshot_id(results[:2])


@pytest.mark.requires_neo4j
def test_neo4j_holds_exactly_the_batch(neo4j_driver: Any, results: list[Any]) -> None:
    report = load(neo4j_driver, results)
    assert graph_counts(neo4j_driver) == report.counts


@pytest.mark.requires_neo4j
def test_reloading_the_same_input_adds_nothing(neo4j_driver: Any, results: list[Any]) -> None:
    first = load(neo4j_driver, results)
    second = load(neo4j_driver, results, replace=False)
    assert second.snapshot == first.snapshot
    assert graph_counts(neo4j_driver) == first.counts


@pytest.mark.requires_neo4j
def test_lineage_links_across_files(neo4j_driver: Any, results: list[Any]) -> None:
    """staging.orders.amount -> mart.top_customers.revenue (a.sql) -> doubled (b.sql)."""
    load(neo4j_driver, results)
    with neo4j_driver.session() as session:
        row = session.run(
            "MATCH p = (s:Column {id: 'staging.orders.amount'})-[:FEEDS*2]->"
            "(t:Column {id: '<result:sub/b.sql>.doubled'}) "
            "RETURN [r IN relationships(p) | r.file] AS files"
        ).single()
    assert row is not None, "no two-hop path across the files"
    assert row["files"] == ["a.sql", "sub/b.sql"]
