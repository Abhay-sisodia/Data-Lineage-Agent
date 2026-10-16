"""Validate a model-written Cypher query before it may run (plan section 5a).

Five checks; failing any one rejects the query:

1. read-only: no write or schema clause, no procedure CALL, no LOAD CSV;
2. schema: every node label and relationship type is one the lineage graph has;
3. Neo4j `EXPLAIN` parses it without running it;
4. a row limit is appended when the query has none;
5. it runs in a READ-mode session (`GraphTools._read`), which Neo4j itself enforces.

Checks 1 and 2 are lexical and deliberately strict: a false rejection costs one retry, a
false acceptance could write to the graph or read outside it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lineage.graph.model import FLOW_TYPES, NODE_LABELS, STRUCTURAL_TYPES

ROW_LIMIT = 500

_WRITE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|CALL|FOREACH|ALTER|GRANT|"
    r"DENY|REVOKE|START|STOP|TERMINATE|USE)\b",
    re.IGNORECASE,
)
_STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_LABEL = re.compile(r"\(\s*\w*\s*:\s*`?(\w+)`?")
_REL = re.compile(r"\[\s*\w*\s*:\s*`?([\w|`:]+)`?")
_LIMIT = re.compile(r"\bLIMIT\s+\d+\s*;?\s*$", re.IGNORECASE)

KNOWN_LABELS = frozenset(NODE_LABELS)
KNOWN_TYPES = frozenset((*FLOW_TYPES.values(), *STRUCTURAL_TYPES))


@dataclass(frozen=True)
class Checked:
    ok: bool
    query: str = ""
    reason: str = ""


def check(query: str) -> Checked:
    """Lexical checks 1, 2 and 4. `EXPLAIN` (check 3) needs the database: see GraphTools."""
    text = query.strip().rstrip(";").strip()
    if not text:
        return Checked(False, reason="empty query")
    code = _STRING.sub("''", text)  # literals cannot trip the keyword checks
    if ";" in code:
        return Checked(False, reason="one statement only")
    write = _WRITE.search(code)
    if write:
        return Checked(False, reason=f"not read-only: {write.group(1).upper()} is not allowed")
    for label in _LABEL.findall(code):
        if label not in KNOWN_LABELS:
            return Checked(False, reason=f"unknown node label {label}")
    for group in _REL.findall(code):
        for rel in filter(None, re.split(r"[|`:]+", group)):
            if rel not in KNOWN_TYPES:
                return Checked(False, reason=f"unknown relationship type {rel}")
    if not re.search(r"\bRETURN\b", code, re.IGNORECASE):
        return Checked(False, reason="the query must RETURN something")
    if not _LIMIT.search(code):
        text = f"{text}\nLIMIT {ROW_LIMIT}"
    return Checked(True, query=text)
