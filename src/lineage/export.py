"""The analyser's result as a versioned JSON document, and back (plan C1).

Everything downstream of the parser - the knowledge-graph build first - reads this
document rather than calling the analyser, so it has to carry the whole result: edges,
boundaries AND refusals, the statement counts, and the provenance that pins it to the SQL
and the settings that produced it.

It replaces the payload `lineage analyse --output` used to write, which handed raw
`Boundary` objects to `json.dumps` and crashed on the first boundary, and left refusals
out entirely - so no consumer could ever have told a refused statement from an empty one.

**Round-trip is exact:** `from_document(to_document(r))` gives back equal edges, boundaries
and refusals, in the same order. That is the C1 gate, and what lets a consumer trust the
file as the result rather than a summary of it.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lineage import __version__
from lineage.analysis.refusal import Refusal, RefusalCode
from lineage.ir.model import Boundary, IREdge

FORMAT = "lineage-result/1"
"""Bump the number on any change a reader of the previous version would misread."""


@dataclass(frozen=True)
class SourceInfo:
    """Which SQL the result describes."""

    path: str
    sha256: str
    dialect: str


@dataclass
class ExportedResult:
    """What a lineage-result document holds, read back into the IR's own types."""

    source: SourceInfo
    provenance: dict[str, str]
    statements_seen: int
    statements_analysed: int
    partial_statements: int
    edges: list[IREdge] = field(default_factory=list)
    boundaries: list[Boundary] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    join_keys: list[IREdge] = field(default_factory=list)
    """Lookup-join keys (D-57), apart from `edges`. Absent from older documents."""


def sha256_of(text: str) -> str:
    """The hash recorded for a source: of its UTF-8 bytes, as the answer keys hash SQL."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_document(
    result: Any,
    *,
    source_path: Path | str,
    source_text: str,
    dialect: str,
    config_fingerprint: str,
    dictionary_fingerprint: str,
) -> dict[str, Any]:
    """`AnalysisResult` -> a JSON-ready document. Lists keep the analyser's own order."""
    coverage = result.parse_coverage
    return {
        "format": FORMAT,
        "source": {
            "path": Path(source_path).as_posix(),
            "sha256": sha256_of(source_text),
            "dialect": dialect,
        },
        "provenance": {
            "analyser_version": __version__,
            "config_fingerprint": config_fingerprint,
            "dictionary_fingerprint": dictionary_fingerprint,
        },
        "statements": {
            "seen": result.statements_seen,
            "analysed": result.statements_analysed,
            "partial": result.partial_statements,
            "parse_coverage": coverage,
        },
        "edges": [edge.model_dump(mode="json") for edge in result.edges],
        "boundaries": [boundary.model_dump(mode="json") for boundary in result.boundaries],
        "refusals": [_refusal_row(refusal) for refusal in result.refusals],
        "join_keys": [edge.model_dump(mode="json") for edge in getattr(result, "join_keys", [])],
    }


def from_document(document: dict[str, Any]) -> ExportedResult:
    """A lineage-result document -> the IR's types. Refuses a format it does not know."""
    found = document.get("format")
    if found != FORMAT:
        raise ValueError(f"not a {FORMAT} document (format: {found!r})")
    source = document["source"]
    statements = document["statements"]
    return ExportedResult(
        source=SourceInfo(path=source["path"], sha256=source["sha256"], dialect=source["dialect"]),
        provenance=dict(document["provenance"]),
        statements_seen=statements["seen"],
        statements_analysed=statements["analysed"],
        partial_statements=statements["partial"],
        edges=[IREdge.model_validate(row) for row in document["edges"]],
        boundaries=[Boundary.model_validate(row) for row in document["boundaries"]],
        refusals=[
            Refusal(**{**row, "code": RefusalCode(row["code"])}) for row in document["refusals"]
        ],
        join_keys=[IREdge.model_validate(row) for row in document.get("join_keys", [])],
    )


def _refusal_row(refusal: Refusal) -> dict[str, Any]:
    row = dataclasses.asdict(refusal)
    row["code"] = refusal.code.value
    return row
