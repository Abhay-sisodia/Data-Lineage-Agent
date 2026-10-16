"""Replay holdouts through ONE commit's code: refusals, edges, ledger, graph recall.

Run from inside a worktree of that commit (see replay.sh), so its own `src` is what loads:

    cd <worktree> && NEO4J_URI=bolt://localhost:17687 python <repo>/scripts/holdout/replay.py \
        <worktree> <label> <out.json> NAME=HOLDOUT_DIR::DICTIONARY.json [...]

Each holdout is analysed, built into the (scratch!) graph - replacing it - and scored by
`score.expectations`, with and without join keys, and with and without unverified hops
where the commit has them. This is how the per-step table in docs/handoff-2026-10-16.md
was measured.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

WORKTREE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(WORKTREE / "src"))
import lineage  # noqa: E402

assert Path(lineage.__file__).resolve().is_relative_to(WORKTREE), lineage.__file__

from lineage.analysis.procedure import analyse_source  # noqa: E402
from lineage.cli import app  # noqa: E402
from lineage.config import AnalysisConfig  # noqa: E402
from lineage.graph.neo4j_conn import connect  # noqa: E402
from lineage.graph.tools import GraphTools  # noqa: E402
from lineage.resolution.dictionary import Dictionary  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score import HEADS, expectations  # noqa: E402

logging.getLogger("sqlglot").setLevel(logging.CRITICAL)


def score(tools: GraphTools, entries, join_keys: bool, fallback: bool):
    takes_fallback = "fallback" in inspect.signature(tools.upstream).parameters
    if not fallback and not takes_fallback:
        return None
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for target, expected in entries:
        node = next(
            (m["id"] for m in tools.find(target) if m["id"].lower() == target.lower()), None
        )
        options = {"join_keys": join_keys, **({"fallback": fallback} if takes_fallback else {})}
        reached = (
            {
                e["source"].lower()
                for e in tools.upstream(node, 15, **options)
                if e["source"].split(".")[0] in ("raw", "ref")
            }
            if node
            else set()
        )
        scored = set().union(*(expected[h] for h in HEADS[:3]))
        for head, columns in [*((h, expected[h]) for h in HEADS), ("SCORED", scored)]:
            totals[head][0] += sum(c.lower() in reached for c in columns)
            totals[head][1] += len(columns)
    return dict(totals)


def main() -> None:
    label, out_path = sys.argv[2], Path(sys.argv[3])
    holdouts = {}
    for spec in sys.argv[4:]:
        name, _, paths = spec.partition("=")
        folder, _, dictionary = paths.partition("::")  # `::`: a Windows path holds `:`
        holdouts[name] = (Path(folder), Path(dictionary))
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    out: dict[str, object] = {"label": label}
    for name, (folder, dictionary) in holdouts.items():
        book = Dictionary.load(dictionary)
        refusals, edges, keys, statuses = 0, 0, 0, Counter()
        for path in sorted((folder / "sql").rglob("*.sql")):
            result = analyse_source(path.read_text(encoding="utf-8"), book, config)
            refusals += len(result.refusals)
            edges += len(result.edges)
            keys += len(getattr(result, "join_keys", []) or [])
            statuses.update(e.status for e in getattr(result, "ledger", []) or [])
        with contextlib.suppress(SystemExit):
            app(
                ["graph", "build", str(folder / "sql"), "--dictionary", str(dictionary)],
                standalone_mode=False,
            )
        entries = expectations(folder / "expectations.md")
        with connect() as driver:
            tools = GraphTools(driver)
            scores = {
                "default": score(tools, entries, False, True),
                "keys": score(tools, entries, True, True),
                "default_verified": score(tools, entries, False, False),
                "keys_verified": score(tools, entries, True, False),
            }
        out[name] = {"refusals": refusals, "edges": edges, "join_keys": keys,
                     "ledger": dict(statuses), "scores": scores}  # fmt: skip
        print(name, scores["default"]["SCORED"], scores["keys"]["SCORED"], refusals, flush=True)
    out_path.write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
