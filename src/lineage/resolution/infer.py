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
    aliases: dict[str, str] = {}

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
            if table.alias:
                aliases[fold(table.alias, dialect)] = key
            aliases[fold(name, dialect)] = key

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
    passthrough: dict[str, str] = {}
    for statement in statements:
        for cte in statement.find_all(exp.CTE):
            body = cte.this
            if not any(isinstance(s, exp.Star) for s in body.find_all(exp.Star)):
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
    unattributed = 0
    for statement in statements:
        for column in statement.find_all(exp.Column):
            name = column.name
            if not name or name == "*":
                continue

            qualifier = column.table
            if qualifier:
                key = aliases.get(fold(qualifier, dialect))
                if key is None:
                    # A `SELECT *` passthrough CTE over one relation: the column is that
                    # relation's, determined by the star rather than guessed.
                    key = passthrough.get(fold(qualifier, dialect))
                    if key is None:
                        continue  # a CTE or derived table that is not a passthrough
                    columns.setdefault(key, [])
            else:
                key = _sole_relation(column.parent_select, aliases, dialect)
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


def _sole_relation(
    select: Any, aliases: dict[str, str], dialect: str
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

    return aliases.get(fold(source.alias or source.name, dialect))


def _qualified(table: Any, default_schema: str, dialect: str) -> str:
    schema = table.db or default_schema
    return f"{fold(schema, dialect)}.{fold(table.name, dialect)}"


def _empty(default_schema: str, dialect: str) -> Dictionary:
    return Dictionary(
        captured_at=INFERRED_MARKER, default_schema=default_schema, dialect=dialect
    )
