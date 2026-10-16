"""The lineage Q&A harness: deterministic guards around one Claude agent loop (C6-C7).

    question
      -> write guard        deterministic: a request to change the graph is refused
      -> name resolver      deterministic: an ambiguous name -> a clarifying question
      -> agent loop         Claude, with ONLY the typed graph tools + validated Cypher
      -> grounding check    every cited id must come from a tool result in THIS run
      -> coverage gate      boundaries on the cited statements are added as unknowns
      -> answer + trace

The model runs through the Claude Agent SDK (Claude Code credentials; no API key needed in
development). Every built-in Claude Code tool is switched off and no user or project
settings are loaded: the agent can do nothing but read the lineage graph.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Final

from lineage.agent.store import Trace, TraceStore, cache_key, fingerprint
from lineage.evals.golden import Answer
from lineage.graph.tools import GraphTools
from lineage.semantics import load as load_semantics

MODEL = "claude-sonnet-5-5"
EFFORT: Final = "medium"
SERVER = "lineage"
MAX_TURNS = 24

_WRITE_REQUEST = re.compile(
    r"^\s*(please\s+)?(delete|drop|remove|truncate|update|insert|create|merge|alter|"
    r"rename|overwrite|wipe|clear|reset)\b",
    re.IGNORECASE,
)
_IDENT = re.compile(r"(<result:[^>]+>(?:\.\w+)?|[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)+|\b\w*_\w+\b)")

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "clarify", "refused"]},
        "text": {"type": "string"},
        "cited_edges": {"type": "array", "items": {"type": "string"}},
        "cited_nodes": {"type": "array", "items": {"type": "string"}},
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "candidates": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "text", "cited_edges", "cited_nodes", "unknowns", "candidates"],
    "additionalProperties": False,
}

INSTRUCTIONS = """
You answer questions about SQL column-level lineage using ONLY the lineage tools.

Return the answer in the structured format:
- status: "answered"; "clarify" if a name is ambiguous (list candidate node ids);
  "refused" for any request to change data or the graph.
- text: a short answer for a person, in the vocabulary of the semantics document.
- cited_edges: the `id` of EVERY lineage edge your answer rests on, exactly as the tools
  returned it - and ONLY those that answer the question (e.g. for "which columns feed X"
  cite the FEEDS edges into X, not filters or influences). Never invent or alter an id.
- cited_nodes: node ids the answer lists, when the answer is a list of nodes rather than
  edges (e.g. the result of a comparison such as "columns that filter but never feed",
  columns without lineage, statements that write a table). Put EVERY listed id here,
  even though the text names them too; the edges that prove the list go in cited_edges.
  Otherwise [].
- unknowns: the `id` of every boundary or refusal that limits the answer (from `gaps`).
  A refused statement comes back twice - as a boundary of kind `refusal` and as a
  refusal: cite both ids. Also every UNVERIFIED hop the answer passes through (an edge the
  tools mark `unverified: true` - MENTIONS_COL or OPAQUE, what a refused statement still
  names) and every effect on a table the answer is about (`effects` from `gaps` /
  `coverage`: RESETS, DROPS, ALTERS - e.g. emptied by a TRUNCATE): cite their ids here,
  never in cited_edges, and say so in the text.
- candidates: only for "clarify".

