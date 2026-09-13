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
    "DialectMismatchError",
    "UnsupportedDialectError",
    "for_name",
    "resolve_dialect",
]


class UnsupportedDialectError(ValueError):
    """Named dialect has no implementation. Raised rather than defaulted, on purpose."""


SUPPORTED: dict[str, Dialect] = {
    ORACLE.name: ORACLE,
}


def for_name(name: str) -> Dialect:
    """The dialect implementation for a name from config or from a captured dictionary."""
    key = (name or "").strip().lower()
    dialect = SUPPORTED.get(key)
    if dialect is None:
        known = ", ".join(sorted(SUPPORTED)) or "none"
        raise UnsupportedDialectError(f"no implementation for dialect {name!r} (have: {known})")
    return dialect


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
