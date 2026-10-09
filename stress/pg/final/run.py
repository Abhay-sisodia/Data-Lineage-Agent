# ruff: noqa: E501 - report lines and probe statements quote SQL verbatim.
"""Score the final PostgreSQL acceptance suite, CLOSED WORLD.

    .\\.venv\\Scripts\\python.exe stress/pg/final/run.py                 every tier
    .\\.venv\\Scripts\\python.exe stress/pg/final/run.py --tier easy     one tier
    .\\.venv\\Scripts\\python.exe stress/pg/final/run.py e1_latest       tests whose name contains this
    .\\.venv\\Scripts\\python.exe stress/pg/final/run.py --diag e1       also print every emitted edge

Written 2026-10-07 for the final suite (stress/pg/final/LIMITATIONS.md has the bounds).

Unlike the torture runners, nothing is scoped out. Every edge the analyser emits - value,
influence AND filter, into any target - is matched against the key, so an edge the key
does not state is a false positive wherever it lands. Every boundary it declares
(refusals included: kind `refusal`, subject `<SCRIPT>:<line>`) must be `expect`ed by the
key, and every expected one must be declared. A test PASSES only with FP 0, FN 0, no
forbidden edge, and boundaries equal both ways.

METAMORPHIC GROUPS (H5): a keyspec comment `# metamorphic: rel_a = rel_b [= rel_c]` names
relations written by equivalent spellings. Their lineage - every edge into each, with the
relation's own name set aside - must be identical, whatever the key says. A group that
differs fails the test as well.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE.parent))

import keygen  # noqa: E402

from lineage.analysis.procedure import analyse_source  # noqa: E402
from lineage.config import AnalysisConfig  # noqa: E402
from lineage.harness.labels import GroundTruth  # noqa: E402
from lineage.harness.scoring import score  # noqa: E402
from lineage.resolution.dictionary import Dictionary  # noqa: E402

TIERS = ("easy", "medium", "hard")
BOOK = HERE / "dictionary.json"


def _short(node: str) -> str:
    return node.split(":", 1)[1] if ":" in node else node


def _metamorphic(spec: Path, edges: list) -> list[str]:
    """Each `# metamorphic:` group whose relations received different lineage."""
    failures = []
    for raw in spec.read_text(encoding="utf-8").splitlines():
        if not raw.startswith("# metamorphic:"):
            continue
        relations = [r.strip() for r in raw.split(":", 1)[1].split("=")]
        shapes = {}
        for relation in relations:
            shape = set()
            for e in edges:
                target = _short(str(e.target))
                if target == relation:
                    column = "<relation>"
                elif target.startswith(relation + "."):
                    column = target[len(relation) + 1 :]
                else:
                    continue
                shape.add((_short(str(e.source)), column, e.flow.value, e.transform.value,
                           str(getattr(e.phase, "value", e.phase))))
            shapes[relation] = shape
        first = relations[0]
        for other in relations[1:]:
            if shapes[other] != shapes[first]:
                only_first = sorted(shapes[first] - shapes[other])
                only_other = sorted(shapes[other] - shapes[first])
                failures.append(f"{first} != {other}: only in {first} {only_first}; only in {other} {only_other}")
            elif not shapes[first]:
                failures.append(f"{first} = {other}, but both received NO lineage")
    return failures


def run_one(sql: Path, book: Dictionary, config: AnalysisConfig, diag: bool) -> bool:
    spec = sql.with_suffix(".keyspec")
    print("=" * 100)
    if not spec.exists():
        print(f"{sql.relative_to(HERE)}: NO KEY - not scored")
        return False
    sql_text = sql.read_text(encoding="utf-8")
    if "\r" in sql_text:
        print(f"{sql.relative_to(HERE)}: CRLF in a keyed file - its hash differs on checkout")
        return False
    spec.with_suffix(".yaml").write_text(keygen.compile_spec(spec), encoding="utf-8", newline="\n")
    truth = GroundTruth.load(spec.with_suffix(".yaml"))
    truth.verify_against(HERE.parents[1])

    result = analyse_source(sql_text, book, config, None)
    report = score(truth, result.edges, result.boundaries)

    expected = {b.identity() for b in truth.expected_boundaries}
    declared = {b.identity() for b in result.boundaries}
    unexpected = sorted(declared - expected)
    undeclared = sorted(expected - declared)

    tp = sum(c.true_positives for c in report.cells.values())
    fp = sum(c.false_positives for c in report.cells.values())
    fn = sum(c.false_negatives for c in report.cells.values())
    metamorphic = _metamorphic(spec, result.edges)
    passed = not (fp or fn or report.forbidden_violations or unexpected or undeclared or metamorphic)
    lines = sql_text.count("\n") + 1

    print(
        f"{'PASS' if passed else 'FAIL'}  {sql.relative_to(HERE).as_posix()}  ({lines} lines)  "
        f"TP {tp}  FP {fp}  FN {fn}  of {len(truth.edges)} facts"
    )
    print(
        f"      statements seen {result.statements_seen}, partial {result.partial_statements}, "
        f"edges {len(result.edges)}, refusals {len(result.refusals)}, "
        f"boundaries {len(result.boundaries)}"
    )
    for (band, flow), cell in sorted(report.cells.items()):
        print(
            f"      band {band} {flow:9}  TP {cell.true_positives:4}  "
            f"FP {cell.false_positives:4}  FN {cell.false_negatives:4}"
        )
    for missed in sorted(report.missed_edges):
        print(f"    MISS  {missed[2]:9} {missed[3]:11} {_short(missed[0])} -> {_short(missed[1])}  [{missed[4]}]")
    for spurious in sorted(report.spurious_edges):
        print(f"    FP    {spurious[2]:9} {spurious[3]:11} {_short(spurious[0])} -> {_short(spurious[1])}  [{spurious[4]}]")
    for key, reason in report.forbidden_violations:
        print(f"    FORBIDDEN  {key}: {reason}")
    details = {b.identity(): b for b in result.boundaries}
    for kind, subject in unexpected:
        detail = (details[(kind, subject)].detail or "")[:120]
        print(f"    UNEXPECTED BOUNDARY  {kind}: {subject} - {detail}")
    for refusal in result.refusals:
        print(f"      (refusal line {refusal.line} {refusal.code.value}: {refusal.reason[:110]})")
    for kind, subject in undeclared:
        print(f"    EXPECTED, NOT DECLARED  {kind}: {subject}")
    groups = sum(1 for raw in spec.read_text(encoding="utf-8").splitlines() if raw.startswith("# metamorphic:"))
    if groups:
        print(f"      metamorphic groups: {groups}, differing: {len(metamorphic)}")
    for failure in metamorphic:
        print(f"    METAMORPHIC  {failure}")
    if diag:
        print("    -- every emitted edge --")
        for edge in sorted(result.edges, key=lambda e: (str(e.target), e.flow.value, str(e.source))):
            print(
                f"      {edge.flow.value:9} {edge.transform.value:11} "
                f"{_short(str(edge.source))} -> {_short(str(edge.target))}  (line {edge.origin.line})"
            )
        print(f"      flows: {dict(Counter(e.flow.value for e in result.edges))}")
    return passed


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tier = sys.argv[sys.argv.index("--tier") + 1] if "--tier" in sys.argv else None
    if tier:
        args = [a for a in args if a != tier]
    tiers = [tier] if tier else list(TIERS)
    book = Dictionary.load(BOOK)
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    files = [
        sql
        for name in tiers
        for sql in sorted((HERE / name).glob("*.sql"))
        if not args or any(a in sql.stem for a in args)
    ]
    passed = sum(run_one(sql, book, config, "--diag" in sys.argv) for sql in files)
    print("=" * 100)
    print(f"{passed} of {len(files)} tests pass closed-world")
    return 0 if passed == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
