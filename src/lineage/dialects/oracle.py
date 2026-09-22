"""Oracle — the dialect phase 0 was measured on.

Nothing here is new behaviour. Every value is the constant it replaces, moved from where it
was written to where a second dialect can be written beside it. The signed phase-0
measurement must not move by one edge when this lands, and that is the only test of this
file that matters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from lineage.parsing.frontend import Frontend


class OracleDialect:
    """Oracle Database, 21c and compatible.

    Folds unquoted identifiers to UPPER case, which is why this whole codebase upper-cases
    names and why `fold` has to exist before anything else can be supported.
    """

    @property
    def name(self) -> str:
        return "oracle"

    @property
    def frontend(self) -> Frontend:
        # Imported here rather than at module level: `parsing.plsql` loads the generated
        # ANTLR parser, which is heavy and which `dialects` must not require just to be
        # imported - the registry is consulted by code that never parses anything.
        from lineage.parsing.plsql import ORACLE_FRONTEND

        return ORACLE_FRONTEND

    def fold(self, identifier: str) -> str:
        return identifier.upper()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "OracleDialect()"


ORACLE = OracleDialect()
