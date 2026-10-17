"""The statement ledger: every statement in a PostgreSQL source, with what became of it (step 3).

Edges, refusals and boundaries describe what the analyser SAID. A statement it said nothing
about - a `DROP TABLE`, an `ANALYZE`, a `DELETE` matched on keys only - appeared nowhere,
and a reader could not tell "nothing to trace" from "never looked at". The holdout's three
quiet statements were found by a one-off script; a user would never have found them.

The ledger closes that. Every top-level statement the splitter finds gets exactly ONE entry,
and its status is decided from the analyser's own output first and the statement's kind
(from PostgreSQL's parser, `pg_referee`) only when the output is empty:

    not_valid      PostgreSQL's own parser rejects it - refused, never analysed (D-59)
    column_level   edges came out of it, nothing refused
    partial        edges came out, and a part of it was refused
    unclassified   refused, but PostgreSQL's tree still names its columns (`mention`, D-60)
    table_level    refused, but PostgreSQL's tree still names its tables (`opaque`, D-60)
    refused        refused, by code, and nothing more could be read
    declared       no edge, no refusal, a boundary names what stopped it
    effect         changes what a table holds or is (DROP, TRUNCATE, RENAME, ALTER ...)
    no_lineage     nothing to trace: DDL, settings, maintenance, constants only, a key match
    not_analysed   a statement that can move data, and nothing came of it - no edge, no
                   refusal, no boundary. Never silent: it is listed, with that reason.

The number of entries equals the number of statements by construction; `silent` does not
exist. A status the analyser's output does not explain falls to `not_analysed`, the loud one.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from lineage.ir.model import NodeKind
from lineage.parsing.pg_referee import Verdict

STATUSES = (
    "column_level", "partial", "unclassified", "table_level", "refused", "declared", "effect",
    "no_lineage", "not_analysed", "not_valid",
)  # fmt: skip

# Statements that change what a table holds or is, rather than moving column values.
EFFECT_KINDS = {
    "DropStmt": "drops it",
    "TruncateStmt": "empties it",
    "RenameStmt": "renames it",
    "AlterTableStmt": "changes its structure",
    "AlterObjectSchemaStmt": "moves it to another schema",
}

# The ALTER TABLE actions that change what the table holds; the others (owner, defaults,
# constraints, storage) change nothing a column's lineage could show.
_TABLE_CHANGES = {
    "AT_DropColumn": "drops a column",
    "AT_AlterColumnType": "rewrites a column's values",
    "AT_AttachPartition": "attaches a partition's rows",
    "AT_DetachPartition": "detaches a partition's rows",
}
_EXPLAIN_ANALYZE = re.compile(r"\bANALY[SZ]E\b", re.IGNORECASE)
_RETURNS_TRIGGER = re.compile(r"\bRETURNS\s+(?:event_)?trigger\b", re.IGNORECASE)
_NO_DATA = re.compile(r"\bWITH\s+NO\s+DATA\b", re.IGNORECASE)
_IF_NOT_EXISTS = re.compile(r"^\s*CREATE\b[^(]*?\bTABLE\s+IF\s+NOT\s+EXISTS\b", re.IGNORECASE)
_WRITE_STATEMENTS = {
    "stmt:InsertStmt", "stmt:UpdateStmt", "stmt:DeleteStmt", "stmt:MergeStmt",
    "stmt:CreateTableAsStmt", "stmt:TruncateStmt", "stmt:CopyStmt",
}  # fmt: skip

# Statements that can move data: one of these with nothing to show is `not_analysed`.
MOVING_KINDS = {
    "InsertStmt", "UpdateStmt", "MergeStmt", "CreateTableAsStmt", "ViewStmt", "CallStmt",
    "CreateFunctionStmt", "DoStmt", "PrepareStmt", "ExecuteStmt", "CreateTrigStmt",
    "RuleStmt", "CopyStmt", "ExplainStmt", "SelectStmt",
}  # fmt: skip

_NOTHING_TO_TRACE = {
    "VacuumStmt": "maintenance",
    "ClusterStmt": "maintenance",
    "ReindexStmt": "maintenance",
    "CheckPointStmt": "maintenance",
    "IndexStmt": "an index",
    "CommentStmt": "a comment",
    "GrantStmt": "privileges",
    "GrantRoleStmt": "privileges",
    "VariableSetStmt": "a session setting",
    "VariableShowStmt": "a session setting",
    "TransactionStmt": "transaction control",
    "LockStmt": "a lock",
    "DeallocateStmt": "a prepared statement released",
    "RefreshMatViewStmt": "re-runs a definition analysed where it is written",
}


@dataclass(frozen=True)
class LedgerEntry:
    line: int
    end_line: int
    kind: str
    status: str
    reason: str
    edges: int = 0


def _in(line: int, entry: Verdict) -> bool:
    return entry.line <= line <= entry.end_line


def _edges(count: int) -> str:
    return f"{count} edge{'' if count == 1 else 's'}"


def writes_rows(verdict: Verdict) -> bool:
    """Whether a statement - or a routine's body - contains a write."""
    return bool(verdict.constructs & _WRITE_STATEMENTS)


