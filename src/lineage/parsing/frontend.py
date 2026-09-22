"""The procedural front end: what the analysis asks of a program's STRUCTURE.

ANTLR owns the program and SQLGlot owns the SQL inside it. That division has held since the
first commit, and it is the reason a second dialect is possible at all: band 0 needs nothing
from this module, because everything it does happens inside a statement.

Band 1 and band 2 are different. They ask the *program* questions - what is declared, which
cursor a loop runs over, what a FETCH writes into, where a RETURN is - and until A3 every one
of those questions was asked directly of a class in the generated Oracle grammar. Fourteen
context classes, reached by name from five modules, with the four-entry list of "what counts
as a unit" copied into four of them.

**This protocol is those questions, and nothing else.** It was written by enumerating what
the analysis modules actually call - not by imagining what a front end might offer. Every
method here has a caller today. A PostgreSQL front end is a checklist of this file.

WHAT STAYS OPAQUE. Contexts are `Any`. The analysis never inspects one; it hands it back to
the front end that produced it. That is what lets the Oracle implementation be the existing
ANTLR code wrapped, rather than moved, and it is what makes A3 a refactor that can be checked
against the signed measurement rather than a rewrite that has to be trusted.

WHAT IS DELIBERATELY NOT HERE YET. The control-flow shape - `statements_of`, if/elsif/else,
while/for - that `cfg.py` and `dynamic.py` walk. That is A3b, separated so that the
declaration half can be measured on its own first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class UnitRef:
    """One callable unit, as the analysis needs to address it.

    `kind` is the grammar's own word for it - the Oracle front end reports `procedure`,
    `function`, `packaged_procedure`, `packaged_function` - because whether a unit is
    packaged decides what state it can see. Note that Oracle's `procedure_body` rule also
    matches a procedure NESTED inside another unit, so such a unit is reported as
    `packaged_procedure` as well; that is pre-existing behaviour, carried over unchanged.
    `name` is NOT folded: folding is the caller's decision, made with the dialect in force,
    exactly as for every other identifier.
    """

    kind: str
    name: str
    line: int
    ctx: Any


@dataclass(frozen=True)
class Declared:
    """A parameter or a local variable, as declared."""

    name: str  # as written; the caller folds
    line: int
    has_default: bool = False


@dataclass(frozen=True)
class CursorDecl:
    """`CURSOR c IS SELECT ...` - the name and the query's source text."""

    name: str
    query: str
    line: int


@dataclass(frozen=True)
class LoopParam:
    """The header of a `FOR ... IN ...` loop, in whichever of its three forms it takes.

    Exactly one of `query`, `cursor` or `index` is set:

    * `FOR rec IN (SELECT ...)`  -> `record` and `query`
    * `FOR rec IN c`             -> `record` and `cursor`    (S4-02's form)
    * `FOR i IN 1 .. n`          -> `index`

    The distinction between the first two is the whole of stress finding S4-02: only the
    inline form was handled, so a named cursor registered no row source at all and
    `rec.field` bound to the TARGET table.
    """

    line: int
    record: str | None = None
    query: str | None = None
    cursor: str | None = None
    index: str | None = None


@dataclass(frozen=True)
class Assignment:
    """`target := expression`, both as source text."""

    target: str
    expression: str


@dataclass(frozen=True)
class Fetch:
    """`FETCH c INTO a, b` - the cursor and the receiving names, as written."""

    cursor: str
    targets: tuple[str, ...]


@runtime_checkable
class Frontend(Protocol):
    """Everything the declaration and def-use analyses ask of a parsed program.

    Each method takes the opaque context it is asked about and returns plain data. Names
    come back AS WRITTEN and the caller folds them: the front end does not know the
    dialect's folding rule, and giving it one would be a second place for that rule to live.
    """

    def parse(self, source: str) -> Any:
        """Parse one source file into a `Program`. Never raises on malformed input."""
        ...

    # ---- units --------------------------------------------------------------------

    def units(self, tree: Any) -> list[UnitRef]:
        """Every callable unit - standalone or packaged - in source order."""
        ...

    def package_bodies(self, tree: Any) -> list[tuple[str, Any]]:
        """`(package name, context)` for each package body, so packaged units can inherit
        its state."""
        ...

    def enclosing_package(self, ctx: Any) -> str | None:
        """The package body this context sits inside, as written, or None."""
        ...

    # ---- declarations, within one unit --------------------------------------------

    def parameters(self, unit_ctx: Any) -> list[Declared]:
        """Formal parameters of this unit - not of any unit nested inside it."""
        ...

    def variables(self, unit_ctx: Any) -> list[Declared]:
        """Local variable declarations of this unit - not of any unit nested inside it."""
        ...

    def cursors(self, unit_ctx: Any) -> list[CursorDecl]:
        """Declared cursors, with their query text."""
        ...

    def loop_params(self, unit_ctx: Any) -> list[LoopParam]:
        """Every `FOR ... IN` header in this unit, in any of its forms."""
        ...

    # ---- statements ---------------------------------------------------------------

    def assignment(self, statement_ctx: Any) -> Assignment | None:
        """If this statement is an assignment, its two halves as text."""
        ...

    def fetch(self, statement_ctx: Any) -> Fetch | None:
        """If this statement is a FETCH, its cursor and targets."""
        ...

    def returns(self, unit_ctx: Any) -> list[str]:
        """Source text of every RETURN expression in the unit."""
        ...

    def exception_handlers(self, tree: Any) -> list[Any]:
        """Every exception handler context, for the WHEN OTHERS THEN NULL check."""
        ...
