"""Whole-row references and composite field access, restated as columns (finding B4-01, D-20).

PostgreSQL lets a table alias stand for its whole row: `row_to_json(c)`, `md5(c::text)`,
`WHERE c IS NOT NULL`, and field access `(c).id`. SQLGlot's `qualify` recognises the alias
and turns it into `exp.TableColumn` - which is NOT an `exp.Column`, so every column walk in
the analyser stepped over it. A statement made only of such references produced no edge
and no boundary: the table looked unwritten (R1, silent).

The restatement, applied once to the qualified tree:

* `(a).f` is `a.f`, field-exact - identity when projected bare (D-20).
* `(x.c).f`, where `x` is a derived table or CTE projecting the row `c` of alias `a`, gets
  a hidden projection `a.f` inside `x`, and reads that - so the field stays exact across
  the level instead of widening to the whole row.
* Any other whole-row `a` becomes the tuple of `a`'s columns - from the dictionary for a
  table, from the projection for a derived table or CTE - so each column is a derived value
  (or, in WHERE, a filter) of whatever reads the row.
* A whole row whose columns cannot be listed becomes an ordinary column reference `a.a`,
  so the analyser declares it unresolved rather than dropping it.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from lineage.resolution.dictionary import Dictionary, UnknownObjectError, fold

_HIDDEN = "{row}.{field}"  # a projection name no SQL text can collide with unquoted


def star_rows(parsed: Any) -> Any:
    """`a.*` used as a VALUE is the whole row `a` (finding F0-02); run before `qualify`.

    `row_to_json(a.*)`, `(a.*)::text`, `ROW(a.*)`: PostgreSQL reads `a.*` in an expression
    as the row itself, exactly as `row_to_json(a)`. SQLGlot keeps it a column named `*`,
    which nothing resolves, so it was declared unresolved with no edge while the spelling
    without the star was traced (D-20). Only a select-list or RETURNING item expands into
    columns; everywhere else it becomes the `TableColumn` `qualify` makes of a whole-row
    reference - directly, not as a bare `a`, which would bind to a COLUMN named `a` where
    one exists, while `a.*` is always the row.
    """
    for column in list(parsed.find_all(exp.Column)):
        if not isinstance(column.this, exp.Star) or not column.table:
            continue
        if column.arg_key == "expressions" and isinstance(
            column.parent, (exp.Select, exp.Returning)
        ):
            continue
        column.replace(exp.TableColumn(this=column.args["table"].copy()))
    return parsed


def restate(parsed: Any, dictionary: Dictionary) -> Any:
    """Rewrite every whole-row reference and field access in a qualified tree."""
    if not any(True for _ in parsed.find_all(exp.TableColumn)):
        return parsed
    for dot in list(parsed.find_all(exp.Dot)):
        _field_access(dot)
    for row in list(parsed.find_all(exp.TableColumn)):
        if row.parent is not None:
            row.replace(_expanded(row, dictionary))
    return parsed


def _field_access(dot: exp.Dot) -> None:
    holder = dot.this
    if not isinstance(holder, exp.Paren):
        return
    inner, field = holder.this, dot.expression
    if not isinstance(field, exp.Identifier):
        return
    if isinstance(inner, exp.TableColumn):
        dot.replace(exp.column(field.copy(), table=inner.this.copy()))
        return
    if isinstance(inner, exp.Column) and inner.table:
        query = _source_query(dot, inner.table)
        if query is None:
            return
        carried = _projection(query, inner.name)
        if carried is None or not isinstance(carried.unalias(), exp.TableColumn):
            return  # not a carried row: the whole-row fallback below covers it
        row_alias = carried.unalias().this
        name = _HIDDEN.format(row=inner.name, field=field.name)
        if _projection(query, name) is None:
            query.select(
                exp.alias_(exp.column(field.copy(), table=row_alias.copy()), name, quoted=True),
                copy=False,
            )
        table = inner.args["table"].copy()
        dot.replace(exp.column(exp.to_identifier(name, quoted=True), table=table))


def _expanded(row: exp.TableColumn, dictionary: Dictionary) -> exp.Expression:
    alias = row.this
    names = _columns_of(row, alias.name, dictionary)
    if not names:
        return exp.column(alias.copy(), table=alias.copy())
    return exp.Tuple(
        expressions=[
            exp.column(exp.to_identifier(n, quoted=True), table=alias.copy()) for n in names
        ]
    )


def _columns_of(node: exp.Expression, alias: str, dictionary: Dictionary) -> list[str]:
    source = _source_named(node, alias)
    if isinstance(source, exp.Table):
        qualifier = fold(source.text("db") or "", dictionary.dialect)
        table = fold(source.name, dictionary.dialect)
        lookup = f"{qualifier}.{table}" if qualifier else table
        try:
            return [fold(c, dictionary.dialect) for c in dictionary.columns_of(lookup)]
        except UnknownObjectError:
            return []
    query = _query_of(source)
    if query is not None:
        return [p.alias_or_name for p in query.selects if p.alias_or_name]
    return []


def _source_named(node: exp.Expression, alias: str) -> exp.Expression | None:
    """The FROM / JOIN source or CTE called `alias`, innermost enclosing query first."""
    select = node.find_ancestor(exp.Select)
    while select is not None:
        for source in _sources_of(select):
            if source.alias_or_name == alias:
                if isinstance(source, exp.Table) and not source.args.get("db"):
                    cte = _cte_named(select, source.name)
                    if cte is not None:
                        return cte
                return source
        select = select.parent.find_ancestor(exp.Select) if select.parent else None
    return None


def _sources_of(select: exp.Select) -> list[exp.Expression]:
    found = []
    from_ = select.args.get("from_") or select.args.get("from")
    if from_ is not None:
        found.append(from_.this)
    for join in select.args.get("joins") or []:
        found.append(join.this)
    return found


def _cte_named(node: exp.Expression, name: str) -> exp.CTE | None:
    current: exp.Expression | None = node
    while current is not None:
        with_ = current.args.get("with_") or current.args.get("with")
        if with_ is not None:
            for cte in with_.expressions:
                if cte.alias_or_name == name:
                    return cte
        current = current.parent
    return None


def _source_query(node: exp.Expression, alias: str) -> exp.Select | None:
    return _query_of(_source_named(node, alias))


def _query_of(source: exp.Expression | None) -> exp.Select | None:
    if isinstance(source, (exp.Subquery, exp.CTE)):
        query = source.this
        return query if isinstance(query, exp.Select) else None
    return None


def _projection(query: exp.Select, name: str) -> exp.Expression | None:
    for projection in query.selects:
        if projection.alias_or_name == name:
            return projection
    return None
