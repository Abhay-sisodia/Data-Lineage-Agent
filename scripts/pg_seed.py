"""Load the stress suites' DDL into the local PostgreSQL, one database per suite (MVP M2).

    docker compose up -d postgres
    .\\.venv\\Scripts\\python.exe scripts\\pg_seed.py

Each database is dropped and re-created from its `schema.sql`, so a run is repeatable:

    lineage_batch  <- stress/pg/schema.sql        (default schema core)
    lineage_final  <- stress/pg/final/schema.sql  (default schema public; gitignored, skipped
                                                   when absent)

Then `lineage schema pull` reads each one back as a dictionary - the way a user's real
warehouse is read - instead of the hand-run DDL builder. Schemas a DDL file uses without
creating, and the extensions its column types need, are created first; each database's
`search_path` is set to the suite's default schema. Connection settings: `PG_*` (see
`postgres_connect`).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lineage.resolution.postgres_connect import PgSettings, connect  # noqa: E402

SUITES = {  # database -> (DDL, default schema: the database's search_path)
    "lineage_batch": (ROOT / "stress" / "pg" / "schema.sql", "core"),
    "lineage_final": (ROOT / "stress" / "pg" / "final" / "schema.sql", "public"),
}


# Column types that live in a contrib extension rather than in core PostgreSQL.
EXTENSION_TYPES = {"hstore": "hstore", "citext": "citext", "ltree": "ltree", "cube": "cube"}


def extensions_needed(ddl: str) -> list[str]:
    """Extensions whose types the DDL uses: a real database refuses the type without them."""
    return sorted(
        extension
        for type_name, extension in EXTENSION_TYPES.items()
        if re.search(rf"\b{type_name}\b", ddl, re.I)
    )


def schemas_needed(ddl: str) -> list[str]:
    """Schemas the DDL's tables live in but that it never creates itself."""
    used = {
        statement.find(exp.Table).db  # type: ignore[union-attr]
        for statement in sqlglot.parse(ddl, dialect="postgres")
        if isinstance(statement, exp.Create) and statement.kind == "TABLE"
    }
    created = set(re.findall(r"CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", ddl, re.I))
    return sorted(used - created - {""})


def seed(database: str, schema_sql: Path, default_schema: str) -> None:
    admin = PgSettings.from_env()
    with connect(admin.conninfo, read_only=False) as connection:
        connection.autocommit = True
        connection.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        connection.execute(f'CREATE DATABASE "{database}"')
        # What an unqualified name means, as the suite's dictionary states it; `schema pull`
        # reads it back from here.
        # `public` stays on the path after it: extensions' types (hstore) live there.
        path = ", ".join(f'"{s}"' for s in dict.fromkeys([default_schema, "public"]))
        connection.execute(f'ALTER DATABASE "{database}" SET search_path = {path}')

    ddl = schema_sql.read_text(encoding="utf-8")
    target = PgSettings.from_env(database)
    with connect(target.conninfo, read_only=False) as connection:
        for extension in extensions_needed(ddl):
            connection.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}" SCHEMA public')
        for schema in schemas_needed(ddl):
            connection.execute(f'CREATE SCHEMA "{schema}"')
        connection.execute(ddl)  # no parameters, so the whole script runs as one batch
        tables = connection.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
        ).fetchone()
    count = tables[0] if tables else 0
    print(f"{database:<14} {count:>3} tables  <- {schema_sql.relative_to(ROOT)}")


def main() -> int:
    for database, (schema_sql, default_schema) in SUITES.items():
        if not schema_sql.exists():
            print(f"{database:<14} skipped: {schema_sql.relative_to(ROOT)} not found")
            continue
        seed(database, schema_sql, default_schema)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
