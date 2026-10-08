"""C5 gate: the scorer judges answers correctly - right answers pass, planted wrong ones fail.

A correct answer set is built from the golden expectations (each expected fact mapped to
its edge ids in the graph); then each kind of wrong answer is planted and must be caught.
Needs the final-suite graph (gitignored SQL) and Neo4j; replaces the graph's contents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lineage.config import AnalysisConfig
from lineage.evals.golden import Answer, GoldenQuestion, load_questions, score, score_all
from lineage.graph.build import analyse_folder, folder_sources, load
from lineage.graph.tools import GraphTools
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
FINAL = ROOT / "stress" / "pg" / "final"
QUESTIONS = load_questions(ROOT / "evals" / "golden_questions.yaml")


def test_the_golden_set_is_thirty_questions_covering_every_intent() -> None:
    assert len(QUESTIONS) == 30
    assert {q.intent for q in QUESTIONS} == {
        "value_sources",
        "filters",
        "influences",
        "impact",
        "path",
        "gaps",
        "coverage",
        "open",
        "unsafe",
        "ambiguous",
    }


@pytest.fixture(scope="module")
def tools(neo4j_driver: Any) -> GraphTools:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    book = Dictionary.load(FINAL / "dictionary.json")
    load(neo4j_driver, analyse_folder(FINAL, book, config), sources=folder_sources(FINAL))
    return GraphTools(neo4j_driver)


def _edge_ids(tools: GraphTools, facts: list[list[str]]) -> list[str]:
    ids: list[str] = []
    for rel, source, target in facts:
        rows = tools._read(
            f"MATCH (a {{id: $s}})-[r:{rel}]->(b {{id: $t}}) RETURN r.id AS id", s=source, t=target
        )
        assert rows, f"expected fact not in graph: {rel} {source} -> {target}"
        ids.extend(row["id"] for row in rows)
    return ids


def _correct(tools: GraphTools, q: GoldenQuestion) -> Answer:
    e = q.expected
    return Answer(
        question_id=q.id,
        status=e.status,
        cited_edges=_edge_ids(tools, e.facts),
        cited_nodes=list(e.nodes),
        unknowns=list(e.unknowns),
        candidates=list(e.candidates),
    )


@pytest.mark.requires_neo4j
@pytest.mark.skipif(not FINAL.exists(), reason="the final suite is gitignored")
def test_correct_answers_all_pass(tools: GraphTools) -> None:
    verdicts = score_all(QUESTIONS, [_correct(tools, q) for q in QUESTIONS], tools.resolve_edges)
    assert [v.reasons for v in verdicts if not v.correct] == []


@pytest.mark.requires_neo4j
@pytest.mark.skipif(not FINAL.exists(), reason="the final suite is gitignored")
def test_planted_wrong_answers_are_caught(tools: GraphTools) -> None:
    by_id = {q.id: q for q in QUESTIONS}

    def verdict(qid: str, **change: Any) -> bool:
        answer = _correct(tools, by_id[qid]).model_copy(update=change)
        return score(by_id[qid], answer, tools.resolve_edges).correct

    a1 = _correct(tools, by_id["A1"])
    assert not verdict("A1", cited_edges=a1.cited_edges[1:]), "a missing fact"
    b3 = _correct(tools, by_id["B3"])
    assert not verdict("A1", cited_edges=a1.cited_edges + b3.cited_edges), "an extra fact"
    assert not verdict("A1", cited_edges=[*a1.cited_edges, "invented-edge-id"]), "an invented id"
    assert not verdict("F1", unknowns=[]), "an unknown not reported"
    assert not verdict("I1", status="answered"), "an unsafe request answered"
    assert not verdict("J1", status="answered"), "an ambiguous name answered without asking"
    assert score(by_id["A1"], None, tools.resolve_edges).correct is False, "no answer"
