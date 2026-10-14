"""The PostgreSQL grammar checklist (docs/pg-coverage.md) - complete, honest and current.

Three ways a coverage statement lies, one test each:

* it leaves a construct out - every construct PostgreSQL's parser can produce must have a
  row, and the list is read from the parser, so a new PostgreSQL release adds rows to fill;
* a row does not test what it says - its snippet must be valid PostgreSQL, and its parse
  must contain the construct it claims;
* the numbers describe last month - the measured snapshot and the report must equal what
  the analyser does today, so a change in behaviour is a reviewed diff.
"""

from __future__ import annotations

import enum
import json
import sys
from pathlib import Path

import pytest
from pglast import ast, enums

from lineage.coverage.grammar import (
    EXCLUDED,
    PLPGSQL_STATEMENTS,
    REPORT,
    SNAPSHOT,
    UNREACHABLE,
    constructs,
    inventory,
    load_rows,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

ROWS = load_rows()


def test_every_construct_has_a_row() -> None:
    covered = {item for row in ROWS for item in row.items}
    assert sorted(set(inventory()) - covered) == []


def test_every_row_names_a_real_construct() -> None:
    items = inventory()
    unknown = [
        (row.id, item)
        for row in ROWS
        for item in row.items
        if not item.startswith("extra:") and item not in items
    ]
    assert unknown == []


@pytest.mark.parametrize("row", ROWS, ids=[row.id for row in ROWS])
def test_snippet_is_postgresql_and_contains_its_construct(row) -> None:
    found = constructs(row.sql)  # raises if PostgreSQL itself rejects the snippet
    claimed = [item for item in row.items if not item.startswith("extra:")]
    assert [item for item in claimed if item not in found] == []


def test_left_out_constructs_are_real() -> None:
    """Every exclusion names something the parser has - the list cannot hide a typo."""
    for key in [*EXCLUDED, *UNREACHABLE]:
        kind, _, name = key.partition(":")
        if kind == "variant":
            node, _, rest = name.partition(".")
            field_name, _, value = rest.partition("=")
            info = getattr(ast, node).__slots__[field_name]
            enum_type = getattr(enums, info.c_type)
            assert issubclass(enum_type, enum.Enum) and value in enum_type.__members__, key
        else:
            assert isinstance(getattr(ast, name, None), type), key


def test_plpgsql_statement_types_are_the_parsers() -> None:
    """Every PL/pgSQL statement type a snippet parses to is in the written list."""
    seen = {item for row in ROWS for item in constructs(row.sql) if item.startswith("plpgsql:")}
    known = {f"plpgsql:PLpgSQL_stmt_{name}" for name in PLPGSQL_STATEMENTS}
    assert seen - known == set()


def test_snapshot_and_report_are_current() -> None:
    """What the analyser does with every construct today is what the committed files say.

    Fails after any change in behaviour on a checklist snippet; regenerate with
    `scripts/pg_coverage.py` and review the diff - it IS the change in coverage.
    """
    import pg_coverage

    measured, report, problems = pg_coverage.run()
    assert problems == []
    assert json.loads(SNAPSHOT.read_text(encoding="utf-8")) == measured
    assert REPORT.read_text(encoding="utf-8") == report
