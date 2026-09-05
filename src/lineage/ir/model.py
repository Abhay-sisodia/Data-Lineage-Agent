"""The v0 intermediate representation (T2.1).

The IR is the compounding asset: dialect-independent, queryable, versionable. A new
source language becomes a new parser rather than a rewrite, and when migration becomes a
product later, transpilation is IR out to a target dialect.

Designed against what the corpus actually contained. Four things it forced:

* **Variables are nodes.** `ref_policy.window_days -> v_days -> v_cutoff` is two edges.
  Collapsing to endpoints hides where def-use broke, which is the entire diagnostic
  value of band 1.
* **Guard is part of identity, not of matching** (ADR-0001 amendment 1). `b1_09` writes
  the same edge on the happy path and inside an exception handler; those are two facts.
  But guard stays out of the match key so the precision gate cannot move on
  string-comparison noise.
* **Mechanism and tier are separate.** Mechanism is *how it was found*; tier is *how well
  it was corroborated*. An AST-derived edge is still only Tier A if nothing contradicts it.
* **`unexercised` is its own axis.** An edge can be Tier A - provably in the code - and
  never observed running. That combination is a finding, not a low tier.

Edges are never updated. They are superseded: close the old validity interval, append the
new one. An auditor never asks what is true now; they ask whether it was true when you
said it was, and only an append-only record can answer that.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Phase 0 has no ledger, so nothing supplies a real transaction time. A fixed sentinel
# keeps analyser output byte-identical between runs, which the reproducibility test
# depends on. A real run overrides it from the run record.
UNSET_TIME = datetime(1970, 1, 1, tzinfo=UTC)


class NodeKind(StrEnum):
    """What a lineage endpoint is.

    BOUNDARY is a first-class node, not an error: a DB link, a refused schema, a Java
    procedure. Declaring where knowledge stops is the product.
    """

    COLUMN = "column"
    VARIABLE = "variable"
    RELATION = "relation"
    PROCEDURE = "procedure"
    STATEMENT = "statement"
    BOUNDARY = "boundary"
    LITERAL = "literal"


class Flow(StrEnum):
    """What kind of claim the edge makes. Scored separately, never blended."""

    VALUE = "value"
    FILTER = "filter"


class Transform(StrEnum):
    """How the value was changed in transit. Part of the match key."""

    IDENTITY = "identity"
    DERIVED = "derived"
    AGGREGATED = "aggregated"
    CONDITIONAL = "conditional"


class Mechanism(StrEnum):
    """How the analyser derived the edge."""

    AST = "AST"
    DEF_USE = "DEF-USE"
    CFG = "CFG"
    LOG = "LOG"
    INFERRED = "INFERRED"


class Tier(StrEnum):
    """How well the edge is corroborated — by evidence type, never by model confidence."""

    A = "A"  # parser only, resolved AST path
    B = "B"  # code, runtime and profile agree
    C = "C"  # model inference reproduced by a second, different path
    D = "D"  # model reasoning alone - never enters a signed packet


class Node(BaseModel):
    """One endpoint of an edge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: NodeKind
    name: str = Field(min_length=1)

    @model_validator(mode="after")
    def _normalise(self) -> Self:
        # Oracle identifiers are case-insensitive; comparing raw case would produce
        # mismatches that say nothing about the analysis.
        object.__setattr__(self, "name", self.name.strip().upper())
        return self

    def __str__(self) -> str:
        return f"{self.kind.value}:{self.name}"


class Origin(BaseModel):
    """Where in the source this edge came from.

    Required on every edge. A fact whose origin cannot be stated is not evidence, and
    "which line produced this" is the first thing anyone asks when challenging a trace.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    unit: str = Field(min_length=1, description="Program unit, e.g. PKG_POLICY.LOAD")
    line: int = Field(ge=1)

    def __str__(self) -> str:
        return f"{self.unit}:{self.line}"


MatchKey = tuple[str, str, str, str]
IdentityKey = tuple[str, str, str, str, str, str]


class IREdge(BaseModel):
    """One lineage fact.

    Never mutated. To change one, supersede it: close its validity interval and append
    the replacement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Node
    target: Node
    flow: Flow = Flow.VALUE
    transform: Transform = Transform.IDENTITY
    band: int = Field(ge=0, le=3)

    mechanism: Mechanism
    tier: Tier = Tier.A

    guard: str | None = Field(
        default=None,
        description="Branch condition. The only nullable descriptive attribute, because "
        "most edges are genuinely unconditional.",
    )
    origin: Origin

    unexercised: bool = Field(
        default=False,
        description="Present in code but never observed executing. A separate axis from "
        "tier - an edge can be Tier A and unexercised, which is itself a finding.",
    )

    valid_from: datetime = Field(
        default=UNSET_TIME, description="When the code began behaving this way."
    )
    valid_to: datetime | None = Field(
        default=None,
        description="When it stopped. None means still valid - an open interval, which "
        "is structural rather than missing data.",
    )
    tx_from: datetime = Field(default=UNSET_TIME, description="When we learned it.")

    def match_key(self) -> MatchKey:
        """What decides a hit when scoring.

        Guard and origin are excluded on purpose (ADR-0001 §5): otherwise the gate would
        move on string-comparison noise rather than on analysis quality.
        """
        return (str(self.source), str(self.target), self.flow.value, self.transform.value)

    def identity(self) -> IdentityKey:
        """What makes this fact distinct in the ledger (ADR-0001 amendment 1).

        Two edges differing only by the condition under which they fire are two facts.
        """
        return (*self.match_key(), self.guard or "", str(self.origin))

    @property
    def is_current(self) -> bool:
        return self.valid_to is None

    def superseded_at(self, when: datetime) -> IREdge:
        """A copy with its validity interval closed. The original is untouched."""
        return self.model_copy(update={"valid_to": when})


class EdgeLedger(BaseModel):
    """An append-only collection of IR edges.

    The temporal half of the architecture, in miniature. Phase 2 replaces this with a
    bitemporal store; the point of having it now is that supersession semantics are
    designed in rather than retrofitted, which the architecture is explicit is a rewrite
    if left late.
    """

    model_config = ConfigDict(extra="forbid")

    edges: list[IREdge] = Field(default_factory=list)

    def append(self, edge: IREdge) -> None:
        self.edges.append(edge)

    def supersede(self, replacement: IREdge, when: datetime | None = None) -> None:
        """Replace the current version of a fact without destroying the old one.

        Closes any open edge sharing the replacement's identity, then appends. The old
        version stays queryable, which is what makes "was it true when you said it was"
        answerable at all.
        """
        moment = when or replacement.tx_from
        identity = replacement.identity()
        for index, existing in enumerate(self.edges):
            if existing.is_current and existing.identity() == identity:
                self.edges[index] = existing.superseded_at(moment)
        self.edges.append(replacement)

    def current(self) -> list[IREdge]:
        """Edges that have not been superseded."""
        return [edge for edge in self.edges if edge.is_current]

    def as_of(self, when: datetime) -> list[IREdge]:
        """What the lineage looked like at a point in time.

        The regulator's question is never "what does this look like today".
        """
        return [
            edge
            for edge in self.edges
            if edge.valid_from <= when and (edge.valid_to is None or edge.valid_to > when)
        ]
