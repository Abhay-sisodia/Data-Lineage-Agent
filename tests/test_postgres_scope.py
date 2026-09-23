"""The PostgreSQL scope contract, pinned.

`docs/postgres_scope.md` states a boundary: inside it nothing fails and nothing is refused;
outside it everything is refused or declared, never silent. `scripts/probe_postgres.py`
measures that boundary construct by construct. This file runs the probe's own construct list
as a test, so the contract cannot drift without a failure.

**Silence is the single thing this asserts hardest.** A statement skipped before it is
counted is invisible in parse coverage - a table can acquire its entire contents and be
reported as having no writer, which reads as a finding rather than as a gap. Whether a
construct is in scope is a decision; whether it is silent is not.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from probe_postgres import (  # noqa: E402
    CONSTRUCTS,
    IN_SCOPE,
    NO_LINEAGE,
    OUT_OF_SCOPE,
    bucket,
    dictionary,
)


@pytest.fixture(scope="module")
def book():
    return dictionary()


ALL = [pytest.param(expectation, name, sql, id=name) for expectation, name, sql in CONSTRUCTS]


@pytest.mark.parametrize(("expectation", "name", "sql"), ALL)
def test_every_construct_lands_where_the_scope_says(
    expectation: str, name: str, sql: str, book
) -> None:
    """One test per construct, so a failure names the construct rather than a count."""
    verdict, detail = bucket(sql.strip(), book)

    if expectation == IN_SCOPE:
        assert verdict == "EDGES", f"{name}: in scope but {verdict} ({detail})"
    elif expectation == NO_LINEAGE:
        assert verdict == "NO-SOURCE", f"{name}: expected counted-and-empty, got {verdict}"
    else:
        assert verdict in ("REFUSED", "DECLARED"), (
            f"{name}: out of scope and {verdict} - out of scope must still be declared"
        )


def test_nothing_is_silent_and_nothing_crashes(book) -> None:
    """The assertion that holds whether or not the scope itself is right.

    A construct being out of scope is a decision and can be revisited. A construct being
    SILENT is never acceptable: it was not counted, so no number anywhere records that the
    analyser saw it and did nothing.
    """
    bad = []
    for _expectation, name, sql in CONSTRUCTS:
        verdict, detail = bucket(sql.strip(), book)
        if verdict in ("SILENT", "CRASH"):
            bad.append(f"{name}: {verdict} ({detail})")

    assert bad == [], "constructs fell through unowned:\n  " + "\n  ".join(bad)


def test_the_scope_has_not_quietly_shrunk(book) -> None:
    """Counts pinned, so a construct cannot slip from EDGES to REFUSED unnoticed.

    A refusal is honest, which makes it the easy place to retreat to when something breaks.
    Pinning the numbers means retreating has to be deliberate and has to edit this line.
    """
    counts: dict[str, int] = {}
    for _expectation, _name, sql in CONSTRUCTS:
        verdict, _detail = bucket(sql.strip(), book)
        counts[verdict] = counts.get(verdict, 0) + 1

    assert counts == {"EDGES": 34, "NO-SOURCE": 2, "REFUSED": 6, "DECLARED": 1}


def test_out_of_scope_refusals_are_postgresql_only(book) -> None:
    """Oracle must not inherit a PostgreSQL refusal.

    `CREATE TRIGGER` is FULLY HANDLED in Oracle, through the dictionary. Refusing it for
    both dialects would be false about Oracle and would move a signed measurement - which
    is why `Construct` carries `dialects` at all.
    """
    from lineage.analysis.refusal import classify_statement

    postgres_only = [
        "DO $$ BEGIN NULL; END; $$;",
        "COPY t (a) FROM '/tmp/x.csv';",
        "CREATE TRIGGER trg AFTER INSERT ON t FOR EACH ROW EXECUTE FUNCTION f();",
        "TRUNCATE TABLE t;",
    ]
    for sql in postgres_only:
        assert classify_statement(sql, "postgres") is not None, sql
        assert classify_statement(sql, "oracle") is None, f"Oracle wrongly refuses: {sql}"
