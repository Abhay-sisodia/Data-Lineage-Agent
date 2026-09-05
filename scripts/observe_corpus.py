"""Observe what a procedure actually changes (T1.2).

    .\\.venv\\Scripts\\python.exe scripts\\observe_corpus.py "pkg_policy_state.load_policy('EU')"

Snapshots every table, runs the given PL/SQL call, snapshots again, and reports what
moved. This is the referee for ground-truth labelling (ADR-0001 §1): if one party writes
both the answer key and the analyser, a shared misunderstanding scores 100% and certifies
itself. Execution is independent of what anyone believes the code does.

Be precise about what this establishes, because overclaiming here would undermine the
whole point:

* TARGETS - definitive. A column whose values changed was written. No inference.
* SOURCES - evidential, not proof. Candidate sources are found by value containment:
  if every new value in a target column also appears in some source column, that is
  real evidence, but coincidence is possible on small or low-cardinality data.
* TRANSFORM - partial. Exact value matches suggest identity; a collapse in row count
  suggests aggregation. Anything else needs reading the source.

So labels derived from here are marked `observed` only where target AND source both hold
up. Everything else is `source_read` and carries lower authority - and the harness
reports that split alongside every score.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from typing import Any

from lineage.oracle import OracleSettings, connect

# Columns that carry no lineage signal and would produce noisy value matches.
IGNORED_COLUMNS = {"CHANGED_AT"}


def list_tables(cursor: Any) -> dict[str, list[str]]:
    """Every table in the schema and its columns."""
    cursor.execute(
        """
        SELECT t.table_name, c.column_name
          FROM user_tables t
          JOIN user_tab_columns c ON c.table_name = t.table_name
         ORDER BY t.table_name, c.column_id
        """
    )
    tables: dict[str, list[str]] = defaultdict(list)
    for table, column in cursor.fetchall():
        tables[table].append(column)
    return dict(tables)


def snapshot(cursor: Any, tables: dict[str, list[str]]) -> dict[str, dict[str, list[Any]]]:
    """Read every column of every table into memory.

    The corpus is tiny by design, so a full read is simpler and more reliable than
    trying to be clever about change detection.
    """
    state: dict[str, dict[str, list[Any]]] = {}
    for table, columns in tables.items():
        selectable = [c for c in columns if c not in IGNORED_COLUMNS]
        if not selectable:
            continue
        try:
            cursor.execute(f"SELECT {', '.join(selectable)} FROM {table}")
            rows = cursor.fetchall()
        except Exception as exc:
            print(f"  (could not read {table}: {str(exc).splitlines()[0]})")
            continue
        state[table] = {
            column: [row[index] for row in rows] for index, column in enumerate(selectable)
        }
    return state


def changed_columns(
    before: dict[str, dict[str, list[Any]]],
    after: dict[str, dict[str, list[Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Which columns changed, and what the new values are."""
    changes: dict[str, dict[str, dict[str, Any]]] = {}
    for table, columns in after.items():
        previous = before.get(table, {})
        for column, values in columns.items():
            old = previous.get(column, [])
            # Counted, not set-based: inserting rows identical to ones already present
            # is a real write, and a set comparison would silently call it unchanged.
            if Counter(v for v in old if v is not None) == Counter(
                v for v in values if v is not None
            ):
                continue
            new_values = _multiset(values) - _multiset(old)
            changes.setdefault(table, {})[column] = {
                "rows_before": len(old),
                "rows_after": len(values),
                "new_values": sorted(new_values, key=str)[:8],
                "new_value_count": len(new_values),
            }
    return changes


def _multiset(values: list[Any]) -> set[Any]:
    return {v for v in values if v is not None}


def candidate_sources(
    before: dict[str, dict[str, list[Any]]],
    table: str,
    column: str,
    new_values: set[Any],
) -> list[str]:
    """Source columns whose pre-existing values contain every new value.

    Evidence, not proof. Reported so a human can rule on it rather than presented as
    settled fact.
    """
    if not new_values:
        return []
    candidates = []
    for other_table, columns in before.items():
        for other_column, values in columns.items():
            if other_table == table and other_column == column:
                continue
            available = _multiset(values)
            if available and new_values <= available:
                candidates.append(f"{other_table}.{other_column}")
    return sorted(candidates)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Observe what a procedure actually changes.",
        epilog=(
            "Calls run in ONE session, in order. This matters: package-level state is "
            "session-scoped, so pkg.load_policy('EU') and pkg.apply_policy observed in "
            "separate sessions would show no effect at all."
        ),
    )
    parser.add_argument("calls", nargs="+", help="PL/SQL calls to observe")
    parser.add_argument(
        "--setup",
        action="append",
        default=[],
        metavar="STMT",
        help=(
            "PL/SQL or SQL to run and commit BEFORE the snapshot, so its effects are not "
            "attributed to the call under observation. Needed when a branch only writes "
            "if some precondition holds, or when the seed data makes a write idempotent "
            "and therefore invisible."
        ),
    )
    arguments = parser.parse_args()
    calls = arguments.calls
    call = " ; ".join(calls)

    settings = OracleSettings.from_env()
    with connect(settings) as connection, connection.cursor() as cursor:
        tables = list_tables(cursor)

        # Setup runs and commits BEFORE the snapshot, so nothing it changes is
        # attributed to the call being observed.
        for statement in arguments.setup:
            text = statement.strip().rstrip(";")
            block = text if text.upper().startswith(("BEGIN", "DECLARE")) else None
            try:
                cursor.execute(block or (f"BEGIN {text}; END;" if "(" in text else text))
            except Exception as exc:
                raise SystemExit(f"setup failed: {str(exc).splitlines()[0]}") from exc
            print(f"setup: {text}")
        if arguments.setup:
            connection.commit()

        print(f"observing: {call}")
        print(f"snapshotting {len(tables)} tables\n")

        before = snapshot(cursor, tables)

        cursor.execute(
            "BEGIN DBMS_APPLICATION_INFO.SET_MODULE('LINEAGE_OBSERVE', :a); END;",
            a=call[:32],
        )
        # One anonymous block, one session: package state set by an earlier call is
        # still live for a later one.
        block = "BEGIN " + " ".join(f"{c.rstrip(';')};" for c in calls) + " END;"
        try:
            cursor.execute(block)
        except Exception as exc:
            raise SystemExit(f"call failed: {str(exc).splitlines()[0]}") from exc
        connection.commit()

        after = snapshot(cursor, tables)
        changes = changed_columns(before, after)

        if not changes:
            print("NOTHING CHANGED.")
            print("Either the procedure is a no-op on this data, or its branch was not taken.")
            print("An unexercised branch cannot be labelled by observation - mark it source_read.")
            return

        for table, columns in sorted(changes.items()):
            print(f"{table}")
            for column, detail in sorted(columns.items()):
                rows = f"{detail['rows_before']} -> {detail['rows_after']} rows"
                print(f"  {column:<20} WRITTEN   ({rows}, {detail['new_value_count']} new values)")
                sources = candidate_sources(
                    before,
                    table,
                    column,
                    set(detail["new_values"]) if detail["new_values"] else set(),
                )
                if sources:
                    print(f"    value-consistent sources: {', '.join(sources[:6])}")
                else:
                    print("    no value-consistent source - derived, aggregated, or literal")
            print()

        print("TARGETS above are definitive. SOURCES are evidence for a human to rule on.")


if __name__ == "__main__":
    main()
