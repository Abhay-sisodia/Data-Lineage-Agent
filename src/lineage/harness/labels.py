"""Ground-truth label format (T1.1).

The answer key the analyser is scored against. Shaped by ADR-0001, and the decisions
there are load-bearing rather than stylistic:

* edge identity for matching is ``(source, target, flow, transform)`` — so a correct
  pair of endpoints with the wrong transform class is a MISS, not partial credit;
* variables are first-class nodes, so a def-use chain is scored hop by hop;
* filter influence is an edge, tagged and scored separately from value flow;
* guards are recorded but excluded from precision;
* an edge's band is the hardest construct on its path.

Labels also record *how* they were established. A label confirmed by executing the
corpus and observing what actually changed carries more authority than one read from
source, and the harness reports the split — because a benchmark whose provenance cannot
be stated is not evidence.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# The IR is the shared vocabulary. Labels describe the same world the analyser emits
# into, so they use the same node kinds, flow kinds and transform classes rather than a
# parallel set that could drift.
from lineage.ir.model import Flow, Node, NodeKind, Origin, Transform

__all__ = [
    "Evidence",
    "Flow",
    "GroundTruth",
    "LabelledEdge",
    "Node",
    "NodeKind",
    "Origin",
    "Transform",
]


class Evidence(StrEnum):
    """How this label was established — the label's own provenance.

    OBSERVED is the strong form: the corpus was executed and the change was seen. It is
    independent of anyone's reading of the code, which is the entire point.
    """

    OBSERVED = "observed"
    SOURCE_READ = "source_read"
    ADJUDICATED = "adjudicated"


class LabelledEdge(BaseModel):
    """One hand-established lineage fact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Node
    target: Node
    flow: Flow = Flow.VALUE
    transform: Transform = Transform.IDENTITY
    band: int = Field(ge=0, le=3, description="Hardest construct on the path (ADR-0001 §6)")
    origin: Origin
    evidence: Evidence
    guard: str | None = Field(
        default=None,
        description="Branch condition, if any. Excluded from precision (ADR-0001 §5).",
    )
    unexercised: bool = Field(
        default=False,
        description=(
            "Present in code but never observed executing. A separate axis from tier, "
            "not a low tier - and impossible to backfill later."
        ),
    )
    note: str | None = None

    def key(self) -> tuple[str, str, str, str]:
        """Identity used for matching against analyser output.

        Guard, band, origin and evidence are deliberately absent: they are carried and
        reported, but they do not decide whether an edge matches.
        """
        return (str(self.source), str(self.target), self.flow.value, self.transform.value)


class GroundTruth(BaseModel):
    """The complete answer key for one corpus package."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package: str = Field(description="Path relative to corpus/")
    source_sha256: str = Field(
        min_length=64,
        max_length=64,
        description="Hash of the labelled source. A label set that does not match the "
        "code it describes is worse than none, so this is checked on load.",
    )
    labelled_by: str
    labelled_at: str
    edges: list[LabelledEdge]
    boundaries: list[str] = Field(
        default_factory=list,
        description="Known unknowns this package should produce - unresolvable "
        "constructs, DB links, refused schemas. Declaring these is scored too: "
        "silently omitting one is the failure mode the whole product exists to avoid.",
    )
    note: str | None = None

    @model_validator(mode="after")
    def _reject_duplicates(self) -> Self:
        keys = [edge.key() for edge in self.edges]
        duplicates = {key for key in keys if keys.count(key) > 1}
        if duplicates:
            raise ValueError(
                f"duplicate edges in ground truth for {self.package}: {sorted(duplicates)}"
            )
        return self

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    def verify_against(self, corpus_root: Path) -> None:
        """Fail if the labelled source has changed since it was labelled.

        Silent drift between code and its answer key would quietly invalidate every
        score computed afterwards, with no symptom.
        """
        source = corpus_root / self.package
        if not source.exists():
            raise FileNotFoundError(f"labelled package missing: {source}")
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != self.source_sha256:
            raise ValueError(
                f"{self.package} has changed since labelling.\n"
                f"  labelled: {self.source_sha256}\n"
                f"  actual:   {actual}\n"
                "Re-label deliberately, or restore the source. Do not simply update the hash."
            )

    def by_band(self, band: int) -> list[LabelledEdge]:
        return [edge for edge in self.edges if edge.band == band]

    def by_flow(self, flow: Flow) -> list[LabelledEdge]:
        return [edge for edge in self.edges if edge.flow == flow]

    def evidence_split(self) -> dict[str, int]:
        """How many labels rest on observation versus on reading the source.

        Reported alongside every score. A benchmark that cannot state its own provenance
        is not evidence.
        """
        split: dict[str, int] = {}
        for edge in self.edges:
            split[edge.evidence.value] = split.get(edge.evidence.value, 0) + 1
        return split
