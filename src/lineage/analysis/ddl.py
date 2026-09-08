"""DDL that moves data (T3.4b).

Silent failure 4. `ALTER TABLE ... EXCHANGE PARTITION` moves an entire dataset into a
table with no `INSERT` anywhere. Analyse DML only and the report says the table has no
writer - while in reality every row in it arrived this way.

**"No writer" is the worst available wrong answer.** It is worse than a missing edge,
because it reads as a positive finding: an auditor asking who populates this regulated
fact table gets a silence that looks like an answer. That asymmetry is the whole reason
this module exists for one statement type.

**The binding is positional, and that is not a shortcut.** An exchange swaps segments
between two tables. There is no select list to read names from, so the mapping is by
column position in the table definitions - exactly like a `UNION` arm or a `FETCH INTO`.
Oracle requires the shapes to match for the exchange to succeed at all, so a mismatch
against the dictionary means the dictionary is stale, and the honest response is to refuse
rather than to zip two different shapes together and produce four confident wrong edges.

**Band 2, not band 0.** In this corpus the exchange is inside an `EXECUTE IMMEDIATE`. Even
recovered, the hardest construct on the path is dynamic DDL (ADR-0001 §6), and crediting
band 0 with it would let that band's score claim work it did not do.
"""

from __future__ import annotations

import re

from lineage.ir.model import (
    Boundary,
    BoundaryKind,
    Flow,
    IREdge,
    Mechanism,
    Node,
    NodeKind,
    Origin,
    Tier,
    Transform,
)
from lineage.resolution.dictionary import Dictionary

__all__ = ["EXCHANGE_PARTITION", "exchange_partition_edges"]

EXCHANGE_PARTITION = re.compile(
    r"\bALTER\s+TABLE\s+(?P<partitioned>[\w.$#]+)\s+EXCHANGE\s+(?:SUB)?PARTITION\s+"
    r"(?P<partition>[\w.$#]+)\s+WITH\s+TABLE\s+(?P<staging>[\w.$#]+)",
    re.IGNORECASE | re.DOTALL,
)


def _simple(name: str) -> str:
    return name.rpartition(".")[2].upper()


def exchange_partition_edges(
    text: str,
    origin: Origin,
    dictionary: Dictionary,
    band: int = 2,
) -> tuple[list[IREdge], list[Boundary]]:
    """Edges for one `EXCHANGE PARTITION`, plus whatever it forces us to declare.

    Returns `([], [])` for any statement that is not an exchange - callers hand this every
    statement rather than pre-filtering, so that adding a second lineage-bearing DDL form
    later is one entry here rather than a new call site.
    """
    match = EXCHANGE_PARTITION.search(" ".join(text.split()))
    if match is None:
        return [], []

    partitioned = _simple(match.group("partitioned"))
    staging = _simple(match.group("staging"))
    partition = match.group("partition").upper()

    try:
        target_columns = dictionary.columns_of(partitioned)
        source_columns = dictionary.columns_of(staging)
    except Exception as exc:
        return [], [
            Boundary(
                kind=BoundaryKind.DDL_SEMANTICS,
                subject=f"{_simple(match.group('partitioned'))}:{partition}",
                detail=f"EXCHANGE PARTITION {partition}: {exc} - the exchange moves a "
                f"whole dataset and its column mapping cannot be stated",
                unit=origin.unit,
                line=origin.line,
            )
        ]

    if len(target_columns) != len(source_columns):
        # Oracle rejects an exchange between mismatched shapes, so this cannot be a
        # property of the running system - the dictionary is stale relative to the code.
        # Zipping them anyway would produce a full set of confident, wrong, positional
        # edges, which is precisely the failure mode this corpus exists to catch.
        return [], [
            Boundary(
                kind=BoundaryKind.DDL_SEMANTICS,
                subject=f"{partitioned}:{partition}",
                detail=f"EXCHANGE PARTITION {partition}: {staging} has "
                f"{len(source_columns)} columns and {partitioned} has "
                f"{len(target_columns)} - the exchange cannot be bound positionally "
                f"against this dictionary snapshot, so no edge is claimed",
                unit=origin.unit,
                line=origin.line,
                attaches_to=Node(kind=NodeKind.RELATION, name=partitioned),
            )
        ]

    edges = [
        IREdge(
            source=Node(kind=NodeKind.COLUMN, name=f"{staging}.{source}"),
            target=Node(kind=NodeKind.COLUMN, name=f"{partitioned}.{target}"),
            flow=Flow.VALUE,
            transform=Transform.IDENTITY,
            band=band,
            mechanism=Mechanism.AST,
            tier=Tier.A,
            origin=origin,
        )
        for source, target in zip(source_columns, target_columns, strict=True)
    ]

    # An exchange is a SWAP: the partition's old segment lands in the staging table. Here
    # the partition was empty, so that direction carries nothing and emitting it would be
    # inventing rows. Declared instead of guessed, because on a non-empty partition it is
    # a real flow and the next reader of this code needs to know it was considered.
    #
    # Attached to the partitioned table, because that is the object whose knowledge stops
    # here - "who writes FCT_REVENUE_PART" is the auditor's question, and a boundary that
    # cannot be queried beside that relation does not answer it.
    bounded = Node(kind=NodeKind.RELATION, name=partitioned)
    boundaries = [
        Boundary(
            kind=BoundaryKind.DDL_SEMANTICS,
            subject=f"{partitioned}:{partition}",
            detail=f"EXCHANGE PARTITION {partition}: an exchange swaps segments both "
            f"ways. The reverse flow {partitioned} -> {staging} carries whatever the "
            f"partition held before the exchange, which no static reading can know",
            unit=origin.unit,
            line=origin.line,
            attaches_to=bounded,
        ),
        Boundary(
            kind=BoundaryKind.DDL_SEMANTICS,
            subject=f"{partitioned}:{partition}",
            detail=f"EXCHANGE PARTITION {partition}: rows enter {partitioned} through "
            f"DDL, so no INSERT names it as a target - a DML-only reading reports it as "
            f"having no writer",
            unit=origin.unit,
            line=origin.line,
            attaches_to=bounded,
        ),
    ]
    return edges, boundaries
