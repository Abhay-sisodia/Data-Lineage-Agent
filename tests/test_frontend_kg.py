"""C9: the Streamlit views over the lineage graph render - without any model call.

`Lineage graph` is driven end to end. `Ask the lineage` is driven with an answer already in
the cache for the exact question, graph snapshot, model and prompt, so the page goes
through the real harness and is served from the cache. Delete with `frontend/`.
These tests replace the graph's contents; rebuild with `lineage graph build` afterwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from lineage.agent.harness import EFFORT, MODEL, prompt_fingerprint
from lineage.agent.store import Trace, TraceStore, cache_key
from lineage.config import AnalysisConfig
from lineage.evals.golden import Answer
from lineage.graph.build import analyse_folder, folder_sources, load
from lineage.graph.tools import GraphTools
from lineage.resolution.dictionary import Dictionary

pytestmark = pytest.mark.requires_neo4j

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "frontend" / "app.py")
SQL = (
    "INSERT INTO mart.top_customers (customer_id, revenue)\n"
    "SELECT o.customer_id, SUM(o.amount) FROM staging.orders AS o\n"
    "WHERE o.status = 'PAID' GROUP BY o.customer_id;\n"
)
QUESTION = "Which columns feed mart.top_customers.revenue?"


@pytest.fixture(scope="module")
def tools(neo4j_driver: Any, tmp_path_factory: pytest.TempPathFactory) -> GraphTools:
    folder = tmp_path_factory.mktemp("sql")
    (folder / "a.sql").write_bytes(SQL.encode("utf-8"))
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    book = Dictionary.load(ROOT / "stress" / "pg" / "dictionary.json")
    load(neo4j_driver, analyse_folder(folder, book, config), sources=folder_sources(folder))
    return GraphTools(neo4j_driver)


def _app(view: str) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    app.sidebar.radio[0].set_value(view).run()
    return app


def test_the_lineage_graph_view_draws_a_columns_lineage(tools: GraphTools) -> None:
    app = _app("Lineage graph")
    app.sidebar.text_input[0].set_value("top_customers.revenue").run()
    assert not app.exception, app.exception
    assert app.title[0].value == "Lineage graph"
    frames = app.get("iframe")
    assert frames, "no graph drawn"
    assert "staging.orders" in str(frames[0].proto)
    assert len(app.dataframe) == 1


def test_the_ask_view_answers_through_the_harness_from_the_cache(
    tools: GraphTools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINEAGE_CACHE_DIR", str(tmp_path))
    edges = [
        e["id"] for e in tools.upstream("mart.top_customers.revenue", 1) if e["type"] == "FEEDS"
    ]
    key = cache_key(QUESTION, tools.snapshot(), MODEL, EFFORT, prompt_fingerprint())
    TraceStore(tmp_path).put(
        Trace(
            key=key,
            question=QUESTION,
            snapshot=tools.snapshot(),
            model=MODEL,
            effort=EFFORT,
            prompt=prompt_fingerprint(),
            stage="agent",
            answer=Answer(
                question_id="q", status="answered", text="revenue = SUM(amount)", cited_edges=edges
            ),
        )
    )
    app = _app("Ask the lineage")
    app.text_area[0].set_value(QUESTION).run()
    app.button[0].click().run()
    assert not app.exception, app.exception
    assert any("revenue = SUM(amount)" in m.value for m in app.markdown)
    assert app.get("iframe"), "no evidence graph"
    assert app.metric[1].value == "cache", "the cached answer was not used"


def test_pasted_sql_loads_into_the_graph_and_the_graph_view_reads_it(
    tools: GraphTools,
) -> None:
    """Parse SQL -> "Load into graph" -> the Lineage graph view shows the pasted lineage."""
    pasted = (
        "INSERT INTO mart.top_customers (customer_id, revenue)\n"
        "SELECT p.order_id, SUM(p.amount) FROM staging.payments AS p GROUP BY p.order_id;\n"
    )
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    next(r for r in app.sidebar.radio if r.label == "Source dialect").set_value("postgres").run()
    app.text_area[0].set_value(pasted).run()
    next(t for t in app.text_input if t.label == "File name in the graph").set_value(
        "pasted/payments.sql"
    ).run()
    next(b for b in app.button if b.label == "Load into graph").click().run()
    assert not app.exception, app.exception
    assert any("Loaded `pasted/payments.sql`" in s.value for s in app.success)

    sources = {e["source"] for e in tools.upstream("mart.top_customers.revenue", 1)}
    assert sources == {"staging.payments.amount", "staging.payments.order_id"}, (
        "replace mode: only the pasted SQL's lineage is in the graph"
    )

    graph = _app("Lineage graph")
    graph.sidebar.text_input[0].set_value("top_customers.revenue").run()
    assert "staging.payments" in str(graph.get("iframe")[0].proto)


def test_the_dictionary_is_auto_detected_from_the_sql() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "frontend"))
    import kg_pages

    choices = {
        "batch": ROOT / "stress" / "pg" / "dictionary.json",
        "legacy": ROOT / "stress" / "dictionary_postgres.json",
    }
    assert kg_pages.best_dictionary("SELECT o.id FROM staging.orders AS o", choices) == "batch"
    final = ROOT / "stress" / "pg" / "final"
    if final.exists():
        m5 = (final / "medium" / "m5_names_under_load.sql").read_text(encoding="utf-8")
        assert (
            kg_pages.best_dictionary(m5, {**choices, "final": final / "dictionary.json"}) == "final"
        )


def test_a_load_with_no_lineage_warns_instead_of_claiming_success(tools: GraphTools) -> None:
    app = AppTest.from_file(APP, default_timeout=60)
    app.run()
    next(r for r in app.sidebar.radio if r.label == "Source dialect").set_value("postgres").run()
    app.text_area[0].set_value("INSERT INTO nowhere.t (a) SELECT x.a FROM nowhere.s AS x;").run()
    next(b for b in app.button if b.label == "Load into graph").click().run()
    assert not app.exception, app.exception
    assert not app.success and any("no lineage edges" in w.value for w in app.warning)


def test_the_graph_view_shows_a_file_or_the_entire_graph(
    neo4j_driver: Any, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # Its own graph: earlier tests in this module replace the shared one.
    folder = tmp_path_factory.mktemp("scope")
    (folder / "a.sql").write_bytes(SQL.encode("utf-8"))
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    book = Dictionary.load(ROOT / "stress" / "pg" / "dictionary.json")
    load(neo4j_driver, analyse_folder(folder, book, config), sources=folder_sources(folder))
    for scope in ("A file", "Entire graph"):
        app = _app("Lineage graph")
        next(r for r in app.sidebar.radio if r.label == "Show").set_value(scope).run()
        assert not app.exception, app.exception
        frames = app.get("iframe")
        assert frames, f"{scope}: no graph drawn"
        assert "staging.orders" in str(frames[0].proto)
        assert int(next(m for m in app.metric if m.label == "Edges").value) > 0
