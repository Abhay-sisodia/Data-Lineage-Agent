"""Which columns in a predicate are lineage, and which are plumbing.

One home, imported by `band0` and `defuse`. Finding S2-08 is on the register because
`_transform_of` was duplicated across those two modules and drifted, so a rule that both
of them need does not get written twice.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp


def _structural_column_ids(node: Any) -> set[int]:
    """Identities of columns that appear only in a join's `ON` clause."""
    found: set[int] = set()
    for join in node.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        found.update(id(column) for column in on.find_all(exp.Column))
    return found


# EQUALITY ONLY, which is what D-11 decided: "a column-equals-column condition in WHERE".
# The first implementation took every comparison operator and so read a self-join's
# `child.signup_date > parent.signup_date` as plumbing - `stress_01`'s key labels it a real
# predicate, and nothing decided otherwise. Whether a NON-equality join condition is
# structural is an open question; widening this tuple is that decision, not a tidy-up.
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
    return [column for column in node.find_all(exp.Column) if id(column) not in structural]
