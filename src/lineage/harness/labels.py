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
    "ForbiddenEdge",
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


class ForbiddenEdge(BaseModel):
    """An edge that must NOT appear — the plausible wrong answer a package invites.

    A silent-failure package is defined by the specific mistake it elicits, and absence
    of the true edge is not the same failure as presence of the false one. Recall already
    punishes the first; nothing punished the second, because a wrong edge that happens to
    be absent from the key is only ever one anonymous false positive among others.

    Naming it changes what a regression means. ``s5`` losing its true edge is a miss;
    ``s5`` producing ``stg_orders.order_date -> tmp_recent.cust_id`` is the exact defect
    the case was written to catch, and it should be reported by name rather than as a
    number moving.

    ``transform`` is optional: omitted, the edge is forbidden under every transform class,
    which is usually what is meant. A wrong binding is wrong however it was computed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Node
    target: Node
    flow: Flow = Flow.VALUE
    transform: Transform | None = Field(
        default=None,
        description="Omit to forbid this pair of endpoints under any transform class.",
    )
    reason: str = Field(description="Why this looks right and is not. Stated, never implied.")

    def matches(self, key: tuple[str, str, str, str]) -> bool:
        """Does an emitted edge's match key describe this forbidden fact?"""
        source, target, flow, transform = key
        if (source, target, flow) != (str(self.source), str(self.target), self.flow.value):
            return False
        return self.transform is None or transform == self.transform.value


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
    forbidden: list[ForbiddenEdge] = Field(
        default_factory=list,
        description="Edges that must NOT be produced - the plausible wrong answer this "
        "package was written to elicit. Scored by presence, not by absence.",
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

    @model_validator(mode="after")
    def _reject_self_contradiction(self) -> Self:
        """A key that both requires and forbids the same edge is broken, not strict.

        Cheap to write by accident when a package's true edge and its trap share
        endpoints, and it would make the package unscoreable in a way no number reveals.
        """
        for forbidden in self.forbidden:
            for edge in self.edges:
                if forbidden.matches(edge.key()):
                    raise ValueError(
                        f"{self.package}: edge is both required and forbidden: {edge.key()}"
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
