"""PostgreSQL's catalogue, read through `information_schema` and `pg_catalog`.

The Oracle catalogue reads six ALL_* views. This reads the same six facts from a database
that organises them differently, and two of the six do not exist at all — which is recorded
here rather than papered over.

**Bind style.** Oracle's driver takes named binds (`:owner`); psycopg takes `%s`
positionally. That difference lives in each catalogue's own queries and never reaches the
seam, which is the reason `Catalogue` is handed a cursor rather than a connection.

**`information_schema` versus `pg_catalog`.** `information_schema` is the standard and is
used wherever it suffices. It does not expose view definitions usefully for our purpose
(`view_definition` is NULL unless you own the view), nor trigger bodies, nor temporariness,
so those three read `pg_catalog` directly. Mixing the two is deliberate: the standard view
where it works, the catalogue where it does not.

**Unlogged and temporary.** `pg_class.relpersistence` is `p` permanent, `u` unlogged, `t`
temporary. Only `t` is session-private, and only session-private matters here — an unlogged
table is shared, it just is not crash-safe, so composing lineage through it is observation
rather than invention.
"""

from __future__ import annotations

from typing import Any

from lineage.resolution.dictionary import ObjectInfo, TriggerInfo

# `relkind` values worth resolving a name to, and what to call them. Deliberately not
# every kind: an index or a toast table is never the target of lineage, and reporting one
# would put a name in the dictionary that no statement can legitimately reference.
_RELKINDS = {
    "r": "TABLE",
    "p": "TABLE",  # partitioned parent - a table as far as lineage is concerned
    "f": "TABLE",  # foreign table
    "v": "VIEW",
    "m": "MATERIALIZED VIEW",
    "S": "SEQUENCE",
}


