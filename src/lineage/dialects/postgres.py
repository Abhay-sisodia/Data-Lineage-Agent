"""PostgreSQL.

Band 0 in full; bands 1 and 2 not yet, and declared rather than silent.

The set-based analyser needs nothing from a dialect but its name (ADR-0002 §1), so column
lineage through projections, CTEs, joins, views, alias chains, windows and the transform
ladder works here on the day the dialect is registered. What is missing is the procedural
half, because a PL/pgSQL body is a dollar-quoted string rather than part of the parse tree
and this project has not yet chosen a grammar for it (ADR-0002 §5).

Until it does, `SqlScriptFrontend` finds statement boundaries lexically and every routine
body is REFUSED BY NAME - counted against parse coverage, never silently absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from lineage.parsing.frontend import Frontend
    from lineage.resolution.catalogue import Catalogue


class PostgresDialect:
    """PostgreSQL 13+.

    Folds unquoted identifiers DOWN, which is the opposite of Oracle and the reason the
    whole of A2 existed. `dialects.validate` checks that this agrees with SQLGlot's own
    normalisation for `postgres`, because a dialect whose folding disagrees with its parser
    binds nothing at all.
    """

    @property
    def name(self) -> str:
        return "postgres"

    @property
    def frontend(self) -> Frontend:
        from lineage.parsing.sqlscript import SqlScriptFrontend

        return SqlScriptFrontend(self.name)

    @property
    def catalogue(self) -> Catalogue:
        from lineage.resolution.postgres_catalogue import POSTGRES_CATALOGUE

        return POSTGRES_CATALOGUE

    def fold(self, identifier: str) -> str:
        return identifier.lower()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "PostgresDialect()"


POSTGRES = PostgresDialect()
