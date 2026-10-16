"""Score the lineage graph against a holdout's hand-written answer key (`expectations.md`).

Expected = the raw./ref. columns under each heading of each entry (notes ignored).
Found    = the raw./ref. columns the graph reaches walking upstream from the entry's target.
Recall is reported per heading and over values + filters + grouping (SCORED); join keys are
reported apart - D-5 gives a plain join key no edge by design, `--keys` walks KEYS edges too.

    NEO4J_URI=bolt://localhost:17687 python scripts/holdout/score.py EXPECTATIONS.md [--keys]
        [--detail] [--verified-only]

The graph must hold the holdout (`lineage graph build <holdout>/sql --dictionary ...`).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from lineage.graph.neo4j_conn import connect
from lineage.graph.tools import GraphTools

HEADS = ("values", "filters", "grouping / ordering", "join keys")
_COLUMN = re.compile(r'^\s+(raw|ref)\.("?[A-Za-z_]\w*"?)\.("?[A-Za-z_]\w*"?)(\s|$)')
_HEAD = re.compile(r"(values|filters|grouping / ordering|join keys|path):")


def _norm(schema: str, table: str, column: str) -> str:
    def one(name: str) -> str:
        return name[1:-1] if name.startswith('"') else name.lower()

    return f"{schema}.{one(table)}.{one(column)}"


def expectations(path: Path) -> list[tuple[str, dict[str, set[str]]]]:
    """(target column, heading -> expected base columns) per answer-key entry."""
    entries = []
    for block in re.split(r"\n## \d+\. ", path.read_text(encoding="utf-8"))[1:]:
        target, section, found = block.split()[0], None, defaultdict(set)
        for line in block.splitlines():
            head = _HEAD.match(line.strip())
            if head:
                section = head.group(1)
                continue
            column = _COLUMN.match(line)
            if section in HEADS and column:
                found[section].add(_norm(*column.group(1, 2, 3)))
        entries.append((target, found))
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("expectations", type=Path)
    parser.add_argument("--keys", action="store_true", help="also walk KEYS (join-key) edges")
    parser.add_argument("--detail", action="store_true", help="list what each entry misses")
    parser.add_argument(
        "--verified-only", action="store_true", help="leave out unverified (fallback) hops"
    )
    args = parser.parse_args()

    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    with connect() as driver:
        tools = GraphTools(driver)
        for target, expected in expectations(args.expectations):
            node = next(
                (m["id"] for m in tools.find(target) if m["id"].lower() == target.lower()), None
            )
            reached: set[str] = set()
            if node:
                walk = tools.upstream(
                    node, 15, join_keys=args.keys, fallback=not args.verified_only
                )
                reached = {
                    e["source"].lower() for e in walk if e["source"].split(".")[0] in ("raw", "ref")
                }
            scored = set().union(*(expected[h] for h in HEADS[:3]))
            line = [f"{target:<52} {'ok' if node else 'NODE MISSING':<12}"]
            for head, columns in [*((h, expected[h]) for h in HEADS), ("SCORED", scored)]:
                hit = {c for c in columns if c.lower() in reached}
                totals[head][0] += len(hit)
                totals[head][1] += len(columns)
                line.append(f"{head.split()[0][:6]} {len(hit):>3}/{len(columns):<3}")
            print("  ".join(line))
            if args.detail:
                for head in HEADS:
                    missing = sorted(c for c in expected[head] if c.lower() not in reached)
                    if missing:
                        print(f"      missing {head}: {', '.join(missing)}")
    print()
    for head, (hit, total) in totals.items():
        print(f"  {head:<20} {hit:>4}/{total:<4} {100 * hit / max(1, total):5.1f}%")


if __name__ == "__main__":
    main()
