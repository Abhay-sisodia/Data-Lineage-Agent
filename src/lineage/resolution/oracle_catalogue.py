"""Oracle's data dictionary, read through the ALL_* views.

Every query here is the one that used to sit inline in `resolution.dictionary.capture`,
moved without a word changed. The committed `corpus/dictionary.json` was produced by that
code, so `test_committed_dictionary_matches_the_live_database` comparing fingerprints
against a live database is the check that this move was faithful.

**ALL_* rather than USER_*.** A real deployment resolves names across schemas, and binding
everything to one owner is the schema-context silent failure (s2) written into the capture
step instead of into the analyser.
"""

from __future__ import annotations

from typing import Any

from lineage.resolution.dictionary import ObjectInfo, TriggerInfo


class OracleCatalogue:
    """`resolution.catalogue.Catalogue` for Oracle Database."""

    def objects(self, cursor: Any, owner: str) -> dict[str, ObjectInfo]:
        cursor.execute(
            """
            SELECT owner, object_name, object_type
              FROM all_objects
             WHERE owner = :owner
               AND object_type IN ('TABLE','VIEW','SYNONYM','PROCEDURE','FUNCTION',
                                   'PACKAGE','TRIGGER','SEQUENCE')
            """,
            owner=owner,
        )
        return {
            f"{object_owner}.{name}": ObjectInfo(
                owner=object_owner, name=name, object_type=object_type
            )
            for object_owner, name, object_type in cursor.fetchall()
        }

    def synonyms(self, cursor: Any, owner: str) -> dict[str, str]:
        cursor.execute(
            """
            SELECT owner, synonym_name, NVL(table_owner, :owner), table_name
              FROM all_synonyms
             WHERE owner = :owner
            """,
            owner=owner,
        )
        return {
            f"{synonym_owner}.{synonym_name}": f"{target_owner}.{target_name}"
            for synonym_owner, synonym_name, target_owner, target_name in cursor.fetchall()
        }

    def columns(self, cursor: Any, owner: str) -> dict[str, list[str]]:
        cursor.execute(
            """
            SELECT owner, table_name, column_name
              FROM all_tab_columns
             WHERE owner = :owner
             ORDER BY owner, table_name, column_id
            """,
            owner=owner,
        )
        found: dict[str, list[str]] = {}
        for column_owner, table_name, column_name in cursor.fetchall():
            found.setdefault(f"{column_owner}.{table_name}", []).append(column_name)
        return found

    def view_text(self, cursor: Any, owner: str) -> dict[str, str]:
        cursor.execute(
            "SELECT owner, view_name, text FROM all_views WHERE owner = :owner",
            owner=owner,
        )
        return {
            f"{view_owner}.{view_name}": str(text) if text is not None else ""
            for view_owner, view_name, text in cursor.fetchall()
        }

    def temporary(self, cursor: Any, owner: str) -> dict[str, bool]:
        # Temporary-ness is a property of the object, not of its name.
        cursor.execute(
            "SELECT owner, table_name, temporary FROM all_tables WHERE owner = :owner",
            owner=owner,
        )
        return {
            f"{table_owner}.{table_name}": flag == "Y"
            for table_owner, table_name, flag in cursor.fetchall()
        }

    def triggers(self, cursor: Any, owner: str) -> dict[str, TriggerInfo]:
        cursor.execute(
            """
            SELECT owner, trigger_name, table_owner, table_name, trigger_type,
                   triggering_event, base_object_type, trigger_body
              FROM all_triggers
             WHERE owner = :owner
               AND status = 'ENABLED'
            """,
            owner=owner,
        )
        found: dict[str, TriggerInfo] = {}
        for row in cursor.fetchall():
            (
                trigger_owner,
                trigger_name,
                table_owner,
                table_name,
                trigger_type,
                event,
                base_object_type,
                body,
            ) = row
            found[f"{trigger_owner}.{trigger_name}"] = TriggerInfo(
                owner=trigger_owner,
                name=trigger_name,
                table=f"{table_owner or owner}.{table_name}",
                # ALL_TRIGGERS reports e.g. "AFTER EACH ROW"; the timing word is enough
                # for evidence, and INSTEAD OF is the one that changes the analysis.
                timing=str(trigger_type),
                event=str(event),
                body=str(body) if body is not None else "",
                base_object_type=str(base_object_type or "TABLE"),
            )
        return found


ORACLE_CATALOGUE = OracleCatalogue()
