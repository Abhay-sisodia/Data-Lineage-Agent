"""Reading a dictionary out of a database, without a database.

A4 moved six `all_*` queries behind `resolution.catalogue.Catalogue`. The honest check on
that move is `test_committed_dictionary_matches_the_live_database`, which fingerprints a
live capture against the committed snapshot - but it needs Oracle running, and a refactor
that can only be verified when a container is up is a refactor that will be verified rarely.

So the assembly is tested here against a FAKE CURSOR replaying canned rows. That checks what
this step actually changed: that the six pieces are asked for, that they land in the right
fields, and that the schema name is folded with the dialect's rule. **It cannot check that
the SQL is valid Oracle** - only a live database can, and nothing here pretends otherwise.
"""

from __future__ import annotations

from typing import Any

import pytest

from lineage.resolution.catalogue import Catalogue, capture
from lineage.resolution.dictionary import ObjectInfo, TriggerInfo
from lineage.resolution.oracle_catalogue import ORACLE_CATALOGUE

ROWS: dict[str, list[tuple[Any, ...]]] = {
    "all_objects": [
        ("LINEAGE", "STG_ORDERS", "TABLE"),
        ("LINEAGE", "V_RECENT", "VIEW"),
        ("LINEAGE", "CUSTOMER_TARGET", "SYNONYM"),
    ],
    "all_synonyms": [("LINEAGE", "CUSTOMER_TARGET", "LINEAGE", "DIM_CUSTOMER")],
    "all_tab_columns": [
        ("LINEAGE", "STG_ORDERS", "ORDER_ID"),
        ("LINEAGE", "STG_ORDERS", "CUST_ID"),
        ("LINEAGE", "STG_ORDERS", "GROSS_AMOUNT"),
    ],
    "all_views": [("LINEAGE", "V_RECENT", "SELECT cust_id FROM stg_orders")],
    "all_tables": [("LINEAGE", "STG_ORDERS", "N"), ("LINEAGE", "GTT_STAGE", "Y")],
    "all_triggers": [
        (
            "LINEAGE",
            "TRG_AUDIT",
            "LINEAGE",
            "DIM_CUSTOMER",
            "AFTER EACH ROW",
            "INSERT OR UPDATE",
            "TABLE",
            "BEGIN NULL; END;",
        )
    ],
}


