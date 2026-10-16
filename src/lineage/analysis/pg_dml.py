"""PostgreSQL writes, restated as the reads they perform (batch B3).

Band 0 analyses an `INSERT ... SELECT` thoroughly: scopes, CTEs, D-5/D-11 joins, the D-9 /
D-12 / D-14 rules, phases. Every other PostgreSQL write is the same computation wearing a
different statement, and the analyser had a thinner path for each - def-use's UPDATE, a
predicate walk for DELETE, nothing at all for ON CONFLICT DO UPDATE, RETURNING or a
data-modifying CTE. Batch B3 measured what that cost: 43% of the facts on first run.

So each write is restated as the read it performs, and that read goes through the one
path that already knows the rules - the move `_analyse_create_table_as` and
`_analyse_standalone_query` already make:

    UPDATE t AS a SET c = e FROM s WHERE w
        -> INSERT INTO t (c) SELECT e FROM t AS a, s WHERE w
    DELETE FROM t AS a USING s WHERE w
        -> INSERT INTO t (<first column>) SELECT NULL FROM t AS a, s WHERE w
    INSERT INTO t AS a (c) <q> ON CONFLICT ... DO UPDATE SET d = f WHERE g
        -> the INSERT itself, and
           INSERT INTO t (d) SELECT f FROM t AS a CROSS JOIN (<q>) AS __excluded(c) WHERE g
    ... RETURNING r                               (D-16)
        -> SELECT r FROM t AS a, <the statement's own sources> WHERE w    -> <result>
    WITH x AS (<write> RETURNING r) <statement>   (D-17)
        -> the write analysed as itself, and x replaced by the read of its RETURNING

Why the restatements are faithful, rule by rule:

* **An UPDATE's FROM list is joined to the target** - PostgreSQL's own definition - so
  `t AS a, s` is the right FROM, and D-11 makes their equality the structural join it is
  (findings B3-04, B3-07).
* **Every SET reads the OLD row**, and a SELECT over `t` reads exactly that.
* **A row-valued SET `(a, b) = (x, y)` is two SETs**, and `(a, b) = (SELECT x, y ...)` is
  two scalar subqueries over the same rows - each then falls under D-12 (D-18), and no
  edge can land on a column with an empty name (B3-06).
* **A DELETE writes no value**, so NULL is projected and only its predicate survives -
  convention (c).
* **EXCLUDED is the row the INSERT proposed**, so it is the INSERT's own query, aliased by
  the target's column names (D-15). The conflict target is not a predicate of the
  restated query at all, which is what D-15 says it is.
* **A returned column is the written table's column** (D-16), and the statement's own
  predicate decides which rows are returned.
* **A data-modifying CTE runs whether or not it is read** (PostgreSQL executes it), so its
  write is always analysed; a read of it is the read of its RETURNING, carrying its
  predicate, which is D-17.

PostgreSQL only. A PostgreSQL routine body is refused, so a script has no PL/pgSQL variable
for a SELECT-shaped restatement to mistake for a column; the Oracle paths, where def-use
has to tell the two apart, are untouched.
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from lineage.resolution.dictionary import Dictionary, UnknownObjectError

_WRITES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)


def expand(parsed: Any, dictionary: Dictionary) -> list[Any]:
    """The statements to analyse in place of `parsed` - possibly none, possibly several."""
    if (dictionary.dialect or "").lower() != "postgres":
        return [parsed]
    statement, writes = _split_writable_ctes(parsed, dictionary)
    parts: list[Any] = []
    for write in writes:
        parts.extend(_expand_one(write, dictionary))
    parts.extend(_expand_one(statement, dictionary))
    return parts


def _sql(node: Any, dictionary: Dictionary) -> str:
    return node.sql(dialect=dictionary.dialect)


def _is_default(item: Any) -> bool:
    """The keyword DEFAULT in a VALUES row - a column, not a subquery, named DEFAULT."""
    return (item.name or "").upper() == "DEFAULT" and not isinstance(item, exp.Subquery)


def _parse(text: str, dictionary: Dictionary) -> Any:
    return sqlglot.parse_one(text, dialect=dictionary.dialect)


def _with(statement: Any) -> Any:
    return statement.args.get("with_") or statement.args.get("with")


def _with_prefix(statement: Any, dictionary: Dictionary) -> str:
    clause = _with(statement)
    return f"{_sql(clause, dictionary)} " if clause is not None else ""


def _unaliased(table: Any) -> Any:
    bare = table.copy()
    bare.set("alias", None)
    return bare


def _alias_of(table: Any) -> str:
    return table.alias or table.name


def _target_columns(table: Any, dictionary: Dictionary) -> list[str]:
    """The target's column order, from the dictionary."""
    name = f"{table.db}.{table.name}" if table.db else table.name
    try:
        return list(dictionary.columns_of(name))
    except UnknownObjectError:
        return []


def _returning_list(statement: Any, dictionary: Dictionary) -> str | None:
    returning = statement.args.get("returning")
    if returning is None:
        return None
    return ", ".join(_sql(item, dictionary) for item in returning.expressions)


