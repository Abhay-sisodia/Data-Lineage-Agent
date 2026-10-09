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
        columns[key] = [
            column.name for column in schema.expressions if isinstance(column, exp.ColumnDef)
        ]
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


def from_ddl(sql: str, default_schema: str = "public") -> Dictionary:
    """A `Dictionary` from `CREATE TABLE` statements."""
    return Dictionary.model_validate(ddl_payload(sql, default_schema))
