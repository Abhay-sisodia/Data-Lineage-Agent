"""MVP plan F3: the impact report - what breaks if a column or table changes.

Gate: the affected set is exactly `downstream` with join keys (nodes and nearest hops); the
statements to edit include writers, readers, lookups keyed on the column (D-57) and SQL that
names it with no lineage edge (a key whose join supplies no value, D-5); refused statements
that name an affected table are unknowns.

These tests replace the graph's contents; rebuild with `lineage graph build` afterwards.
The small-graph tests run first: the last test loads the final suite over them.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

import pytest

from lineage.config import AnalysisConfig
from lineage.graph.build import analyse_folder, folder_sources, load
from lineage.graph.impact import to_csv, to_markdown
from lineage.graph.tools import GraphTools
from lineage.resolution.dictionary import Dictionary

pytestmark = pytest.mark.requires_neo4j

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "stress" / "pg" / "dictionary.json"
FINAL = ROOT / "stress" / "pg" / "final"
POSTGRES = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})

FILES = {
    "a.sql": (
        "INSERT INTO mart.top_customers (customer_id, region, revenue)\n"
        "SELECT o.customer_id, s.region, SUM(o.amount)\n"
        "FROM staging.orders AS o\n"
        "JOIN core.dim_store AS s ON s.id = o.store_id\n"
        "WHERE o.status = 'PAID'\n"
        "GROUP BY o.customer_id, s.region;\n"
    ),
    "b.sql": "SELECT t.customer_id, t.revenue * 2 AS doubled FROM mart.top_customers AS t;\n",
    "c.sql": "COPY mart.top_customers FROM '/data/top.csv';\n",
    "d.sql": (  # the join only keeps matching rows: the customers table supplies no value
        "INSERT INTO mart.events_log (id, kind)\n"
        "SELECT w.id, w.event_type FROM staging.web_events AS w\n"
        "JOIN staging.customers AS c ON c.id = w.customer_id;\n"
    ),
}


def _load(driver: Any, folder: Path, book: Path) -> GraphTools:
    results = analyse_folder(folder, Dictionary.load(book), POSTGRES)
    load(driver, results, sources=folder_sources(folder))
    return GraphTools(driver)


@pytest.fixture(scope="module")
def small(neo4j_driver: Any, tmp_path_factory: pytest.TempPathFactory) -> GraphTools:
    folder = tmp_path_factory.mktemp("sql")
    for name, text in FILES.items():
        (folder / name).write_bytes(text.encode("utf-8"))
    return _load(neo4j_driver, folder, BOOK)


def _nearest(tools: GraphTools, node: str) -> dict[str, int]:
    hops: dict[str, int] = {}
    for edge in tools.downstream(node, 15, join_keys=True):
        hops[edge["target"]] = min(hops.get(edge["target"], 99), edge["depth"])
    return hops


def test_drop_lists_affected_edits_and_unknowns(small: GraphTools) -> None:
    report = small.impact("staging.orders.amount", "drop")
    affected = {a["node"]: (a["hops"], a["flow"], a["transform"]) for a in report["affected"]}
    assert affected == {
        "mart.top_customers.revenue": (1, "value", "aggregated"),
        "<result:b.sql>.doubled": (2, "value", "derived"),
    }
    assert {a["node"]: a["hops"] for a in report["affected"]} == _nearest(
        small, "staging.orders.amount"
    )
    assert [(e["file"], e["line"], e["lines"], e["possible"]) for e in report["edit"]] == [
        ("a.sql", 1, [2], False)
    ]
    assert [x["file"] for x in report["unknowns"]["refusals"]] == ["c.sql"]


def test_a_lookup_key_affects_what_the_lookup_supplies(small: GraphTools) -> None:
    """`s.region` is read from the dim_store row `o.store_id` picks (D-57)."""
    report = small.impact("staging.orders.store_id", "drop")
    assert [(a["node"], a["flow"]) for a in report["affected"]] == [
        ("mart.top_customers.region", "join key")
    ]
    assert [(e["file"], e["lines"], e["reason"], e["possible"]) for e in report["edit"]] == [
        ("a.sql", [4], "joins a lookup on it", False)
    ]


def test_a_join_key_with_no_lookup_is_a_possible_edit(small: GraphTools) -> None:
    report = small.impact("staging.web_events.customer_id", "drop")
    assert report["affected"] == []
    assert [(e["file"], e["lines"], e["possible"]) for e in report["edit"]] == [
        ("d.sql", [3], True)
    ]


def test_rename_edits_both_the_writer_and_the_reader(small: GraphTools) -> None:
    report = small.impact("mart.top_customers.revenue", "rename")
    reasons = {e["file"]: e["reason"] for e in report["edit"]}
    assert reasons == {"a.sql": "writes it", "b.sql": "reads it into lineage"}
    assert "new name" in report["consequence"]


def test_a_filter_column_affects_the_whole_table(small: GraphTools) -> None:
    report = small.impact("staging.orders.status", "type")
    assert ("mart.top_customers", "filter") in {(a["node"], a["flow"]) for a in report["affected"]}


def test_a_table_covers_every_column(small: GraphTools) -> None:
    report = small.impact("staging.orders", "drop")
    assert report["kind"] == "table"
    assert "staging.orders.amount" in report["columns"]
    assert [e["file"] for e in report["edit"]] == ["a.sql"]
    assert report["edit"][0]["lines"] == [3]  # the line naming the table


def test_unknown_nodes_and_changes(small: GraphTools) -> None:
    assert small.impact("no.such.column")["found"] is False
    with pytest.raises(ValueError, match="change must be"):
        small.impact("staging.orders.amount", "truncate")


def test_markdown_and_csv_carry_the_same_report(small: GraphTools) -> None:
    report = small.impact("staging.orders.amount", "drop")
    text = to_markdown(report)
    for heading in ("## Statements to edit", "## Affected downstream", "## Unknowns"):
        assert heading in text
    assert "`mart.top_customers.revenue`" in text and "`c.sql`:1" in text
    rows = list(csv.DictReader(io.StringIO(to_csv(report))))
    assert [r["section"] for r in rows].count("affected") == len(report["affected"])
    assert [r["section"] for r in rows].count("edit") == len(report["edit"])


@pytest.mark.skipif(not FINAL.exists(), reason="the final suite is not on this machine")
def test_final_suite_impact_equals_downstream(neo4j_driver: Any) -> None:
    tools = _load(neo4j_driver, FINAL, FINAL / "dictionary.json")
    column = "s2p.po_distribution.amount_cancelled"
    report = tools.impact(column, "drop")
    assert {a["node"]: a["hops"] for a in report["affected"]} == _nearest(tools, column)
    assert report["summary"]["affected"] == 21
    assert {(e["file"], tuple(e["lines"])) for e in report["edit"]} == {
        ("hard/h2_po_fact_load.sql", (103, 196)),
        ("medium/m1_procurement_savings.sql", (70, 139)),
    }


def test_a_column_reached_as_value_and_influence_shows_both() -> None:
    """D-47: one source can be a value AND an influence of the same column. The report must
    say both, with the value's transform - keeping one edge understated a value as an
    influence."""
    from lineage.graph.impact import _affected

    class _Tools:
        def downstream(self, node: str, depth: int, join_keys: bool = False) -> list[Any]:
            base = {"source": node, "statement": "s", "file": "f.sql", "line": 1}
            return [
                {
                    **base,
                    "id": "a",
                    "type": "INFLUENCES",
                    "target": "r.x",
                    "transform": "identity",
                    "depth": 1,
                },
                {
                    **base,
                    "id": "b",
                    "type": "FEEDS",
                    "target": "r.x",
                    "transform": "aggregated",
                    "depth": 1,
                },
                {
                    **base,
                    "id": "c",
                    "type": "FEEDS",
                    "target": "r.y",
                    "transform": "derived",
                    "depth": 1,
                },
                {
                    **base,
                    "id": "d",
                    "type": "FEEDS",
                    "target": "r.y",
                    "transform": "derived",
                    "depth": 2,
                },
            ]

    rows = {r["node"]: r for r in _affected(_Tools(), ["s.t.c"], 2)}  # type: ignore[arg-type]
    assert (rows["r.x"]["flow"], rows["r.x"]["transform"], rows["r.x"]["edge"]) == (
        "value + influence",
        "aggregated",
        "b",
    )
    assert rows["r.x"]["edges"] == ["a", "b"]
    assert (rows["r.y"]["flow"], rows["r.y"]["hops"], rows["r.y"]["edges"]) == ("value", 1, ["c"])
