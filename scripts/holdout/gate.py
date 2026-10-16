"""The semantic-ir gate: every holdout's run against the baseline, one verdict.

The thresholds are those of docs/semantic-ir-plan.md.

Reads what `run.sh` wrote for each holdout - check.txt, score.txt, score--keys.txt - in a run
folder and in the baseline folder, and checks the plan's thresholds:

* recall and recall with keys never fall below the baseline (holdout1, holdout3 ...);
* holdout4 recall with keys reaches its floor;
* column-level >= 98%, scan vs ledger 0 mismatches, 0 silent, for every holdout.

    python scripts/holdout/gate.py [--runs DIR] [--baseline .holdout-runs/baseline-ccaa793]

Exit status 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

HOLDOUTS = ("holdout", "holdout3", "holdout4")
FLOORS = {"holdout4": {"recall_keys": 95.0}}
COLUMN_LEVEL_MIN = 98.0


@dataclass
class Numbers:
    recall: float
    recall_keys: float
    column_level: float
    mismatches: int
    silent: int


def _scored(text: str) -> float:
    found = re.search(r"SCORED\s+\d+/\d+\s+([\d.]+)%", text)
    if found is None:
        raise ValueError("no SCORED line")
    return float(found.group(1))


def read(folder: Path) -> Numbers:
    check = (folder / "check.txt").read_text(encoding="utf-8")
    level = re.search(r"column-level \d+/\d+ statements that move data \(([\d.]+)%\)", check)
    mismatches = re.search(r"mismatches (\d+) of", check)
    silent = re.search(r"files with a statement missing from the ledger: (\d+)", check)
    if level is None or mismatches is None or silent is None:
        raise ValueError(f"{folder / 'check.txt'}: not a complete check")
    return Numbers(
        recall=_scored((folder / "score.txt").read_text(encoding="utf-8")),
        recall_keys=_scored((folder / "score--keys.txt").read_text(encoding="utf-8")),
        column_level=float(level.group(1)),
        mismatches=int(mismatches.group(1)),
        silent=int(silent.group(1)),
    )


def checks(name: str, now: Numbers, base: Numbers) -> list[tuple[str, str, str, str, bool]]:
    """(metric, baseline, now, threshold, passed) for one holdout."""
    rows = []
    for metric in ("recall", "recall_keys"):
        floor = max(getattr(base, metric), FLOORS.get(name, {}).get(metric, 0.0))
        value = getattr(now, metric)
        rows.append((metric, f"{getattr(base, metric):.1f}%", f"{value:.1f}%", f">= {floor:.1f}%",
                     value >= floor))  # fmt: skip
    rows.append(("column_level", f"{base.column_level:.1f}%", f"{now.column_level:.1f}%",
                 f">= {COLUMN_LEVEL_MIN:.1f}%", now.column_level >= COLUMN_LEVEL_MIN))  # fmt: skip
    rows.append(("scan_mismatches", str(base.mismatches), str(now.mismatches), "= 0",
                 now.mismatches == 0))  # fmt: skip
    rows.append(("silent", str(base.silent), str(now.silent), "= 0", now.silent == 0))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=Path, default=Path(".holdout-runs"))
    parser.add_argument("--baseline", type=Path, default=Path(".holdout-runs/baseline-ccaa793"))
    args = parser.parse_args()

    failed = 0
    print(f"{'holdout':<10} {'metric':<16} {'baseline':>9} {'now':>9} {'threshold':>10}  verdict")
    for name in HOLDOUTS:
        base, now = read(args.baseline / name), read(args.runs / name)
        for metric, before, after, threshold, passed in checks(name, now, base):
            failed += not passed
            print(f"{name:<10} {metric:<16} {before:>9} {after:>9} {threshold:>10}  "
                  f"{'pass' if passed else 'FAIL'}")  # fmt: skip
    print(f"\ngate: {'PASS' if not failed else f'FAIL ({failed} checks)'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
