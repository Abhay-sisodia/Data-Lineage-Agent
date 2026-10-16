"""A holdout's sanity and coverage, without Neo4j: what a blind run is measured on.

1. Validity: every schema / seed / ETL statement through PostgreSQL's own parser (pglast).
2. Answer key: every listed raw./ref. column (and every target) exists in the dictionary.
3. Ledger: each ETL statement's status, analysed as a folder run (files in order, D-65);
   column-level % of statements that move data, refusals, files whose ledger misses a
   statement (must be 0).
4. Scan vs ledger: `lineage scan`'s prediction against what the analysis then said, per
   statement (the step-9 gate: they must agree).

    python scripts/holdout/check.py HOLDOUT_DIR DICTIONARY.json [--out DIR]

HOLDOUT_DIR holds schema/, seed/, sql/ and expectations.md (see docs/handoff-2026-10-16.md).
Build the dictionary first: concatenate schema/0*.sql, then `lineage schema from-ddl`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from lineage.analysis.procedure import RunCarry, analyse_source
from lineage.config import AnalysisConfig
from lineage.coverage.scan import scan_folder
from lineage.parsing import psql
from lineage.parsing.pg_referee import verdicts
from lineage.parsing.sqlscript import split_statements
from lineage.resolution.dictionary import Dictionary

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score import expectations

_SAME = {
    "column_level": "column-level",
    "no_lineage": "no lineage",
    "not_valid": "not valid PostgreSQL",
}
_NOT_MOVING = ("effect", "no_lineage", "not_valid")


def validity(holdout: Path) -> None:
    print("== 1. PostgreSQL validity (pglast)")
    files = [*sorted(holdout.glob("schema/*.sql")), *sorted(holdout.glob("seed/*.sql")),
             *sorted(holdout.rglob("sql/*/*.sql"))]  # fmt: skip
    for path in files:
        found = verdicts(psql.restate(path.read_text(encoding="utf-8"))[0])
        invalid = [v for v in found if not v.valid]
        print(f"  {path.relative_to(holdout).as_posix():<60} {len(found):>3} stmts  "
              f"{len(invalid)} invalid")  # fmt: skip
        for v in invalid:
            print(f"      line {v.line}: {v.error}")


def answer_key(holdout: Path, book: Dictionary) -> None:
    print("\n== 2. Answer key")
    columns = {k.lower(): {c.lower() for c in v} for k, v in book.columns.items()}
    entries = expectations(holdout / "expectations.md")
    missing = []
    for target, expected in entries:
        schema, table, column = target.lower().split(".")
        if column.strip('"') not in columns.get(f"{schema}.{table}", set()):
            missing.append(f"target {target} (fine if the ETL itself creates it)")
        for cols in expected.values():
            for col in cols:
                s, t, c = col.lower().split(".")
                if c not in columns.get(f"{s}.{t}", set()):
                    missing.append(f"{target}: {col}")
    heads = Counter({h: sum(len(e[h]) for _, e in entries) for h in ("values", "filters",
                     "grouping / ordering", "join keys")})  # fmt: skip
    print(f"  entries {len(entries)}  base columns {dict(heads)}")
    print(f"  not in the dictionary: {len(missing)}")
    for item in missing:
        print(f"      {item}")


def ledger(holdout: Path, book: Dictionary) -> list[dict[str, object]]:
    print("\n== 3. Ledger (folder run)")
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    carry = RunCarry(book)
    rows: list[dict[str, object]] = []
    status: Counter[str] = Counter()
    short = 0
    for path in sorted((holdout / "sql").rglob("*.sql")):
        text = path.read_text(encoding="utf-8")
        result = analyse_source(text, book, config, carry=carry)
        if len(result.ledger) != len(split_statements(psql.restate(text)[0])):
            short += 1
        rel = path.relative_to(holdout / "sql").as_posix()
        for entry in result.ledger:
            status[entry.status] += 1
            rows.append({"file": rel, "line": entry.line, "kind": entry.kind,
                         "status": entry.status, "reason": entry.reason})  # fmt: skip
        for refusal in result.refusals:
            print(f"  REFUSED {rel}:{refusal.line} {refusal.code.value} {refusal.reason[:110]}")
    moving = sum(n for s, n in status.items() if s not in _NOT_MOVING)
    print(f"  {dict(status)}  total {sum(status.values())}")
    print(f"  column-level {status['column_level']}/{moving} statements that move data "
          f"({100 * status['column_level'] / max(1, moving):.1f}%); "
          f"files with a statement missing from the ledger: {short}")  # fmt: skip
    for row in rows:
        if row["status"] not in ("column_level", "effect", "no_lineage"):
            print(f"  {row['status']:<13} {row['file']}:{row['line']} {row['kind']} "
                  f"- {str(row['reason'])[:100]}")  # fmt: skip
    return rows


def scan_vs_ledger(holdout: Path, rows: list[dict[str, object]]) -> None:
    print("\n== 4. Scan prediction vs ledger")
    scans = {(s.file.replace("\\", "/"), s.line): s for s in scan_folder(holdout / "sql")}
    differ = [
        (key, scans[key].kind, scans[key].prediction, row["status"])
        for row in rows
        if (key := (str(row["file"]), int(row["line"]))) in scans
        and scans[key].prediction != _SAME.get(str(row["status"]), str(row["status"]))
    ]
    print(f"  mismatches {len(differ)} of {len(rows)}")
    for item in differ:
        print(f"      {item}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("holdout", type=Path)
    parser.add_argument("dictionary", type=Path)
    parser.add_argument("--out", type=Path, help="write ledger.json here")
    args = parser.parse_args()
    book = Dictionary.load(args.dictionary)
    validity(args.holdout)
    answer_key(args.holdout, book)
    rows = ledger(args.holdout, book)
    scan_vs_ledger(args.holdout, rows)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "ledger.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
