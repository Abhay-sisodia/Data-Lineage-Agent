"""Run one stress batch and print everything triage needs, statement by statement.

    .\\.venv\\Scripts\\python.exe stress/pg/triage.py b1_core_select

`stress/run_stress.py` prints the scoring grid, which says HOW MANY facts were missed or
invented. Triage needs WHICH ones and WHERE: every miss and every false positive with the
line of the statement it belongs to, every refusal and boundary, every forbidden edge that
appeared - and, for files carrying `-- @pair` markers, whether each metamorphic pair agreed.

Nothing here changes a score; it reads the same key and the same analyser output the runner
does. The analyser is run unchanged.

**The per-statement check (finding B4-04).** The harness scores a FILE: its match key has no
origin, so a fact two statements both produce is one fact, and a statement that misses it
scores as a hit whenever another statement found it. Triage therefore also checks each
statement on its own - the facts stated at its line plus the ones its keyspec marks
`repeat` - and prints every HIDDEN MISS: a fact absent from its statement that the
per-file score counted as found.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lineage.analysis.procedure import analyse_source  # noqa: E402
from lineage.config import AnalysisConfig  # noqa: E402
from lineage.harness.labels import Flow, GroundTruth  # noqa: E402
from lineage.harness.scoring import score  # noqa: E402
from lineage.resolution.dictionary import Dictionary  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from keygen import repeats_of  # noqa: E402

STRESS = ROOT / "stress"
HERE = Path(__file__).resolve().parent
PAIR = re.compile(r"^--\s*@pair\s+(\S+)\s+([AB])\s*$")


def _fact(edge: object) -> tuple[str, str, str, str, str]:
    return (
        str(edge.source),  # type: ignore[attr-defined]
        str(edge.target),  # type: ignore[attr-defined]
        edge.flow.value,  # type: ignore[attr-defined]
        edge.transform.value,  # type: ignore[attr-defined]
        edge.phase.value,  # type: ignore[attr-defined]
    )


def _show(key: tuple[str, ...]) -> str:
    source, target, flow, transform, phase = key[:5]
    extra = f" {phase}" if flow == "filter" and phase != "pre-aggregation" else ""
    return f"{flow:9} {transform:11} {source} -> {target}{extra}"


def _statement_spans(sql: str) -> list[tuple[int, int, int]]:
    """(first line of the span, last line, first line of code) for every statement.

    A span runs from the line after the previous `;` to this statement's own `;`, so it
    includes the comment header above the code. The analyser numbers a statement from the
    start of its SPAN; the key numbers it from its first line of code. Mapping both onto the
    code line is what makes a miss and a false positive from one statement land together.
    """
    spans: list[tuple[int, int, int]] = []
    begin, code = 1, None
    for number, line in enumerate(sql.splitlines(), start=1):
        text = line.strip()
        if code is None and re.match(
            r"(?i)^(insert|with|select|update|delete|merge|create)\b", text
        ):
            code = number
        if text.endswith(";") and not text.startswith("--"):
            spans.append((begin, number, code or number))
            begin, code = number + 1, None
    return spans


def _statement_starts(sql: str) -> list[int]:
    return [code for _, _, code in _statement_spans(sql)]


def _owner(line: int, spans: list[tuple[int, int, int]]) -> int:
    for begin, end, code in spans:
        if begin <= line <= end:
            return code
    return line


def triage(key_path: Path, book: Dictionary, config: AnalysisConfig) -> None:
    truth = GroundTruth.load(key_path)
    truth.verify_against(STRESS)
    sql_path = STRESS / truth.package
    sql = sql_path.read_text(encoding="utf-8")
    result = analyse_source(sql, book, config, None)
    report = score(truth, result.edges, result.boundaries)
    starts = _statement_starts(sql)
    spans = _statement_spans(sql)

    print("=" * 96)
    print(
        f"{sql_path.name}   statements seen {result.statements_seen}, analysed "
        f"{result.statements_analysed}, refusals {len(result.refusals)}, "
        f"boundaries {len(result.boundaries)}"
    )
    print("=" * 96)
    for flow in (Flow.VALUE, Flow.FILTER, Flow.INFLUENCE):
        cell = report.cells.get((0, flow.value))
        if cell is None:
            continue
        print(
            f"  {flow.value:9}  TP {cell.true_positives:4}  FP {cell.false_positives:4}  "
            f"FN {cell.false_negatives:4}"
        )

    for refusal in result.refusals:
        print(f"  REFUSED  line {refusal.line}  {refusal.code.value}: {refusal.reason[:150]}")
    for boundary in result.boundaries:
        print(
            f"  BOUNDARY {boundary.kind.value}: {boundary.subject} - "
            f"{(boundary.detail or '')[:100]}"
        )

    label_line = {key.key(): key.origin.line for key in truth.edges}
    by_line: dict[int, list[str]] = defaultdict(list)
    for missed in report.missed_edges:
        by_line[label_line.get(missed[:5], 0)].append(f"MISS  {_show(missed)}")
    predicted_line: dict[tuple[str, ...], int] = {}
    for edge in result.edges:
        predicted_line.setdefault(_fact(edge), _owner(edge.origin.line, spans))
    for spurious in report.spurious_edges:
        by_line[predicted_line.get(spurious[:5], 0)].append(f"FP    {_show(spurious)}")
    for violation, reason in report.forbidden_violations:
        by_line[predicted_line.get(violation[:5], 0)].append(
            f"FORBIDDEN  {_show(violation)}   <- {reason[:90]}"
        )

    for line in sorted(by_line):
        print(f"  -- statement at line {line}")
        for entry in sorted(by_line[line]):
            print(f"       {entry}")

    _per_statement(key_path, truth, report, result.edges, spans)
    _pairs(sql, result.edges, starts, spans)


def _per_statement(
    key_path: Path,
    truth: GroundTruth,
    report: object,
    edges: list[object],
    spans: list[tuple[int, int, int]],
) -> None:
    expected: dict[int, set[tuple[str, ...]]] = defaultdict(set)
    for label in truth.edges:
        expected[label.origin.line].add(label.key()[:5])
    spec = key_path.with_suffix(".keyspec")
    if spec.exists():
        for line, facts in repeats_of(spec).items():
            for source, target, flow, transform, phase, kind in facts:
                expected[line].add((f"column:{source}", f"{kind}:{target}", flow, transform, phase))
    produced: dict[int, set[tuple[str, ...]]] = defaultdict(set)
    for edge in edges:
        produced[_owner(edge.origin.line, spans)].add(_fact(edge))
    missed = {m[:5] for m in report.missed_edges}  # type: ignore[attr-defined]

    total = found = 0
    hidden: list[tuple[int, tuple[str, ...]]] = []
    for line, facts in expected.items():
        total += len(facts)
        for fact in facts:
            if fact in produced[line]:
                found += 1
            elif fact not in missed:
                hidden.append((line, fact))
    print(f"  per statement: {found} / {total} found, {len(hidden)} hidden miss(es)")
    for line, fact in sorted(hidden):
        print(f"       HIDDEN MISS at line {line}: {_show(fact)}")


def _pairs(
    sql: str, edges: list[object], starts: list[int], spans: list[tuple[int, int, int]]
) -> None:
    marks: dict[str, dict[str, int]] = defaultdict(dict)
    pending: tuple[str, str] | None = None
    for number, line in enumerate(sql.splitlines(), start=1):
        match = PAIR.match(line.strip())
        if match:
            pending = (match.group(1), match.group(2))
        elif pending and number in starts:
            marks[pending[0]][pending[1]] = number
            pending = None
    if not marks:
        return

    facts: dict[int, set[tuple[str, ...]]] = defaultdict(set)
    for edge in edges:
        facts[_owner(edge.origin.line, spans)].add(_fact(edge))  # type: ignore[attr-defined]

    print("  -- metamorphic pairs")
    for name in sorted(marks, key=lambda n: int(n[1:])):
        a, b = marks[name].get("A"), marks[name].get("B")
        if a is None or b is None:
            print(f"     {name}: incomplete pair")
            continue
        only_a, only_b = facts[a] - facts[b], facts[b] - facts[a]
        verdict = "EQUAL" if not only_a and not only_b else "DIFFER"
        print(f"     {name} (A@{a}, B@{b}): {verdict}   A={len(facts[a])} B={len(facts[b])}")
        for fact in sorted(only_a):
            print(f"         only A  {_show(fact)}")
        for fact in sorted(only_b):
            print(f"         only B  {_show(fact)}")


def main() -> int:
    batch = sys.argv[1] if len(sys.argv) > 1 else "b1_core_select"
    book = Dictionary.load(HERE / "dictionary.json")
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    for key_path in sorted((HERE / batch).glob("*.yaml")):
        triage(key_path, book, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
