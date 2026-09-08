"""Capture an execution witness from the query log (T3.5b).

    .\\.venv\\Scripts\\python.exe scripts\\capture_witness.py --months 18 --output evidence/witness.json

Answers one question per statement: **did this run inside the window?** That is what turns
`unexercised` from a modelling idea into a fact, and it is the one input the analyser
cannot derive from source at any price.

**Attribution is the hard part, exactly as it is for dynamic SQL (T2.9), and it fails the
same way.** `V$SQL` holds statement text, not program structure. Tying a logged statement
back to the line that emitted it uses the same ladder, and this script deliberately refuses
to go below the top rung:

* **MODULE / ACTION** — set by `DBMS_APPLICATION_INFO`, and the only signal strong enough
  to found a *negative* claim on. A unit whose module never appears is recorded as
  UNCOVERED, never as unexercised.
* **Statement shape** — used only to place a sighting *within* a unit already covered by
  module attribution. It was demoted from high confidence on evidence in T2.9: with
  session attributes stripped, shape matching attributed 2 of 5 statements to the WRONG
  procedure and identified none correctly.

**Why the asymmetry matters here more than anywhere else.** Everywhere else in this system
a bad attribution creates a wrong edge, which precision catches. Here a missing attribution
would create a *silent negative*: "this statement never ran", asserted about a unit the log
simply could not see. That is a finding a migration team might act on by deleting code.
So the rule is absolute - **no coverage, no verdict** - and it is enforced by the witness
type rather than by care.

**The window is part of the answer.** A year-end path absent from a 30-day window says
almost nothing; the same path absent from 18 months is a real finding. `--months` is
recorded into the artefact and printed beside every count downstream.

Requires a live Oracle. With none available the correct output is no witness file at all,
not an empty one: an empty witness would read as "nothing ran".
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime
from pathlib import Path

from lineage.evidence.witness import ExecutionWitness, StatementSighting
from lineage.oracle import OracleSettings, connect
from lineage.parsing.plsql import parse_program

CORPUS = Path("corpus")

# Long enough to identify a statement rather than match everything, and the same threshold
# T2.9 settled on for literal fragments.
MIN_SHAPE = 24


def _shape(text: str) -> str:
    """Normalise a statement for comparison: whitespace, case, and bind names only.

    Deliberately shallow. A cleverer normaliser that reordered clauses or resolved aliases
    would silently equate two different statements, and the cost of that here is a false
    negative about execution rather than a visible wrong edge.
    """
    collapsed = re.sub(r"\s+", " ", text.strip().upper())
    collapsed = re.sub(r":\w+", ":B", collapsed)
    return re.sub(r"'[^']*'", "'L'", collapsed)


def logged_statements(cursor, months: int) -> list[tuple[str, str]]:
    """(module, sql_text) for everything in the window. Nothing is filtered by shape here."""
    cursor.execute(
        """
        SELECT module, sql_fulltext
          FROM v$sql
         WHERE last_active_time > SYSDATE - :days
           AND module IS NOT NULL
        """,
        days=months * 31,
    )
    return [(str(module), str(text)) for module, text in cursor.fetchall()]


def build(months: int) -> ExecutionWitness:
    connection = connect(OracleSettings())
    with connection.cursor() as cursor:
        rows = logged_statements(cursor, months)

    seen_modules = {module.upper() for module, _ in rows}
    seen_shapes = {_shape(text) for _, text in rows if len(text) >= MIN_SHAPE}

    units: set[str] = set()
    sightings: set[StatementSighting] = set()

    for path in sorted(CORPUS.rglob("*.sql")):
        program = parse_program(path.read_text(encoding="utf-8"))
        for unit in program.units:
            name = unit.name.upper()
            # Module attribution only. A unit the log cannot name is left uncovered, and
            # every edge in it keeps `unexercised = None`.
            if name not in seen_modules and f"LINEAGE_CORPUS.{name}" not in seen_modules:
                continue
            units.add(name)

        for statement in program.statements:
            enclosing = [u for u in program.units if u.line <= statement.line]
            if not enclosing:
                continue
            name = enclosing[-1].name.upper()
            if name not in units:
                continue
            if len(statement.text) >= MIN_SHAPE and _shape(statement.text) in seen_shapes:
                sightings.add(StatementSighting(unit=name, line=statement.line))

    return ExecutionWitness(
        window=f"{months} months to {datetime.now(UTC).date().isoformat()}",
        sightings=frozenset(sightings),
        units=frozenset(units),
        note=(
            "Units are attributed by MODULE only; statements are placed within a covered "
            "unit by shape. A unit absent here is uncovered, not unexercised."
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=18, help="Log window, in months.")
    parser.add_argument("--output", type=Path, default=Path("evidence/witness.json"))
    args = parser.parse_args()

    witness = build(args.months)
    if not witness.units:
        # An empty witness reads as "nothing ran", which is a claim about the estate
        # rather than about the log. Refuse to write one.
        raise SystemExit(
            "no unit could be attributed from the log - refusing to write a witness, "
            "because an empty witness reads as 'nothing ran' rather than 'nothing seen'"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    witness.dump(args.output)
    print(
        f"wrote {args.output}: {len(witness.units)} unit(s) covered, "
        f"{len(witness.sightings)} statement(s) seen, window {witness.window}"
    )


if __name__ == "__main__":
    main()
