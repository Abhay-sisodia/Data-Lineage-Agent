"""Dialect registry.

One lookup, by name, with an explicit failure. A dialect the analyser has never been
measured against must not resolve to a default — analysing PostgreSQL source with Oracle's
folding rule would produce a full set of confident, wrong, unresolvable names rather than an
error, and this project's opening principle is that a plausible wrong answer costs more than
a refusal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lineage.dialects.base import Dialect
from lineage.dialects.oracle import ORACLE

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, type-only here
    from lineage.config import AnalysisConfig
    from lineage.resolution.dictionary import Dictionary

__all__ = [
    "SUPPORTED",
    "Dialect",
    "DialectContractError",
    "DialectMismatchError",
    "UnsupportedDialectError",
    "fold",
    "for_name",
    "register",
    "resolve_dialect",
    "validate",
]


class UnsupportedDialectError(ValueError):
    """Named dialect has no implementation. Raised rather than defaulted, on purpose."""


SUPPORTED: dict[str, Dialect] = {
    ORACLE.name: ORACLE,
}


class DialectContractError(ValueError):
    """A registered dialect breaks one of the two invariants `validate` checks."""


def validate(key: str, dialect: Dialect) -> None:
    """The two things a dialect cannot get wrong without failing silently.

    **1. The registry key IS the SQLGlot dialect name.** A1 stored the key on `Dictionary`
    and handed it straight to `sqlglot.parse_one(..., dialect=...)`. For Oracle the two
    coincide, so nothing distinguished them and the conflation went unnoticed until a test
    registered a dialect under a different key and every statement refused with *"Unknown
    dialect"*. One name, used everywhere, is simpler than two names that are equal by
    accident.

    **2. `fold` must agree with SQLGlot's own normalisation for that name.** These are not
    independent settings. `qualify` applies `normalize_identifiers`, which is keyed on the
    dialect name, so a dialect that folds one way while SQLGlot folds the other produces a
    dictionary keyed in one casing and a parse tree in the other. NOTHING BINDS, and the
    failure is total rather than partial: zero edges, no error, every relation reported as a
    dangling reference. Found exactly that way - by an instrument built with `name="oracle"`
    and `fold=lower`, which is not a dialect that can exist.

    Checked at registration rather than per call: it is a property of the implementation,
    and a wrong pairing should be impossible to install rather than merely detectable.
    """
    if key != dialect.name:
        raise DialectContractError(
            f"registry key {key!r} must equal the dialect's SQLGlot name {dialect.name!r} - "
            f"the key is what reaches sqlglot"
        )

    from sqlglot import exp
    from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

    for sample in ("Cust_Id", "ORDER_DATE", "net_amount"):
        normalised = normalize_identifiers(exp.to_identifier(sample), dialect=key).name
        if dialect.fold(sample) != normalised:
            raise DialectContractError(
                f"{key}: fold({sample!r}) is {dialect.fold(sample)!r} but SQLGlot normalises "
                f"it to {normalised!r} - the dictionary and the parse tree would disagree "
                f"about every name"
            )


def register(dialect: Dialect) -> None:
    """Install a dialect, refusing one that breaks the contract."""
    validate(dialect.name, dialect)
    SUPPORTED[dialect.name] = dialect


def for_name(name: str) -> Dialect:
    """The dialect implementation for a name from config or from a captured dictionary."""
    key = (name or "").strip().lower()
    dialect = SUPPORTED.get(key)
    if dialect is None:
        known = ", ".join(sorted(SUPPORTED)) or "none"
        raise UnsupportedDialectError(f"no implementation for dialect {name!r} (have: {known})")
    return dialect


def fold(identifier: str, dialect: str) -> str:
    """One unquoted identifier, as the named dialect's catalogue stores it.

    The call-site form of `Dialect.fold`, taking the dialect's NAME because that is what
    travels on a `Dictionary` and through the resolvers. A registry lookup per identifier is
    a dict get and a method call; the alternative is threading a resolved object through
    forty functions to save it.

    **WHY THIS REPLACES `.upper()` RATHER THAN SITTING BESIDE IT.** SQLGlot's
    `normalize_identifiers` — which `qualify` applies — already folds per dialect: a column
    read off a qualified Oracle tree is `CUST_ID` and off a PostgreSQL one is `cust_id`. So
    the 147 `.upper()` calls in this codebase are, at the post-qualify sites, a NO-OP on
    Oracle and would silently re-fold PostgreSQL names upward into something the catalogue
    does not contain.

    `fold` is used at the un-normalised sites too — ANTLR `getText()`, an unqualified
    `parse_one`, a regex scan over source text — because those really do arrive as written,
    and because `qualify` has fallback paths where it raised and the tree was never
    normalised at all. Folding is idempotent, so one rule covers both cases and no call site
    has to know which it is in.

    **Only identifiers.** A keyword, a flow name, a transform, a refusal code or a guard
    string is not a database identifier and must keep whatever normalisation it already has;
    routing those through a dialect would make the dialect responsible for things that do
    not vary by dialect.
    """
    return for_name(dialect).fold(identifier)


class DialectMismatchError(ValueError):
    """The configured dialect and the captured dictionary's dialect disagree."""


def resolve_dialect(config: AnalysisConfig, dictionary: Dictionary) -> Dialect:
    """The one dialect in force, checked against both places that name it.

    `AnalysisConfig.dialect` is the DECLARED authority — it is in `declared_limits()` and in
    the run fingerprint, so it is the value a signed measurement is answerable for. The
    dictionary's `dialect` is what the snapshot was actually taken from, and it is the copy
    that travels to the resolvers.

    **They are checked rather than reconciled, and the check is the reason the field earns
    its place.** Analysing Oracle source against a PostgreSQL catalogue would not crash: the
    folding rules differ, so every name would fail to bind and the run would report a large,
    plausible, entirely artificial set of dangling references. Under this project's own
    taxonomy that is the worst available outcome — a coverage gap that is really a
    configuration error, indistinguishable in the output from a real one.

    Raised rather than defaulted for the same reason `for_name` refuses an unknown name.
    """
    declared = (config.dialect or "").strip().lower()
    captured = (dictionary.dialect or "").strip().lower()
    if declared != captured:
        raise DialectMismatchError(
            f"config declares dialect {declared!r} but the dictionary was captured from "
            f"{captured!r} — every name would fail to bind and be reported as a coverage gap"
        )
    return for_name(declared)
