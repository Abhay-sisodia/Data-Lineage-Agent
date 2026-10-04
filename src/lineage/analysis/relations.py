"""Which schema an unqualified relation means - decided before SQLGlot sees the tree.

**Finding B9-01.** The SQLGlot schema used to be keyed by BARE table name, so two tables
with one name in two schemas collided and the last one won: `core.ledger` replaced
`staging.ledger`'s columns, valid statements were refused "Unknown column", and a column the
two shared bound to the wrong table without a trace. The schema is now keyed by schema and
table (`band0._schema_for`), and every unqualified relation is bound to a schema HERE, by the
rules decided for it, so SQLGlot never has to guess:

* **D-23** - with a search_path known (from `SET search_path` earlier in the script), the
  first schema on the path that has the name. A name on no schema of the path is left on the
  path's first schema, so it is dangling - PostgreSQL would error.
* **D-24** - with no path known, the dictionary's default schema if it has the name; else the
  ONE schema that has it; else, when two or more do, the reference is ambiguous and is bound
  to `AMBIGUOUS`, which no lookup finds - `band0._trace` declares it and emits nothing.

A CTE name hides a table only for an unqualified reference, so CTE references are skipped;
so are table functions and names the dictionary does not know at all (left for the existing
dangling-reference path).
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any

from sqlglot import exp

from lineage.resolution.dictionary import Dictionary, fold

AMBIGUOUS = "__ambiguous__"
"""Schema bound to an unqualified name that two or more non-default schemas have (D-24)."""


def identifier(node: Any, dialect: str) -> str:
    """A name as the catalogue stores it: a quoted one exactly, else folded (finding B9-02).

    Takes an Identifier, or a Column / Table (its own name). `qualify` quotes every
    identifier it has already normalised, so on a qualified tree this keeps the normalised
    text; folding it again turned `"LegacyFeed"` into `legacyfeed`, which no catalogue holds.
    """
    if isinstance(node, (exp.Column, exp.Table)):
        if not isinstance(node.this, exp.Identifier):
            return fold(node.name, dialect)  # a table function: its name, not its text
        node = node.this
    if isinstance(node, exp.Identifier):
        return node.name if node.quoted else fold(node.name, dialect)
    return fold(str(node), dialect)


_INDEX: dict[int, tuple[Dictionary, dict[str, tuple[str, ...]]]] = {}


def _owners_by_name(dictionary: Dictionary) -> dict[str, tuple[str, ...]]:
    cached = _INDEX.get(id(dictionary))
    if cached is not None and cached[0] is dictionary:
        return cached[1]
    found: dict[str, list[str]] = {}
    for info in dictionary.objects.values():
        found.setdefault(info.name, []).append(info.owner)
    index = {name: tuple(sorted(owners)) for name, owners in found.items()}
    _INDEX[id(dictionary)] = (dictionary, index)
    return index


def schema_for(name: str, dictionary: Dictionary, path: list[str] | None) -> str | None:
    """The schema an unqualified `name` means, `AMBIGUOUS`, or None if no schema has it."""
    owners = _owners_by_name(dictionary).get(name, ())
    if "pg_temp" in owners:
        return "pg_temp"  # a temp table shadows every schema on any path (D-31, B7-04)
    if path is not None:
        for schema in path:
            if schema in owners:
                return schema
        return path[0] if path else None
    if not owners:
        return None
    default = fold(dictionary.default_schema, dictionary.dialect)
    if default in owners:
        return default
    if len(owners) == 1:
        return owners[0]
    return AMBIGUOUS


def bind(parsed: Any, dictionary: Dictionary, path: list[str] | None = None) -> Any:
    """Give every unqualified relation in `parsed` its schema, in place.

    `path` defaults to the search_path in force for the statement (`SEARCH_PATH`, D-23).
    """
    if path is None:
        path = SEARCH_PATH.get()
    for table in list(parsed.find_all(exp.Table)):
        if table.args.get("db") or not isinstance(table.this, exp.Identifier):
            continue
        name = identifier(table.this, dictionary.dialect)
        if not name or _is_cte_reference(table, name, dictionary.dialect):
            continue
        schema = schema_for(name, dictionary, path)
        if schema is None:
            continue
        table.set("db", exp.to_identifier(schema, quoted=True))
        table.set("this", exp.to_identifier(name, quoted=True))
        table.meta["schema_bound"] = True  # the text did not name it; this module did
    return parsed


def _is_cte_reference(table: exp.Table, name: str, dialect: str) -> bool:
    node: Any = table.parent
    while node is not None:
        with_ = node.args.get("with_") or node.args.get("with")
        if with_ is not None:
            for cte in with_.expressions:
                if identifier(cte.args["alias"].this, dialect) == name:
                    return True
        node = node.parent
    return False


def expand_qualified_stars(parsed: Any, dictionary: Dictionary) -> Any:
    """`x.*` beside a USING / NATURAL join, as x's OWN columns (finding B9-04).

    `qualify` expands `x.*` through the join's merged columns, so `x.id` became
    `COALESCE(c.id, x.id)` and then, by B1-02's rule for an inner join, `c.id` - a wrong
    edge. PostgreSQL's merged column exists only for the unqualified name; a qualified star
    keeps each input's own column. Spelled out here, before `qualify`, so it never merges.
    Only where a USING or NATURAL join is present: everywhere else `qualify` is right.
    """
    for star in list(parsed.find_all(exp.Column)):
        if not isinstance(star.this, exp.Star) or not star.table:
            continue
        select = star.find_ancestor(exp.Select)
        if select is None or not any(
            join.args.get("using") or (join.args.get("method") or "").upper() == "NATURAL"
            for join in select.args.get("joins") or []
        ):
            continue
        names = _source_columns(select, star.table, dictionary)
        if not names:
            continue
        alias = star.args["table"]
        columns = [
            exp.column(exp.to_identifier(n, quoted=True), table=alias.copy()) for n in names
        ]
        if star.parent is select:
            index = select.expressions.index(star)
            select.set("expressions", [*select.expressions[:index], *columns,
                                       *select.expressions[index + 1:]])
    return parsed


def _source_columns(select: exp.Select, alias: str, dictionary: Dictionary) -> list[str]:
    sources = [select.args.get("from_") or select.args.get("from")]
    sources += [join for join in select.args.get("joins") or []]
    for holder in sources:
        if holder is None:
            continue
        source = holder.this
        if source.alias_or_name != alias:
            continue
        if isinstance(source, exp.Table):
            name = identifier(source.this, dictionary.dialect)
            written = source.args.get("db")
            db = identifier(written, dictionary.dialect) if written else None
            cte = None if db else _cte_query(select, name, dictionary.dialect)
            if cte is not None:
                return [p.alias_or_name for p in cte.selects]
            try:
                return list(dictionary.columns_of(f"{db}.{name}" if db else name))
            except Exception:
                return []
        query = source.this if isinstance(source, exp.Subquery) else None
        if isinstance(query, exp.Select) and not any(
            isinstance(p, exp.Star) or isinstance(p.this, exp.Star) for p in query.selects
        ):
            return [p.alias_or_name for p in query.selects]
    return []


def _cte_query(node: Any, name: str, dialect: str) -> exp.Select | None:
    while node is not None:
        with_ = node.args.get("with_") or node.args.get("with")
        if with_ is not None:
            for cte in with_.expressions:
                if identifier(cte.args["alias"].this, dialect) == name and isinstance(
                    cte.this, exp.Select
                ):
                    return cte.this
        node = node.parent
    return None


# PostgreSQL keywords that read like a column when written bare. SQLGlot types most of them
# (`current_user`, `session_user`, `current_schema`), but leaves `user` and `current_role`
# as columns - and `qualify` then binds `user` to any column called "user".
_SESSION_KEYWORDS = {"user": exp.CurrentUser, "current_role": exp.CurrentUser}


def session_keywords(parsed: Any) -> Any:
    """Unquoted, unqualified `user` / `current_role` are functions, not columns (B9 T4 S4).

    Only the unquoted spelling: `f."user"` IS a column, and PostgreSQL agrees.
    """
    for column in list(parsed.find_all(exp.Column)):
        ident = column.this
        if column.table or not isinstance(ident, exp.Identifier) or ident.quoted:
            continue
        kind = _SESSION_KEYWORDS.get(ident.name.lower())
        if kind is not None and column.parent is not None:
            column.replace(kind())
    return parsed


SEARCH_PATH: ContextVar[list[str] | None] = ContextVar("search_path", default=None)
"""The search_path in force for the statement being analysed (D-23); None when unknown."""

_SET_PATH = re.compile(
    r"^\s*SET\s+(?:SESSION\s+|LOCAL\s+)?search_path\s*(?:TO|=)\s*(?P<path>.+?)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_RESET_PATH = re.compile(r"^\s*RESET\s+(?:search_path|ALL)\s*;?\s*$", re.IGNORECASE)
_UNSET = object()


def search_path_change(text: str, dialect: str) -> Any:
    """What a statement does to the search_path: a new path, None (reset), or `_UNSET`.

    Read from the text: SQLGlot parses `SET search_path TO a, b` as an opaque Command and
    `SET search_path = a` as a Set, and neither form is needed beyond its list of names.
    `"$user"` and schemas outside the dictionary are kept in order - they simply hold
    nothing - and `DEFAULT` is the same as RESET.
    """
    if dialect != "postgres":
        return _UNSET
    body = " ".join(
        line for line in text.splitlines() if not line.strip().startswith("--")
    ).strip()
    if _RESET_PATH.match(body):
        return None
    match = _SET_PATH.match(body)
    if match is None:
        return _UNSET
    items = [item.strip() for item in match.group("path").split(",") if item.strip()]
    if [item.upper() for item in items] == ["DEFAULT"]:
        return None
    return [
        item[1:-1] if item.startswith('"') and item.endswith('"') else fold(item, dialect)
        for item in (i.strip("'") for i in items)
    ]


def is_path_change(change: Any) -> bool:
    return change is not _UNSET
