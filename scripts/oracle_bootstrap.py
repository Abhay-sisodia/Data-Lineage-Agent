"""Prepare the local Oracle for the spike.

Run after ``docker compose up -d``:

    .\\.venv\\Scripts\\python.exe scripts\\oracle_bootstrap.py

The container image already creates the application user. What it does not grant is
read access to the ``V$`` views, and without those the log-recovery sub-spike (T3.3)
has nothing to read — which is most of the reason this database exists at all.

Idempotent: safe to run repeatedly. Grants that are already in place are reported and
skipped rather than treated as failures.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from lineage.oracle import OracleSettings

# Privileges the corpus needs in order to exist and run.
OBJECT_PRIVILEGES = [
    "GRANT CREATE SESSION TO {user}",
    "GRANT CREATE TABLE TO {user}",
    "GRANT CREATE VIEW TO {user}",
    "GRANT CREATE PROCEDURE TO {user}",
    "GRANT CREATE SYNONYM TO {user}",
    "GRANT CREATE TRIGGER TO {user}",
    "GRANT CREATE SEQUENCE TO {user}",
    "GRANT CREATE TYPE TO {user}",
    "GRANT UNLIMITED TABLESPACE TO {user}",
]

# Read access to the runtime evidence. SELECT_CATALOG_ROLE covers the V$ views in one
# grant; the explicit grants are the fallback when the role cannot be granted.
CATALOG_ROLE = "GRANT SELECT_CATALOG_ROLE TO {user}"
CATALOG_VIEWS = ["V_$SQL", "V_$SQLAREA", "V_$SQLSTATS", "V_$SESSION", "V_$SQL_BIND_CAPTURE"]

# ORA-01031 insufficient privileges is a real failure; "already granted" is not.
BENIGN_ERRORS = ("ORA-01919", "ORA-01920", "ORA-01921", "ORA-04043")


def wait_for_database(settings: OracleSettings, timeout_seconds: int = 600) -> Any:
    """Poll until the database accepts connections.

    First container start creates the database and legitimately takes minutes, so this
    waits rather than failing fast.
    """
    import oracledb

    deadline = time.monotonic() + timeout_seconds
    attempt = 0
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        attempt += 1
        try:
            connection = oracledb.connect(
                user=settings.user, password=settings.password, dsn=settings.dsn
            )
        except Exception as exc:
            last_error = exc
            if attempt % 6 == 1:
                print(f"  waiting for {settings.dsn} ... ({exc.__class__.__name__})")
            time.sleep(5)
        else:
            print(f"connected to {settings.dsn} as {settings.user}")
            return connection

    raise SystemExit(f"database not ready within {timeout_seconds}s. Last error: {last_error}")


def _execute(cursor: Any, statement: str) -> str:
    """Run one DDL statement, tolerating 'already done' outcomes."""
    try:
        cursor.execute(statement)
    except Exception as exc:
        message = str(exc)
        if any(code in message for code in BENIGN_ERRORS):
            return "already in place"
        return f"FAILED: {message.strip().splitlines()[0]}"
    return "granted"


def main() -> None:
    admin = OracleSettings.admin_from_env()
    app = OracleSettings.from_env()

    connection = wait_for_database(admin)
    failures = 0

    with connection, connection.cursor() as cursor:
        print(f"\ngranting object privileges to {app.user}")
        for template in OBJECT_PRIVILEGES:
            statement = template.format(user=app.user)
            outcome = _execute(cursor, statement)
            privilege = statement.removeprefix("GRANT ").split(" TO ")[0]
            print(f"  {privilege:<24} {outcome}")
            failures += outcome.startswith("FAILED")

        print("\ngranting read access to runtime evidence (V$ views)")
        role_outcome = _execute(cursor, CATALOG_ROLE.format(user=app.user))
        print(f"  SELECT_CATALOG_ROLE      {role_outcome}")
        if role_outcome.startswith("FAILED"):
            print("  falling back to explicit grants")
            for view in CATALOG_VIEWS:
                outcome = _execute(cursor, f"GRANT SELECT ON SYS.{view} TO {app.user}")
                print(f"    {view:<22} {outcome}")
                failures += outcome.startswith("FAILED")

    print("\nverifying the application user can read V$SQL")
    from lineage.oracle import connect

    try:
        with connect(app) as app_connection, app_connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM v$sql")
            (count,) = cursor.fetchone()
        print(f"  OK - {app.user} can see {count} statements in V$SQL")
    except Exception as exc:
        print(f"  FAILED - {app.user} cannot read V$SQL: {exc}")
        failures += 1

    if failures:
        sys.exit(f"\nbootstrap finished with {failures} failure(s)")
    print("\nbootstrap complete")


if __name__ == "__main__":
    main()
