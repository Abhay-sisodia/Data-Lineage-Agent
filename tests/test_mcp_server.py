"""C4 gate: over a real stdio MCP connection the tools return exactly what GraphTools returns.

The server is launched as a subprocess (`python -m lineage.mcp.server`), exactly as Claude
Code launches it, and ten calls covering every tool are compared with direct calls.
These tests replace the graph's contents; rebuild with `lineage graph build` afterwards.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from lineage.config import AnalysisConfig
from lineage.graph.build import analyse_folder, folder_sources, load
from lineage.graph.tools import GraphTools
from lineage.resolution.dictionary import Dictionary
from lineage.semantics import load as load_semantics

pytestmark = pytest.mark.requires_neo4j

ROOT = Path(__file__).resolve().parent.parent
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

CALLS: list[tuple[str, dict[str, Any]]] = [
    ("find", {"name": "top_customers.revenue"}),
    ("find", {"name": "customer_id"}),
    ("upstream", {"node": "<result:b.sql>.doubled", "depth": 2}),
    ("upstream", {"node": "mart.top_customers.revenue"}),
    ("downstream", {"node": "staging.orders.amount", "depth": 2}),
    ("path", {"source": "staging.orders.amount", "target": "<result:b.sql>.doubled"}),
    ("gaps", {"file": "c.sql"}),
    ("gaps", {"node": "<result:c.sql>.m"}),
    ("coverage", {"table": "mart.top_customers"}),
    ("coverage", {"table": "no.such_table"}),
]


@pytest.fixture(scope="module")
def tools(neo4j_driver: Any, tmp_path_factory: pytest.TempPathFactory) -> GraphTools:
    folder = tmp_path_factory.mktemp("sql")
    for name, text in FILES.items():
        (folder / name).write_bytes(text.encode("utf-8"))
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    book = Dictionary.load(ROOT / "stress" / "pg" / "dictionary.json")
    load(neo4j_driver, analyse_folder(folder, book, config), sources=folder_sources(folder))
    return GraphTools(neo4j_driver)


def _payload(result: Any) -> Any:
    """A list result is wrapped as {"result": [...]}; a dict result is the dict itself."""
    content = result.structured_content
    return content["result"] if set(content) == {"result"} else content


async def _over_mcp(calls: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lineage.mcp.server"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        names = sorted(tool.name for tool in (await session.list_tools()).tools)
        results = []
        for name, arguments in calls:
            result = await session.call_tool(name, arguments)
            assert not result.is_error, result
            results.append(_payload(result))
        explained = await session.call_tool("explain_edge", {"edge": results[5][0][0]["id"]})
        resource = await session.read_resource("lineage://semantics")
        return {
            "names": names,
            "results": results,
            "explained": _payload(explained),
            "semantics": resource.contents[0].text,
        }


def test_mcp_returns_exactly_what_the_tools_return(tools: GraphTools) -> None:
    got = asyncio.run(_over_mcp(CALLS))
    assert got["names"] == sorted(
        ["find", "upstream", "downstream", "path", "explain_edge", "gaps", "coverage"]
    )
    for (name, arguments), over_mcp in zip(CALLS, got["results"], strict=True):
        direct = getattr(tools, name)(**arguments)
        assert json.loads(json.dumps(direct)) == over_mcp, f"{name}({arguments}) differs"
    first_edge = tools.path("staging.orders.amount", "<result:b.sql>.doubled")[0][0]["id"]
    assert got["explained"] == tools.explain_edge(first_edge)
    assert got["semantics"] == load_semantics()
