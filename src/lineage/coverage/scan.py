"""What the analyser will make of a PostgreSQL folder - predicted BEFORE analysing it (step 2).

A coverage figure that arrives only after a full analysis arrives too late: the user has
already been shown lineage and has no idea which statements it leaves out. The scan reads
every statement with PostgreSQL's own parser (`pg_referee`), maps each grammar construct it
contains to its row in the grammar checklist (`docs/pg-coverage.md`, measured, never typed),
and tries the one thing a per-construct table cannot see - whether SQLGlot parses the
statement as a whole after our pre-pass (holdout gap G1 was a combination of two constructs
that each pass on their own).

Each statement gets one prediction:

* `column-level`  - a statement that moves data, every construct in it handled;
* `gap`           - a statement that moves data, with a construct or combination we do not
                    handle yet - each named with its checklist row and measured outcome;
* `effect`        - changes what a table holds or is (TRUNCATE, DROP ...): not modelled yet;
* `no lineage`    - nothing to trace (DDL, settings), and counted;
* `not counted`   - nothing to trace, but the analyser does not even count it today;
* `not valid PostgreSQL` - PostgreSQL's own parser rejects it; refused, never analysed.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from pglast import ast, enums

from lineage.analysis import ledger
from lineage.analysis.procedure import restate_postgres
from lineage.config import AnalysisConfig
from lineage.coverage.grammar import SNAPSHOT, load_rows
from lineage.parsing import psql
from lineage.parsing.pg_referee import verdicts
from lineage.parsing.sqlscript import split_statements

PREDICTIONS = (
    "column-level", "gap", "effect", "declared", "no lineage", "not counted",
    "not valid PostgreSQL",
)  # fmt: skip
_ROUTINES = ("CreateFunctionStmt", "DoStmt")
_USING = re.compile(r"\bUSING\b", re.IGNORECASE)
_DYNAMIC = re.compile(r"\bEXECUTE\s+(?!')", re.IGNORECASE)
"""`EXECUTE` of anything but a plain string literal - a literal folds (D-42)."""


@dataclass
class StatementScan:
    file: str
    line: int
    kind: str
    prediction: str
    gaps: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    row: str
    target: str
    status: str
    met: bool


def checklist_outcomes() -> dict[str, list[Outcome]]:
    """Every checklist construct id -> the measured outcome of each row standing for it."""
    measured = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    out: dict[str, list[Outcome]] = {}
    seen: set[str] = set()
    for index, row in enumerate(load_rows()):
        # A repeated id is keyed by its position, as `scripts/pg_coverage.py` writes it.
        value = measured[row.id if row.id not in seen else f"{row.id}#{index}"]
        seen.add(row.id)
        outcome = Outcome(row.id, row.target, value["status"], value["met"])
        for item in row.items:
            out.setdefault(item, []).append(outcome)
    return out


def _restated_as_lineage(v: Any) -> bool:
    """ALTER TABLE forms that carry column lineage: `ALTER COLUMN ... TYPE ... USING expr` is
    restated as an UPDATE (step 8); ATTACH PARTITION flows the child's rows to the parent
    (D-34)."""
    if v.kind != "AlterTableStmt":
        return False
    retyped = "variant:AlterTableCmd.subtype=AT_AlterColumnType" in v.constructs
    return (retyped and _USING.search(v.text) is not None) or (
        "variant:AlterTableCmd.subtype=AT_AttachPartition" in v.constructs
    )


def _written_by_the_run(v: Any, run: _Run) -> bool:
    """A bound trigger function (D-41) that writes - rows, or its NEW row's fields (T4) - or a
    REFRESH of a matview the run made."""
    if v.kind == "CreateFunctionStmt" and (ledger.writes_rows(v) or ledger.sets_new(v)):
        return run.binds(v)
    if v.kind == "RefreshMatViewStmt" and v.tree is not None:
        rel = v.tree.relation
        name = f"{rel.schemaname}.{rel.relname}" if rel.schemaname else str(rel.relname)
        return name.lower() in run.matviews
    return False


_WRITES = (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt, ast.MergeStmt)


def _track_prepared(v: Any, prepared: set[str]) -> bool:
    """Follow PREPARE / DEALLOCATE in file order (one psql session); True for an EXECUTE of
    a prepared write - the analyser traces it there, D-35 (semantic-ir T9, holdout4)."""
    tree = v.tree if v.valid else None
    if tree is None:
        return False
    if v.kind == "PrepareStmt" and isinstance(tree.query, _WRITES):
        prepared.add(str(tree.name).lower())
    elif v.kind == "DeallocateStmt":
        if getattr(tree, "isall", False) or not tree.name:
            prepared.clear()
        else:
            prepared.discard(str(tree.name).lower())
    elif v.kind == "ExecuteStmt":
        return str(tree.name).lower() in prepared
    return False


def _only_dynamic(v: Any) -> bool:
    """A routine whose only writes are `EXECUTE <text that is not a literal>`: what it runs
    is not decidable before it runs (D-42), so it is declared, not 'no lineage'."""
    return (
        v.kind in _ROUTINES
        and not ledger.writes_rows(v)
        and "plpgsql:PLpgSQL_stmt_dynexecute" in v.constructs
        and _DYNAMIC.search(v.text) is not None
    )


def _key_only_delete(v: Any) -> bool:
    """A DELETE whose WHERE only matches keys (`t.k = s.k AND ...`): no lineage (D-5) -
    except in row-deciding mode, where those keys decide which rows go (T0, option C)."""
    if v.kind != "DeleteStmt" or v.tree is None or v.tree.whereClause is None:
        return False
    if AnalysisConfig.load().row_deciding_keys:
        return False

    def keys(node: Any) -> bool:
        if isinstance(node, ast.BoolExpr) and node.boolop is enums.BoolExprType.AND_EXPR:
            return all(keys(arg) for arg in node.args)
        return (
            isinstance(node, ast.A_Expr)
            and node.kind is enums.A_Expr_Kind.AEXPR_OP
            and [n.sval for n in node.name] == ["="]
            and isinstance(node.lexpr, ast.ColumnRef)
            and isinstance(node.rexpr, ast.ColumnRef)
        )

    return keys(v.tree.whereClause)


def _sqlglot_error(text: str) -> str:
    try:
        sqlglot.parse_one(text, read="postgres")
    except Exception as error:  # SQLGlot's own verdict is the finding
        return str(error).splitlines()[0][:120]
    return ""


def scan_source(
    text: str, file: str, outcomes: dict[str, list[Outcome]], run: _Run | None = None
) -> list[StatementScan]:
    run = run or _Run.of([text])
    served, _ = psql.restate(text)
    restated = dict(split_statements(restate_postgres(text)[0]))
    out: list[StatementScan] = []
    prepared: set[str] = set()  # this session's prepared statements that write (T9)
    for v in verdicts(served):
        executes_a_write = _track_prepared(v, prepared)
        if not v.valid:
            out.append(StatementScan(file, v.line, "?", "not valid PostgreSQL", [v.error]))
            continue
        kind_target = next(
            (o.target for o in outcomes.get(f"stmt:{v.kind}", ())), "no_lineage"
        ).split(":")[0]
        gaps, effects, silent = [], [], []
        for item in sorted(v.constructs):
            for o in outcomes.get(item, ()):
                if o.met:
                    continue
                named = f"{o.row} ({o.status})"
                if o.target == "effect":
                    effects.append(named)
                elif o.target == "no_lineage" and o.status == "SILENT":
                    silent.append(named)
                else:
                    gaps.append(named)
        notes = []
        if "plpgsql:unreadable_offline" in v.constructs:
            notes.append("PL/pgSQL body not readable offline - its constructs are not predicted")
        if v.kind not in _ROUTINES and v.line in restated:
            error = _sqlglot_error(restated[v.line])
            if error:
                gaps.append(f"SQLGlot cannot parse it ({error})")
        if v.kind in _ROUTINES:
            # A routine's declared boundary (RETURN NEXT: rows built in a loop) names what it
            # returns, and the rest of the routine is read - it is not a gap in what it writes.
            notes += [g for g in gaps if "(DECLARED" in g]
            gaps = [g for g in gaps if "(DECLARED" not in g]
        # R6 (holdout3): what the analyser concludes when a statement yields nothing is a
        # function of PostgreSQL's parse alone (`ledger.nothing_reason`) - the scan uses the
        # same rule rather than a second, coarser copy of it.
        nothing, _ = ledger.nothing_reason(v)
        if gaps and (kind_target == "lineage" or v.kind in _ROUTINES):
            prediction = "gap"
        elif _restated_as_lineage(v) or _written_by_the_run(v, run) or executes_a_write:
            prediction = "column-level"
        elif _only_dynamic(v):
            prediction = "declared"
        elif nothing == "effect":
            prediction = "effect"
        elif _key_only_delete(v) or (nothing == "no_lineage" and v.kind != "DeleteStmt"):
            prediction = "no lineage"
        elif kind_target == "lineage" or nothing == "not_analysed":
            prediction = "column-level"
        elif silent or gaps:
            prediction = "not counted" if silent else "gap"
        else:
            prediction = "no lineage"
        listed = gaps + effects + (silent if prediction == "not counted" else [])
        out.append(
            StatementScan(
                file, v.line, v.kind, prediction, sorted(set(listed)), v.writes, v.reads, notes
            )
        )
    return out


def scan_folder(folder: Path) -> list[StatementScan]:
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    outcomes = checklist_outcomes()
    paths = [folder] if folder.is_file() else sorted(folder.rglob("*.sql"))
    texts = {path: path.read_text(encoding="utf-8").replace("\r\n", "\n") for path in paths}
    run = _Run.of(texts.values())
    found: list[StatementScan] = []
    for path in paths:
        name = path.name if folder.is_file() else path.relative_to(folder).as_posix()
        found.extend(scan_source(texts[path], name, outcomes, run))
    return found


@dataclass
class _Run:
    """What the whole folder says about one statement's fate (R6, holdout3): a trigger
    function writes where a CREATE TRIGGER binds it (D-41), and a REFRESH re-runs a
    materialized view the run created (D-32)."""

    bound: set[str] = field(default_factory=set)
    matviews: set[str] = field(default_factory=set)

    @classmethod
    def of(cls, texts: Any) -> _Run:
        run = cls()
        for text in texts:
            for v in verdicts(psql.restate(text)[0]):
                if v.kind == "CreateTrigStmt" and v.tree is not None:
                    run.bound.add(".".join(n.sval for n in v.tree.funcname).lower())
                elif (
                    v.kind == "CreateTableAsStmt"
                    and v.tree is not None
                    and v.tree.objtype is enums.ObjectType.OBJECT_MATVIEW
                ):
                    run.matviews.update(w.lower() for w in v.writes)
        return run

    def binds(self, v: Any) -> bool:
        names = [".".join(n.sval for n in v.tree.funcname).lower()] if v.tree is not None else []
        return any(n in self.bound or n.split(".")[-1] in self.bound for n in names)


def summary(scans: list[StatementScan]) -> str:
    counts = Counter(s.prediction for s in scans)
    moving = counts["column-level"] + counts["gap"]
    lines = [
        f"{len(scans)} statements | predicted column-level {counts['column-level']}/{moving} "
        f"statements that move data ({100 * counts['column-level'] / max(1, moving):.1f}%)",
        "  " + " | ".join(f"{p} {counts[p]}" for p in PREDICTIONS),
    ]
    for scan in scans:
        if scan.prediction in ("gap", "not valid PostgreSQL"):
            lines.append(f"  {scan.prediction.upper():<6} {scan.file}:{scan.line} {scan.kind}")
            lines.extend(f"         - {gap}" for gap in scan.gaps)
    return "\n".join(lines)


def to_json(scans: list[StatementScan]) -> str:
    return json.dumps([asdict(s) for s in scans], indent=1)
