"""A dictionary built from DDL: the `CREATE TABLE` scripts a user uploads (MVP plan M2).

The second of three ways to get a dictionary without writing one by hand, after pulling a
live catalogue (`postgres_connect.pull`) and before inferring from the SQL itself (`infer`).
Every table and column here was DECLARED, not observed - the DDL is trusted as the schema.

Only `CREATE TABLE` is read. A view, function or trigger in the DDL is not in the result:
use a live pull when they matter. Every table must be schema-qualified, and every table
must have columns - a column-less relation refuses whole runs - so both are errors here
rather than a quieter dictionary.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from lineage.resolution.dictionary import Dictionary

CAPTURED_AT = "2026-10-01T00:00:00Z"
"""Fixed, not "now": rebuilding an unchanged schema must give an identical file."""


def ddl_payload(sql: str, default_schema: str, captured_at: str = CAPTURED_AT) -> dict[str, object]:
    """The dictionary as plain JSON-ready data, in the shape the committed files use."""
    objects: dict[str, dict[str, str]] = {}
    columns: dict[str, list[str]] = {}

    for statement in sqlglot.parse(sql, dialect="postgres"):
        if not isinstance(statement, exp.Create) or statement.kind != "TABLE":
            continue
        schema = statement.this
        table = schema.this if isinstance(schema, exp.Schema) else schema
        owner, name = table.db, table.name
        if not owner:
            raise ValueError(f"unqualified table {name!r} in the DDL - qualify it")
        key = f"{owner}.{name}"
        objects[key] = {"owner": owner, "name": name, "object_type": "TABLE"}
        columns[key] = _columns(statement, schema, columns)
        if not columns[key]:
            raise ValueError(f"{key} has no columns - a column-less relation refuses whole runs")

    return {
        "captured_at": captured_at,
        "default_schema": default_schema,
        "dialect": "postgres",
        "objects": objects,
        "columns": columns,
        "temporary": {key: False for key in objects},
    }


def _columns(
    statement: exp.Create, schema: exp.Expression, known: dict[str, list[str]]
) -> list[str]:
    """A table's columns in PostgreSQL's order: inherited ones first, then its own.

    `PARTITION OF p` has exactly p's columns, `INHERITS (p)` puts p's before its own, and
    `(LIKE p)` puts p's where the LIKE stands. The parent must come earlier in the DDL, as
    PostgreSQL itself requires.
    """

    def parent(table: exp.Table) -> list[str]:
        found = known.get(f"{table.db}.{table.name}")
        if found is None:
            raise ValueError(f"{table.db}.{table.name} is used before its CREATE TABLE")
        return list(found)

    properties = statement.args.get("properties")
    props = properties.expressions if properties else []
    for prop in props:
        if isinstance(prop, exp.PartitionedOfProperty):
            return parent(prop.this)
    names: list[str] = []
    for prop in props:
        if isinstance(prop, exp.InheritsProperty):
            for table in prop.expressions:
                names.extend(parent(table))
    for item in schema.expressions if isinstance(schema, exp.Schema) else ():
        if isinstance(item, exp.ColumnDef):
            names.append(item.name)
        elif isinstance(item, exp.LikeProperty):
            names.extend(parent(item.this))
    return list(dict.fromkeys(names))


def from_ddl(sql: str, default_schema: str = "public") -> Dictionary:
    """A `Dictionary` from `CREATE TABLE` statements."""
    return Dictionary.model_validate(ddl_payload(sql, default_schema))
