"""C3 gate: the typed tools return exactly what the parser said - checked against the keys.

The final PostgreSQL acceptance suite's keys are closed-world: they state every edge the
parser should emit. So for every keyed target, `upstream` at one hop must return exactly
the key's facts for that file, and `gaps` must return exactly the key's expected
boundaries and refusals. The final suite is gitignored; that test skips without it.

These tests replace the graph's contents; rebuild with `lineage graph build` afterwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lineage.config import AnalysisConfig
from lineage.graph.build import analyse_folder, folder_sources, load
from lineage.graph.tools import GraphTools
from lineage.harness.labels import GroundTruth
from lineage.resolution.dictionary import Dictionary

pytestmark = pytest.mark.requires_neo4j

ROOT = Path(__file__).resolve().parent.parent
FINAL = ROOT / "stress" / "pg" / "final"
BOOK = ROOT / "stress" / "pg" / "dictionary.json"
POSTGRES = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})

FILES = {
    "a.sql": (
        "INSERT INTO mart.top_customers (customer_id, revenue)\n"
        "SELECT o.customer_id, SUM(o.amount) FROM staging.orders AS o\n"
        "WHERE o.status = 'PAID' GROUP BY o.customer_id;\n"
    ),
    "b.sql": "SELECT t.customer_id, t.revenue * 2 AS doubled FROM mart.top_customers AS t;\n",
    "c.sql": (
        "SELECT o.id, calc_margin(o.amount) AS m FROM staging.orders AS o;\n"
        "TRUNCATE mart.events_log;\n"
    ),
}


def _build(driver: Any, folder: Path, book: Path) -> GraphTools:
    results = analyse_folder(folder, Dictionary.load(book), POSTGRES)
    load(driver, results, sources=folder_sources(folder))
    return GraphTools(driver)


@pytest.fixture(scope="module")
def small(neo4j_driver: Any, tmp_path_factory: pytest.TempPathFactory) -> GraphTools:
    folder = tmp_path_factory.mktemp("sql")
    for name, text in FILES.items():
        (folder / name).write_bytes(text.encode("utf-8"))
    return _build(neo4j_driver, folder, BOOK)


def test_find_resolves_partial_and_ambiguous_names(small: GraphTools) -> None:
    assert small.find("top_customers.revenue") == [
        {"id": "mart.top_customers.revenue", "label": "Column"}
    ]
    assert len(small.find("customer_id")) > 1, "an ambiguous name must return every match"


def test_upstream_walks_across_files(small: GraphTools) -> None:
    edges = small.upstream("<result:b.sql>.doubled", depth=2)
    assert ("staging.orders.amount", "mart.top_customers.revenue", 2) in {
        (e["source"], e["target"], e["depth"]) for e in edges
    }
    assert {e["file"] for e in edges} == {"a.sql", "b.sql"}


def test_downstream_is_the_impact(small: GraphTools) -> None:
    targets = {e["target"] for e in small.downstream("staging.orders.amount", depth=2)}
    assert {"mart.top_customers.revenue", "<result:b.sql>.doubled"} <= targets


def test_path_explain_and_ids(small: GraphTools) -> None:
    paths = small.path("staging.orders.amount", "<result:b.sql>.doubled")
    assert len(paths) == 1 and [e["file"] for e in paths[0]] == ["a.sql", "b.sql"]
    explained = small.explain_edge(paths[0][0]["id"])
    assert explained is not None
    assert explained["sql"].startswith("INSERT INTO mart.top_customers")
    assert small.explain_edge("no-such-edge") is None


def test_snippet_shows_the_whole_statement_up_to_its_cap() -> None:
    from lineage.graph.tools import _SNIPPET_LINES, _snippet

    body = ["WITH a AS (", *[f"  -- line {i}" for i in range(120)], ")", "SELECT 1 FROM a;", "X;"]
    assert _snippet("\n".join(body), 1) == "\n".join(body[:-1])
    long = ["SELECT", *["  1," for _ in range(_SNIPPET_LINES + 30)], "  2;"]
    cut = _snippet("\n".join(long), 1)
    assert cut is not None and cut.endswith("-- ... 32 more lines of this statement")
    assert len(cut.splitlines()) == _SNIPPET_LINES + 1


def test_gaps_name_the_boundary_and_the_refusal(small: GraphTools) -> None:
    by_file = small.gaps(file="c.sql")
    assert {b["kind"] for b in by_file["boundaries"]} == {"source_unavailable", "refusal"}
    assert [r["code"] for r in by_file["refusals"]] == ["UNSUPPORTED_CONSTRUCT"]
    by_node = small.gaps(node="<result:c.sql>.m")
    assert "SOURCE_UNAVAILABLE:CALC_MARGIN" in {b["id"] for b in by_node["boundaries"]}


def test_a_refusal_is_reported_only_for_lineage_it_names(small: GraphTools) -> None:
    """Baseline finding: `TRUNCATE mart.events_log` was reported for every column of c.sql.

    It names only mart.events_log, so it is no unknown for `m` (read from staging.orders);
    the file still declares it, and a question about mart.events_log still gets it.
    """
    assert small.gaps(node="<result:c.sql>.m")["refusals"] == []
    refused = small.gaps(file="c.sql")["refusals"]
    assert [r["tables"] for r in refused] == [["mart.events_log"]]


def test_the_tables_a_refused_statement_names() -> None:
    from lineage.graph.model import statement_tables

    assert statement_tables("TRUNCATE mart.load_audit") == {"mart.load_audit"}
    assert statement_tables("COPY staging.orders FROM STDIN") == {"staging.orders"}
    # unreadable SQL falls back to the qualified names in the text
    assert "mart.x" in statement_tables("FROBNICATE mart.x WITH ((")


def test_the_resolver_asks_only_about_bare_ambiguous_names(small: GraphTools) -> None:
    from lineage.agent.harness import Harness

    harness = Harness(small)
    assert harness._ambiguous_names("Where does customer_id come from?") is not None
    assert (
        harness._ambiguous_names("Which tables are filtered by a column named customer_id?") is None
    )
    assert harness._ambiguous_names("Where does mart.top_customers.customer_id come from?") is None


def test_coverage_counts_columns_and_writers(small: GraphTools) -> None:
    found = small.coverage("mart.top_customers")
    assert found["found"] and found["columns_with_lineage"] == found["columns"] == 2
    assert [f["source"] for f in found["filters"]] == ["staging.orders.status"]
    assert small.coverage("no.such_table") == {"id": "no.such_table", "found": False}


# --- the gate: every keyed fact in the final suite, through the tools -----------------


def _keyed(path: Path) -> tuple[str, GroundTruth]:
    return path.relative_to(FINAL).with_suffix(".sql").as_posix(), GroundTruth.load(path)


def _local(name: str, file: str) -> str:
    return name.replace("<result>", f"<result:{file}>", 1)


@pytest.mark.skipif(not FINAL.exists(), reason="the final suite is gitignored")
def test_tools_return_exactly_the_keyed_lineage_of_the_final_suite(neo4j_driver: Any) -> None:
    tools = _build(neo4j_driver, FINAL, FINAL / "dictionary.json")
    flow_type = {"value": "FEEDS", "influence": "INFLUENCES", "filter": "FILTERS"}
    problems: list[str] = []
    for key_path in sorted(FINAL.glob("[emh]*/*.yaml")):
        file, truth = _keyed(key_path)
        keyed: dict[tuple[str, bool], set[tuple[str, str, str, str]]] = {}
        for edge in truth.edges:
            is_filter = edge.flow.value == "filter"  # a filter's target is a relation
            keyed.setdefault((_local(edge.target.name, file), is_filter), set()).add(
                (
                    _local(edge.source.name, file),
                    flow_type[edge.flow.value],
                    edge.transform.value,
                    edge.phase.value,
                )
            )
        for (target, is_filter), expected in sorted(keyed.items()):
            if is_filter:
                found = tools.coverage(target).get("filters", [])
            else:
                found = [e for e in tools.upstream(target, depth=1) if e["type"] != "FILTERS"]
            got = {
                (e["source"], e["type"], e["transform"], e["phase"])
                for e in found
                if e["file"] == file and e["target"] == target
            }
            if got != expected:
                problems.append(
                    f"{file} {target}: missing {expected - got}, extra {got - expected}"
                )

        gaps = tools.gaps(file=file)
        declared = {(b["kind"], b["subject"]) for b in gaps["boundaries"]}
        expected_b = {(b.kind.value, b.subject.upper()) for b in truth.expected_boundaries}
        if declared != expected_b:
            problems.append(f"{file} gaps: expected {expected_b}, got {declared}")
        refused = {f"<SCRIPT>:{r['line']}" for r in gaps["refusals"]}
        expected_r = {
            b.subject.upper() for b in truth.expected_boundaries if b.kind.value == "refusal"
        }
        if refused != expected_r:
            problems.append(f"{file} refusals: expected {expected_r}, got {refused}")
    assert not problems, "\n".join(problems)
