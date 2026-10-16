#!/usr/bin/env bash
# Run every holdout end to end into the SCRATCH Neo4j, then the semantic-ir gate.
#
#   NEO4J_URI=bolt://localhost:17687 HOLDOUT_ROOT=<folder holding holdout*/> bash scripts/holdout/gate.sh
#
# HOLDOUT_ROOT holds holdout/holdout, holdout3/holdout3, holdout4/holdout4 (as unpacked).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="${HOLDOUT_ROOT:?set HOLDOUT_ROOT to the folder holding the holdouts}"
if [ -x "$REPO/.venv/Scripts/python.exe" ]; then PY="$REPO/.venv/Scripts/python.exe"; else PY="$REPO/.venv/bin/python"; fi

for name in holdout holdout3 holdout4; do
  echo "== $name"
  bash "$REPO/scripts/holdout/run.sh" "$ROOT/$name/$name" "$REPO/.holdout-runs/$name" > "$REPO/.holdout-runs/$name.log" 2>&1
done
cd "$REPO" && "$PY" scripts/holdout/gate.py
