"""C3b gate: the semantics document covers every word of the parser and graph vocabulary.

The document is what the LLM reads to decipher the parser output. If the parser gains a
boundary kind or the graph a relationship type and the document does not explain it, the
LLM would meet a word it was never taught - so these tests fail first.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

import pytest

from lineage.analysis.refusal import RefusalCode
from lineage.export import FORMAT
from lineage.graph.model import FLOW_TYPES, NODE_LABELS, SCHEMA_VERSION, STRUCTURAL_TYPES
from lineage.ir.model import BoundaryKind, FilterPhase, Flow, Mechanism, Tier, Transform
from lineage.semantics import DOCUMENT, load

TEXT = load()
ROOT = Path(__file__).resolve().parent.parent

VOCABULARY: list[type[Enum]] = [
    BoundaryKind,
    RefusalCode,
    Flow,
    Transform,
    FilterPhase,
    Tier,
    Mechanism,
]


@pytest.mark.parametrize(
    "member",
    [member for enum in VOCABULARY for member in enum],
    ids=lambda m: f"{type(m).__name__}.{m.name}",
)
def test_every_enum_value_is_explained(member: Enum) -> None:
    assert f"`{member.value}`" in TEXT, f"`{member.value}` is not in {DOCUMENT.name}"


@pytest.mark.parametrize("name", [*NODE_LABELS, *FLOW_TYPES.values(), *STRUCTURAL_TYPES], ids=str)
def test_every_graph_label_and_relationship_is_explained(name: str) -> None:
    assert f"`{name}`" in TEXT, f"`{name}` is not in {DOCUMENT.name}"


def test_the_document_states_the_versions_it_describes() -> None:
    assert f"`{FORMAT}`" in TEXT
    assert f"`{SCHEMA_VERSION}`" in TEXT


def test_the_skill_points_at_the_document() -> None:
    skill = (ROOT / ".claude" / "skills" / "lineage-graph" / "SKILL.md").read_text(encoding="utf-8")
    assert "src/lineage/semantics/lineage_semantics.md" in skill
    assert skill.startswith("---\nname: lineage-graph\n")
