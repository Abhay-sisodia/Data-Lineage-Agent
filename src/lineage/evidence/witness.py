"""What actually ran, and over what window (T3.5).

`unexercised` is the axis nobody else reports, and the register is precise about why it is
an axis rather than a tier:

> The EU path ran 9,120 times and looks strong. The APAC branch exists in the code, has
> never run inside the log retention window, and looks weak or absent. Demoting it by tier
> says *we are unsure it is real*. It is provably real - it has simply never fired.

Those are different facts. Tier answers *how well corroborated*; this answers *did it
run*. An edge can be Tier A - the strongest static evidence there is - and never observed,
and that combination is the finding.

**Three states, not two, and the third is the important one.**

| value | meaning |
|---|---|
| `False` | the statement was seen executing inside the window |
| `True` | a window was examined and the statement never appeared in it |
| `None` | no window was examined - nothing is known either way |

A two-state model has to fold `None` into one of the others, and both choices are lies.
Defaulting to `False` claims every edge ran, which is the claim this whole axis exists to
avoid making. Defaulting to `True` reports the entire corpus as dead code. **Absence of a
witness is not evidence of non-execution**, and the type has to be able to say so.

**The window travels with the verdict.** "Never observed" is meaningless without "over
what period" - a year-end path absent from a 30-day window says almost nothing, and the
same path absent from an 18-month window is a real finding. So `WITNESS.window` is
required, and the harness prints it beside the count rather than tucking it into
provenance.

**This is not a coverage tool.** It records statements, not branches or lines. A statement
that ran once and a statement that ran nine thousand times are both `False` here; the
count belongs to profiling, and conflating the two would put a popularity contest inside a
lineage fact.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["ExecutionWitness", "StatementSighting"]


@dataclass(frozen=True)
class StatementSighting:
    """One statement seen executing, addressed the way a refusal and an edge are."""

    unit: str
    line: int

    def __str__(self) -> str:
        return f"{self.unit}:{self.line}"


@dataclass(frozen=True)
class ExecutionWitness:
    """Which statements were seen running, and over what window.

    Built from `V$SQL` plus `DBMS_APPLICATION_INFO` attribution - the same evidence path
    T2.9 uses for dynamic-SQL recovery, and it inherits that path's weakness: a statement
    the log cannot attribute to a unit leaves that unit **uncovered**, not exercised and
    not unexercised. `covers()` is what keeps the difference visible.
    """

    window: str
    """Human-readable period the witness was taken over, e.g. "18 months to 2026-09-08".

    Required, because "never observed" without a window is not a statement about anything.
    """

    sightings: frozenset[StatementSighting] = field(default_factory=frozenset)
    units: frozenset[str] = field(default_factory=frozenset)
    """Units the log could attribute at all.

    Distinct from the units appearing in `sightings`: a unit can be attributable and have
    some of its statements never fire, which is exactly the s7 case. A unit absent here
    was never seen at all, and nothing about its edges can be concluded.
    """

    unobservable: frozenset[StatementSighting] = field(default_factory=frozenset)
    """Statements this evidence source cannot see *even inside a covered unit*.

    MEASURED, NOT ANTICIPATED. `V$SQL` does not hold
    `ALTER TABLE ... EXCHANGE PARTITION` at all - Oracle parses DDL, runs it, and does not
    keep it as a shareable cursor. `s4_partition_exchange` ran successfully and its
    exchange appears nowhere in the log.

    Without this field the consequences compound in the worst possible direction. `s4` is a
    silent-failure case precisely BECAUSE a DML-only reading misses the exchange; the
    analyser now finds those four edges statically and correctly; and then the witness,
    seeing a covered unit and no sighting, would stamp them **unexercised** - reporting
    the movement of an entire regulated dataset as dead code. Two independent blind spots
    landing on the same statement.

    So coverage is not one question but two: *was the unit seen* and *can this source see
    a statement of this kind at all*. A negative verdict requires both, and this set
    records the second.
    """

    note: str | None = None

    def covers(self, unit: str) -> bool:
        """Whether this witness can say anything about the given unit."""
        return unit.upper() in self.units

    def ran(self, unit: str, line: int) -> bool | None:
        """Did the statement at (unit, line) execute inside the window?

        `None` when the witness does not cover the unit, and `None` again when the source
        cannot observe this statement's kind. The caller must propagate that rather than
        substituting a default, which is the entire point of the tri-state.
        """
        if not self.covers(unit):
            return None
        sighting = StatementSighting(unit.upper(), line)
        if sighting in self.sightings:
            return True
        # Absence is only evidence where presence was possible.
        return None if sighting in self.unobservable else False

    @classmethod
    def load(cls, path: Path) -> ExecutionWitness:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            window=payload["window"],
            sightings=frozenset(
                StatementSighting(unit=item["unit"].upper(), line=int(item["line"]))
                for item in payload.get("sightings", [])
            ),
            units=frozenset(unit.upper() for unit in payload.get("units", [])),
            unobservable=frozenset(
                StatementSighting(unit=item["unit"].upper(), line=int(item["line"]))
                for item in payload.get("unobservable", [])
            ),
            note=payload.get("note"),
        )

    def dump(self, path: Path) -> None:
        payload = {
            "window": self.window,
            "note": self.note,
            "units": sorted(self.units),
            "sightings": [
                {"unit": s.unit, "line": s.line}
                for s in sorted(self.sightings, key=lambda s: (s.unit, s.line))
            ],
            "unobservable": [
                {"unit": s.unit, "line": s.line}
                for s in sorted(self.unobservable, key=lambda s: (s.unit, s.line))
            ],
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
