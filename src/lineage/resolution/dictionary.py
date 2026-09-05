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

    return Dictionary(
        captured_at=captured_at,
        default_schema=owner,
        objects=objects,
        synonyms=synonyms,
        columns=columns,
        view_text=view_text,
    )
