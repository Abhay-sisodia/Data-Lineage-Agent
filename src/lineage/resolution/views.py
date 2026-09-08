"""Views resolved to the base tables underneath them (T3.4c).

Silent failure 6, in one sentence: **you write to a view, the real write lands on a base
table you never connected to anything, and the regulated table appears to have no writer.**
"No writer" is worse than a missing edge, because it reads as a positive finding - an
auditor asking who populates this table gets a silence that looks like an answer.

**Why this lives in `resolution` rather than in an analyser.** It arrived three separate
times: band 0 inlines view text before analysing a projection, the trigger analyser
rewrites an `INSTEAD OF` trigger's `:NEW` row, and now an ordinary `UPDATE` through a join
view needs the same thing. Three copies of a resolution rule is three chances for them to
disagree about the same object, which is the exact failure the dictionary exists to
prevent. A view is a naming fact, so it belongs beside the synonym resolver.

**The relation end follows the column end.** An edge like
`V_CUSTOMER_EDITABLE.REGION -> relation:V_CUSTOMER_EDITABLE [filter]` has a view on both
ends. Resolving only the column would leave the edge claiming that a column of
`dim_customer` filters a view, which is a worse answer than either consistent one. So the
column is resolved first and the relation follows it to the same base table: the predicate
restricts rows of the table the column actually lives in.

**A join view is declared, not silently picked apart.** `v_customer_editable` joins
`dim_customer` to `stg_customer`. Column by column the mapping is exact, but the ROW
CORRESPONDENCE between the two base tables is the join predicate, and an edge set that
resolves each column independently does not carry it. That is stated as a boundary rather
than assumed away.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from lineage.ir.model import IREdge, Node, NodeKind
from lineage.resolution.dictionary import Dictionary

DIALECT = "oracle"

__all__ = ["base_column", "base_tables_of", "is_view", "resolve_views"]


def is_view(relation: str, dictionary: Dictionary) -> bool:
    try:
        return dictionary.resolve(relation).object_type == "VIEW"
    except Exception:
        return False


def _definition(relation: str, dictionary: Dictionary) -> exp.Select | None:
    """The view's captured SELECT, or None if it cannot be read.

    None is never treated as "not a view" by the caller - an unexpandable view is a
    declared boundary, because the base table it hides is exactly what is missing.
    """
    try:
        resolved = dictionary.resolve(relation)
    except Exception:
        return None
    if resolved.object_type != "VIEW" or resolved.qualified is None:
        return None
    text = dictionary.view_text.get(resolved.qualified)
    if not text:
        return None
    try:
        parsed = sqlglot.parse_one(text, dialect=DIALECT)
    except Exception:
        return None
    return parsed if isinstance(parsed, exp.Select) else None


def base_tables_of(relation: str, dictionary: Dictionary) -> list[str]:
    """Every table the view reads from, in text order."""
    select = _definition(relation, dictionary)
    if select is None:
        return []
    seen: list[str] = []
    for table in select.find_all(exp.Table):
        name = table.name.upper()
        if name and name not in seen:
            seen.append(name)
    return seen


def base_column(relation: str, column: str, dictionary: Dictionary) -> str | None:
    """Follow one column of a view down to the base column it projects from.

    Returns a qualified `TABLE.COLUMN`, or None when the view's text is unavailable or the
    projection is an expression over several columns rather than a plain reference - in
    which case there is no single base column and inventing one would be a guess.
    """
    select = _definition(relation, dictionary)
    if select is None:
        return None

    sources = {
        (alias or table.name).upper(): table.name.upper()
        for table in select.find_all(exp.Table)
        for alias in [table.alias]
    }

    for projection in select.selects:
        if (projection.alias_or_name or "").upper() != column.upper():
            continue
        for reference in projection.find_all(exp.Column):
            qualifier = (reference.table or "").upper()
            base = sources.get(qualifier) or next(iter(sources.values()), None)
            if base is None:
                continue
            return f"{base}.{reference.name.upper()}"
    return None


def resolve_views(edges: list[IREdge], dictionary: Dictionary) -> tuple[list[IREdge], list[str]]:
    """Rewrite every view reference on an edge to the base object underneath it.

    Idempotent, and safe to run over edges whose views some other pass already inlined:
    a node naming a table is left exactly as it is.
    """
    rewritten: list[IREdge] = []
    unresolved: list[str] = []

    for edge in edges:
        update: dict[str, Node] = {}
        resolved_to: dict[str, str] = {}

        for side in ("source", "target"):
            node: Node = getattr(edge, side)
            if node.kind is not NodeKind.COLUMN or "." not in node.name:
                continue
            relation, _, column = node.name.rpartition(".")
            if not is_view(relation, dictionary):
                continue
            base = base_column(relation, column, dictionary)
            if base is None:
                unresolved.append(
                    f"{node.name} is a view column and the view's text could not be "
                    "expanded - the base table it writes is not known"
                )
                continue
            update[side] = Node(kind=NodeKind.COLUMN, name=base)
            resolved_to[relation.upper()] = base.rpartition(".")[0]

        # The relation end follows the column end. Doing this the other way round - or not
        # at all - leaves an edge asserting that a column of `dim_customer` filters
        # `v_customer_editable`, which is less true than either consistent reading.
        for side in ("source", "target"):
            node = getattr(edge, side)
            if node.kind is not NodeKind.RELATION or not is_view(node.name, dictionary):
                continue
            followed = resolved_to.get(node.name.upper())
            if followed is None:
                bases = base_tables_of(node.name, dictionary)
                if len(bases) != 1:
                    unresolved.append(
                        f"{node.name} is a view over {len(bases) or 'unknown'} base tables "
                        "and this edge names no column of it - which base object the "
                        "relation refers to cannot be decided"
                    )
                    continue
                followed = bases[0]
            update[side] = Node(kind=NodeKind.RELATION, name=followed)

        if update:
            rewritten.append(edge.model_copy(update=update))
            for view in resolved_to:
                bases = base_tables_of(view, dictionary)
                if len(bases) > 1:
                    unresolved.append(
                        f"{view} joins {', '.join(bases)} - each column resolves exactly, "
                        "but the row correspondence between those tables is the view's "
                        "join predicate and is not carried by any single edge"
                    )
        else:
            rewritten.append(edge)

    return rewritten, sorted(set(unresolved))
