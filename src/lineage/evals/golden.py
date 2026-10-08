"""Golden questions, the answer format, and the scorer (plan C5).

**An answer is judged by what it cites, never by its wording.** The agent returns the ids
of the graph edges and nodes its answer rests on, the boundaries and refusals it reports as
unknown, and a status. The scorer resolves each cited edge id in the graph to its fact -
(relationship type, source, target) - and compares SETS of facts with the expectation.
Phrasing can vary freely; a wrong, missing or invented fact cannot.

A cited edge id that is not in the graph is an invented fact, and makes the answer wrong.

Statuses:
- `answered`: a lineage answer; facts, nodes and unknowns must match exactly.
- `clarify`: the name is ambiguous; the candidates offered must include every expected one.
- `refused`: the request is outside what the agent may do (a write); nothing may be cited.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

Status = Literal["answered", "clarify", "refused"]

Fact = tuple[str, str, str]
"""(relationship type, source node id, target node id)."""


class Expected(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Status = "answered"
    facts: list[list[str]] = Field(default_factory=list)
    """Each [TYPE, source, target]; the cited edges must resolve to exactly this set."""
    nodes: list[str] = Field(default_factory=list)
    """Node ids the answer must cite exactly (for questions answered by nodes)."""
    unknowns: list[str] = Field(default_factory=list)
    """Boundary or refusal ids that must be reported, exactly."""
    candidates: list[str] = Field(default_factory=list)
    """For `clarify`: node ids that must all be offered."""


class GoldenQuestion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    question: str
    intent: str
    expected: Expected
    note: str = ""


class Answer(BaseModel):
    """What the agent returns for one question."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    status: Status
    text: str = ""
    cited_edges: list[str] = Field(default_factory=list)
    cited_nodes: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)


@dataclass
class Verdict:
    question_id: str
    correct: bool
    reasons: list[str] = field(default_factory=list)


def load_questions(path: Path) -> list[GoldenQuestion]:
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]
    questions = [GoldenQuestion.model_validate(row) for row in rows]
    ids = [q.id for q in questions]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate golden question ids")
    return questions


def score(question: GoldenQuestion, answer: Answer | None, resolve_edges: Any) -> Verdict:
    """Judge one answer. `resolve_edges(ids) -> {id: (TYPE, source, target)}` reads the graph."""
    if answer is None:
        return Verdict(question.id, False, ["no answer"])
    expected = question.expected
    reasons: list[str] = []
    if answer.status != expected.status:
        reasons.append(f"status {answer.status!r}, expected {expected.status!r}")

    if expected.status == "clarify":
        missing = set(expected.candidates) - set(answer.candidates)
        if missing:
            reasons.append(f"candidates missing {sorted(missing)}")
    elif expected.status == "refused":
        if answer.cited_edges or answer.cited_nodes:
            reasons.append("a refused request must cite nothing")
    else:
        resolved = resolve_edges(sorted(set(answer.cited_edges)))
        invented = sorted(set(answer.cited_edges) - set(resolved))
        if invented:
            reasons.append(f"cited edge ids not in the graph: {invented}")
        got: set[Fact] = set(resolved.values())
        want: set[Fact] = {(f[0], f[1], f[2]) for f in expected.facts}
        if got != want:
            reasons.append(_diff("facts", want, got))
        if set(answer.cited_nodes) != set(expected.nodes) and expected.nodes:
            reasons.append(_diff("nodes", set(expected.nodes), set(answer.cited_nodes)))
        if set(answer.unknowns) != set(expected.unknowns):
            reasons.append(_diff("unknowns", set(expected.unknowns), set(answer.unknowns)))
    return Verdict(question.id, not reasons, reasons)


def score_all(
    questions: list[GoldenQuestion], answers: list[Answer], resolve_edges: Any
) -> list[Verdict]:
    by_id = {answer.question_id: answer for answer in answers}
    return [score(q, by_id.get(q.id), resolve_edges) for q in questions]


def _diff(what: str, want: set[Any], got: set[Any]) -> str:
    missing = sorted(want - got, key=str)
    extra = sorted(got - want, key=str)
    return (
        f"{what}: missing {missing[:5]}{'...' if len(missing) > 5 else ''}, "
        f"extra {extra[:5]}{'...' if len(extra) > 5 else ''}"
    )