_SETS_NEW = re.compile(
    r"(?is)\bINTO\s+(?:STRICT\s+)?NEW\s*\.\s*\w+"
    r"|\bNEW\s*\.\s*\w+\s*:=\s*[^;]*?(?:\b[A-Za-z_]\w*\s*\.\s*[A-Za-z_]\w*|\bSELECT\b)"
)


def sets_new(verdict: Verdict) -> bool:
    """A trigger body that sets a NEW field from a column or a query: a write of the trigger
    table's row (semantic-ir T4) - `NEW.f := now()` alone sets nothing traceable."""
    return verdict.kind == "CreateFunctionStmt" and _SETS_NEW.search(verdict.text) is not None


def nothing_reason(verdict: Verdict) -> tuple[str, str]:
    """(status, reason) for a statement the analyser produced nothing for."""
    kind = verdict.kind
    if kind == "AlterTableStmt":
        changed = [
            what
            for subtype, what in _TABLE_CHANGES.items()
            if f"variant:AlterTableCmd.subtype={subtype}" in verdict.constructs
        ]
        if not changed:
            return "no_lineage", "nothing to trace - DDL that changes no column's content"
        return "effect", f"{', '.join(changed)} - {verdict.text.split()[2]} (an effect)"
    if kind == "DropStmt" and not verdict.writes:
        return "no_lineage", "nothing to trace - drops an object that holds no rows"
    if kind == "ExplainStmt" and not _EXPLAIN_ANALYZE.search(verdict.text):
        return "no_lineage", "plans the statement without running it"
    if kind == "CopyStmt" and not verdict.writes:
        return "no_lineage", "an export - nothing in the database changes"
    if kind in EFFECT_KINDS:
        target = ", ".join(verdict.writes) or "an object"
        return "effect", f"{EFFECT_KINDS[kind]} - {target} (an effect, not a column flow)"
    if kind == "DeleteStmt":
        if "clause:DeleteStmt.whereClause" not in verdict.constructs:
            target = ", ".join(verdict.writes) or "the table"
            return "effect", f"deletes every row of {target} (an effect, not a column flow)"
        return "no_lineage", "removes the rows its match finds; the match is on keys only (D-5)"
    if kind == "CallStmt":
        called = verdict.text.split("(", 1)[0].split()[-1]
        return "no_lineage", f"runs {called} - its lineage is that routine's, where it is defined"
    if kind == "PrepareStmt":
        return "no_lineage", "prepares a statement - its lineage is recorded where EXECUTE runs it"
    if kind == "CreateTrigStmt":
        return "no_lineage", "attaches a function to a table - its lineage is on the table's writes"
    if kind == "CreateFunctionStmt" and _RETURNS_TRIGGER.search(verdict.text):
        return "no_lineage", "a trigger function - its lineage is where a trigger binds it (D-41)"
    if kind in ("CreateFunctionStmt", "DoStmt") and not verdict.constructs & _WRITE_STATEMENTS:
        return "no_lineage", "a routine that writes nothing - its lineage shows where it is called"
    if kind == "CreateTableAsStmt" and _NO_DATA.search(verdict.text):
        return "no_lineage", "created empty (WITH NO DATA) - no row is written"
    if kind == "CreateTableAsStmt" and _IF_NOT_EXISTS.search(verdict.text):
        return "no_lineage", "IF NOT EXISTS of a table the dictionary already has - nothing written"
    if kind in MOVING_KINDS:
        if "node:ColumnRef" not in verdict.constructs and "node:A_Star" not in verdict.constructs:
            return "no_lineage", "writes constants only - no column is read"
        return "not_analysed", "it can move data, and produced no edge, refusal or boundary"
    what = _NOTHING_TO_TRACE.get(kind)
    if what is None:
        what = "DDL" if kind.startswith(("Create", "Alter", "Drop", "Define")) else kind
    return "no_lineage", f"nothing to trace - {what}"


