"""Pull a dictionary from a live PostgreSQL (MVP plan M2): no hand-written dictionary.

The catalogue queries live in `postgres_catalogue`; this module owns the CONNECTION, which
`resolution.catalogue` deliberately leaves to the caller. Settings come from the
environment with local development defaults (the `postgres` service in
docker-compose.yml), like `graph.neo4j_conn`.

**Read-only by construction.** Every session opens with `default_transaction_read_only`,
so PostgreSQL itself rejects a write in it - the pull only reads `pg_catalog` and
`information_schema`, and a user's database must never be changed by pointing us at it.
Use a read-only role in production as well; this is the second lock, not the only one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lineage.resolution.catalogue import capture_schemas
from lineage.resolution.dictionary import Dictionary

_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")


@dataclass(frozen=True)
class PgSettings:
    """Where the PostgreSQL is and who to connect as."""

    host: str = "localhost"
    port: int = 5432
    user: str = "lineage"
    password: str = "lineage_pg"
    database: str = "lineage"

    @classmethod
    def from_env(cls, database: str | None = None) -> PgSettings:
        return cls(
            host=os.environ.get("PG_HOST", "localhost"),
            port=int(os.environ.get("PG_PORT", "5432")),
            user=os.environ.get("PG_USER", "lineage"),
            password=os.environ.get("PG_PASSWORD", "lineage_pg"),
            database=database or os.environ.get("PG_DATABASE", "lineage"),
        )

    @property
    def conninfo(self) -> str:
        return (
            f"host={self.host} port={self.port} user={self.user} "
            f"password={self.password} dbname={self.database}"
        )


def connect(conninfo: str, read_only: bool = True) -> Any:
    """A psycopg connection. `conninfo` is a DSN (`postgresql://...`) or key=value string."""
    import psycopg

    options = "-c default_transaction_read_only=on" if read_only else None
    return psycopg.connect(conninfo, options=options, connect_timeout=10)


def user_schemas(connection: Any) -> list[str]:
    """Every schema that holds at least one relation, system schemas excluded."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT n.nspname
              FROM pg_namespace n
              JOIN pg_class c ON c.relnamespace = n.oid
             WHERE n.nspname <> ALL(%s)
               AND n.nspname NOT LIKE 'pg\\_temp\\_%%'
               AND n.nspname NOT LIKE 'pg\\_toast\\_temp\\_%%'
               AND c.relkind IN ('r', 'p', 'f', 'v', 'm')
             ORDER BY 1
            """,
            (list(_SYSTEM_SCHEMAS),),
        )
        return [row[0] for row in cursor.fetchall()]


def default_schema_of(connection: Any) -> str:
    """What an unqualified name means here: the first existing schema on `search_path`."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT (current_schemas(false))[1]")
        row = cursor.fetchone()
    return str(row[0]) if row and row[0] else "public"


def pull(
    conninfo: str, schemas: list[str] | None = None, default_schema: str | None = None
) -> Dictionary:
    """The dictionary of `schemas` (default: every user schema), read from the live catalogue.

    `default_schema` is what an unqualified name means; by default it is read from the
    database's own `search_path`, so it matches how the SQL actually runs there.
    """
    with connect(conninfo) as connection:
        wanted = schemas or user_schemas(connection)
        default_schema = default_schema or default_schema_of(connection)
        return capture_schemas(
            connection,
            schemas=wanted,
            default_schema=default_schema,
            captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
            dialect="postgres",
        )


def is_available(settings: PgSettings | None = None) -> bool:
    """True when the PostgreSQL answers and the driver is installed."""
    try:
        with connect((settings or PgSettings.from_env()).conninfo) as connection:
            connection.execute("SELECT 1")
        return True
    except Exception:
        return False
