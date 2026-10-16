"""Generate `dictionary.json` from `schema.sql`. Run after any change to the schema.

    .\\.venv\\Scripts\\python.exe stress/pg/build_dictionary.py
    .\\.venv\\Scripts\\python.exe stress/pg/build_dictionary.py --schema <schema.sql> \\
        --out <dictionary.json> [--default-schema <name>] [--check]

`schema.sql` is the single source of truth. A hand-maintained dictionary beside it would
drift, and a dictionary that is wrong rather than thin produces confident edges to the
wrong columns - the one failure mode a lineage tool cannot afford. So the dictionary is
never edited; it is rebuilt, and `--check` fails if the file on disk is stale.

The dictionary is SYNTHETIC: it is read from DDL, not captured from a live catalogue.
`captured_at` is fixed rather than "now" so rebuilding an unchanged schema is a no-op.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from lineage.resolution.ddl import ddl_payload

HERE = Path(__file__).resolve().parent
SCHEMA = HERE / "schema.sql"
OUTPUT = HERE / "dictionary.json"

# Every stress statement schema-qualifies its relations. The default only decides what an
# UNQUALIFIED name means, which is batch B9's subject - so it is stated here, once.
DEFAULT_SCHEMA = "core"
CAPTURED_AT = "2026-10-01T00:00:00Z"


def _option(name: str, default: str) -> str:
    """`--name value` from the command line; the shared warehouse's paths when absent."""
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


def build(
    schema_sql: Path | None = None, default_schema: str = DEFAULT_SCHEMA
) -> dict[str, object]:
    # `SCHEMA` read at CALL time: the torture runners point it at their own schema.sql
    # before calling, and a default bound at definition time silently ignored that.
    schema_sql = schema_sql or SCHEMA
    try:
        return ddl_payload(schema_sql.read_text(encoding="utf-8"), default_schema, CAPTURED_AT)
    except ValueError as error:
        raise SystemExit(f"{error} ({schema_sql.name})") from error


def main() -> int:
    # Another schema (the final suite's, 2026-10-07) is built the same way; the defaults are
    # the shared warehouse, so the plain command is unchanged.
    schema_sql = Path(_option("--schema", str(SCHEMA)))
    output = Path(_option("--out", str(OUTPUT)))
    rendered = json.dumps(build(schema_sql, _option("--default-schema", DEFAULT_SCHEMA)), indent=2)
    rendered += "\n"
    if "--check" in sys.argv:
        current = output.read_text(encoding="utf-8") if output.exists() else ""
        if current != rendered:
            print(f"{output.name} is stale - rebuild it from {schema_sql.name}")
            return 1
        return 0
    # LF on every platform: .gitattributes pins eol=lf, and a generated file that differs
    # from its committed form only in line endings would read as drift.
    output.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
