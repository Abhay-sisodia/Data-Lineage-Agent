"""Spark SQL and Databricks (finding PO-0).

A Databricks model used to be analysed through the PostgreSQL path, the nearest the analyser
had, and nothing said so. It mostly worked, because PostgreSQL's reader tolerates most Spark
spellings, and that was the problem: what it did not tolerate came out as a confident wrong
answer rather than a refusal. `COLLECT_SET` arrived as an unknown function and lost its
aggregation (PO-1); `SIZE` was "a routine whose source is not in this analysis".

Here SQLGlot's own `spark` / `databricks` reader parses the model, so every Spark function
arrives typed - `COLLECT_SET` as an aggregate, `SIZE` as array size, `date_format`,
`TRY_CAST`, `DATEDIFF` as what they are - and band 0 analyses the tree exactly as it does
any other. Nothing in band 0 is Spark-specific; the PostgreSQL-only restatements (psql,
PL/pgSQL, the grammar restatements, SRFs in FROM, system columns) are gated on
`postgres` and do not run.

Two names, one implementation: `databricks` is Spark SQL plus the Databricks extensions,
and the registry key must be the SQLGlot dialect name (`dialects.validate`).

**What is not here.** No procedural front end (a notebook's Python is not SQL - the
consolidated model is what is analysed), and no live catalogue: a Spark dictionary comes
from DDL or JSON, never from a Unity Catalog capture, which raises rather than pretending.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from lineage.parsing.frontend import Frontend
    from lineage.resolution.catalogue import Catalogue


class SparkCatalogueUnavailableError(NotImplementedError):
    """There is no live Spark / Unity Catalog capture; supply the dictionary instead."""


class _NoCatalogue:
    """Refuses every capture by name, so a Spark run can never fall back to another catalogue."""

    def __getattr__(self, name: str) -> Any:
        raise SparkCatalogueUnavailableError(
            "no live catalogue capture for Spark / Databricks - build the dictionary from "
            "DDL or JSON (finding PO-0)"
        )


class SparkDialect:
    """Spark SQL, or Databricks SQL. Folds unquoted identifiers DOWN, as SQLGlot does."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def frontend(self) -> Frontend:
        from lineage.parsing.sqlscript import SqlScriptFrontend

        return SqlScriptFrontend(self.name)

    @property
    def catalogue(self) -> Catalogue:
        return _NoCatalogue()  # type: ignore[return-value]

    def fold(self, identifier: str) -> str:
        return identifier.lower()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return f"SparkDialect({self._name!r})"


SPARK = SparkDialect("spark")
DATABRICKS = SparkDialect("databricks")
