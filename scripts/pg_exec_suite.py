"""Execute a suite's SQL in the seeded PostgreSQL, statement by statement, then roll back.

    .\\.venv\\Scripts\\python.exe scripts\\pg_seed.py
    .\\.venv\\Scripts\\python.exe scripts\\pg_exec_suite.py [--database lineage_final]
        [FOLDER ...]     (default: stress/pg/final/medium stress/pg/final/hard)

Proves the DDL covers the test SQL: a missing table or column, a wrong type or a typo is
an error from the database itself, not from our parser. Each file runs in one transaction
that is ROLLED BACK, with a savepoint per statement so one failure does not hide the rest;
nothing is left behind. The tables are empty, so this checks names and types, not data.

An error is not automatically a defect: a test may call a function that exists only in
the production system on purpose (a boundary case). The report says which statement
failed and why; deciding is the reader's job.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lineage.parsing.sqlscript import split_statements  # noqa: E402
from lineage.resolution.postgres_connect import PgSettings, connect  # noqa: E402

DEFAULT = [ROOT / "stress" / "pg" / "final" / "medium", ROOT / "stress" / "pg" / "final" / "hard"]


def _shown(path: Path) -> str:
    """The path relative to the repository when it is inside it, else as given."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def run_file(connection: object, path: Path) -> tuple[int, list[tuple[int, str]]]:
    """(statements run, [(line, first line of the error)]) - always rolled back."""
    statements = split_statements(path.read_text(encoding="utf-8"))
    failures: list[tuple[int, str]] = []
    cursor = connection.cursor()  # type: ignore[attr-defined]
    try:
        for index, (line, text) in enumerate(statements):
            cursor.execute(f"SAVEPOINT s{index}")
            try:
                cursor.execute(text)
            except Exception as error:  # the database's verdict, reported, never raised
                cursor.execute(f"ROLLBACK TO SAVEPOINT s{index}")
                failures.append((line, str(error).splitlines()[0]))
    finally:
        connection.rollback()  # type: ignore[attr-defined]
    return len(statements), failures


def main() -> int:
    args = sys.argv[1:]
    database = "lineage_final"
    if "--database" in args:
        at = args.index("--database")
        database = args[at + 1]
        del args[at : at + 2]
    folders = [Path(a).resolve() for a in args] or DEFAULT
    files = sorted(f for folder in folders for f in folder.rglob("*.sql"))
    if not files:
        print("no .sql files found")
        return 1

    clean = 0
    with connect(PgSettings.from_env(database).conninfo, read_only=False) as connection:
        for path in files:
            count, failures = run_file(connection, path)
            name = _shown(path)
            if not failures:
                clean += 1
                print(f"OK    {name}  ({count} statements)")
                continue
            print(f"ERROR {name}  ({len(failures)} of {count} statements)")
            for line, message in failures:
                print(f"        line {line}: {message}")
    print(f"\n{clean}/{len(files)} files execute cleanly against {database}")
    return 0 if clean == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
