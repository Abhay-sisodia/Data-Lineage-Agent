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
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import sqlglot

from lineage.analysis.procedure import restate_postgres
from lineage.coverage.grammar import SNAPSHOT, load_rows
from lineage.parsing import psql
from lineage.parsing.pg_referee import verdicts
from lineage.parsing.sqlscript import split_statements

PREDICTIONS = (
    "column-level", "gap", "effect", "no lineage", "not counted", "not valid PostgreSQL",
)  # fmt: skip
_ROUTINES = ("CreateFunctionStmt", "DoStmt")


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


def _sqlglot_error(text: str) -> str:
    try:
        sqlglot.parse_one(text, read="postgres")
    except Exception as error:  # SQLGlot's own verdict is the finding
        return str(error).splitlines()[0][:120]
    return ""


def scan_source(text: str, file: str, outcomes: dict[str, list[Outcome]]) -> list[StatementScan]:
    served, _ = psql.restate(text)
    restated = dict(split_statements(restate_postgres(text)[0]))
    out: list[StatementScan] = []
    for v in verdicts(served):
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
        if gaps and (kind_target == "lineage" or v.kind in _ROUTINES):
            prediction = "gap"
        elif effects or kind_target == "effect":
            prediction = "effect"
        elif kind_target == "lineage":
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
    found: list[StatementScan] = []
    for path in paths:
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        name = path.name if folder.is_file() else path.relative_to(folder).as_posix()
        found.extend(scan_source(text, name, outcomes))
    return found


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
