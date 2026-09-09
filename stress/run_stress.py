"""Score the stress packages in isolation. Phase-0 numbers are not touched.

`lineage.cli measure` globs `ground_truth/*.yaml` and fingerprints `corpus/`, so putting a
stress package there would change the corpus fingerprint and move every figure in the
signed measurement. That is a decision to take deliberately, with the measurement re-run
and recorded - not a side effect of writing a test.

So this runner does the scoring itself, against `stress/`, and prints the same per-band,
per-flow grid the harness prints. Same scoring code, different inputs.

    .\\.venv\\Scripts\\python.exe stress/run_stress.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lineage.analysis.procedure import analyse_source  # noqa: E402
from lineage.config import AnalysisConfig  # noqa: E402
from lineage.harness.labels import GroundTruth  # noqa: E402
from lineage.harness.scoring import render, score  # noqa: E402
from lineage.resolution.dictionary import Dictionary  # noqa: E402

STRESS = ROOT / "stress"


def main() -> int:
    dictionary = Dictionary.load(ROOT / "corpus" / "dictionary.json")
    config = AnalysisConfig.load()

    worst = 0
    for key_path in sorted(STRESS.glob("*.yaml")):
        truth = GroundTruth.load(key_path)
        source = (STRESS / truth.package).read_text(encoding="utf-8")

        # Hash check, exactly as the corpus keys get: a key that has drifted from the code
        # it describes is worse than no key.
        truth.verify_against(STRESS)

        result = analyse_source(source, dictionary, config, None)
        report = score(truth, result.edges, result.boundaries)

        print("=" * 78)
        print(f"STRESS PACKAGE: {truth.package}")
        print("=" * 78)
        print(render(report))

        print(f"statements seen {result.statements_seen}  analysed {result.statements_analysed}")
        coverage = result.parse_coverage
        print(f"parse coverage  {'n/a' if coverage is None else f'{coverage * 100:.1f}%'}")
        print()

        if report.forbidden_violations:
            worst = max(worst, 2)
        if report.origin_violations or report.boundaries_undeclared:
            worst = max(worst, 1)

    return worst


if __name__ == "__main__":
    raise SystemExit(main())
