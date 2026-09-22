"""The dialect seam.

Phase 0 was measured on Oracle and every dialect-specific fact was written where it was
needed: `DIALECT = "oracle"` in five modules, `.upper()` at 147 sites, fourteen ANTLR context
class names, six `all_tab_columns`-family queries. This is step A1 of narrowing that to one
place, and its only real requirement is that **the signed measurement does not move** — which
the stress grids and phase 0 assert, not this file.

What this file pins is the seam's own contract: one lookup, explicit failure, and a check that
the two places naming a dialect agree.
"""

from __future__ import annotations

import pytest

from lineage.config import AnalysisConfig
from lineage.dialects import (
    SUPPORTED,
    DialectContractError,
    DialectMismatchError,
    UnsupportedDialectError,
    for_name,
    resolve_dialect,
    validate,
)
from lineage.dialects.base import Dialect
from lineage.dialects.oracle import ORACLE
from lineage.resolution.dictionary import Dictionary


def _dictionary(dialect: str = "oracle") -> Dictionary:
    return Dictionary(captured_at="2026-09-14T00:00:00Z", default_schema="LINEAGE", dialect=dialect)


def test_oracle_resolves_and_satisfies_the_protocol() -> None:
    """`Dialect` is a runtime-checkable Protocol, so this is a real assertion.

    Structural typing rather than inheritance, because the Oracle implementation is
    assembled from modules that predate the seam - requiring a base class would mean moving
    code for the sake of a type, which is the opposite of introducing a seam safely.
    """
    assert for_name("oracle") is ORACLE
    assert isinstance(ORACLE, Dialect)


def test_the_name_is_the_sqlglot_dialect_name() -> None:
    """The whole of band 0 needs this and nothing else from the seam.

    71 SQLGlot references in `band0.py` and zero references to the Oracle grammar: the
    set-based analyser is already dialect-agnostic apart from this string.
    """
    assert ORACLE.name == "oracle"


def test_oracle_folds_identifiers_upward() -> None:
    """Oracle folds unquoted identifiers to upper case.

    This is why the entire codebase upper-cases names, and why `fold` has to exist before a
    dialect that folds DOWNWARD can be added. PostgreSQL folds to lower; MySQL preserves.
    """
    assert ORACLE.fold("cust_id") == "CUST_ID"
    assert ORACLE.fold("CUST_ID") == "CUST_ID"


def test_an_unknown_dialect_raises_rather_than_defaulting() -> None:
    """A dialect never measured must not silently resolve to Oracle.

    Defaulting would fold PostgreSQL identifiers upward and report every name as a dangling
    reference: a large, plausible coverage gap that is really a configuration error. This
    project's opening principle is that a confident wrong answer costs more than a refusal.
    """
    with pytest.raises(UnsupportedDialectError):
        for_name("duckdb")
    with pytest.raises(UnsupportedDialectError):
        for_name("")


def test_the_registry_holds_only_what_has_been_measured() -> None:
    """Pinned so a dialect cannot be added without this file being read.

    A name in `SUPPORTED` is a claim that the analyser has been measured against it.

    PostgreSQL was added on 2026-09-23 with band 0 only: the set-based analyser needs
    nothing from a dialect but its name (ADR-0002 section 1), while bands 1 and 2 need a
    PL/pgSQL grammar that has not been chosen. Its routine bodies are refused by name, so
    the gap is counted rather than silent.
    """
    assert set(SUPPORTED) == {"oracle", "postgres"}


def test_config_and_dictionary_must_agree() -> None:
    """The check that makes `Dictionary.dialect` a safety property, not a convenience.

    Oracle source against a PostgreSQL catalogue does not crash - the folding rules differ,
    so every name fails to bind and the run reports a large set of dangling references that
    is indistinguishable in the output from a real coverage gap.
    """
    assert resolve_dialect(AnalysisConfig(), _dictionary("oracle")) is ORACLE

    with pytest.raises(DialectMismatchError):
        resolve_dialect(AnalysisConfig(dialect="postgres"), _dictionary("oracle"))


def test_the_registry_key_must_be_the_sqlglot_dialect_name() -> None:
    """The first invariant, learned the hard way.

    A1 stored the registry key on `Dictionary` and handed it straight to
    `sqlglot.parse_one(dialect=...)`. For Oracle the key and the SQLGlot name coincide, so
    nothing distinguished them - until a test registered a dialect under a different key and
    every statement refused with *"Unknown dialect"*. One name, used everywhere.
    """

    class Misnamed:
        @property
        def name(self) -> str:
            return "postgres"

        def fold(self, identifier: str) -> str:
            return identifier.lower()

    with pytest.raises(DialectContractError, match="must equal"):
        validate("pg", Misnamed())  # type: ignore[arg-type]


def test_fold_must_agree_with_sqlglots_own_normalisation() -> None:
    """The second invariant, and the more dangerous of the two.

    `qualify` applies `normalize_identifiers`, which is keyed on the dialect name. A dialect
    that folds one way while SQLGlot folds the other keys the dictionary in one casing and
    the parse tree in the other: NOTHING binds, no error is raised, and every relation is
    reported as a dangling reference. The failure is total rather than partial, which is what
    makes it hard to spot - a smaller mistake would show up as a few missing edges.

    Found by building exactly this: `name="oracle"` with `fold=lower`, which is not a dialect
    that can exist. The analysis produced zero edges and the "nothing escaped folding"
    assertion passed over the wreckage.
    """

    class Incoherent:
        @property
        def name(self) -> str:
            return "oracle"  # SQLGlot normalises this UP

        def fold(self, identifier: str) -> str:
            return identifier.lower()  # ... and this folds DOWN

    with pytest.raises(DialectContractError, match="SQLGlot normalises"):
        validate("oracle", Incoherent())  # type: ignore[arg-type]


def test_oracle_satisfies_its_own_contract() -> None:
    """The shipped dialect passes the checks the registry applies to newcomers."""
    validate(ORACLE.name, ORACLE)


def test_a_dictionary_captured_before_the_field_existed_is_oracle() -> None:
    """Backward compatibility, asserted rather than assumed.

    Every dictionary JSON on disk predates this field, including the corpus one the signed
    measurement is taken against. If the default were anything else, phase 0 would fail to
    resolve on the first run after this change.
    """
    loaded = Dictionary.model_validate(
        {"captured_at": "2026-09-06T18:52:28+00:00", "default_schema": "LINEAGE"}
    )
    assert loaded.dialect == "oracle"
