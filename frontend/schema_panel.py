"""Sidebar schema sources for the Parse SQL view (MVP plan M2): no hand-written dictionary.

* **Live PostgreSQL:** pull the dictionary from a running database's catalogue (read-only).
* **Upload DDL:** build it from `CREATE TABLE` statements.

Each returns a `Dictionary`, or None until the user has supplied one. Temporary UI, like the
rest of `frontend/`: it imports the product code and renders it, nothing more.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from lineage.resolution.ddl import from_ddl
from lineage.resolution.dictionary import Dictionary
from lineage.resolution.postgres_connect import PgSettings, pull

BUNDLED = "bundled test dictionary"
LIVE = "live PostgreSQL"
DDL = "upload DDL"
INFER = "infer from the SQL"


def live_postgres() -> Dictionary | None:
    """Connection fields and a Pull button; the pulled dictionary lives in the session."""
    env = PgSettings.from_env("lineage_final")
    host = st.text_input("Host", value=env.host)
    port = st.number_input("Port", value=env.port, min_value=1, max_value=65535, step=1)
    database = st.text_input(
        "Database", value=env.database, help="lineage_final / lineage_batch: scripts/pg_seed.py"
    )
    user = st.text_input("User", value=env.user)
    password = st.text_input("Password", value=env.password, type="password")
    schemas = st.text_input(
        "Schemas (optional)", value="", help="Comma-separated. Blank: every user schema."
    )
    settings = PgSettings(host, int(port), user, password, database)
    key = (host, int(port), database, user, schemas)

    if st.button("Pull schema"):
        wanted = [s.strip() for s in schemas.split(",") if s.strip()] or None
        try:
            st.session_state["pg_pull"] = (key, pull(settings.conninfo, wanted))
        except Exception as error:  # a connection or catalogue error, shown not raised
            st.session_state.pop("pg_pull", None)
            st.error(f"Could not pull the schema: {str(error).splitlines()[0]}")
            return None

    pulled = st.session_state.get("pg_pull")
    if not pulled or pulled[0] != key:
        st.info("Press **Pull schema** to read the dictionary from this database.")
        return None
    book: Dictionary = pulled[1]
    st.caption(summary(book, f"pulled from {database}@{host}"))
    return book


def uploaded_ddl() -> Dictionary | None:
    """A DDL file and the schema an unqualified name means."""
    upload = st.file_uploader("DDL file (CREATE TABLE ...)", type=["sql", "ddl", "txt"])
    default_schema = st.text_input("Default schema", value="public")
    if upload is None:
        st.info("Upload a file of CREATE TABLE statements.")
        return None
    try:
        book = _ddl_book(upload.getvalue().decode("utf-8"), default_schema or "public")
    except ValueError as error:
        st.error(f"Could not read the DDL: {error}")
        return None
    st.caption(summary(book, f"built from {upload.name}"))
    return book


@st.cache_data(show_spinner=False)
def _ddl_book(text: str, default_schema: str) -> Dictionary:
    return from_ddl(text, default_schema)


def summary(book: Any, origin: str) -> str:
    schemas = sorted({info.owner for info in book.objects.values()})
    return (
        f"Dictionary {origin}: **{len(book.columns)} relations** in {', '.join(schemas)}; "
        f"unqualified names mean `{book.default_schema}`."
    )
