"""The coverage statement: counting what we were never given (T3.6d, T3.6e).

Precision and recall answer *"of the things we claimed, how many were right"*. They are
silent about the far more dangerous question: **what was never in front of us at all.** A
corpus can score 100% on both while a third of the estate sits outside the parser's reach,
and nothing in either number says so.

So this module counts three things that are structurally invisible to a confusion matrix.
All three are derived from the finished edge set and the dictionary, never hand-written -
a coverage statement somebody types is a coverage statement that describes last month.

**1. Dangling references.** An object the code names that we were never given.
`remote_customer@crm_link` is real, load-bearing and unreadable from here; the far side is
a different instance. The failure this prevents is a report that simply omits it, leaving
a reader to assume the upstream was checked.

**2. Orphan upstream - equivalently, a table with no writer in scope.** Something in the
corpus reads it and nothing in the corpus writes it, so lineage stops there for a reason
nobody stated. This is the `s4` failure counted rather than described: an auditor asking
who populates a regulated table gets silence, and silence reads as an answer. Many of these
are entirely legitimate - `stg_orders` is loaded by an ingestion process this corpus has
never seen - and that is exactly the point. **The tool cannot tell a staging table from a
missed writer, so it must name the tables in that state rather than rule on them.**

**3. Untouched relations.** A table the dictionary holds that appears in no edge at all,
on either end. Not a gap in a trace - a gap in the map, and the one nobody notices, because
nothing in a report about tables A and B suggests table C was never looked at.

**Why these three and not the register's literal third, "unattributed writers".** A write
whose emitting unit cannot be named **cannot arise in this IR**: `Origin` is mandatory on
every edge, so an edge either exists with a unit and a line or does not exist at all. That
is a design property worth stating rather than a check worth running, and emitting an
always-empty list would imply a test that never happens. What the register was reaching for
- a table that demonstrably receives data with nothing accounting for it - is signal 2.

**Why these and not others.** Each is a place where an *absence* in the output could be
read as a *finding* by someone acting on it. That is the only category of gap worth
counting separately, because every other gap shows up as a miss.

**Views are excluded from 2 and 3, and counted on their own.** A view is never written and
its upstream is its own definition, which the resolver follows to base tables (T3.4c). A
view that appears in no edge was resolved *through*, deliberately, and listing it as a gap
would fill the statement with noise. A coverage statement full of false gaps is one nobody
reads, which is the same argument that excluded triggers from the fusion hazards.

**The tier check is here for the same reason.** The register's line is explicit: if 70% of
edges land in Tier C or D, the lineage technically works but the evidence story does not.
That is a property of the whole run rather than of any package, so it is evaluated once,
here, against a threshold written down before the number was known.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lineage.ir.model import Flow, IREdge, NodeKind
from lineage.resolution.dictionary import Dictionary

# Emitted by band0 when a name resolves to nothing the dictionary holds. Shared as a
# constant so the producer and this consumer cannot drift apart on a magic string.
DANGLING_MARKER = "(relation not in dictionary)"

# From the register, written down before the distribution was known so it cannot be
# adjusted to whatever came out.
WEAK_EVIDENCE_CEILING = 0.70

# Objects that can be a lineage endpoint at all. Counting a procedure as a table with no
# writer would be noise, and noise is what makes a coverage statement go unread.
STORAGE_TYPES = {"TABLE", "VIEW"}

__all__ = ["DANGLING_MARKER", "WEAK_EVIDENCE_CEILING", "Coverage", "build_coverage", "render"]


@dataclass
class Coverage:
    """What the run could and could not see."""

    packages: int = 0
    edges: int = 0

    dangling_references: list[str] = field(default_factory=list)
    orphan_upstream: list[str] = field(default_factory=list)
    untouched_relations: list[str] = field(default_factory=list)

    relations_touched: int = 0
    storage_objects: int = 0
    views_resolved_through: int = 0

    tier_distribution: dict[str, int] = field(default_factory=dict)
    unexercised_claimed: int = 0
    no_execution_evidence: int = 0
    refusals: int = 0
    boundaries_declared: int = 0

    @property
    def weak_evidence_share(self) -> float | None:
        """Share of edges resting on Tier C or D — model inference rather than a parse."""
        if not self.edges:
            return None
        weak = self.tier_distribution.get("C", 0) + self.tier_distribution.get("D", 0)
        return weak / self.edges

    @property
    def evidence_story_holds(self) -> bool:
        """False when most of the lineage rests on inference rather than on evidence.

        Deliberately not a percentage to optimise. It is the register's own pass/fail:
        below the ceiling the tool is producing evidence, above it the tool is producing
        opinions with a schema.
        """
        share = self.weak_evidence_share
        return share is None or share < WEAK_EVIDENCE_CEILING

    @property
    def relation_coverage(self) -> float | None:
        """Storage objects any edge touches, over storage objects the dictionary holds."""
        if not self.storage_objects:
            return None
        return self.relations_touched / self.storage_objects


def _relation_of(node) -> str | None:  # type: ignore[no-untyped-def]
    if node.kind is NodeKind.RELATION:
        return node.name
    if node.kind is NodeKind.COLUMN and "." in node.name:
        return node.name.rsplit(".", 1)[0]
    return None


def build_coverage(
    edges: list[IREdge],
    boundaries: list[str],
    dictionary: Dictionary,
    *,
    packages: int = 0,
    refusals: int = 0,
) -> Coverage:
    """Derive the coverage statement from a finished run.

    Takes the corpus-wide edge set rather than one package's, because every one of the
    three signals is a statement about the whole graph. A table written in package A and
    read in package B has a writer; asking the question per package would report it as an
    orphan and manufacture a finding out of the corpus layout.
    """
    coverage = Coverage(packages=packages, edges=len(edges), refusals=refusals)
    coverage.boundaries_declared = len(boundaries)

    written: set[str] = set()
    read: set[str] = set()

    for edge in edges:
        coverage.tier_distribution[edge.tier.value] = (
            coverage.tier_distribution.get(edge.tier.value, 0) + 1
        )
        if edge.unexercised is True:
            coverage.unexercised_claimed += 1
        elif edge.unexercised is None:
            coverage.no_execution_evidence += 1

        source = _relation_of(edge.source)
        if source:
            read.add(source)
        target = _relation_of(edge.target)
        if target:
            # Only a VALUE edge writes. A filter edge names the relation whose rows were
            # selected; treating that as a write would report every table anyone read from
            # as having a writer, and quietly empty this whole section.
            (written if edge.flow is Flow.VALUE else read).add(target)

    # An object the code names that the dictionary does not hold. Read from the analyser's
    # own declared boundaries rather than re-derived, so the count cannot disagree with
    # what the report says.
    dangling: set[str] = set()
    for entry in boundaries:
        if DANGLING_MARKER not in entry:
            continue
        subject = entry.split(":", 1)[-1].split(DANGLING_MARKER)[0].strip()
        relation = subject.rsplit(".", 1)[0] if "." in subject else subject
        if relation:
            dangling.add(relation.upper())
    coverage.dangling_references = sorted(dangling)

    storage = {
        info.name.upper()
        for info in dictionary.objects.values()
        if info.object_type in STORAGE_TYPES
    }
    views = {
        info.name.upper() for info in dictionary.objects.values() if info.object_type == "VIEW"
    }
    tables = storage - views

    coverage.storage_objects = len(storage)
    coverage.relations_touched = len((written | read) & storage)
    coverage.views_resolved_through = len(views - (written | read))

    # Read by something in scope, written by nothing in scope. Both halves matter: a table
    # nobody reads is signal 3, not a broken trace. Restricted to objects the dictionary
    # holds, because a dangling reference is already counted above and counting one gap
    # under two headings doubles its apparent size.
    coverage.orphan_upstream = sorted((read - written) & tables)

    # In the map and in no trace at all. The gap nobody notices, because nothing in a
    # report about tables A and B suggests that table C was never looked at.
    coverage.untouched_relations = sorted(tables - (written | read))

    return coverage


def render(coverage: Coverage) -> str:
    """The coverage statement, generated from the run (T3.6e).

    Deterministic and ordered, because it is compared between runs and a diff full of
    reordering is a diff nobody reads.
    """
    lines: list[str] = []
    lines.append("COVERAGE STATEMENT  (generated from the run - never written by hand)")
    lines.append("=" * 78)
    lines.append(f"  packages scored        {coverage.packages}")
    lines.append(f"  edges emitted          {coverage.edges}")
    lines.append(
        f"  storage objects        {coverage.relations_touched} of "
        f"{coverage.storage_objects} touched by at least one edge "
        f"({_pct(coverage.relation_coverage)})"
    )
    lines.append(f"  boundaries declared    {coverage.boundaries_declared}")
    lines.append(f"  statements refused     {coverage.refusals}")

    lines.append("")
    lines.append("WHAT WE WERE NEVER GIVEN  (invisible to precision and recall)")
    lines.append(
        f"  dangling references    {len(coverage.dangling_references)}"
        "   <- named by the code, absent from the dictionary"
    )
    for name in coverage.dangling_references:
        lines.append(f"      {name}")
    lines.append(
        f"  orphan upstream        {len(coverage.orphan_upstream)}"
        "   <- read in scope, written by nothing in scope"
    )
    for name in coverage.orphan_upstream:
        lines.append(f"      {name}")
    lines.append(
        f"  untouched relations    {len(coverage.untouched_relations)}"
        "   <- in the map, in no trace at all"
    )
    for name in coverage.untouched_relations:
        lines.append(f"      {name}")
    lines.append(
        "  unattributed writers   n/a  <- cannot arise: Origin is mandatory on every edge, "
        "so a write either names its unit or does not exist"
    )
    lines.append(
        f"  views resolved through {coverage.views_resolved_through}"
        "   <- absent from every edge by design, not by omission"
    )

    lines.append("")
    lines.append("EVIDENCE STORY  (tier is evidence TYPE, never model confidence)")
    lines.append(f"  tier distribution      {coverage.tier_distribution or '-'}")
    lines.append(
        f"  Tier C or D            {_pct(coverage.weak_evidence_share)}"
        f"   ceiling {WEAK_EVIDENCE_CEILING:.0%}"
    )
    lines.append(
        "  verdict                "
        + (
            "PASS - the lineage rests on parsed evidence"
            if coverage.evidence_story_holds
            else "FLAGGED - most edges rest on inference; the lineage technically works "
            "and the evidence story does not"
        )
    )

    lines.append("")
    lines.append("EXECUTION AXIS")
    lines.append(f"  claimed unexercised    {coverage.unexercised_claimed}")
    lines.append(
        f"  no execution evidence  {coverage.no_execution_evidence}"
        "   <- not a claim that they never ran"
    )

    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"
