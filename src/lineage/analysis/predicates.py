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
    structural = _structural_column_ids(node)
    return [column for column in node.find_all(exp.Column) if id(column) not in structural]
