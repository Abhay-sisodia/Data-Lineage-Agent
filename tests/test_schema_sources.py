"""MVP plan M2: a dictionary without writing one by hand - from DDL, or from a live PostgreSQL.

The gate: a dictionary PULLED from a PostgreSQL holding the batch suite's DDL is identical
(same fingerprint) to the one BUILT from that DDL - so every score measured on the built
one holds for the pulled one. The live tests need `docker compose up -d postgres`; they
create and drop their own database, and never touch the seeded ones.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from lineage.resolution.catalogue import capture_schemas
from lineage.resolution.ddl import from_ddl
from lineage.resolution.dictionary import Dictionary, ObjectInfo

ROOT = Path(__file__).resolve().parent.parent
BATCH_DDL = ROOT / "stress" / "pg" / "schema.sql"
BATCH_BOOK = ROOT / "stress" / "pg" / "dictionary.json"
TEST_DATABASE = "lineage_test_m2"


def test_from_ddl_matches_the_committed_dictionary() -> None:
    built = from_ddl(BATCH_DDL.read_text(encoding="utf-8"), default_schema="core")
    assert built.fingerprint() == Dictionary.load(BATCH_BOOK).fingerprint()


def test_from_ddl_rejects_what_would_resolve_wrongly() -> None:
    with pytest.raises(ValueError, match="unqualified"):
        from_ddl("CREATE TABLE orders (id int);")
    with pytest.raises(ValueError, match="no columns"):
        from_ddl("CREATE TABLE s.empty ();")
    only_tables = from_ddl("CREATE TABLE s.t (a int, b text); CREATE VIEW s.v AS SELECT 1 AS x;")
    assert only_tables.columns == {"s.t": ["a", "b"]}


class _FakeCatalogue:
    """One table per schema, so a merge across schemas is visible."""

    def objects(self, cursor: Any, owner: str) -> dict[str, ObjectInfo]:
        return {f"{owner}.t": ObjectInfo(owner=owner, name="t", object_type="TABLE")}

    def synonyms(self, cursor: Any, owner: str) -> dict[str, str]:
        return {}

    def columns(self, cursor: Any, owner: str) -> dict[str, list[str]]:
        return {f"{owner}.t": ["id"]}

    def view_text(self, cursor: Any, owner: str) -> dict[str, str]:
        return {}

    def temporary(self, cursor: Any, owner: str) -> dict[str, bool]:
        return {f"{owner}.t": False}

    def triggers(self, cursor: Any, owner: str) -> dict[str, Any]:
        return {}


class _FakeConnection:
    def cursor(self) -> Any:
        import contextlib

        return contextlib.nullcontext(None)


def test_capture_schemas_merges_every_schema() -> None:
    book = capture_schemas(
        _FakeConnection(), ["LGX", "crm"], "Public", "2026-10-09", catalogue=_FakeCatalogue()
    )
    assert set(book.columns) == {"lgx.t", "crm.t"}  # folded to PostgreSQL's lower case
    assert book.default_schema == "public"


def _seed_module() -> Any:
    spec = importlib.util.spec_from_file_location("pg_seed", ROOT / "scripts" / "pg_seed.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def batch_database(pg_settings: Any) -> Any:
    """A throwaway database holding the batch DDL; dropped afterwards."""
    from lineage.resolution.postgres_connect import PgSettings, connect

    _seed_module().seed(TEST_DATABASE, BATCH_DDL, "core")
    yield PgSettings(
        pg_settings.host, pg_settings.port, pg_settings.user, pg_settings.password, TEST_DATABASE
    )
    with connect(pg_settings.conninfo, read_only=False) as connection:
        connection.autocommit = True
        connection.execute(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)')


@pytest.mark.requires_postgres
def test_pulled_dictionary_equals_the_ddl_built_one(batch_database: Any) -> None:
    from lineage.resolution.postgres_connect import pull

    pulled = pull(batch_database.conninfo)  # schemas and default schema read from the DB
    assert pulled.default_schema == "core"
    assert pulled.fingerprint() == Dictionary.load(BATCH_BOOK).fingerprint()


@pytest.mark.requires_postgres
def test_pull_can_be_limited_to_named_schemas(batch_database: Any) -> None:
    from lineage.resolution.postgres_connect import pull

    pulled = pull(batch_database.conninfo, schemas=["mart"])
    assert {info.owner for info in pulled.objects.values()} == {"mart"}


@pytest.mark.requires_postgres
def test_the_pull_session_is_read_only(batch_database: Any) -> None:
    import psycopg

    from lineage.resolution.postgres_connect import connect

    with (
        connect(batch_database.conninfo) as connection,
        pytest.raises(psycopg.errors.ReadOnlySqlTransaction),
    ):
        connection.execute("CREATE TABLE core.must_not_exist (id int)")
