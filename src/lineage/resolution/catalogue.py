"""Reading a data dictionary out of a live database.

The fourth and last part of the dialect seam (A4). The `Dictionary` MODEL is dialect-neutral
and stays where it is; what differs is where each piece comes from — Oracle reads `all_objects`
and its siblings, PostgreSQL reads `information_schema` and `pg_catalog`, MySQL reads
`information_schema` with different column names again.

**The assembly is shared and the queries are not.** `capture` below is the same for every
dialect: fold the schema name, ask the catalogue for six things, build one `Dictionary`. A
catalogue implementation is six small methods, each returning plain data.

TWO CONCEPTS THAT DO NOT SURVIVE THE PORT, and are handled by saying so rather than by
pretending:

* **Synonyms.** Neither PostgreSQL nor MySQL has them. `synonyms()` returns `{}` there, and
  `Dictionary.resolve` already treats an empty synonym map correctly - it simply never
  redirects. That is the honest answer: there is no redirection to miss, as opposed to
  redirection we failed to read.
* **Global temporary tables.** Oracle's `all_tables.temporary` flag has no exact analogue.
  PostgreSQL's temporary tables live in a per-session schema and are invisible to a
  catalogue snapshot taken from another session, so `temporary()` there reports what it can
  see and nothing more. The flag exists because a lineage path composed *through* a
  session-private relation across two procedures is invented rather than observed
  (`Dictionary.temporary`), so under-reporting it is the direction that loses edges rather
  than inventing them.

WHAT IS NOT HERE: connecting. A connection is dialect-specific plumbing - `oracledb` thin
mode, `psycopg`, `mysqlclient` - and the caller owns it, as `scripts/capture_dictionary.py`
does today. The catalogue is handed a cursor and asks it questions; it never opens anything.
Keeping that out means a catalogue can be tested against a fake cursor, which is the only way
any of this is testable without a database.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from lineage.dialects import fold
from lineage.resolution.dictionary import Dictionary, ObjectInfo, TriggerInfo


@runtime_checkable
class Catalogue(Protocol):
    """Where each part of a `Dictionary` comes from, for one dialect.

    Every method takes an open cursor and the folded schema name, and returns the piece of
    the dictionary it is responsible for. Keys are `OWNER.OBJECT`, in the database's own
    casing - the catalogue is reading a catalogue, so what it finds is already folded and
    must not be folded again.
    """

    def objects(self, cursor: Any, owner: str) -> dict[str, ObjectInfo]:
        """Every object the analysis may need to resolve a name to."""
        ...

    def synonyms(self, cursor: Any, owner: str) -> dict[str, str]:
        """`OWNER.SYNONYM -> OWNER.TARGET`. Empty where the dialect has no synonyms."""
        ...

    def columns(self, cursor: Any, owner: str) -> dict[str, list[str]]:
        """`OWNER.RELATION -> ordered column names`. Order matters: it is what makes
        `SELECT *` expansion and positional binding possible at all."""
        ...

    def view_text(self, cursor: Any, owner: str) -> dict[str, str]:
        """`OWNER.VIEW -> its defining SQL`, so lineage can reach the base tables."""
        ...

    def temporary(self, cursor: Any, owner: str) -> dict[str, bool]:
        """`OWNER.RELATION -> is this session-private`. See the module docstring."""
        ...

    def triggers(self, cursor: Any, owner: str) -> dict[str, TriggerInfo]:
        """`OWNER.TRIGGER -> the trigger and the object it fires on`.

        Captured from the catalogue rather than from source files because a trigger belongs
        to its TABLE, and the procedure that writes that table is usually in a different
        file with no mention of it.
        """
        ...


def capture(
    connection: Any,
    schema: str,
    captured_at: str,
    dialect: str = "oracle",
    catalogue: Catalogue | None = None,
) -> Dictionary:
    """Read the dictionary out of a live database.

    The schema name is FOLDED with the dialect's rule before anything else, because every
    key built below is qualified with it: Oracle's `LINEAGE` and PostgreSQL's `public` are
    the same kind of name and must be written the same way the catalogue writes them. This
    was the last `.upper()` left on the resolution path after A2, deferred to here precisely
    because the catalogue is what decides the casing of everything it returns.

    `catalogue` is injectable so this can be tested against a fake cursor without a
    database; the default is the one belonging to `dialect`.
    """
    from lineage.dialects import for_name

    resolved = catalogue if catalogue is not None else for_name(dialect).catalogue
    owner = fold(schema, dialect)

    with connection.cursor() as cursor:
        return Dictionary(
            captured_at=captured_at,
            default_schema=owner,
            dialect=dialect,
            objects=resolved.objects(cursor, owner),
            synonyms=resolved.synonyms(cursor, owner),
            columns=resolved.columns(cursor, owner),
            view_text=resolved.view_text(cursor, owner),
            temporary=resolved.temporary(cursor, owner),
            triggers=resolved.triggers(cursor, owner),
        )
