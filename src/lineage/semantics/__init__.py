"""The lineage semantics document: how an LLM must read the parser output and the graph.

One fixed text, loaded from `lineage_semantics.md`. It is the agent's cached system-prompt
prefix (C6/C7), the MCP server's resource and prompt (C4), and the Claude Code skill
`.claude/skills/lineage-graph/SKILL.md` points at it - so the meaning of a filter, a
boundary or a refusal is stated once. `tests/test_semantics.py` fails if a parser or graph
vocabulary item is missing from it.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

DOCUMENT = Path(__file__).with_name("lineage_semantics.md")


@cache
def load() -> str:
    """The document's text (read once per process)."""
    return DOCUMENT.read_text(encoding="utf-8")
