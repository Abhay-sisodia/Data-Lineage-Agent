"""Plan step 7: unknowns are part of every answer - walks, gaps and coverage show them inline.

A refused routine in the middle of a load, a TRUNCATE before a reload: before step 7 a
question about the tables involved came back looking complete. Now the refused routine's
table-level hop (`OPAQUE`, D-60) is on the walk, flagged `unverified`, and the TRUNCATE's
effect (`RESETS`, D-62) and the refused statement's ledger status are in `gaps`.

These tests replace the graph's contents; rebuild with `lineage graph build` afterwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lineage.config import AnalysisConfig
from lineage.graph.build import analyse_folder, folder_sources, load
from lineage.graph.tools import GraphTools
from lineage.resolution.dictionary import Dictionary

pytestmark = pytest.mark.requires_neo4j

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"
POSTGRES = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})

FILES = {
    "load.sql": (
        "TRUNCATE mart.top_customers;\n"
        "INSERT INTO mart.top_customers (customer_id, revenue)\n"
        "SELECT o.customer_id, o.amount FROM staging.orders AS o;\n"
        "CREATE PROCEDURE mart.log_status(p integer) LANGUAGE plpgsql AS $$\n"
        "DECLARE k ALIAS FOR p;\n"
        "BEGIN\n"
        "  INSERT INTO mart.events_log (kind) SELECT o.status FROM staging.orders AS o;\n"
        "END $$;\n"
    ),
    "read.sql": "SELECT e.kind FROM mart.events_log AS e;\n",
}


@pytest.fixture(scope="module")
def tools(neo4j_driver: Any, tmp_path_factory: pytest.TempPathFactory) -> GraphTools:
    folder = tmp_path_factory.mktemp("sql")
    for name, text in FILES.items():
        (folder / name).write_bytes(text.encode("utf-8"))
    results = analyse_folder(folder, Dictionary.load(BOOK), POSTGRES)
    load(neo4j_driver, results, sources=folder_sources(folder))
    return GraphTools(neo4j_driver)


OPAQUE = ("OPAQUE", "staging.orders", "mart.events_log")


def _hops(edges: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    return {(e["type"], e["source"], e["target"]) for e in edges}


def test_upstream_crosses_a_refused_routine_and_says_so(tools: GraphTools) -> None:
    edges = tools.upstream("<result:read.sql>.kind", depth=3)
    assert OPAQUE in _hops(edges)
    assert all(e.get("unverified") for e in edges if e["type"] == "OPAQUE")
    assert OPAQUE not in _hops(tools.upstream("<result:read.sql>.kind", depth=3, fallback=False))


def test_downstream_reaches_what_a_refused_routine_wrote(tools: GraphTools) -> None:
    assert OPAQUE in _hops(tools.downstream("staging.orders.status", depth=3))


def test_gaps_name_the_effect_and_the_statement_that_is_not_plain_lineage(
    tools: GraphTools,
) -> None:
    gaps = tools.gaps(node="mart.top_customers.revenue")
    assert [(e["type"], e["table"], e["line"]) for e in gaps["effects"]] == [
        ("RESETS", "mart.top_customers", 1)
    ]
    assert ("effect", 1) in {(s["status"], s["line"]) for s in gaps["statements"]}

    by_file = tools.gaps(file="load.sql")
    assert ("table_level", 4) in {(s["status"], s["line"]) for s in by_file["statements"]}
    assert [e["detail"] for e in by_file["effects"]] == ["emptied (TRUNCATE)"]


def test_coverage_of_a_table_names_its_effects(tools: GraphTools) -> None:
    found = tools.coverage("mart.top_customers")
    assert [(e["type"], e["detail"]) for e in found["effects"]] == [
        ("RESETS", "emptied (TRUNCATE)")
    ]


def test_impact_reports_unverified_reach_and_the_effects_on_affected_tables(
    tools: GraphTools,
) -> None:
    from lineage.graph.impact import to_csv, to_markdown

    status = tools.impact("staging.orders.status")
    unverified = {a["node"] for a in status["unknowns"]["unverified"]}
    assert "mart.events_log" in unverified

    amount = tools.impact("staging.orders.amount")
    assert ("RESETS", "mart.top_customers") in {(e["type"], e["table"]) for e in amount["effects"]}
    text = to_markdown(amount)
    assert "## Effects on these tables" in text and "emptied (TRUNCATE)" in text
    assert "effect,mart.top_customers,1,,RESETS" in to_csv(amount)


def test_the_explorer_draws_unverified_hops_and_marks_effects(tools: GraphTools) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "frontend"))
    import explorer

    edges = explorer.explore(tools, "<result:read.sql>.kind", "upstream", depth=3)
    assert ("OPAQUE", "staging.orders", "mart.events_log") in _hops(edges)
    drawn = explorer.model(edges)
    assert any(e["kind"] == "OPAQUE" and "unverified" in e["tip"] for e in drawn["edges"])
    assert {"staging.orders", "mart.events_log"} <= {c["key"] for c in drawn["cards"]}

    marks = explorer.gap_marks(tools, {"mart.top_customers"})
    assert "emptied (TRUNCATE) at load.sql:1" in marks["mart.top_customers"]

    rows = explorer.not_analysed(tools)
    assert {(r["file"], r["line"], r["status"]) for r in rows} >= {
        ("load.sql", 1, "effect"),
        ("load.sql", 4, "table_level"),
    }
    routine = next(r for r in rows if r["line"] == 4)
    assert routine["sql"].startswith("CREATE PROCEDURE mart.log_status")
    assert "staging.orders" in routine["tables"]
