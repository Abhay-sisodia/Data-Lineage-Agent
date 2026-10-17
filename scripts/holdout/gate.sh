#!/usr/bin/env bash
# Run every holdout end to end into the SCRATCH Neo4j, then the semantic-ir gate.
#
#   NEO4J_URI=bolt://localhost:17687 HOLDOUT_ROOT=<folder holding holdout*/> bash scripts/holdout/gate.sh
#
# HOLDOUT_ROOT holds holdout/holdout, holdout3/holdout3, holdout4/holdout4 (as unpacked).
# Row-deciding mode (T0, option C): LINEAGE_ROW_DECIDING_KEYS=1, and RUNS=<folder> to keep
# its outputs apart from the default mode's (.holdout-runs).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="${HOLDOUT_ROOT:?set HOLDOUT_ROOT to the folder holding the holdouts}"
RUNS="${RUNS:-$REPO/.holdout-runs}"
if [ -x "$REPO/.venv/Scripts/python.exe" ]; then PY="$REPO/.venv/Scripts/python.exe"; else PY="$REPO/.venv/bin/python"; fi

mkdir -p "$RUNS"
for name in holdout holdout3 holdout4; do
  echo "== $name"
  bash "$REPO/scripts/holdout/run.sh" "$ROOT/$name/$name" "$RUNS/$name" > "$RUNS/$name.log" 2>&1
done
cd "$REPO" && "$PY" scripts/holdout/gate.py --runs "$RUNS"
