"""Data-dictionary name resolution (T1.4).

**Never trust the literal name in code.** A synonym quietly redirects `customer_target`
to `DW_DIM_CUSTOMER_V2`; an unqualified name resolves through whichever schema is
executing; a view hides the base table that actually receives the write.

These are *silent* failures: the parser succeeds, binds the wrong object, and reports it
at Tier B — with the logs and the data profile agreeing, because they describe the same
wrong name. Evidence triangulation offers no protection, so the defence has to live here,
at ingest.

Two design points that are not incidental:

* **The dictionary is a snapshot with a fingerprint**, not a live lookup. Resolution must
  be reproducible: re-running March's analysis has to use March's dictionary, or a
  synonym repointed in June silently rewrites a signed finding.
* **An unknown name is never resolved to nothing.** It comes back explicitly unresolved
  so the caller can declare a boundary and count it. Returning an empty column list for
  a `SELECT *` against an unknown table is precisely the stale-DDL failure this module
  exists to prevent — it produces a confident, wrong, silent answer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field


class UnknownObjectError(LookupError):
    """Raised when a name cannot be resolved and the caller demanded resolution.

    Deliberately loud. The alternative — quietly returning nothing — is the failure mode
    that puts a wrong answer into a filing.
    """


class StaleDictionaryError(RuntimeError):
    """The dictionary is not the one the analysis was pinned to.

    A schema snapshot must be versioned alongside the code snapshot. Expanding SELECT *
    against a dictionary that has moved on produces the WRONG column list with no
    symptom at all.
    """


@dataclass(frozen=True)
class ResolvedName:
    """A name, resolved, with the chain that got there.

    ``via`` is evidence, not decoration: "customer_target is a synonym for
    LINEAGE.DW_DIM_CUSTOMER_V2" is the sentence that survives a challenge.
    """

    original: str
    owner: str | None
    name: str | None
    object_type: str | None
    via: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.name is not None

    @property
    def qualified(self) -> str | None:
        if self.name is None:
            return None
        return f"{self.owner}.{self.name}" if self.owner else self.name

    def __str__(self) -> str:
        return self.qualified or f"<unresolved:{self.original}>"


class ObjectInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str
    name: str
    object_type: str


class TriggerInfo(BaseModel):
    """A trigger, and the object it fires on.

    Triggers belong in the dictionary rather than in whichever source file happens to
    declare them, and that is the whole design point of T3.3. `trg_recent_audit` is
    written in `b2_05_triggers.sql`, but it fires for every one of the seven procedures in
    this corpus that insert into `tmp_recent` — none of which mentions it. Attaching the
    edge to the CALLER would credit one procedure with a write it never issued and miss
    the other six; attaching it to the TABLE is what makes inheritance work.

    ``body`` is the PL/SQL between BEGIN and END, exactly as Oracle stores it, so the
    correlation names `:NEW` and `:OLD` are still present to be resolved.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str
    name: str
    table: str = Field(description="OWNER.TABLE the trigger fires on")
    timing: str = Field(description="BEFORE / AFTER / INSTEAD OF, per ALL_TRIGGERS")
    event: str = Field(description="INSERT, UPDATE, DELETE, or a combination")
    body: str
    base_object_type: str = "TABLE"

    @property
    def qualified(self) -> str:
        return f"{self.owner}.{self.name}"

    def fires_on(self, events: str) -> bool:
        """Does this trigger run for any of these events?

        ``events`` may name more than one — a MERGE both inserts and updates, and which
        arm a given row takes is not statically decidable. Any overlap counts: a trigger
        that MIGHT fire is a real edge, and dropping it would be a silent miss.
        """
        mine = self.event.upper()
        return any(word and word in mine for word in events.upper().split())

    def describe(self) -> str:
        return f"{self.timing} {self.event} ON {self.table}"