Work from tool results only. Resolve names with `find` first. Answer the exact question
asked:
- One hop unless the question asks for more ("ultimate", "through every intermediate
  table", "how does A reach B", "directly or indirectly", a hop count): cite only the
  edges into (or out of) the asked node. Further hops may be named in the text, not cited.
- "Influences" means the INFLUENCES edges - a source that is both a value and an
  influence of a target has both facts, and is listed for each (D-47).
- If the graph has nothing, say so with empty citations.
"""


@dataclass
class Run:
    """One question through the harness: the answer, and everything that produced it."""

    answer: Answer
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stage: str = ""
    cost_usd: float = 0.0
    turns: int = 0
    notes: list[str] = field(default_factory=list)
    key: str = ""
    snapshot: str = ""
    trace_path: str = ""


HARNESS_VERSION = "4"  # plan step 7: unverified hops and effects are unknowns
"""Bump when a tool description or harness stage changes: it is part of every cache key."""


def prompt_fingerprint() -> str:
    """Everything the model is told: semantics, instructions, answer schema, tools."""
    return fingerprint(
        load_semantics(),
        INSTRUCTIONS,
        json.dumps(ANSWER_SCHEMA, sort_keys=True),
        ",".join(_TOOL_NAMES),
        HARNESS_VERSION,
    )


class Harness:
    def __init__(
        self,
        tools: GraphTools,
        model: str = MODEL,
        store: TraceStore | None = None,
        read_cache: bool = True,
    ) -> None:
        self.tools = tools
        self.model = model
        self.store = store
        self.read_cache = read_cache

    async def ask(self, question: str, question_id: str = "q") -> Run:
        """Answer from the cache when this exact question met this exact graph and prompt
        before; otherwise run the harness and store the trace (C8)."""
        snapshot = self.tools.snapshot()
        key = cache_key(question, snapshot, self.model, EFFORT, prompt_fingerprint())
        if self.store is not None and self.read_cache:
            cached = self.store.get(key)
            if cached is not None:
                answer = cached.answer.model_copy(update={"question_id": question_id})
                return Run(
                    answer,
                    cached.tool_calls,
                    "cache",
                    0.0,
                    0,
                    [*cached.notes, f"served from cache ({cached.stage})"],
                    key,
                    snapshot,
                )
        run = await self._answer(question, question_id)
        run.key, run.snapshot = key, snapshot
        if self.store is not None:
            path = self.store.put(
                Trace(
                    key=key,
                    question=question,
                    snapshot=snapshot,
                    model=self.model,
                    effort=EFFORT,
                    prompt=prompt_fingerprint(),
                    answer=run.answer,
                    tool_calls=run.tool_calls,
                    stage=run.stage,
                    notes=run.notes,
                    cost_usd=run.cost_usd,
                    turns=run.turns,
                )
            )
            run.trace_path = str(path)
        return run

    async def _answer(self, question: str, question_id: str) -> Run:
        if _WRITE_REQUEST.search(question):
            return Run(
                Answer(
                    question_id=question_id,
                    status="refused",
                    text="I can only read lineage; I cannot change the graph or the data.",
                ),
                stage="write guard",
            )
        ambiguous = self._ambiguous_names(question)
        if ambiguous:
            name, matches = ambiguous
            return Run(
                Answer(
                    question_id=question_id,
                    status="clarify",
                    text=f"'{name}' matches {len(matches)} columns - which one?",
                    candidates=matches,
                ),
                stage="name resolver",
            )

        run = await self._agent(question, question_id)
        problems = self._ungrounded(run)
        if problems:
            run.notes.append(f"grounding failed: {problems}; retried")
            retry = await self._agent(
                f"{question}\n\nYour previous answer cited ids no tool returned "
                f"({problems}). Answer again, citing only ids from tool results.",
                question_id,
            )
            retry.cost_usd += run.cost_usd
            retry.notes = run.notes
            run = retry
            leftover = self._ungrounded(run)
            if leftover:  # never pass on an invented id: drop it, and say so
                run.notes.append(f"dropped ungrounded ids {leftover}")
                bad = set(leftover)
                run.answer.cited_edges = [i for i in run.answer.cited_edges if i not in bad]
                run.answer.cited_nodes = [i for i in run.answer.cited_nodes if i not in bad]
                run.answer.unknowns = [i for i in run.answer.unknowns if i not in bad]
        self._coverage_gate(run)
        return run

    # --- deterministic stages ----------------------------------------------------------

    def _ambiguous_names(self, question: str) -> tuple[str, list[str]] | None:
        for name in dict.fromkeys(_IDENT.findall(question)):
            if "." in name or name.startswith("<result"):
                continue  # qualified names resolve on their own
            if _introduced_as_a_name(question, name):
                continue  # "a column named status_code" asks about every such column
            matches = self.tools.find(name, limit=50)
            exact_column = [m for m in matches if m["id"].rsplit(".", 1)[-1] == name]
            if len(exact_column) > 1 and not _qualified_elsewhere(question, name):
                return name, [m["id"] for m in exact_column]
        return None

    def _ungrounded(self, run: Run) -> list[str]:
        seen = _ids_in(run.tool_calls)
        cited = [*run.answer.cited_edges, *run.answer.cited_nodes, *run.answer.unknowns]
        return [i for i in dict.fromkeys(cited) if i not in seen]

    def _coverage_gate(self, run: Run) -> None:
        if run.answer.status != "answered" or not run.answer.cited_edges:
            return
        statements = {
            call_edge.get("statement")
            for call in run.tool_calls
            for call_edge in _edges_in(call["result"])
            if call_edge.get("id") in set(run.answer.cited_edges)
        } - {None}
        if not statements:
            return
        rows = self.tools._read(
            """
            MATCH (s:Statement)-[:BLOCKED_BY]->(b:Boundary) WHERE s.id IN $statements
            RETURN DISTINCT b.id AS id ORDER BY id
            """,
            statements=sorted(statements),
        )
        found = [row["id"] for row in rows]
        # Plan step 7: an unverified hop (refused SQL, D-60) the run walked through, and an
        # effect (D-62) on a table the cited edges touch, limit the answer just as much.
        found += [
            call_edge["id"]
            for call in run.tool_calls
            for call_edge in _edges_in(call["result"])
            if call_edge.get("unverified") and call_edge.get("id")
        ]
        tables = sorted(
            {
                end.rpartition(".")[0] if end.count(".") >= 2 else end
                for call in run.tool_calls
                for call_edge in _edges_in(call["result"])
                if call_edge.get("id") in set(run.answer.cited_edges)
                for end in (call_edge.get("source") or "", call_edge.get("target") or "")
                if end
            }
        )
        found += [row["id"] for row in self.tools.effects(tables)] if tables else []
        added = [i for i in dict.fromkeys(found) if i not in run.answer.unknowns]
        if added:
            run.answer.unknowns.extend(added)
            run.notes.append(f"coverage gate added unknowns {added}")

    # --- the model ---------------------------------------------------------------------

    async def _agent(self, question: str, question_id: str) -> Run:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        calls: list[dict[str, Any]] = []
        server = self._server(calls)
        options = ClaudeAgentOptions(
            tools=[],
            allowed_tools=[f"mcp__{SERVER}__{name}" for name in _TOOL_NAMES],
            mcp_servers={SERVER: server},
            strict_mcp_config=True,
            setting_sources=[],
            system_prompt=f"{load_semantics()}\n\n# Your task\n{INSTRUCTIONS}",
            model=self.model,
            effort=EFFORT,
            max_turns=MAX_TURNS,
            output_format={"type": "json_schema", "schema": ANSWER_SCHEMA},
            permission_mode="dontAsk",
        )
        result: Any = None
        async for message in query(prompt=question, options=options):
            if isinstance(message, ResultMessage):
                result = message
        structured = getattr(result, "structured_output", None) or _json(
            getattr(result, "result", "")
        )
        if not structured:
            answer = Answer(
                question_id=question_id,
                status="answered",
                text=f"no structured answer ({getattr(result, 'subtype', 'none')})",
            )
        else:
            answer = Answer(question_id=question_id, **structured)
        return Run(
            answer=answer,
            tool_calls=calls,
            stage="agent",
            cost_usd=float(getattr(result, "total_cost_usd", 0) or 0),
            turns=int(getattr(result, "num_turns", 0) or 0),
        )

    def _server(self, calls: list[dict[str, Any]]) -> Any:
        from claude_agent_sdk import create_sdk_mcp_server, tool

        t = self.tools

        def wrap(name: str, description: str, schema: dict[str, Any], fn: Any) -> Any:
            @tool(name, description, schema)
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                try:
                    result = fn(**args)
                except Exception as error:
                    result = {"error": str(error)[:300]}
                calls.append({"tool": name, "args": args, "result": result})
                return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

            return handler

        def obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
            return {"type": "object", "properties": props, "required": required}

        s, i, b = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
        return create_sdk_mcp_server(
            SERVER,
            tools=[
                wrap(
                    "find",
                    "Resolve a (partial) table or column name to graph node ids. "
                    "Several results means ambiguous.",
                    obj({"name": s, "limit": i}, ["name"]),
                    t.find,
                ),
                wrap(
                    "upstream",
                    "Lineage edges INTO a column: FEEDS and INFLUENCES into it, and "
                    "FILTERS on its table; `depth` hops up (default 1). `join_keys`: also "
                    "the KEYS edges - the keys that chose a lookup's row. `fallback` "
                    "(default true): also what REFUSED statements still name, each edge "
                    "marked `unverified` - report those as unknowns, never cite them.",
                    obj({"node": s, "depth": i, "join_keys": b, "fallback": b}, ["node"]),
                    t.upstream,
                ),
                wrap(
                    "downstream",
                    "Impact: lineage edges OUT of a column, `depth` hops down. "
                    "`join_keys`: also the lookups it keys. `fallback` (default true): also "
                    "tables reached only through refused SQL - `unverified`.",
                    obj({"node": s, "depth": i, "join_keys": b, "fallback": b}, ["node"]),
                    t.downstream,
                ),
                wrap(
                    "path",
                    "Every shortest lineage path from source to target (lists of edges).",
                    obj({"source": s, "target": s, "max_hops": i}, ["source", "target"]),
                    t.path,
                ),
                wrap(
                    "explain_edge",
                    "One lineage edge by id, with the SQL that produced it.",
                    obj({"edge": s}, ["edge"]),
                    t.explain_edge,
                ),
                wrap(
                    "gaps",
                    "Where knowledge stops, on a node's lineage or in a file: boundaries, "
                    "refusals, unverified hops, effects on the tables (TRUNCATE, DROP, "
                    "ALTER) and statements that are not plain lineage.",
                    obj({"node": s, "file": s}, []),
                    lambda **a: t.gaps(**a),
                ),
                wrap(
                    "coverage",
                    "A table's columns, which are fed by lineage (incoming only), its "
                    "writers, filters, gaps. Not what the table feeds: use downstream.",
                    obj({"table": s}, ["table"]),
                    t.coverage,
                ),
                wrap(
                    "graph_query",
                    "LAST RESORT, only when no other tool fits: one read-only "
                    "Cypher query over the lineage graph schema; return ids.",
                    obj({"cypher": s}, ["cypher"]),
                    t.graph_query,
                ),
            ],
        )


_TOOL_NAMES = (
    "find",
    "upstream",
    "downstream",
    "path",
    "explain_edge",
    "gaps",
    "coverage",
    "graph_query",
)


def _introduced_as_a_name(question: str, name: str) -> bool:
    """`named X` / `called X`: the question is about every column with that name."""
    return bool(re.search(rf"\b(named|called)\s+[`'\"]?{re.escape(name)}\b", question, re.I))


def _qualified_elsewhere(question: str, name: str) -> bool:
    return bool(re.search(rf"[\w>]\.{re.escape(name)}\b", question))


def _edges_in(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "id" in value and "type" in value and "source" in value:
            found.append(value)
        for inner in value.values():
            found.extend(_edges_in(inner))
    elif isinstance(value, list):
        for inner in value:
            found.extend(_edges_in(inner))
    return found


def _ids_in(calls: list[dict[str, Any]]) -> set[str]:
    """Every string a tool returned in this run (edge, node, boundary, refusal ids)."""
    seen: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for inner in value.values():
                walk(inner)
        elif isinstance(value, list):
            for inner in value:
                walk(inner)
        elif isinstance(value, str):
            seen.add(value)

    for call in calls:
        walk(call["result"])
    return seen


def _json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text or "")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