class _FakeCursor:
    """Replays canned rows, keyed by which catalogue view the SQL names."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, **binds: Any) -> None:
        lowered = " ".join(sql.split()).lower()
        for view, rows in ROWS.items():
            if f"from {view}" in lowered:
                self.seen.append(view)
                self._rows = rows
                # Every Oracle query in this catalogue is scoped to one owner. A query
                # that forgot its bind would silently capture the whole database.
                assert binds.get("owner") == "LINEAGE", f"{view} was not scoped to an owner"
                return
        raise AssertionError(f"unexpected query: {lowered[:80]}")

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_object = _FakeCursor()

    def cursor(self) -> _FakeCursor:
        return self.cursor_object


def test_the_oracle_catalogue_satisfies_the_protocol() -> None:
    assert isinstance(ORACLE_CATALOGUE, Catalogue)


def test_capture_assembles_every_part_of_the_dictionary() -> None:
    """All six pieces are asked for, and each lands in its own field.

    Pinned as a set because a catalogue that silently skipped one - say, triggers - would
    still produce a perfectly valid `Dictionary`, just one with no band-2 lineage in it.
    """
    connection = _FakeConnection()
    dictionary = capture(connection, schema="lineage", captured_at="2026-09-22T00:00:00Z")

    assert set(connection.cursor_object.seen) == set(ROWS)

    assert dictionary.objects["LINEAGE.STG_ORDERS"] == ObjectInfo(
        owner="LINEAGE", name="STG_ORDERS", object_type="TABLE"
    )
    assert dictionary.synonyms == {"LINEAGE.CUSTOMER_TARGET": "LINEAGE.DIM_CUSTOMER"}
    assert dictionary.columns["LINEAGE.STG_ORDERS"] == ["ORDER_ID", "CUST_ID", "GROSS_AMOUNT"]
    assert dictionary.view_text["LINEAGE.V_RECENT"] == "SELECT cust_id FROM stg_orders"
    assert dictionary.temporary == {"LINEAGE.STG_ORDERS": False, "LINEAGE.GTT_STAGE": True}
    assert dictionary.triggers["LINEAGE.TRG_AUDIT"] == TriggerInfo(
        owner="LINEAGE",
        name="TRG_AUDIT",
        table="LINEAGE.DIM_CUSTOMER",
        timing="AFTER EACH ROW",
        event="INSERT OR UPDATE",
        body="BEGIN NULL; END;",
        base_object_type="TABLE",
    )


def test_the_schema_name_is_folded_with_the_dialects_rule() -> None:
    """`schema="lineage"` becomes `LINEAGE`, because every key is qualified with it.

    This was the last `.upper()` left on the resolution path after A2, deferred to A4
    precisely because the catalogue is what decides the casing of everything it returns.
    A lower-folding dialect would keep it lower, and its keys would match.
    """
    dictionary = capture(
        _FakeConnection(), schema="lineage", captured_at="2026-09-22T00:00:00Z"
    )
    assert dictionary.default_schema == "LINEAGE"
    assert dictionary.resolve("stg_orders").qualified == "LINEAGE.STG_ORDERS"


def test_the_captured_dialect_is_recorded_on_the_snapshot() -> None:
    """A dictionary belongs to one database, so it says which. `resolve_dialect` checks
    this against the configured dialect at every entry point - the check that stops Oracle
    source being analysed against a PostgreSQL catalogue and reported as a coverage gap."""
    dictionary = capture(
        _FakeConnection(), schema="lineage", captured_at="2026-09-22T00:00:00Z"
    )
    assert dictionary.dialect == "oracle"


def test_column_order_survives_capture() -> None:
    """Order is not cosmetic: positional binding and `SELECT *` expansion both depend on
    it, and a dict that lost it would produce confident, wrongly-ordered edges."""
    dictionary = capture(
        _FakeConnection(), schema="LINEAGE", captured_at="2026-09-22T00:00:00Z"
    )
    assert dictionary.columns_of("stg_orders") == ["ORDER_ID", "CUST_ID", "GROSS_AMOUNT"]


def test_a_dialect_without_synonyms_captures_none_rather_than_failing() -> None:
    """Neither PostgreSQL nor MySQL has synonyms, so their catalogues return `{}`.

    `resolve` then simply never redirects - which is the honest answer, and different from
    "there is redirection here we failed to read". Asserted with a stub because the shape
    of that answer is a decision this seam makes, not an accident of implementation.

    The stub returns keys in the folding of the dialect it is captured under. The first
    draft returned PostgreSQL-shaped lower-case keys while capturing as Oracle, and nothing
    resolved - the same incoherent-dialect mistake A2 made with `name="oracle", fold=lower`,
    in miniature. Casing is tested by `test_the_schema_name_is_folded_with_the_dialects_rule`;
    this one is about synonyms.
    """

    class _NoSynonyms:
        def objects(self, cursor: Any, owner: str) -> dict[str, ObjectInfo]:
            return {
                f"{owner}.ORDERS": ObjectInfo(owner=owner, name="ORDERS", object_type="TABLE")
            }

        def synonyms(self, cursor: Any, owner: str) -> dict[str, str]:
            return {}

        def columns(self, cursor: Any, owner: str) -> dict[str, list[str]]:
            return {f"{owner}.ORDERS": ["ORDER_ID"]}

        def view_text(self, cursor: Any, owner: str) -> dict[str, str]:
            return {}

        def temporary(self, cursor: Any, owner: str) -> dict[str, bool]:
            return {f"{owner}.ORDERS": False}

        def triggers(self, cursor: Any, owner: str) -> dict[str, TriggerInfo]:
            return {}

    dictionary = capture(
        _FakeConnection(),
        schema="public",
        captured_at="2026-09-22T00:00:00Z",
        catalogue=_NoSynonyms(),  # type: ignore[arg-type]
    )
    assert dictionary.synonyms == {}
    # And resolution still works, it simply never redirects: `via` records the schema
    # binding and nothing else. An `or True` crept into this assertion on the first draft,
    # which would have made it pass whatever the code did - the third vacuous check of this
    # port, and the reason every one of them now has something that can fail beside it.
    resolved = dictionary.resolve("orders")
    assert resolved.qualified == "PUBLIC.ORDERS"
    assert not any("synonym" in step for step in resolved.via)


@pytest.mark.parametrize("method", ["objects", "synonyms", "columns", "view_text", "temporary"])
def test_every_oracle_query_is_scoped_to_one_owner(method: str) -> None:
    """A query that forgot its `:owner` bind would capture the whole database and look
    like an unusually thorough capture. The fake cursor asserts the bind; this drives each
    method through it individually so a single missing bind cannot hide behind the others.
    """
    cursor = _FakeCursor()
    getattr(ORACLE_CATALOGUE, method)(cursor, "LINEAGE")
    assert cursor.seen