class PostgresCatalogue:
    """`resolution.catalogue.Catalogue` for PostgreSQL."""

    def objects(self, cursor: Any, owner: str) -> dict[str, ObjectInfo]:
        """Relations, plus routines, which live in a different catalogue entirely."""
        cursor.execute(
            """
            SELECT n.nspname, c.relname, c.relkind
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s
               AND c.relkind = ANY(%s)
            """,
            (owner, list(_RELKINDS)),
        )
        found = {
            f"{schema}.{name}": ObjectInfo(
                owner=schema, name=name, object_type=_RELKINDS[relkind]
            )
            for schema, name, relkind in cursor.fetchall()
        }

        # Routines are objects too: interprocedural summarisation resolves a callee by
        # name, and a name absent from the dictionary is a dangling reference rather than
        # a call we chose not to follow.
        cursor.execute(
            """
            SELECT n.nspname, p.proname, p.prokind
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = %s
               AND p.prokind IN ('f', 'p')
            """,
            (owner,),
        )
        for schema, name, prokind in cursor.fetchall():
            found[f"{schema}.{name}"] = ObjectInfo(
                owner=schema,
                name=name,
                object_type="PROCEDURE" if prokind == "p" else "FUNCTION",
            )
        return found

    def synonyms(self, cursor: Any, owner: str) -> dict[str, str]:
        """**PostgreSQL has no synonyms.**

        Empty, and that is the honest answer rather than a gap: `Dictionary.resolve` simply
        never redirects, which is correct because there is no redirection to miss. The
        nearest analogues - a view over another table, or `search_path` - are handled
        elsewhere and by different machinery: a view IS resolved, through `view_text`, and
        `search_path` is the schema-context silent failure (s2) that the dictionary's
        explicit `default_schema` exists to make visible.
        """
        return {}

    def columns(self, cursor: Any, owner: str) -> dict[str, list[str]]:
        cursor.execute(
            """
            SELECT table_schema, table_name, column_name
              FROM information_schema.columns
             WHERE table_schema = %s
             ORDER BY table_schema, table_name, ordinal_position
            """,
            (owner,),
        )
        found: dict[str, list[str]] = {}
        for schema, table, column in cursor.fetchall():
            found.setdefault(f"{schema}.{table}", []).append(column)
        return found

    def view_text(self, cursor: Any, owner: str) -> dict[str, str]:
        """Definitions for views AND materialised views.

        `pg_get_viewdef` rather than `information_schema.views.view_definition`, which is
        NULL for a view the caller does not own - a capture running as a reader would
        silently lose every view definition and report the views themselves as sources.
        That is the silent-failure shape this module's docstring is about.
        """
        cursor.execute(
            """
            SELECT n.nspname, c.relname, pg_get_viewdef(c.oid, true)
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s
               AND c.relkind IN ('v', 'm')
            """,
            (owner,),
        )
        return {
            f"{schema}.{name}": str(definition) if definition is not None else ""
            for schema, name, definition in cursor.fetchall()
        }

    def temporary(self, cursor: Any, owner: str) -> dict[str, bool]:
        """Session-private relations only.

        **A capture cannot see another session's temporary tables at all** - they live in a
        per-backend `pg_temp_N` schema. So this reports what is visible and no more, which
        under-reports temporariness. That direction is the safe one: the flag exists to stop
        lineage being composed THROUGH session-private state, so a missed flag costs a
        refusal we should have made rather than inventing a path. Worth knowing when reading
        a PostgreSQL coverage statement.
        """
        cursor.execute(
            """
            SELECT n.nspname, c.relname, c.relpersistence
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s
               AND c.relkind IN ('r', 'p', 'f')
            """,
            (owner,),
        )
        return {
            f"{schema}.{name}": persistence == "t"
            for schema, name, persistence in cursor.fetchall()
        }

    def triggers(self, cursor: Any, owner: str) -> dict[str, TriggerInfo]:
        """Triggers, with the function body they execute.

        **PostgreSQL's trigger model is structurally different from Oracle's and this is
        where it shows.** An Oracle trigger owns its body; a PostgreSQL trigger names a
        FUNCTION that owns it, so the body is a second lookup - joined here so the rest of
        the analyser sees the same `TriggerInfo` shape either way.

        `tgtype` is a bitmask: bit 0 is row-level, bit 1 is BEFORE, bits 2/3/4 are
        INSERT/DELETE/UPDATE, bit 6 is INSTEAD OF. Decoded into the same words
        `ALL_TRIGGERS` reports, because `TriggerInfo.fires_on` matches on those words and
        `INSTEAD OF` is the timing that changes the analysis.

        Internal constraint triggers (`tgisinternal`) are excluded: they implement foreign
        keys and are not user lineage.
        """
        cursor.execute(
            """
            SELECT n.nspname,
                   t.tgname,
                   tn.nspname,
                   c.relname,
                   t.tgtype,
                   p.prosrc
              FROM pg_trigger t
              JOIN pg_class c   ON c.oid = t.tgrelid
              JOIN pg_namespace tn ON tn.oid = c.relnamespace
              JOIN pg_proc p    ON p.oid = t.tgfoid
              JOIN pg_namespace n  ON n.oid = p.pronamespace
             WHERE tn.nspname = %s
               AND NOT t.tgisinternal
               AND t.tgenabled <> 'D'
            """,
            (owner,),
        )
        found: dict[str, TriggerInfo] = {}
        # The function's own schema is not used: a trigger is keyed and owned by the TABLE
        # it fires on, which is the inheritance rule the whole of band 2 rests on.
        for _fn_schema, name, table_schema, table, tgtype, body in cursor.fetchall():
            found[f"{table_schema}.{name}"] = TriggerInfo(
                owner=table_schema,
                name=name,
                table=f"{table_schema}.{table}",
                timing=_timing(int(tgtype)),
                event=_events(int(tgtype)),
                body=str(body) if body is not None else "",
                base_object_type="TABLE",
            )
        return found


def _timing(tgtype: int) -> str:
    """`BEFORE`, `AFTER` or `INSTEAD OF`, plus the row/statement word.

    Rendered to read like Oracle's `ALL_TRIGGERS.trigger_type` - "BEFORE EACH ROW" - so
    evidence prose and the `INSTEAD OF` check mean the same thing in both dialects.
    """
    if tgtype & (1 << 6):
        when = "INSTEAD OF"
    elif tgtype & (1 << 1):
        when = "BEFORE"
    else:
        when = "AFTER"
    scope = "EACH ROW" if tgtype & 1 else "STATEMENT"
    return f"{when} {scope}"


def _events(tgtype: int) -> str:
    """`INSERT`, `UPDATE`, `DELETE`, `TRUNCATE` - any combination, OR-joined.

    A trigger may fire on several, and `TriggerInfo.fires_on` counts any overlap: a trigger
    that MIGHT fire is a real edge, and dropping it would be a silent miss.
    """
    events = [
        name
        for bit, name in ((2, "INSERT"), (3, "DELETE"), (4, "UPDATE"), (5, "TRUNCATE"))
        if tgtype & (1 << bit)
    ]
    return " OR ".join(events)


POSTGRES_CATALOGUE = PostgresCatalogue()
