"""Shared scratch relations and the fusion hazard (T2.5).

Forty procedures all write `tmp_recent`. Key the relation on its name and compose a
lineage path through it, and you fuse forty unrelated lineages — inventing edges that
never existed. An invented edge inside a filing is worse than a missing one, because
someone acts on it.

**Why this module exists now, before path composition does.** Phase 0 emits edges, not
paths, and every edge carries the unit that produced it — so nothing is fused yet. The
hazard appears the moment anything joins `A: stg_customer -> tmp_recent` to
`B: tmp_recent -> fct_revenue`. Detecting the hazard is cheap now and the constraint is
recorded before the code that would violate it is written.

**Two different problems that look identical in the source.**

* A **global temporary table** holds session-private data. Two procedures writing it are
  writing different rows in different sessions. A path composed across them is *invented*
  — and can be refused outright.
* A **permanent table used as scratch** really is shared. Whether one procedure's rows
  reach another's readers depends on execution order and on the `DELETE` that usually
  precedes the load. That is not statically decidable, so the honest output is a declared
  hazard rather than either a confident join or a silent omission.

`tmp_recent` in this corpus is the second kind, despite its name.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from lineage.ir.model import Flow, IREdge, NodeKind
from lineage.resolution.dictionary import Dictionary


@dataclass(frozen=True)
class FusionHazard:
    """A relation written by more than one program unit."""

    relation: str
    writers: tuple[str, ...]
    readers: tuple[str, ...]
    is_temporary: bool

    @property
    def would_invent_paths(self) -> bool:
        """True when composing through this relation would cross unit boundaries."""
        return bool(set(self.readers) - set(self.writers)) or len(self.writers) > 1

    def describe(self) -> str:
        kind = "global temporary table" if self.is_temporary else "permanent scratch table"
        writers = ", ".join(sorted(self.writers))
        readers = ", ".join(sorted(self.readers)) or "none in this source"
        detail = (
            "session-private data, so a path composed across these units would be invented"
            if self.is_temporary
            else "genuinely shared, so whether data flows between writers is not "
            "statically decidable"
        )
        return (
            f"{self.relation} ({kind}) is written by {writers} and read by {readers} - "
            f"{detail}. Composing lineage through it across units must be refused or "
            f"declared, never silently joined."
        )


def _relation_of(edge: IREdge, *, target: bool) -> str | None:
    node = edge.target if target else edge.source
    if node.kind is NodeKind.RELATION:
        return node.name
    if node.kind is NodeKind.COLUMN:
        return node.name.rsplit(".", 1)[0]
    return None


def find_fusion_hazards(edges: list[IREdge], dictionary: Dictionary) -> list[FusionHazard]:
    """Relations whose lineage must not be composed across program units."""
    writers: dict[str, set[str]] = defaultdict(set)
    readers: dict[str, set[str]] = defaultdict(set)

    for edge in edges:
        # A filter edge names the relation whose rows are selected, not a data write.
        if edge.flow is Flow.VALUE:
            written = _relation_of(edge, target=True)
            if written:
                writers[written].add(edge.origin.unit)
        read = _relation_of(edge, target=False)
        if read:
            readers[read].add(edge.origin.unit)

    hazards: list[FusionHazard] = []
    for relation, writing_units in sorted(writers.items()):
        reading_units = readers.get(relation, set())
        if len(writing_units) < 2 and not (reading_units - writing_units):
            continue
        # Only relations used as an intermediate hop can fuse anything.
        if not reading_units:
            continue
        hazards.append(
            FusionHazard(
                relation=relation,
                writers=tuple(sorted(writing_units)),
                readers=tuple(sorted(reading_units)),
                is_temporary=dictionary.is_temporary(relation),
            )
        )

    return [h for h in hazards if h.would_invent_paths]