def build(verdicts: list[Verdict], result: Any) -> list[LedgerEntry]:
    """One entry per statement in `verdicts`, from `result` - an `AnalysisResult`."""
    elsewhere = {unit.upper() for unit in getattr(result, "incomparable_units", ())}
    edges = [
        e for e in [*result.edges, *result.join_keys] if e.origin.unit.upper() not in elsewhere
    ]
    entries: list[LedgerEntry] = []
    for verdict in verdicts:
        if not verdict.valid:
            entries.append(
                LedgerEntry(verdict.line, verdict.end_line, "?", "not_valid", verdict.error)
            )
            continue
        mine = sum(1 for e in edges if _in(e.origin.line, verdict))
        refused = [r for r in result.refusals if _in(r.line, verdict)]
        if (
            mine
            and verdict.kind in ("CreateFunctionStmt", "DoStmt")
            and not any(
                e.target.kind is NodeKind.COLUMN
                and e.target.name.rpartition(".")[0].lower() != e.origin.unit.lower()
                for e in edges
                if _in(e.origin.line, verdict)
            )
        ):
            # R6 (holdout3): a routine whose edges all end in its own variables - or in the
            # rows its SELECT INTO filters - writes no column. Its lineage is where it is
            # called (a summary), as `nothing_reason` says; calling it column-level said
            # otherwise, and no prediction could agree with that. semantic-ir T3: the same for
            # its own OUTPUT relation (named after it) - its rows reach a table where called.
            mine = 0
        if mine and refused:
            status, reason = "partial", f"{_edges(mine)}; refused in part - {refused[0].code.value}"
        elif mine:
            status, reason = "column_level", _edges(mine)
        elif refused:
            status, reason = "refused", f"{refused[0].code.value} - {refused[0].reason}"
            kept = [e for e in result.fallback if _in(e.origin.line, verdict)]
            if any(e.flow.value == "mention" for e in kept):
                status = "unclassified"
                reason += f" - column-level, unclassified: {len(kept)} mentions kept (D-60)"
            elif kept:
                status = "table_level"
                reason += f" - table-level: {len(kept)} relation links kept (D-60)"
        else:
            declared = [b for b in result.boundaries if b.line and _in(b.line, verdict)]
            if declared:
                status, reason = "declared", f"{declared[0].kind.value} - {declared[0].detail}"
            else:
                status, reason = nothing_reason(verdict)
        entries.append(
            LedgerEntry(verdict.line, verdict.end_line, verdict.kind, status, reason, mine)
        )
    return entries


def summary(entries: list[LedgerEntry]) -> str:
    counts = Counter(entry.status for entry in entries)
    return f"{len(entries)} statements: " + " | ".join(
        f"{status.replace('_', ' ')} {counts[status]}" for status in STATUSES if counts[status]
    )
