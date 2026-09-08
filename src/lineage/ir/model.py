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


class BoundaryKind(StrEnum):
    """Why knowledge stops here. A closed list, like the refusal taxonomy.

    Boundaries carry the whole regulatory pitch - "here is what we could not see, counted
    and named" - and until 2026-09-09 they were free text. That made them **unscoreable**:
    the answer key expected `B2_DB_LINKS: remote_customer@crm_link - database link target
    out of coverage` while the analyser declared `insert_statement@20:
    REMOTE_CUSTOMER@CRM_LINK.CUST_ID (relation not in dictionary)`. Same fact, no possible
    string comparison, so every one of 18 expected boundaries read as undeclared while 35
    were declared, and nothing anywhere reported the contradiction.

    A kind plus a SUBJECT - the thing the boundary is about - is what makes two statements
    of the same fact comparable. The prose stays, for humans; it is no longer the identity.
    """

    DANGLING_REFERENCE = "dangling_reference"
    """A named object we were never given: an ungranted schema, a DB link target."""

    CONTEXT_DEPENDENT_BINDING = "context_dependent_binding"
    """The name resolves through the executing schema, so lineage depends on who ran it."""

    DYNAMIC_SQL = "dynamic_sql"
    """Statement text not knowable statically - assembled, or built through DBMS_SQL."""

    REFUSAL = "refusal"
    """A construct the classifier declined (T3.1). Subject is the refused statement."""

    PARSE_FAILURE = "parse_failure"
    """The parser could not read the statement at all."""

    UNRESOLVED_IDENTIFIER = "unresolved_identifier"
    """A name in SQL that is not a column of the relation it would bind to."""

    CROSS_UNIT_STATE = "cross_unit_state"
    """Package state written in one unit and read in another."""

    FUSION_HAZARD = "fusion_hazard"
    """A shared scratch relation where composing paths would invent a flow."""

    DDL_SEMANTICS = "ddl_semantics"
    """Lineage carried by DDL - a partition exchange - that DML-only reading misses."""

    ROW_CORRESPONDENCE = "row_correspondence"
    """Columns resolve but the row-to-row mapping does not."""

    SUPPRESSED_ERROR = "suppressed_error"
    """An exception handler that hides failure, so absence of an edge proves nothing."""

    DEPTH_CAP = "depth_cap"
    """Analysis stopped at a declared budget rather than guessing beyond it."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    """The body exists but cannot be read - wrapped PL/SQL, a missing trigger body."""


class Declared(str):
    """A boundary message that also knows what it is ABOUT.

    A ``str`` subclass, and that is a deliberate transitional choice rather than a clever
    one. Boundaries are produced at a dozen sites and consumed at fifty-odd, and the real
    fix - a first-class ``Boundary`` node that can be an edge endpoint - is `docs/ir-v0.md`
    item 1 and an IR change. Carrying ``kind`` and ``subject`` on the string lets the axis
    be SCORED now, without a refactor of every producer, consumer and test in between.

    Every consumer keeps treating it as the sentence it always was. Anything that wants to
    compare two statements of the same fact reads ``kind`` and ``subject`` instead, because
    prose was never comparable: the key says "database link target out of coverage" where
    the analyser says "(relation not in dictionary)".

    A plain ``str`` where a ``Declared`` was expected is not an error - it is a boundary
    nobody has classified yet, and ``classified`` says so rather than guessing.
    """

    __slots__ = ("kind", "subject")

    kind: BoundaryKind
    subject: str

    def __new__(cls, text: str, *, kind: BoundaryKind, subject: str) -> Declared:
        boundary = super().__new__(cls, text)
        boundary.kind = kind
        boundary.subject = subject.upper()
        return boundary

    def prefixed(self, prefix: str, *, subject_prefix: str = "") -> Declared:
        """The same fact with context prepended, keeping its classification.

        Formatting a ``Declared`` into an f-string yields a plain ``str`` and silently
        loses the structure, which is exactly the bug this class exists to prevent. Every
        site that decorates a boundary message goes through here instead.

        ``subject_prefix`` qualifies the subject as well, for facts raised somewhere that
        does not know its own unit - a parse failure knows its line and not much else, and
        a bare line number is not an identity.
        """
        subject = f"{subject_prefix}{self.subject}" if subject_prefix else self.subject
        return Declared(f"{prefix}{self}", kind=self.kind, subject=subject)

    @staticmethod
    def classified(entry: str) -> tuple[BoundaryKind, str] | None:
        """``(kind, subject)`` if this boundary has been classified, else ``None``."""
        if isinstance(entry, Declared):
            return entry.kind, entry.subject
        return None


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

    unexercised: bool | None = Field(
        default=None,
        description="Present in code but never observed executing. A separate axis from "
        "tier - an edge can be Tier A and unexercised, which is itself a finding. "
        "THREE-STATE ON PURPOSE (T3.5): None means no execution window was examined, so "
        "nothing is known either way. Folding that into False would claim every edge ran, "
        "which is the exact claim this axis exists to avoid; folding it into True would "
        "report the whole estate as dead code. Absence of a witness is not evidence of "
        "non-execution.",
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
