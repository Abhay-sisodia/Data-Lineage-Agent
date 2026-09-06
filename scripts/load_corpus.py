"""Compile the corpus into the local Oracle (T0.2).

    .\\.venv\\Scripts\\python.exe scripts\\load_corpus.py

Why this matters: a corpus that parses is not the same as a corpus that is real. If the
packages do not compile, the hand-labelled ground truth describes code that could never
have run, and the precision number computed against it means nothing.

Compiling also populates the data dictionary that name resolution reads (T1.4) and gives
the log-recovery sub-spike something to execute (T3.3).

Files marked ``parse_only`` in sources.yaml are skipped deliberately - they reference
objects that do not exist here on purpose, standing in for out-of-scope and not-granted
cases.

DESTRUCTIVE: drops every object in the application schema before loading. That schema is
a scratch corpus database and holds nothing else.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from lineage.oracle import OracleSettings, connect

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
SOURCES = CORPUS / "sources.yaml"

# Load order matters: base tables first, then everything that binds against them.
LOAD_ORDER = [
    "schema",
    "adversarial/band0",
    "adversarial/band1",
    "adversarial/band2",
    "adversarial/silent",
    "adversarial/sql",
]

PLSQL_START = re.compile(
    r"^\s*(CREATE\s+(OR\s+REPLACE\s+)?(EDITIONABLE\s+|NONEDITIONABLE\s+)?"
    r"(PROCEDURE|FUNCTION|PACKAGE|TRIGGER|TYPE)|DECLARE|BEGIN)",
    re.IGNORECASE,
)

DROPPABLE = {
    "TABLE": "DROP TABLE {name} CASCADE CONSTRAINTS PURGE",
    "VIEW": "DROP VIEW {name}",
    "SYNONYM": "DROP SYNONYM {name}",
    "PROCEDURE": "DROP PROCEDURE {name}",
    "FUNCTION": "DROP FUNCTION {name}",
    "PACKAGE": "DROP PACKAGE {name}",
    "TRIGGER": "DROP TRIGGER {name}",
    "SEQUENCE": "DROP SEQUENCE {name}",
}


def split_statements(source: str) -> list[str]:
    """Split a SQL script into executable statements.

    A line containing only ``/`` terminates a PL/SQL unit. Everything else is split on
    semicolons. Deliberately simple - it handles this corpus, and a general-purpose SQL
    script splitter is not what the spike is for.
    """
    statements: list[str] = []
    for chunk in re.split(r"^\s*/\s*$", source, flags=re.MULTILINE):
        stripped = _strip_comments(chunk).strip()
        if not stripped:
            continue
        if PLSQL_START.match(stripped):
            # A PL/SQL unit goes to the server whole - its internal semicolons are
            # part of the code, not statement separators.
            statements.append(stripped)
        else:
            statements.extend(s.strip() for s in stripped.split(";") if s.strip())
    return statements


def _strip_comments(text: str) -> str:
    """Remove full-line -- comments so they do not confuse statement splitting."""
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))


def drop_everything(cursor: Any) -> int:
    """Empty the application schema so loading is repeatable."""
    dropped = 0
    for object_type, template in DROPPABLE.items():
        cursor.execute(
            "SELECT object_name FROM user_objects WHERE object_type = :t",
            t=object_type,
        )
        for (name,) in cursor.fetchall():
            try:
                cursor.execute(template.format(name=f'"{name}"'))
                dropped += 1
            except Exception:
                pass
    return dropped


def compile_errors(cursor: Any) -> list[tuple[str, str, int, str]]:
    """Everything in the schema that failed to compile."""
    cursor.execute(
        """
        SELECT name, type, line, text
          FROM user_errors
         WHERE attribute = 'ERROR'
         ORDER BY name, sequence
        """
    )
    return list(cursor.fetchall())


def main() -> None:
    sources = yaml.safe_load(SOURCES.read_text(encoding="utf-8"))
    parse_only = set(sources.get("parse_only") or [])

    settings = OracleSettings.from_env()
    print(f"connecting to {settings.dsn} as {settings.user}")

    with connect(settings) as connection, connection.cursor() as cursor:
        dropped = drop_everything(cursor)
        print(f"dropped {dropped} existing object(s)\n")

        loaded = skipped = failed = 0

        for collection in LOAD_ORDER:
            directory = CORPUS / collection
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.sql")):
                relative = path.relative_to(CORPUS).as_posix()
                if relative in parse_only:
                    print(f"  SKIP  {relative}  (parse-only by design)")
                    skipped += 1
                    continue

                statements = split_statements(path.read_text(encoding="utf-8"))
                errors: list[str] = []
                for statement in statements:
                    try:
                        cursor.execute(statement)
                    except Exception as exc:
                        first_line = str(exc).strip().splitlines()[0]
                        errors.append(f"{first_line}  <<{statement[:60]}...>>")

                if errors:
                    print(f"  FAIL  {relative}  ({len(errors)} of {len(statements)} statements)")
                    for error in errors[:3]:
                        print(f"          {error}")
                    failed += 1
                else:
                    print(f"  ok    {relative}  ({len(statements)} statements)")
                    loaded += 1

        connection.commit()

        print(f"\nloaded {loaded}, skipped {skipped}, failed {failed}")

        errors = compile_errors(cursor)
        if errors:
            print(f"\n{len(errors)} compilation error(s) in the schema:")
            for name, object_type, line, text in errors[:15]:
                print(f"  {object_type} {name} line {line}: {text.strip()}")
            sys.exit("corpus did not compile cleanly")

        cursor.execute(
            "SELECT object_type, COUNT(*) FROM user_objects GROUP BY object_type "
            "ORDER BY object_type"
        )
        print("\nobjects now in the schema:")
        for object_type, count in cursor.fetchall():
            print(f"  {object_type:<16} {count}")

    print("\ncorpus compiled cleanly")


if __name__ == "__main__":
    main()
