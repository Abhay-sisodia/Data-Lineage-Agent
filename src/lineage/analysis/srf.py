"""Built-in set-returning functions in FROM, restated as the select-list form (batch B6).

A set-returning function in the SELECT list was already traced correctly: an element is
derived from its array, a series from its bounds (D-28). In FROM the same function went
through four different paths, and most of them lost the lineage:

* `generate_series`, `string_to_table`, `regexp_split_to_table` were "row source is a
  function" - declared with a false reason, and a literal series raised a boundary for no
  loss at all (finding B6-01);
* `ROWS FROM (f(a), g(b))` was unsupported (B6-02);
* a one-column function aliased by table name only (`AS v`, or unaliased) was "0 alias
  columns", though PostgreSQL names the column by the alias (B6-03);
* `WITH ORDINALITY` under `LATERAL` lost its column inside SQLGlot and refused the whole
  statement (B6-05).

So every built-in SRF in FROM is restated, before `qualify`, as the LATERAL subquery that
computes the same rows:

    FROM t, unnest(t.a, t.b) WITH ORDINALITY AS u(x, y, n)
    FROM t, LATERAL (SELECT unnest(t.a) AS x, unnest(t.b) AS y,
                            ROW_NUMBER() OVER (ORDER BY t.a, t.b) AS n) AS u

Several SRFs in one select list zip by position (PostgreSQL 10+), which is exactly what
`ROWS FROM` and a multi-argument unnest do. The ordinal becomes a ROW_NUMBER ordered by the
arrays: its order is theirs, so under D-4 it carries their influence and no value - D-26 -
and a filter on it is a filter from them, as `rn <= 3` is (D-9).

Left alone: user-defined functions (their body is not visible; they are declared), and an
alias list that does not map one name per function (an array of records expanded into its
fields - the element type, not the dictionary, says which is which; declared as before).
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

_BUILTIN_SRFS = {
    "UNNEST", "GENERATE_SERIES", "STRING_TO_TABLE", "REGEXP_SPLIT_TO_TABLE",
    # Finding TT-3: two more one-column built-ins, declared unresolved in FROM -
    # `regexp_matches(s, re, 'g') AS rm(m)` (a text[] per match) and
    # `generate_subscripts(arr, 1) WITH ORDINALITY AS g(idx, n)` (derived from the array, as a
    # series is from its bounds, D-28).
    "REGEXP_MATCHES", "GENERATE_SUBSCRIPTS",
}

# Batch B5 (finding B5-01): the JSON and hstore set-returning functions, by output shape.
# One column, named as PostgreSQL names it when no alias says otherwise:
_SINGLE_DOCUMENT_SRFS = {
    "JSONB_ARRAY_ELEMENTS": "value", "JSONB_ARRAY_ELEMENTS_TEXT": "value",
    "JSON_ARRAY_ELEMENTS": "value", "JSON_ARRAY_ELEMENTS_TEXT": "value",
    "JSONB_OBJECT_KEYS": "jsonb_object_keys", "JSON_OBJECT_KEYS": "json_object_keys",
    "JSONB_PATH_QUERY": "jsonb_path_query", "SKEYS": "skeys", "SVALS": "svals",
}
# Two columns, key and value:
_PAIR_DOCUMENT_SRFS = {"JSONB_EACH", "JSONB_EACH_TEXT", "JSON_EACH", "JSON_EACH_TEXT", "EACH"}
# As many columns as the column-definition list names - each read from the document:
_RECORD_DOCUMENT_SRFS = {
    "JSONB_TO_RECORDSET", "JSONB_TO_RECORD", "JSON_TO_RECORDSET", "JSON_TO_RECORD",
}
_RECORD = "*record"


def restate(parsed: Any, dialect: str) -> Any:
    """Rewrite every built-in SRF used as a FROM / JOIN source, in place."""
    if dialect != "postgres":
        return parsed
    # A select-list unnest over a subquery (finding B6-06). SQLGlot parses it as
    # `exp.Explode` (or `exp.Unnest`), and its scope builder descends into neither, so
    # the subquery got no scope and its columns were traced from the outer query -
    # "could not resolve". As a plain call it is descended
    # into like any function; UNNEST is listed as a built-in for exactly this spelling.
    for node in list(parsed.find_all(exp.Unnest, exp.Explode)):
        if isinstance(node.parent, (exp.From, exp.Join, exp.Lateral)):
            continue
        if any(True for _ in node.find_all(exp.Subquery, exp.Select)):
            args = [node.this] if isinstance(node, exp.Explode) else list(node.expressions)
            node.replace(exp.Anonymous(this="UNNEST", expressions=args))
    for node in list(parsed.find_all(exp.Unnest, exp.Table, exp.Lateral)):
        holder = node.parent
        if not isinstance(holder, (exp.From, exp.Join)) or holder.this is not node:
            continue
        replacement = _restated(node, dialect, first=isinstance(holder, exp.From))
        if replacement is not None:
            node.replace(replacement)
    return parsed


def _restated(node: Any, dialect: str, first: bool) -> Any:
    alias = node.args.get("alias")
    ordinal: str | None = None
    if isinstance(node, exp.Lateral):
        inner = node.this
        if isinstance(inner, exp.Unnest):
            calls = _calls(inner)
            offset = inner.args.get("offset")
        elif isinstance(inner, (exp.Anonymous, exp.ExplodingGenerateSeries)):
            # `CROSS JOIN LATERAL jsonb_to_recordset(...) AS x(a int)` (finding B11-01).
            # LATERAL changes nothing for a function in FROM - PostgreSQL lets it read the
            # items before it either way - so it is read as the comma-join form is.
            calls = _table_calls(exp.Table(this=inner.copy()))
            offset = node.args.get("ordinality")
        else:
            return None
    elif isinstance(node, exp.Unnest):
        calls = _calls(node)
        offset = node.args.get("offset")
    else:
        calls = _table_calls(node)
        offset = node.args.get("ordinality")
    if not calls:
        return None

    names = [_folded(c) for c in alias.columns] if alias is not None and alias.columns else []
    record = calls[0][0] == _RECORD
    if isinstance(offset, exp.Identifier):
        ordinal = _folded(offset)
    elif offset:
        fits = record or len(names) == len(calls) + 1
        ordinal = names.pop() if fits and names else "ordinality"
    table_alias = _folded(alias.this) if alias is not None and alias.name else None
    if record:
        # A record function's columns are whatever its column-definition list names.
        if not names:
            return None
        calls = [calls[0]] * len(names)

    if not names:
        if len(calls) == 1:
            names = [table_alias or calls[0][0].lower()]
        elif all(name in ("key", "value") for name, _, _ in calls):
            names = [name for name, _, _ in calls]  # jsonb_each's own column names
        else:
            return None
    if len(names) != len(calls):
        return None  # an array of records expanded into fields: declared as before

    projections = [
        f"{sql} AS {_quote(name)}" for (_, sql, _), name in zip(calls, names, strict=True)
    ]
    if ordinal is not None:
        ordering = [arg for _, _, args in calls for arg in args]
        over = f"ORDER BY {', '.join(ordering)}" if ordering else ""
        projections.append(f"ROW_NUMBER() OVER ({over}) AS {_quote(ordinal)}")
    lateral = "" if first else "LATERAL "
    select_list = ", ".join(projections)
    text = f"SELECT 1 FROM {lateral}(SELECT {select_list}) AS {_quote(table_alias or names[0])}"
    source = sqlglot.parse_one(text, read=dialect).args["from_"].this
    return source


def _calls(unnest: exp.Unnest) -> list[tuple[str, str, list[str]]]:
    """One `unnest(array)` call per array argument, with the columns each reads."""
    return [
        ("UNNEST", f"unnest({arg.sql(dialect='postgres')})", _column_args([arg]))
        for arg in unnest.expressions
    ]


def _table_calls(table: exp.Table) -> list[tuple[str, str, list[str]]]:
    rows_from = table.args.get("rows_from")
    if rows_from:
        found: list[tuple[str, str, list[str]]] = []
        for item in rows_from:
            calls = _calls(item) if isinstance(item, exp.Unnest) else _table_calls(item)
            if len(calls) != 1:
                return []
            found.extend(calls)
        return found
    if table.args.get("db"):
        return []  # schema-qualified: a user routine, not a built-in
    function = table.this
    if isinstance(function, exp.ExplodingGenerateSeries):
        args = [a for a in (function.args.get(k) for k in ("start", "end", "step")) if a]
        return [("GENERATE_SERIES", _call("generate_series", args), _column_args(args))]
    if not isinstance(function, exp.Anonymous):
        return []
    name = function.name.upper()
    args = list(function.expressions)
    call = (_call(function.name.lower(), args), _column_args(args))
    if name in _BUILTIN_SRFS:
        return [(name, *call)]
    if name in _SINGLE_DOCUMENT_SRFS:
        return [(_SINGLE_DOCUMENT_SRFS[name].upper(), *call)]
    if name in _PAIR_DOCUMENT_SRFS:
        return [("key", *call), ("value", *call)]
    if name in _RECORD_DOCUMENT_SRFS:
        return [(_RECORD, *call)]
    return []


def _call(name: str, args: list[Any]) -> str:
    return f"{name}({', '.join(a.sql(dialect='postgres') for a in args)})"


def _column_args(args: list[Any]) -> list[str]:
    """The arguments that read a column - what an ordinal is ordered by."""
    return [a.sql(dialect="postgres") for a in args if any(True for _ in a.find_all(exp.Column))]


def _folded(identifier: Any) -> str:
    """A PostgreSQL name as written: quoted kept exactly, unquoted folded to lower case."""
    if isinstance(identifier, exp.ColumnDef):  # `AS r(qty int)`: the name, not the type
        identifier = identifier.this
    return identifier.name if identifier.quoted else identifier.name.lower()


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
