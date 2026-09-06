"""Recovering dynamic SQL from the query log (T2.9).

The premise: what the parser cannot construct, the database already executed and
recorded. `EXECUTE IMMEDIATE v_sql` is statically undecidable, but the materialised
statement is sitting in `V$SQL`.

**The hard part is attribution, not recovery.** Finding the statement is easy; tying it
back to the procedure that emitted it is the problem, and it is the one that fails
quietly. Four signals, in descending order of strength:

1. **MODULE / ACTION** — session attributes set by the code. Strongest by far, and
   commonly absent: the blind spot register warns that a scheduler invoking the procedure
   frequently leaves them unset, which removes this signal exactly when it matters.
2. **Timing** — correlate `FIRST_LOAD_TIME` against a known job window. Weak on a busy
   system where many things run in the same minute.
3. **Statement shape** — match the literal fragments visible in the source against the
   logged text. Survives when the session attributes do not.
4. **Parsing schema / user** — narrows the field, rarely identifies anything alone.

An attribution that rests only on signal 3 or 4 is evidence, not proof, and is reported
as such. A statement nothing attributes is counted as unattributed rather than assigned
to the most plausible candidate — a guessed emitter is exactly the confident wrong answer
this project exists to avoid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Signal(StrEnum):
    """How an attribution was made, strongest first."""

    MODULE_ACTION = "module/action"
    STATEMENT_SHAPE = "statement shape"
    TIMING = "timing"
    SCHEMA = "schema"
    NONE = "unattributed"


STRENGTH = {
    Signal.MODULE_ACTION: 4,
    Signal.STATEMENT_SHAPE: 3,
    Signal.TIMING: 2,
    Signal.SCHEMA: 1,
    Signal.NONE: 0,
}

# Fragments shorter than this match everything and identify nothing.
MIN_FRAGMENT = 8


@dataclass(frozen=True)
class LoggedStatement:
    """One row of the query log."""

    sql_id: str
    sql_text: str
    module: str | None
    action: str | None
    executions: int
    first_load_time: datetime | None
    parsing_schema: str | None

    @property
    def truncated(self) -> bool:
        """Oracle caps V$SQL statement text.

        A truncated statement can still be attributed but may not parse, so the edge is
        marked partial rather than dropped or trusted.
        """
        return len(self.sql_text) >= 1000


@dataclass(frozen=True)
class Attribution:
    statement: LoggedStatement
    unit: str | None
    signal: Signal
    detail: str = ""
    candidates: tuple[str, ...] = ()
    """Emitters the shape is consistent with. Evidence for a human, never a fact.

    Ranked by how many fragments matched, never by how plausible they read - the same
    rule the differential-diagnosis loop uses. A shortlist an SME can settle in five
    minutes is useful; a confident wrong emitter is not.
    """

    @property
    def attributed(self) -> bool:
        return self.unit is not None and self.signal is not Signal.NONE

    @property
    def high_confidence(self) -> bool:
        """Only MODULE/ACTION counts as high confidence.

        DEMOTED ON EVIDENCE, 2026-09-06. Statement shape was originally treated as high
        confidence. Measured against real logged statements with the session attributes
        stripped and every emitter competing, it attributed 2 of 5 to the WRONG procedure
        and identified none correctly.

        A wrong emitter is worse than no emitter: it is a confident, corroborated,
        entirely fictional attribution, and it would place a regulated write inside the
        wrong procedure. Shape is retained as a corroborating signal and as a lead to
        investigate, never as an attribution anyone can rely on.
        """
        return STRENGTH[self.signal] >= STRENGTH[Signal.MODULE_ACTION]


@dataclass
class Emitter:
    """A procedure that builds SQL at runtime."""

    unit: str
    fragments: list[str] = field(default_factory=list)
    module: str | None = None
    action: str | None = None


def literal_fragments(source: str) -> list[str]:
    """String literals in the source, long enough to identify a statement.

    These are what survives concatenation: `'UPDATE dim_customer SET ' || p_column`
    leaves `UPDATE dim_customer SET` in whatever finally executed.
    """
    fragments = []
    for raw in re.findall(r"'([^']*)'", source):
        text = " ".join(raw.split())
        if len(text) >= MIN_FRAGMENT:
            fragments.append(text.upper())
    return sorted(set(fragments), key=len, reverse=True)


def _discriminating(emitters: list[Emitter]) -> dict[str, list[str]]:
    """Fragments unique to one emitter.

    A literal that appears in two procedures cannot tell them apart, and using it anyway
    is how a statement gets attributed to the wrong one.
    """
    counts: dict[str, int] = {}
    for emitter in emitters:
        for fragment in set(emitter.fragments):
            counts[fragment] = counts.get(fragment, 0) + 1
    return {
        emitter.unit: [f for f in emitter.fragments if counts.get(f, 0) == 1]
        for emitter in emitters
    }


def attribute(
    statement: LoggedStatement,
    emitters: list[Emitter],
    window: dict[str, tuple[datetime, datetime]] | None = None,
) -> Attribution:
    """Tie one logged statement back to the procedure that emitted it."""
    text = " ".join(statement.sql_text.split()).upper()

    # 1. MODULE / ACTION. Set by the code, so unambiguous when present.
    for emitter in emitters:
        if (
            emitter.action
            and statement.action
            and emitter.action.upper() == statement.action.upper()
        ):
            return Attribution(statement, emitter.unit, Signal.MODULE_ACTION, statement.action)

    # 2. Statement shape produces CANDIDATES, never an attribution.
    #
    # MEASURED, 2026-09-06. Shape was originally allowed to attribute. Against real
    # logged statements with the session attributes stripped, it named the WRONG
    # procedure for 2 of 5 and the right one for none.
    #
    # The failure is not a weak heuristic that a better one would fix. b2_dynamic_constant
    # embeds a complete statement as a literal, and b2_dynamic_concatenated ASSEMBLES that
    # same text at runtime. The fragment is unique in the source and identical in the
    # output - so source-level distinctiveness cannot save it, and two procedures that can
    # emit the same SQL are indistinguishable by their SQL. That is common rather than
    # exotic.
    distinctive = _discriminating(emitters)
    scores = [(sum(1 for fragment in distinctive[e.unit] if fragment in text), e) for e in emitters]
    ranked = sorted(
        [(score, e) for score, e in scores if score >= 1],
        key=lambda pair: (-pair[0], pair[1].unit),
    )
    if ranked:
        return Attribution(
            statement,
            None,
            Signal.NONE,
            f"shape is consistent with {len(ranked)} emitter(s); needs confirmation",
            candidates=tuple(e.unit for _, e in ranked),
        )

    # 3. Timing. Weak: many things run in the same minute on a busy system.
    if window and statement.first_load_time is not None:
        for unit, (start, end) in window.items():
            if start <= statement.first_load_time <= end:
                return Attribution(statement, unit, Signal.TIMING, "within the job window")

    # 4. Nothing identified it. Counted, never guessed.
    return Attribution(statement, None, Signal.NONE, "no signal identified an emitter")


@dataclass
class RecoveryReport:
    """The number the sub-spike exists to produce."""

    attributions: list[Attribution] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.attributions)

    @property
    def high_confidence(self) -> int:
        return sum(1 for a in self.attributions if a.high_confidence)

    @property
    def rate(self) -> float | None:
        """Fraction attributable to an emitter with high confidence.

        Below roughly 60% the coverage story for compliance work gets uncomfortable.
        """
        return self.high_confidence / self.total if self.total else None

    def by_signal(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for attribution in self.attributions:
            counts[attribution.signal.value] = counts.get(attribution.signal.value, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def truncated(self) -> int:
        return sum(1 for a in self.attributions if a.statement.truncated)


def recover(
    statements: list[LoggedStatement],
    emitters: list[Emitter],
    window: dict[str, tuple[datetime, datetime]] | None = None,
) -> RecoveryReport:
    return RecoveryReport([attribute(s, emitters, window) for s in statements])