def _query_width(query: Any) -> int | None:
    """How many columns the query supplies, or None while a `*` hides the count."""
    if isinstance(query, exp.Values):
        rows = list(query.expressions)
        return len(rows[0].expressions) if rows else 0
    while isinstance(query, exp.SetOperation):
        query = query.this
    selects = getattr(query, "selects", None) or []
    # A star is one projection in the text and N columns after `qualify` expands it
    # (finding B9-03): counted as one, `INSERT INTO t SELECT * FROM t` bound one column.
    if any(isinstance(p, exp.Star) or isinstance(p.this, exp.Star) for p in selects):
        return None
    return len(selects)


# --- reads -------------------------------------------------------------------------------


def _read_of(write: Any, projection: str, dictionary: Dictionary) -> str:
    """SELECT <projection> over the rows a write touches, with its own predicate."""
    if isinstance(write, exp.Update):
        source = write.args.get("from_") or write.args.get("from")
        extra = f", {_sql(source, dictionary)[len('FROM '):]}" if source is not None else ""
        where = write.args.get("where")
        return (
            f"SELECT {projection} FROM {_sql(write.this, dictionary)}{extra}"
            + (f" {_sql(where, dictionary)}" if where is not None else "")
        )
    if isinstance(write, exp.Delete):
        using = write.args.get("using") or []
        extra = "".join(f", {_sql(item, dictionary)}" for item in using)
        where = write.args.get("where")
        return (
            f"SELECT {projection} FROM {_sql(write.this, dictionary)}{extra}"
            + (f" {_sql(where, dictionary)}" if where is not None else "")
        )
    if isinstance(write, exp.Insert):
        table = write.this.this if isinstance(write.this, exp.Schema) else write.this
        bare = _sql(_unaliased(table), dictionary)
        body = write.expression
        if body is None:
            return f"SELECT {projection} FROM {bare} AS {_alias_of(table)}"
        width = _query_width(body)
        # A `*` body keeps its own column names: the count is not known before qualify.
        names = "" if width is None else "(" + ", ".join(
            f"__c{i}" for i in range(1, width + 1)
        ) + ")"
        return (
            f"SELECT {projection} FROM {bare} AS {_alias_of(table)} "
            f"CROSS JOIN ({_sql(body, dictionary)}) AS __rows{names}"
        )
    if isinstance(write, exp.Merge):
        return (
            f"SELECT {projection} FROM {_sql(write.this, dictionary)} "
            f"CROSS JOIN {_sql(write.args['using'], dictionary)}"
        )
    raise ValueError(f"not a write: {type(write).__name__}")


def _split_writable_ctes(statement: Any, dictionary: Dictionary) -> tuple[Any, list[Any]]:
    """Replace each data-modifying CTE by the read of its RETURNING; return the writes.

    Every write is returned for analysis as itself, carrying the converted WITH so it can
    read its siblings - including one that nothing references, because PostgreSQL executes
    it regardless (finding B3-03: such a DELETE used to vanish without a trace).
    """
    clause = _with(statement)
    if clause is None or not any(isinstance(cte.this, _WRITES) for cte in clause.expressions):
        return statement, []
    statement = statement.copy()
    clause = _with(statement)
    writes: list[Any] = []
    for cte in clause.expressions:
        if isinstance(cte.this, _WRITES):
            write = cte.this.copy()
            projection = _returning_list(write, dictionary) or "NULL AS __none"
            cte.set("this", _parse(_read_of(write, projection, dictionary), dictionary))
            writes.append(write)
    for write in writes:
        write.set("with_", clause.copy())
        # Its RETURNING feeds the CTE - already restated above as the read - and is NOT
        # this statement's output. Left on, it was analysed a second time into <result>.
        write.set("returning", None)
    return statement, writes


# --- one write -------------------------------------------------------------------------


def _expand_one(statement: Any, dictionary: Dictionary) -> list[Any]:
    if isinstance(statement, exp.Update):
        parts = [_update_as_insert(statement, dictionary)]
    elif isinstance(statement, exp.Delete):
        parts = _delete_as_insert(statement, dictionary)
    elif isinstance(statement, exp.Insert):
        parts = _insert_parts(statement, dictionary)
    elif isinstance(statement, exp.Merge):
        merge = statement.copy()
        merge.set("returning", None)
        parts = [merge]
    else:
        return [statement]

    projection = _returning_list(statement, dictionary)
    if projection is not None:
        parts.append(
            _parse(
                _with_prefix(statement, dictionary) + _read_of(statement, projection, dictionary),
                dictionary,
            )
        )
    return parts