class Dictionary(BaseModel):
    """A point-in-time snapshot of the data dictionary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    captured_at: str
    default_schema: str
    objects: dict[str, ObjectInfo] = Field(default_factory=dict)
    synonyms: dict[str, str] = Field(
        default_factory=dict, description="OWNER.SYNONYM -> OWNER.TARGET"
    )
    columns: dict[str, list[str]] = Field(
        default_factory=dict, description="OWNER.TABLE -> ordered column names"
    )
    view_text: dict[str, str] = Field(default_factory=dict)
    temporary: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "OWNER.TABLE -> is this a global temporary table. A genuinely temporary "
            "relation holds session-private data, so a lineage path composed through it "
            "across two different procedures is invented rather than observed. A "
            "permanent table used as scratch looks identical in the code and is a "
            "different problem - it really is shared, and whether data flows between "
            "two writers is not statically decidable."
        ),
    )
    triggers: dict[str, TriggerInfo] = Field(
        default_factory=dict,
        description=(
            "OWNER.TRIGGER -> the trigger and the object it fires on. Keyed by trigger "
            "rather than by table because a table may carry several; use triggers_on() "
            "to go the other way."
        ),
    )

    # ---- persistence -----------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def fingerprint(self) -> str:
        """Stable hash of the dictionary's *content*, excluding capture time.

        Capture time is metadata; two captures of an unchanged schema must fingerprint
        identically or the pinning check would fire on every run and be ignored.
        """
        payload = self.model_dump(mode="json")
        payload.pop("captured_at", None)
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def require_fingerprint(self, expected: str) -> None:
        """Fail if this is not the dictionary the analysis was pinned to."""
        actual = self.fingerprint()
        if actual != expected:
            raise StaleDictionaryError(
                "dictionary does not match the one this analysis was pinned to.\n"
                f"  expected {expected}\n"
                f"  actual   {actual}\n"
                "Objects have changed since the snapshot. Re-capture deliberately - do "
                "not silently analyse against a different schema."
            )

    # ---- resolution ------------------------------------------------------------

    def resolve(self, name: str, schema: str | None = None) -> ResolvedName:
        """Resolve a possibly-unqualified, possibly-synonym name to a real object.

        Follows synonym chains, with cycle protection - a synonym loop is a real thing
        in old estates and must not hang the analyser.
        """
        original = name
        text = name.strip().upper().replace('"', "")
        context = (schema or self.default_schema).upper()

        if "." in text:
            owner, _, simple = text.partition(".")
            via: list[str] = []
        else:
            owner, simple = context, text
            via = [f"unqualified '{original}' bound to schema {context}"]

        seen: set[str] = set()
        key = f"{owner}.{simple}"

        while key in self.synonyms:
            if key in seen:
                via.append(f"synonym cycle detected at {key}")
                return ResolvedName(original, None, None, None, via)
            seen.add(key)
            target = self.synonyms[key]
            via.append(f"synonym {key} -> {target}")
            key = target
            owner, _, simple = key.partition(".")

        info = self.objects.get(key)
        if info is None:
            via.append(f"no object named {key} in the dictionary")
            return ResolvedName(original, None, None, None, via)

        return ResolvedName(original, info.owner, info.name, info.object_type, via)

    def columns_of(self, name: str, schema: str | None = None) -> list[str]:
        """Ordered column list for a relation — the basis of SELECT * expansion.

        Raises rather than returning [] when the object is unknown. An empty expansion
        would silently drop every column of a table we simply could not see.
        """
        resolved = self.resolve(name, schema)
        if not resolved.resolved:
            raise UnknownObjectError(
                f"cannot expand columns for '{name}': {'; '.join(resolved.via)}"
            )
        key = resolved.qualified
        assert key is not None
        columns = self.columns.get(key)
        if columns is None:
            raise UnknownObjectError(
                f"'{name}' resolved to {key} ({resolved.object_type}) but the dictionary "
                "holds no columns for it. Refusing to expand rather than guessing."
            )
        return list(columns)

    def is_view(self, name: str, schema: str | None = None) -> bool:
        return self.resolve(name, schema).object_type == "VIEW"

    def is_temporary(self, name: str, schema: str | None = None) -> bool:
        """True for a global temporary table — session-private data.

        Deliberately answered from the data dictionary, not from the name. `tmp_recent`
        in this corpus is a permanent table despite its prefix, and treating a name
        convention as a semantic fact is how a scratch table gets silently mis-modelled.
        """
        resolved = self.resolve(name, schema)
        if resolved.qualified is None:
            return False
        return self.temporary.get(resolved.qualified, False)

    def triggers_on(self, name: str, schema: str | None = None) -> list[TriggerInfo]:
        """Every trigger that fires on this relation, resolved through synonyms.

        The inheritance rule from the blind-spot register: *any statement touching that
        table inherits the trigger's edges*. Resolution goes through `resolve` so a write
        to a synonym still finds the base object's triggers - otherwise the two silent
        failures compound, and a synonym would hide a trigger as well as a table.
        """
        resolved = self.resolve(name, schema)
        if resolved.qualified is None:
            return []
        return [
            trigger
            for _, trigger in sorted(self.triggers.items())
            if trigger.table == resolved.qualified
        ]


def capture(connection: Any, schema: str, captured_at: str) -> Dictionary:
    """Read the dictionary out of a live Oracle connection.

    Reads the ALL_* views rather than USER_*: a real deployment resolves names across
    schemas, and binding everything to one owner is the schema-context silent failure.
    """
    objects: dict[str, ObjectInfo] = {}
    synonyms: dict[str, str] = {}
    columns: dict[str, list[str]] = {}
    view_text: dict[str, str] = {}
    owner = schema.upper()

    with connection.cursor() as cursor:
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
        for object_owner, name, object_type in cursor.fetchall():
            objects[f"{object_owner}.{name}"] = ObjectInfo(
                owner=object_owner, name=name, object_type=object_type
            )

        cursor.execute(
            """
            SELECT owner, synonym_name, NVL(table_owner, :owner), table_name
              FROM all_synonyms
             WHERE owner = :owner
            """,
            owner=owner,
        )
        for synonym_owner, synonym_name, target_owner, target_name in cursor.fetchall():
            synonyms[f"{synonym_owner}.{synonym_name}"] = f"{target_owner}.{target_name}"

        cursor.execute(
            """
            SELECT owner, table_name, column_name
              FROM all_tab_columns
             WHERE owner = :owner
             ORDER BY owner, table_name, column_id
            """,
            owner=owner,
        )
        for column_owner, table_name, column_name in cursor.fetchall():
            columns.setdefault(f"{column_owner}.{table_name}", []).append(column_name)

        cursor.execute(
            "SELECT owner, view_name, text FROM all_views WHERE owner = :owner",
            owner=owner,
        )
        for view_owner, view_name, text in cursor.fetchall():
            view_text[f"{view_owner}.{view_name}"] = str(text) if text is not None else ""

        # Temporary-ness is a property of the object, not of its name.
        cursor.execute(
            "SELECT owner, table_name, temporary FROM all_tables WHERE owner = :owner",
            owner=owner,
        )
        temporary = {
            f"{table_owner}.{table_name}": flag == "Y"
            for table_owner, table_name, flag in cursor.fetchall()
        }

        # Triggers, with the object each fires on. Captured here rather than read from
        # source files because a trigger belongs to its TABLE, and the procedure that
        # writes that table is usually in a different file with no mention of it.
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
        triggers: dict[str, TriggerInfo] = {}
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
            triggers[f"{trigger_owner}.{trigger_name}"] = TriggerInfo(
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

    return Dictionary(
        captured_at=captured_at,
        default_schema=owner,
        objects=objects,
        synonyms=synonyms,
        columns=columns,
        view_text=view_text,
        temporary=temporary,
        triggers=triggers,
    )
