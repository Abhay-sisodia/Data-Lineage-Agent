"""PostgreSQL system columns, read as values with no source (finding TT-1).

Every PostgreSQL table has `ctid`, `xmin`, `xmax`, `cmin`, `cmax` and `tableoid`, and no
catalogue lists them, so the dictionary never will. The shipment torture query used `h.ctid`
as a DISTINCT ON tie-breaker, and SQLGlot's `qualify` refused the whole 2,963-line statement
with "Unknown column: ctid"; an unqualified `xmin` was declared `unresolved_identifier`.

What a system column carries is the row's physical identity or its transaction - nothing
any user column put there. So a reference to one is restated as `NULL` before qualifying:
it gives no edge and no boundary, exactly like the literal it now is, and the expression
around it is analysed as usual.

**Only where it binds to a base table.** A CTE, derived table or LATERAL may project a
column it names `xmin`; that is an ordinary column and is left alone, as is a reference to
an output alias of the same name. A user table cannot have a column of these names -
PostgreSQL rejects it - so on a base table the name is always the system column.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

SYSTEM_COLUMNS = frozenset({"ctid", "xmin", "xmax", "cmin", "cmax", "tableoid"})


def restate(parsed: Any) -> Any:
    """Replace each system-column reference that binds to a base table with `NULL`."""
    ctes = {(cte.alias_or_name or "").lower() for cte in parsed.find_all(exp.CTE)}
    for column in list(parsed.find_all(exp.Column)):
        if column.name.lower() not in SYSTEM_COLUMNS or not isinstance(column.this, exp.Identifier):
            continue
        if _binds_to_base_table(column, ctes):
            column.replace(exp.Null())
    return parsed


def _binds_to_base_table(column: exp.Column, ctes: set[str]) -> bool:
    """Does this reference name a column of a base table, in its own query or an outer one?"""
    qualifier = column.table.lower()
    select = column.find_ancestor(exp.Select)
    while select is not None:
        sources = _sources(select)
        if qualifier:
            if qualifier in sources:
                return _is_base_table(sources[qualifier], ctes)
        else:
            aliases = {(e.alias or "").lower() for e in select.expressions if e.alias}
            if column.name.lower() in aliases:
                return False
            # Unqualified: it can only be the system column if every relation here is a
            # base table; with a CTE or derived table in scope, it may be their column.
            return bool(sources) and all(_is_base_table(s, ctes) for s in sources.values())
        select = select.find_ancestor(exp.Select)
    return False


def _sources(select: exp.Select) -> dict[str, exp.Expression]:
    """The relations a SELECT's FROM and JOINs introduce, by the name they are referred to."""
    found: dict[str, exp.Expression] = {}
    from_ = select.args.get("from_") or select.args.get("from")
    nodes = [from_.this] if from_ is not None else []
    nodes += [join.this for join in select.args.get("joins") or []]
    for node in nodes:
        name = (node.alias_or_name or "").lower()
        if name:
            found[name] = node
    return found


def _is_base_table(node: exp.Expression, ctes: set[str]) -> bool:
    """A named table that is not one of the statement's CTEs."""
    return (
        isinstance(node, exp.Table)
        and isinstance(node.this, exp.Identifier)
        and not (not node.args.get("db") and node.name.lower() in ctes)
    )
