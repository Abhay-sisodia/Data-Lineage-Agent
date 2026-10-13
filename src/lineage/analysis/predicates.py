"""Which columns in a predicate are lineage, and which are plumbing.

One home, imported by `band0` and `defuse`. Finding S2-08 is on the register because
`_transform_of` was duplicated across those two modules and drifted, so a rule that both
of them need does not get written twice.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp


def _structural_column_ids(node: Any) -> set[int]:
    """Identities of the ON-clause columns inside `node` that are join plumbing.

    Until D-48 every column of every ON was plumbing. Now only the key equality is (D-5):
    another condition in an INNER join's ON removes rows exactly as it would in WHERE, so
    it is a filter (D-48, and D-49 for a non-equality). An OUTER join's ON cannot remove a
    row of the query it sits in; inside a predicate it feeds no column, so it adds nothing.
    """
    found: set[int] = set()
    for join in node.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        if is_outer_join(join):
            found.update(id(column) for column in on.find_all(exp.Column))
            continue
        for conjunct in _conjuncts(on):
            if _is_key_equality(conjunct):
                found.update(id(column) for column in conjunct.find_all(exp.Column))
    return found


_LOOKUP_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)


def lookup_key_column_ids(condition: Any, local: set[str]) -> set[int]:
    """Columns of the comparisons that MATCH a lookup's rows to a row outside it (D-58).

    `local` is the lookup's own relations. A top-level conjunct comparing one of their
    columns with a column from elsewhere (`=`, `<>` or a range) - `dp.sku = l.sku` in a
    LATERAL's WHERE, `ship_date BETWEEN fx.valid_from AND fx.valid_to` in an outer join's
    ON - picks which lookup row arrives: it is a key, as an ON equality is (D-5, D-57). A
    comparison with a constant (`g.rollup = 0`) or between two local relations stays what
    it was.
    """
    if isinstance(condition, (exp.Where, exp.Having)):
        condition = condition.this
    found: set[int] = set()
    if condition is None:
        return found
    for conjunct in _conjuncts(condition):
        if not isinstance(conjunct, _LOOKUP_COMPARISONS):
            continue
        columns = list(conjunct.find_all(exp.Column))
        if not columns or any(not column.table for column in columns):
            continue
        tables = {column.table for column in columns}
        if tables & local and tables - local:
            found.update(id(column) for column in columns)
    return found


def is_outer_join(join: Any) -> bool:
    """LEFT, RIGHT or FULL: a join with a null-supplying side."""
    return (join.side or "").upper() in ("LEFT", "RIGHT", "FULL")


def _is_key_equality(conjunct: Any) -> bool:
    """`a.k = b.k`: an equality whose two sides read two different relations (D-5, D-11)."""
    if not isinstance(conjunct, exp.EQ):
        return False
    left, right = _exact_relation(conjunct.this), _exact_relation(conjunct.expression)
    return bool(left and right and left != right)


def _exact_relation(operand: Any) -> str | None:
    """`_side_relation` without case folding: after `qualify`, `o` and `"O"` are two aliases
    (B9 T4's trap, `JOIN staging.payments AS o ON o.order_id = "O".id`)."""
    qualifiers = {column.table for column in operand.find_all(exp.Column)}
    if len(qualifiers) != 1 or "" in qualifiers:
        return None
    return qualifiers.pop()


def on_condition_columns(join: Any) -> list[Any]:
    """The columns of a join's ON that are NOT its key equality (D-48, D-49).

    `JOIN d ON d.id = c.id AND d.is_current` gives `d.is_current`; a range join's bounds
    give both operands of each inequality. What they mean follows the join's side: on an
    inner join a filter, on an outer join a value of the null-supplying side's columns.
    """
    on = join.args.get("on")
    if on is None:
        return []
    found: list[Any] = []
    for conjunct in _conjuncts(on):
        if not _is_key_equality(conjunct):
            found.extend(predicate_columns(conjunct))
    return found


# EQUALITY ONLY, which is what D-11 decided: "a column-equals-column condition in WHERE".
# The first implementation took every comparison operator and so read a self-join's
# `child.signup_date > parent.signup_date` as plumbing - `stress_01`'s key labels it a real
# predicate. D-49 (2026-10-06) decided it: a NON-equality join condition is a filter with
# both operands as sources, in ON or WHERE alike - so this tuple stays as it is.
_COMPARISONS = (exp.EQ,)


def _inner_joined_aliases(select: Any) -> set[str]:
    """Aliases of the relations in this SELECT's own FROM that no outer join can null.

    A LEFT JOIN's table, everything to the LEFT of a RIGHT JOIN, and both sides of a FULL
    JOIN are null-supplying: a WHERE over them removes rows the join had kept, so it is a
    real filter and never a restated join (D-11's exception).
    """
    source = select.args.get("from_") or select.args.get("from")
    if source is None:
        return set()
    order = [source.this.alias_or_name]
    nullable: set[str] = set()
    for join in select.args.get("joins") or []:
        name = join.this.alias_or_name
        side = (join.side or "").upper()
        if side in ("RIGHT", "FULL"):
            nullable.update(order)
        if side in ("LEFT", "FULL"):
            nullable.add(name)
        order.append(name)
    return {name.upper() for name in order if name and name not in nullable}


def _side_relation(operand: Any) -> str | None:
    """The one relation every column of this operand is qualified by, or None."""
    qualifiers = {column.table.upper() for column in operand.find_all(exp.Column)}
    if len(qualifiers) != 1 or "" in qualifiers:
        return None
    return qualifiers.pop()


def _conjuncts(condition: Any) -> list[Any]:
    if isinstance(condition, exp.And):
        return [*_conjuncts(condition.this), *_conjuncts(condition.expression)]
    if isinstance(condition, exp.Paren):
        return _conjuncts(condition.this)
    return [condition]


def _implicit_join_column_ids(node: Any) -> set[int]:
    """Columns of join conditions written in a WHERE instead of an ON (decision D-11).

    `FROM a, b WHERE a.k = b.k` is the same join as `FROM a JOIN b ON a.k = b.k`, and D-5
    makes the ON spelling structural. Before D-11 the comma spelling reported both keys as
    filters: one join, two answers, decided by syntax - T5 pair 9 of batch B1.

    An EQUALITY is a join condition when it is a top-level conjunct of the WHERE and its
    two sides read two DIFFERENT relations of that same SELECT's FROM, both inner-joined.
    Deliberately narrow, because the cost of being wrong is asymmetric - an excluded filter
    is a silent loss:

    * under OR or NOT it is not a join condition, it is logic;
    * an unqualified operand is not claimed - which relation it reads is not known here;
    * a side that names a relation outside this FROM is a correlation, which stays (D-5);
    * a side on the null-supplying end of an outer join stays a filter.
    """
    found: set[int] = set()
    wheres = [node] if isinstance(node, exp.Where) else []
    wheres += list(node.find_all(exp.Where)) if hasattr(node, "find_all") else []
    for where in wheres:
        select = where.parent
        if not isinstance(select, exp.Select):
            continue
        inner = _inner_joined_aliases(select)
        if len(inner) < 2:
            continue
        for conjunct in _conjuncts(where.this):
            if not isinstance(conjunct, _COMPARISONS):
                continue
            left, right = _side_relation(conjunct.this), _side_relation(conjunct.expression)
            if left and right and left != right and left in inner and right in inner:
                found.update(id(column) for column in conjunct.find_all(exp.Column))
    return found


def predicate_columns(node: Any) -> list[Any]:
    """Columns in a predicate subtree that decide which rows survive.

    **A join condition is excluded, wherever it is written** (stress finding S2-14,
    decision D-5). A join connects two tables so a predicate can be evaluated; it does not
    decide which rows are written. The key has said so since stress 1 - *"join conditions
    are structural, not filter lineage"* - and until D-5 that only held for a join in a
    `FROM`:

        FROM stg_orders o JOIN stg_order_lines l ON l.order_id = o.order_id
            -> no edges, correct

        WHERE EXISTS (SELECT 1 FROM stg_order_lines l
                        JOIN stg_orders o2 ON o2.order_id = l.order_id
                       WHERE o2.cust_id = d.cust_id)
            -> TWO filter edges from the ON clause, before D-5

    The same clause, two answers, decided by where it happened to sit. Nothing chose that:
    it fell out of walking the `WHERE` subtree with `find_all(exp.Column)`, which sweeps up
    everything nested inside it including a join's `ON`.

    **The correlation is NOT excluded and that is the distinction that matters.**
    `o2.cust_id = d.cust_id` above is what actually decides which `dim_customer` rows are
    updated, and both of its operands stay - the s7 convention, applied corpus-wide in
    `0b9a9e8`. Excluding the join and keeping the correlation is the difference between
    reporting the dependency and reporting the plumbing that made it reachable.
    """
    if node is None:
        return []
    structural = _structural_column_ids(node) | _implicit_join_column_ids(node)
    return [
        column
        for column in node.find_all(exp.Column)
        if id(column) not in structural and not in_grouping_function(column)
    ]


def in_grouping_function(column: Any) -> bool:
    """Is this column an argument of `grouping(...)`? It carries no lineage there (D-50):
    the bitmask reports the GROUPING SETS shape, and no part of the column is in it."""
    return column.find_ancestor(exp.Grouping) is not None
