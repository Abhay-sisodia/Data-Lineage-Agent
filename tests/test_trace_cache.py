"""C8 gate: traces, the answer cache and replay - without any model call.

The model is replaced by a fake agent that makes REAL tool calls and returns a fixed
answer, so the cache and replay are tested on genuine traces. These tests replace the
graph's contents; rebuild with `lineage graph build` afterwards.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from lineage.agent.harness import Harness, Run
from lineage.agent.store import Trace, TraceStore, cache_key, replay
from lineage.config import AnalysisConfig
from lineage.evals.golden import Answer
from lineage.graph.build import analyse_folder, folder_sources, load
from lineage.graph.tools import GraphTools
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "stress" / "pg" / "dictionary.json"
POSTGRES = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
A_SQL = (
    "INSERT INTO mart.top_customers (customer_id, revenue)\n"
    "SELECT o.customer_id, SUM(o.amount) FROM staging.orders AS o\n"
    "WHERE o.status = 'PAID' GROUP BY o.customer_id;\n"
)
B_SQL = "SELECT t.customer_id, t.revenue * 2 AS doubled FROM mart.top_customers AS t;\n"
QUESTION = "Which columns feed mart.top_customers.revenue?"


def test_the_cache_key_is_everything_an_answer_depends_on() -> None:
    key = cache_key(QUESTION, "snap1", "model", "medium", "prompt1")
    assert key == cache_key(
        "  which COLUMNS feed   mart.top_customers.revenue? ", "snap1", "model", "medium", "prompt1"
    )
    for changed in (
        ("snap2", "model", "medium", "prompt1"),
        ("snap1", "other", "medium", "prompt1"),
        ("snap1", "model", "high", "prompt1"),
        ("snap1", "model", "medium", "prompt2"),
    ):
        assert cache_key(QUESTION, *changed) != key


def _build(driver: Any, folder: Path, files: dict[str, str]) -> GraphTools:
    for name, text in files.items():
        (folder / name).write_bytes(text.encode("utf-8"))
    load(
        driver,
        analyse_folder(folder, Dictionary.load(BOOK), POSTGRES),
        sources=folder_sources(folder),
    )
    return GraphTools(driver)


class FakeAgentHarness(Harness):
    """The harness with the model swapped for real tool calls and a fixed answer."""

    calls = 0

    async def _agent(self, question: str, question_id: str) -> Run:
        FakeAgentHarness.calls += 1
        node = self.tools.find("top_customers.revenue")[0]["id"]
        edges = self.tools.upstream(node, 1)
        calls = [
            {
                "tool": "find",
                "args": {"name": "top_customers.revenue"},
                "result": self.tools.find("top_customers.revenue"),
            },
            {"tool": "upstream", "args": {"node": node, "depth": 1}, "result": edges},
        ]
        answer = Answer(
            question_id=question_id,
            status="answered",
            text="fake",
            cited_edges=[e["id"] for e in edges if e["type"] == "FEEDS"],
        )
        return Run(answer, calls, "agent")


@pytest.mark.requires_neo4j
def test_same_question_same_snapshot_is_served_from_cache(
    neo4j_driver: Any, tmp_path: Path
) -> None:
    tools = _build(neo4j_driver, tmp_path, {"a.sql": A_SQL})
    harness = FakeAgentHarness(tools, store=TraceStore(tmp_path / "cache"))
    FakeAgentHarness.calls = 0
    first = asyncio.run(harness.ask(QUESTION))
    second = asyncio.run(harness.ask("  which columns FEED mart.top_customers.revenue? "))
    assert FakeAgentHarness.calls == 1, "the second ask must not reach the model"
    assert second.stage == "cache" and second.answer == first.answer
    assert Path(first.trace_path).exists()

    uncached = FakeAgentHarness(tools, store=TraceStore(tmp_path / "cache"), read_cache=False)
    asyncio.run(uncached.ask(QUESTION))
    assert FakeAgentHarness.calls == 2, "--no-cache reaches the model"


@pytest.mark.requires_neo4j
def test_a_replayed_trace_reproduces_and_a_changed_graph_is_detected(
    neo4j_driver: Any, tmp_path: Path
) -> None:
    tools = _build(neo4j_driver, tmp_path, {"a.sql": A_SQL})
    store = TraceStore(tmp_path / "cache")
    run = asyncio.run(FakeAgentHarness(tools, store=store).ask(QUESTION))
    trace = Trace.from_json(Path(run.trace_path).read_text(encoding="utf-8"))

    same = replay(trace, tools)
    assert same.reproduced and same.calls == 2 and same.snapshot_now == trace.snapshot

    tampered = Trace.from_json(trace.to_json())
    tampered.tool_calls[1]["result"] = []
    assert not replay(tampered, tools).reproduced

    moved = _build(neo4j_driver, tmp_path, {"a.sql": A_SQL, "b.sql": B_SQL})
    after = replay(trace, moved)
    assert after.snapshot_now != trace.snapshot
    # a new snapshot is a new cache key: the old answer is not served for the new graph
    FakeAgentHarness.calls = 0
    asyncio.run(FakeAgentHarness(moved, store=store).ask(QUESTION))
    assert FakeAgentHarness.calls == 1
