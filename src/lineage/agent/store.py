"""Traces, the answer cache, and replay (plan C8).

Every answered question is stored as one JSON trace: the question, the cache key, the
graph snapshot, the model and prompt fingerprint, every tool call with its arguments and
result, the answer and the harness notes.

**The cache key is everything an answer depends on**: the normalised question, the graph
snapshot id, the model, the effort and the prompt fingerprint (semantics document,
instructions, tool definitions). A changed graph or prompt is a different key, so a stale
answer is never served - the same question on the same snapshot returns the same answer.

**Replay** re-executes each recorded tool call against the current graph and compares the
results. A difference means the graph moved under the answer; nothing is re-asked of the model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lineage.evals.golden import Answer

DEFAULT_DIR = Path(".lineage_cache")


def normalise(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip().casefold()


def cache_key(question: str, snapshot: str, model: str, effort: str, prompt: str) -> str:
    material = json.dumps([normalise(question), snapshot, model, effort, prompt])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass
class Trace:
    key: str
    question: str
    snapshot: str
    model: str
    effort: str
    prompt: str
    answer: Answer
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stage: str = ""
    notes: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    turns: int = 0

    def to_json(self) -> str:
        data = {**self.__dict__, "answer": self.answer.model_dump()}
        return json.dumps(data, indent=2, default=str, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> Trace:
        data = json.loads(text)
        data["answer"] = Answer.model_validate(data["answer"])
        return cls(**data)


class TraceStore:
    """One file per answered question, named by its cache key."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(os.environ.get("LINEAGE_CACHE_DIR", DEFAULT_DIR))

    def get(self, key: str) -> Trace | None:
        path = self.root / f"{key}.json"
        return Trace.from_json(path.read_text(encoding="utf-8")) if path.exists() else None

    def put(self, trace: Trace) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{trace.key}.json"
        path.write_text(trace.to_json(), encoding="utf-8", newline="\n")
        return path


@dataclass
class ReplayReport:
    calls: int
    mismatches: list[str]
    snapshot_then: str
    snapshot_now: str

    @property
    def reproduced(self) -> bool:
        return not self.mismatches


def replay(trace: Trace, tools: Any) -> ReplayReport:
    """Re-run every recorded tool call; report each whose result differs now."""
    mismatches = []
    for index, call in enumerate(trace.tool_calls):
        method = getattr(tools, call["tool"], None)
        if method is None:
            mismatches.append(f"#{index} {call['tool']}: no such tool now")
            continue
        try:
            now = method(**call["args"])
        except Exception as error:
            now = {"error": str(error)[:300]}
        if _canonical(now) != _canonical(call["result"]):
            mismatches.append(f"#{index} {call['tool']}({call['args']}): result differs")
    return ReplayReport(len(trace.tool_calls), mismatches, trace.snapshot, tools.snapshot())


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)
