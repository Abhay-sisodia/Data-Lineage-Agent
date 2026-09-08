"""Capture an execution witness from the query log (T3.5b).

    .\\.venv\\Scripts\\python.exe scripts\\capture_witness.py --output evidence/witness.json

Answers one question per statement: **did this run inside the window?** That is what turns
`unexercised` from a modelling idea into a fact, and it is the one input the analyser
cannot derive from source at any price.

Three things the live database taught this script, all of them the hard way:

**1. `V$SQL` IS MEMORY, NOT A LOG.** It is the shared pool. It empties at instance restart
and ages out under memory pressure, so the honest window is *since startup*, not the 18
months the retention setting talks about. The first version of this script took a
`--months` argument, which was a fiction: nothing older than the last restart is there to
be found. A production capture needs `DBA_HIST_SQLSTAT` (AWR, licensed) or a scheduled job
that persists `V$SQL` before it ages out. Until then, **every negative this witness
produces is bounded by an uptime, and the window field says so.**

**2. `MODULE`/`ACTION` IS STICKY.** `DBMS_APPLICATION_INFO.SET_MODULE` sets a SESSION
attribute. `s7_unexercised_branch` sets it and never clears it, so every statement the
session ran afterwards - all of `s8`, all of `sq_*` - is tagged `s7_unexercised_branch` in
`V$SQL`. Trusting the strongest signal in the ladder on its own would have credited `s7`
with `INSERT INTO fct_product_sales`, a statement it does not contain. So attribution here
requires **both** halves: the action names the unit *and* the statement's shape matches one
the unit actually contains. Shape stays exactly where T2.9 left it - a corroborating
constraint, never an attributor.

**3. ALMOST NOBODY SETS IT.** Of 37 procedures executed, **5** call
`DBMS_APPLICATION_INFO`. The rest are tagged with the client program name or nothing. The
blind-spot register predicted this precisely, and it caps what this signal can ever cover.

**The asymmetry that makes all of the above non-negotiable.** Everywhere else in this
system a bad attribution creates a wrong edge, which precision catches. Here a missing
attribution creates a **silent negative**: "this statement never ran", asserted about a
unit the log could not see. That is a finding a migration team might act on by deleting
code. So: **no coverage, no verdict**, and a unit whose action never appears is recorded as
uncovered rather than as unexercised.

With no live database the correct output is no witness file at all, not an empty one - an
empty witness reads as "nothing ran".
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from lineage.analysis.dynamic import resolve_dynamic_sql
from lineage.evidence.witness import ExecutionWitness, StatementSighting
from lineage.oracle import OracleSettings, connect
from lineage.parsing.plsql import parse_program

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"

# Long enough to identify a statement rather than match everything - the same threshold
# T2.9 settled on for literal fragments.
MIN_SHAPE = 24

# Units that ran but whose cursors have aged out - reported, never given a verdict.
_dropped: list[str] = []

# DDL is parsed, executed, and not retained as a shareable cursor, so V$SQL cannot report
# on it either way. Anything matching this gets no execution verdict rather than a negative
# one - see ExecutionWitness.unobservable for what that prevents.
DDL = re.compile(r"\s*(ALTER|CREATE|DROP|TRUNCATE|COMMENT|GRANT|RENAME)", re.IGNORECASE)


def shape(text: str) -> str:
    """Normalise a statement for comparison: whitespace, identifier case, bind names.

    **LITERALS ARE PRESERVED, VERBATIM AND CASE-SENSITIVE, AND THAT IS THE WHOLE POINT.**
    The first version replaced every literal with a placeholder, on the theory that
    literals are noise. Measured against the real log it equated

        SELECT ... TRUNC(order_date, 'YYYY') ... GROUP BY ...     (s7, YEAR_END branch)
        SELECT ... TRUNC(order_date, 'MM')   ... GROUP BY ...     (s8_aggregated_copy)

    and, combined with the sticky action tag, reported `s7`'s YEAR_END branch as
    **exercised when it had never been called** - destroying the single piece of
    unexercised evidence the whole corpus exists to provide. Here the literal *is* the
    semantic difference, and case matters too: `'EU'` and `'eu'` are different values.

    Deliberately shallow otherwise. A cleverer normaliser that reordered clauses or
    resolved aliases would silently equate two different statements, and the cost of that
    is a false claim about execution rather than a visible wrong edge.
    """
    out: list[str] = []
    inside = False
    for index, char in enumerate(text):
        if char == "'":
            inside = not inside
        out.append(char if inside or char == "'" else char.upper())
        del index
    collapsed = re.sub(r"\s+", " ", "".join(out).strip().rstrip(";").strip())
    collapsed = re.sub(r":\w+", ":B", collapsed)
    return re.sub(r"\s*([(),])\s*", r"\1", collapsed)


def _log_rows(cursor) -> list[tuple[str | None, str]]:
    """(action, sql_text) for everything the application schema parsed since startup."""
    cursor.execute(
        """
        SELECT action, sql_text
          FROM v$sql
         WHERE parsing_schema_name = USER
        """
    )
    return [(row[0], str(row[1])) for row in cursor.fetchall()]


def _window(cursor) -> str:
    cursor.execute(
        "SELECT TO_CHAR(startup_time, 'YYYY-MM-DD HH24:MI'), "
        "       TO_CHAR(SYSDATE, 'YYYY-MM-DD HH24:MI') FROM v$instance"
    )
    started, now = cursor.fetchone()
    return f"V$SQL shared pool, instance up since {started}, captured {now}"


def build() -> ExecutionWitness:
    connection = connect(OracleSettings())
    with connection.cursor() as cursor:
        rows = _log_rows(cursor)
        window = _window(cursor)

    # Coverage is by action alone: an action appears only because that unit set it, so a
    # unit named here really did run. Stickiness contaminates which STATEMENTS are tagged,
    # not whether the unit executed.
    covered = {action.upper() for action, _ in rows if action}

    # Shapes, kept per action rather than pooled, so the intersection below can be done
    # per unit. Pooling them would let one unit's execution vouch for another's statement.
    by_action: dict[str, set[str]] = {}
    for action, text in rows:
        if action and len(text) >= MIN_SHAPE:
            by_action.setdefault(action.upper(), set()).add(shape(text))

    units: set[str] = set()
    sightings: set[StatementSighting] = set()
    unobservable: set[StatementSighting] = set()

    for path in sorted(CORPUS.rglob("*.sql")):
        program = parse_program(path.read_text(encoding="utf-8"))
        for unit in program.units:
            if unit.name.upper() in covered:
                units.add(unit.name.upper())

        # RECOVERED DYNAMIC STATEMENTS COUNT (T3.2). The statement that RUNS is not the
        # statement in the source: `V$SQL` holds `ALTER TABLE ... EXCHANGE PARTITION`,
        # while the source at that line says `EXECUTE IMMEDIATE`. Compared on source text
        # alone, every dynamic site looked unexercised - nine of them, in `s4` and `b2_03`,
        # both of which had just been called. A false negative here is the expensive
        # direction: it says live code is dead.
        #
        # The resolver already stamps each recovered statement with its execution site, so
        # a sighting lands on the line that emitted it, which is also where the edge's
        # origin points.
        statements = list(program.statements) + list(resolve_dynamic_sql(program).statements)

        for statement in statements:
            enclosing = [u for u in program.units if u.line <= statement.line]
            if not enclosing:
                continue
            name = enclosing[-1].name.upper()
            if name not in units or len(statement.text) < MIN_SHAPE:
                continue
            # BOTH halves. The action says the unit ran; the shape says this statement is
            # one the unit contains. Either alone is an attribution T2.9 already measured
            # as unreliable - and the sticky-module effect makes the action half unusable
            # on its own for anything finer than the unit.
            if shape(statement.text) in by_action.get(name, set()):
                sightings.add(StatementSighting(unit=name, line=statement.line))
            elif DDL.match(statement.text.strip()):
                # Oracle does not keep DDL as a shareable cursor, so it is absent from
                # V$SQL whether or not it ran - `s4`'s EXCHANGE PARTITION executed
                # successfully and appears nowhere. Absence is only evidence where
                # presence was possible, so this statement gets no verdict at all.
                unobservable.add(StatementSighting(unit=name, line=statement.line))

    # A UNIT EARNS A VERDICT ONLY IF THE POOL STILL HOLDS AT LEAST ONE OF ITS STATEMENTS.
    #
    # Measured, and it overturned the first rule. Every cursor tagged `s4_partition_exchange`
    # turned out to belong to s5, s6 or s7 - statements that ran AFTER s4 in the same
    # session and inherited its sticky tag - while s4's own INSERT had aged out of the pool
    # entirely, seconds after running. Coverage by tag alone would then have marked all
    # four of s4's exchange edges unexercised, which is false and is the expensive
    # direction of false.
    #
    # If nothing of a unit is resident, absence tells us about the SHARED POOL, not about
    # the estate. One sighting proves the unit's cursors are still there, which is what
    # makes a sibling statement's absence mean anything at all.
    global _dropped
    resident = {sighting.unit for sighting in sightings}
    _dropped = sorted(units - resident)
    units &= resident

    return ExecutionWitness(
        window=window,
        sightings=frozenset(sightings),
        units=frozenset(units),
        unobservable=frozenset(s for s in unobservable if s.unit in units),
        note=(
            "Units attributed by DBMS_APPLICATION_INFO action; statements confirmed by "
            "shape within a covered unit, because MODULE/ACTION is sticky and tags every "
            "later statement in the session. A unit absent here is UNCOVERED, not "
            "unexercised. A unit earns a verdict only if the pool still holds at least "
            "one of its statements; otherwise absence describes the shared pool rather "
            "than the estate. Bounded by instance uptime: V$SQL is memory, not a log."
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "witness.json")
    args = parser.parse_args()

    witness = build()
    if not witness.units:
        # An empty witness reads as "nothing ran", which is a claim about the estate
        # rather than about the log. Refuse to write one.
        raise SystemExit(
            "no unit could be attributed from the log - refusing to write a witness, "
            "because an empty witness reads as 'nothing ran' rather than 'nothing seen'"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    witness.dump(args.output)
    print(f"wrote {args.output}")
    print(f"  window     {witness.window}")
    print(f"  covered    {len(witness.units)} unit(s): {', '.join(sorted(witness.units))}")
    print(f"  statements {len(witness.sightings)} seen")
    print(f"  unobservable {len(witness.unobservable)} (DDL - V$SQL cannot see it either way)")
    if _dropped:
        print(f"  no verdict   {len(_dropped)} unit(s) ran but nothing of theirs is still "
              f"resident: {', '.join(_dropped)}")


if __name__ == "__main__":
    main()