def _assignments(update_like: Any, dictionary: Dictionary) -> tuple[list[str], list[str]]:
    """(columns, value expressions) of a SET list, row-valued SETs split per column."""
    columns: list[str] = []
    values: list[str] = []
    for setter in update_like:
        if not isinstance(setter, exp.EQ):
            continue
        left, right = setter.this, setter.expression
        names = left.expressions if isinstance(left, exp.Tuple) else None
        if names is None and isinstance(left, exp.Paren):
            names = [left.this]
        if names is None:
            columns.append(_sql(exp.to_identifier(left.name), dictionary))
            values.append(_sql(right, dictionary))
            continue
        if isinstance(right, exp.Tuple):
            items = [_sql(item, dictionary) for item in right.expressions]
        elif isinstance(right, exp.Subquery) and isinstance(right.this, exp.Select):
            items = []
            for index in range(len(names)):
                one = right.copy()
                one.this.set("expressions", [right.this.selects[index].copy()])
                items.append(_sql(one, dictionary))
        else:
            items = [_sql(right, dictionary)] * len(names)
        for name, item in zip(names, items, strict=False):
            columns.append(_sql(exp.to_identifier(name.name), dictionary))
            values.append(item)
    return columns, values


def _update_as_insert(update: Any, dictionary: Dictionary) -> Any:
    columns, values = _assignments(update.expressions, dictionary)
    bare = _sql(_unaliased(update.this), dictionary)
    return _parse(
        _with_prefix(update, dictionary)
        + f"INSERT INTO {bare} ({', '.join(columns)}) "
        + _read_of(update, ", ".join(values), dictionary),
        dictionary,
    )


def _delete_as_insert(delete: Any, dictionary: Dictionary) -> list[Any]:
    if delete.args.get("where") is None and not delete.args.get("using"):
        return []  # an unconditional DELETE names no column: a complete answer
    columns = _target_columns(delete.this, dictionary)
    if not columns:
        return [delete]  # unknown target: let the original path declare it
    bare = _sql(_unaliased(delete.this), dictionary)
    first = _sql(exp.to_identifier(columns[0]), dictionary)
    return [
        _parse(
            _with_prefix(delete, dictionary)
            + f"INSERT INTO {bare} ({first}) "
            + _read_of(delete, "NULL", dictionary),
            dictionary,
        )
    ]


def _insert_parts(insert: Any, dictionary: Dictionary) -> list[Any]:
    """The INSERT itself (normalised), and its ON CONFLICT DO UPDATE as an update."""
    if insert.expression is None:
        return []  # DEFAULT VALUES: a row with no source, not a refusal (finding B3-02)
    if isinstance(insert.expression, exp.Values):
        # VALUES rows as SELECTs, so a scalar subquery in a row is a SELECT-list scalar
        # subquery and D-12 / D-18 apply (finding B3-12). DEFAULT supplies nothing: NULL.
        insert = insert.copy()
        rows = []
        for row in insert.expression.expressions:
            items = [
                "NULL" if _is_default(item) else _sql(item, dictionary)
                for item in row.expressions
            ]
            rows.append("SELECT " + ", ".join(items))
        insert.set("expression", _parse(" UNION ALL ".join(rows), dictionary))

    table = insert.this.this if isinstance(insert.this, exp.Schema) else insert.this
    explicit: list[Any] | None = None
    if isinstance(insert.this, exp.Schema):
        explicit = list(insert.this.expressions)
    elif table.args.get("alias") is not None and table.args["alias"].columns:
        # `INSERT INTO t AS a (c1, c2)`: SQLGlot files the column list on the ALIAS, and
        # reading only a Schema lost it - the statement was refused (finding B3-01).
        explicit = list(table.args["alias"].columns)
    columns = (
        [identifier.name for identifier in explicit]
        if explicit is not None
        else _target_columns(table, dictionary)
    )

    plain = insert.copy()
    plain.set("conflict", None)
    plain.set("returning", None)
    target = _unaliased(table)
    if explicit is not None:
        target = exp.Schema(this=target, expressions=[e.copy() for e in explicit])
    elif columns and _query_width(insert.expression) is not None:
        # An omitted list binds to the FIRST columns in table order, as many as the query
        # supplies - not to all of them. Read as all, a short row was refused for arity.
        # With a `*` the width is not known yet; the full list meets the expanded star.
        width = _query_width(insert.expression)
        target = exp.Schema(
            this=target, expressions=[exp.to_identifier(name) for name in columns[:width]]
        )
    plain.set("this", target)
    parts = [plain]

    conflict = insert.args.get("conflict")
    sets = conflict.args.get("expressions") if conflict is not None else None
    if sets:
        width = _query_width(insert.expression)
        excluded = ", ".join(
            _sql(exp.to_identifier(name), dictionary) for name in columns[:width]
        )

        def rename(node: Any) -> Any:
            if isinstance(node, exp.Column) and node.table.upper() == "EXCLUDED":
                return exp.column(node.name, table="__excluded")
            return node

        targets, values = _assignments(
            [setter.copy().transform(rename) for setter in sets], dictionary
        )
        where = conflict.args.get("where")
        where_sql = f" {_sql(where.copy().transform(rename), dictionary)}" if where else ""
        parts.append(
            _parse(
                _with_prefix(insert, dictionary)
                + f"INSERT INTO {_sql(_unaliased(table), dictionary)} ({', '.join(targets)}) "
                + f"SELECT {', '.join(values)} "
                + f"FROM {_sql(_unaliased(table), dictionary)} AS {_alias_of(table)} "
                + f"CROSS JOIN ({_sql(insert.expression, dictionary)}) AS __excluded({excluded})"
                + where_sql,
                dictionary,
            )
        )
    return parts
