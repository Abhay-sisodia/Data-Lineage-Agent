"""A PROVISIONAL dictionary inferred from SQL text. Not a capture, and it says so.

`Dictionary` normally comes from `capture()` reading a live catalogue: every table and
column in it was observed to exist. This builds one from the only other evidence available
when there is no database to hand — the SQL itself — by collecting the relations it reads
and the columns it attributes to them.

**WHAT AN INFERRED DICTIONARY CANNOT DO, stated up front because the failures are quiet:**

* **It holds only the columns the SQL mentions.** A table with forty columns of which the
  query names six will have six here, and that is not a bug in the inference - it is all
  the evidence there is.
* **It therefore cannot expand `SELECT *`.** `columns_of` would return the six, and the
  positional binding built from them would be confidently wrong. The caller is told via
  `inferred_columns_are_partial`; a `SELECT *` against an inferred dictionary should be
  treated as unresolved, not as six columns.
* **An unqualified column cannot always be attributed.** `SELECT cust_id FROM a JOIN b`
  names no table, and guessing one would invent a fact. Those columns are dropped rather
  than assigned, so an inferred dictionary UNDER-reports and never invents.
* **No views, no synonyms, no triggers, no temporariness.** None of it is in the text, and
  an empty `view_text` means lineage stops AT a view rather than resolving through it.

The honest use is exactly one: seeing real structure from real SQL when no catalogue is
available, with the provenance visible in every number that follows. `Dictionary` has no
field for "how was this made" — its `captured_at` is the nearest thing — so the marker goes
there, in a form no real capture would ever produce.
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from lineage.dialects import fold
from lineage.resolution.dictionary import Dictionary, ObjectInfo

INFERRED_MARKER = "inferred-from-sql"
"""Written into `captured_at`. A real capture puts an ISO timestamp there, so this value
is impossible to mistake for one and travels with the dictionary wherever it is saved."""


def is_inferred(dictionary: Dictionary) -> bool:
    """Was this dictionary guessed from SQL rather than read from a database?

    Worth asking before presenting any number computed against it: every figure downstream
    inherits the provenance of the schema it was resolved against.
    """
    return dictionary.captured_at == INFERRED_MARKER


def infer_dictionary(
    sql: str,
    dialect: str = "postgres",
    default_schema: str = "public",
) -> tuple[Dictionary, list[str]]:
    """Build a provisional dictionary from one SQL script.

    Returns the dictionary and a list of WARNINGS - things the inference could not settle,
    each of which is a place a result computed against this dictionary may be wrong. They
    are returned rather than logged because the caller is the one who must show them to
    whoever reads the numbers.
    """
    warnings: list[str] = []
    columns: dict[str, list[str]] = {}

    try:
        statements = [s for s in sqlglot.parse(sql, dialect=dialect) if s is not None]
    except Exception as exc:
        return _empty(default_schema, dialect), [
            f"could not parse the SQL at all: {str(exc).splitlines()[0]}"
        ]

    # Pass 1: every real relation, and what each alias refers to. CTE names are collected
    # separately and EXCLUDED - a CTE is not a stored object, and putting one in the
    # dictionary would make lineage stop at it instead of resolving through it to the base
    # tables, which is the whole job.
    cte_names: set[str] = set()
    for statement in statements:
        for cte in statement.find_all(exp.CTE):
            cte_names.add(fold(cte.alias_or_name, dialect))

    for statement in statements:
        for table in statement.find_all(exp.Table):
            name = table.name
            if not name:
                continue
            key = _qualified(table, default_schema, dialect)
            if fold(name, dialect) in cte_names:
                continue
            columns.setdefault(key, [])

    # A CTE that is a `SELECT *` passthrough over exactly ONE stored relation lends that
    # relation its columns. `pod AS (SELECT * FROM silver.purchase_order_distribution ...)`
    # followed by `pod.distribution_intended_use` tells us, with certainty, that the column
    # belongs to that table - the star is what makes it so, and one relation is what makes
    # it unambiguous.
    #
    # Without this an ETL script built on deduplicating passthrough views resolves almost
    # nothing: measured on a 1,914-line consolidated Spark job, `qualify` refused the whole
    # statement with "Unknown column: distribution_intended_use" and the run produced ZERO
    # edges. A CTE joining two relations is skipped - two candidates is a guess.
    # The star must be in the CTE's OWN projection list, not merely somewhere beneath it
    # (second half of PG-21). `find_all(exp.Star)` searches the whole subtree, so a CTE
    # whose projection RENAMES - `SELECT b.parent_item_id AS top_item_id FROM mfg.bom b
    # CROSS JOIN LATERAL (SELECT * FROM mfg.bom x ...)` - satisfied it on the nested star
    # and lent `mfg.bom` the alias `top_item_id`, a name that table does not have. The
    # projection is what decides whether a CTE passes its source's columns through.
    passthrough: dict[str, str] = {}
    for statement in statements:
        for cte in statement.find_all(exp.CTE):
            body = cte.this
            if not _projects_star(body):
                continue
            relations = {
                _qualified(table, default_schema, dialect)
                for table in body.find_all(exp.Table)
                if table.name and fold(table.name, dialect) not in cte_names
            }
            if len(relations) == 1:
                passthrough[fold(cte.alias_or_name, dialect)] = next(iter(relations))

    # Pass 2: attribute columns, by two rules and no more.
    #
    # 1. A QUALIFIED reference whose qualifier resolves to a real relation - `o.cust_id`
    #    where `o` is `silver.orders`. Unambiguous.
    #
    # 2. An UNQUALIFIED reference in a select whose FROM names exactly ONE stored relation
    #    and nothing else. `SELECT legal_entity_identifier FROM silver.legal_entity_profiles`
    #    has one candidate, so the attribution is DETERMINED rather than guessed - there is
    #    nowhere else the column could come from. This is the single-table CTE, which is
    #    most of what a warehouse model is made of, and without it those relations end up
    #    in the dictionary with no columns at all and nothing selected from them resolves.
    #
    # Anything else is counted and DROPPED. `SELECT cust_id FROM a JOIN b` has two
    # candidates and picking one would invent a fact; an inferred dictionary is allowed to
    # under-report and is never allowed to invent.
    # A qualifier is resolved in the SCOPE THAT CONTAINS IT, never against a file-wide map
    # (finding PG-21). An alias belongs to one SELECT; the same letter routinely names
    # different relations elsewhere in the same file, and a global map silently merged them:
    #
    #     WITH a AS (SELECT b.amount, b.order_no FROM sales.orders    b),
    #          c AS (SELECT b.region, b.tier     FROM sales.customers b)
    #
    # gave `sales.customers` all four columns - two it does not have - and left
    # `sales.orders` EMPTY, because the map was last-write-wins. Both halves are harmful:
    # invented columns let a name bind to the wrong table, and an emptied relation is
    # exactly PG-16's trigger for refusing an entire run.
    scope_cache: dict[int, dict[str, tuple[str, str]]] = {}

    def _scope_map(select: Any) -> dict[str, tuple[str, str]]:
        """Aliases introduced by THIS select's own FROM and JOINs, and nothing deeper.

        A table inside a subquery in the FROM belongs to that subquery's scope, not this
        one - what this scope sees is the subquery's ALIAS, which is not a table at all.
        `_owning_select` is what draws that line.
        """
        cached = scope_cache.get(id(select))
        if cached is not None:
            return cached
        found: dict[str, tuple[str, str]] = {}
        for table in select.find_all(exp.Table):
            if _owning_select(table) is not select:
                continue
            name = table.name
            if not name:
                continue
            folded = fold(name, dialect)
            # A CTE reference is recorded as a CTE, not resolved here: its columns are
            # computed, and only the passthrough rule can say what they came from.
            entry = (
                ("cte", folded)
                if folded in cte_names
                else ("table", _qualified(table, default_schema, dialect))
            )
            if table.alias:
                found[fold(table.alias, dialect)] = entry
            found[folded] = entry

        # A DERIVED TABLE that projects a star over exactly one stored relation lends that
        # relation its columns, exactly as a `SELECT *` CTE does. `CROSS JOIN LATERAL
        # (SELECT * FROM mfg_eng.bill_of_material x WHERE ...) AS b2` followed by
        # `b2.scrap_pct` determines the column's owner - the star makes it so and one
        # relation makes it unambiguous - and neither fact cares whether the alias came
        # from WITH or from a subquery.
        #
        # This was drafted before the scoping fix above and deliberately held back: alias
        # names like `b2`, `q` and `z` are reused far more often than CTE names, so with a
        # file-wide map it would have spread PG-21 rather than helped. It is safe now
        # because the alias is only ever visible in the scope that introduced it.
        for node in select.find_all(exp.Subquery, exp.Lateral):
            if _owning_select(node) is not select or not node.alias:
                continue
            if not _projects_star(_query_of(node)):
                continue
            relations = {
                _qualified(table, default_schema, dialect)
                for table in node.find_all(exp.Table)
                if table.name and fold(table.name, dialect) not in cte_names
            }
            if len(relations) == 1:
                found[fold(node.alias, dialect)] = ("table", next(iter(relations)))

        scope_cache[id(select)] = found
        return found

    def _resolve(column: Any, qualifier: str) -> str | None:
        """The relation `qualifier` names, seen from `column`'s position.

        Walks OUTWARD through enclosing selects, which is what makes a correlated
        reference - `WHERE x.item_id = o.item_id` inside a LATERAL - resolve to the outer
        relation it actually means. The innermost binding wins, as in SQL.
        """
        wanted = fold(qualifier, dialect)
        select = _owning_select(column)
        while select is not None:
            entry = _scope_map(select).get(wanted)
            if entry is not None:
                kind, value = entry
                return value if kind == "table" else passthrough.get(value)
            select = _owning_select(select)
        return None

    unattributed = 0
    for statement in statements:
        for column in statement.find_all(exp.Column):
            name = column.name
            if not name or name == "*":
                continue

            qualifier = column.table
            if qualifier:
                key = _resolve(column, qualifier)
                if key is None:
                    continue  # a derived table, or a CTE that is not a passthrough
                columns.setdefault(key, [])
            else:
                key = _sole_relation(column.parent_select, cte_names, default_schema, dialect)
                if key is None:
                    unattributed += 1
                    continue

            if name not in columns[key]:
                columns[key].append(name)

    if unattributed:
        warnings.append(
            f"{unattributed} column reference(s) name no table (`cust_id` rather than "
            f"`o.cust_id`), so they could not be attributed to a relation and are ABSENT "
            f"from this dictionary. Qualify them, or expect those columns to resolve as "
            f"dangling references."
        )

    empty = sorted(key for key, cols in columns.items() if not cols)
    if empty:
        warnings.append(
            f"{len(empty)} relation(s) are referenced but no column is attributed to them: "
            f"{', '.join(empty[:5])}{' ...' if len(empty) > 5 else ''}. Anything selected "
            f"from them will not resolve."
        )

    if any("*" == c.name for s in statements for c in s.find_all(exp.Column)) or "*" in sql:
        warnings.append(
            "the SQL appears to use `SELECT *`. An inferred dictionary holds only the "
            "columns the text mentions, so a star expansion against it would be "
            "confidently wrong - treat those projections as unresolved."
        )

    book = Dictionary(
        captured_at=INFERRED_MARKER,
        default_schema=default_schema,
        dialect=dialect,
        objects={
            key: ObjectInfo(
                owner=key.split(".", 1)[0], name=key.split(".", 1)[1], object_type="TABLE"
            )
            for key in columns
        },
        columns={key: list(cols) for key, cols in columns.items()},
        temporary=dict.fromkeys(columns, False),
    )
    return book, warnings


def _query_of(node: Any) -> Any:
    """The SELECT inside a derived table, past any LATERAL and subquery wrappers.

    SQLGlot puts the alias of `CROSS JOIN LATERAL (...) AS b2` on the `Lateral` and leaves
    the `Subquery` beneath it unnamed, so the alias and the query it names are two nodes
    apart.
    """
    inner = node.this if isinstance(node, exp.Lateral) else node
    while isinstance(inner, exp.Subquery):
        inner = inner.this
    return inner


def _projects_star(node: Any) -> bool:
    """Does this query's OWN select list contain `*` or `alias.*`?

    Both spellings count: a bare `*` parses as `exp.Star`, while `p.*` parses as a column
    whose `this` is one. Set operations are true if any arm projects a star, since any arm
    can be the one supplying the passthrough columns.
    """
    if isinstance(node, exp.SetOperation):
        return _projects_star(node.this) or _projects_star(node.expression)
    return any(
        isinstance(projection, exp.Star)
        or (isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star))
        for projection in (getattr(node, "selects", None) or [])
    )


def _owning_select(node: Any) -> Any:
    """The nearest enclosing SELECT, or None. The unit of alias scope."""
    parent = node.parent
    while parent is not None and not isinstance(parent, exp.Select):
        parent = parent.parent
    return parent


def _sole_relation(
    select: Any, cte_names: set[str], default_schema: str, dialect: str
) -> str | None:
    """The one stored relation this select reads, or None if that is not certain.

    Returns a key only when the select's own `FROM` names exactly one thing, that thing is
    a table (not a derived table or a CTE reference), it resolves to a stored relation, and
    there are no joins. Every one of those conditions is doing work:

    * more than one source and an unqualified column has more than one candidate;
    * a derived table or CTE supplies COMPUTED columns, which belong to no base relation and
      would pollute the dictionary with names the real table does not have;
    * a join is the commonest way for two candidates to appear.

    Deliberately does NOT look outwards to an enclosing select. A correlated reference in a
    subquery may resolve to an outer relation, and following that would turn a determined
    attribution back into a guess.
    """
    if select is None:
        return None
    if select.args.get("joins"):
        return None

    # SQLGlot spells this arg `from_` in the pinned version and `from` in others, and
    # `.get("from")` against the wrong one returns None rather than raising - so a single
    # hardcoded key fails SILENTLY and attributes nothing. That is exactly how the first
    # draft of this function came to do nothing at all while looking correct.
    from_clause = select.args.get("from_") or select.args.get("from")
    if from_clause is None:
        return None

    source = from_clause.this
    if not isinstance(source, exp.Table):
        return None
    if not source.name or fold(source.name, dialect) in cte_names:
        return None

    # Computed from the table in THIS select's FROM rather than looked up in a file-wide
    # alias map, which carried the same defect as the qualified path (PG-21).
    return _qualified(source, default_schema, dialect)


def _qualified(table: Any, default_schema: str, dialect: str) -> str:
    schema = table.db or default_schema
    return f"{fold(schema, dialect)}.{fold(table.name, dialect)}"


def _empty(default_schema: str, dialect: str) -> Dictionary:
    return Dictionary(
        captured_at=INFERRED_MARKER, default_schema=default_schema, dialect=dialect
    )
