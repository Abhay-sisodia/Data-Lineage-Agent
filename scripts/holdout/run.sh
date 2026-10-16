#!/usr/bin/env bash
# End to end on one holdout: dictionary from its DDL, sanity + ledger + scan check, graph build
# into the SCRATCH Neo4j, recall with and without join keys.
#
#   NEO4J_URI=bolt://localhost:17687 bash scripts/holdout/run.sh "<path>/holdout4" [OUT_DIR]
#
# Never point NEO4J_URI at the dev graph (7687): it holds the user's own SQL.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
HOLDOUT="$1"
OUT="${2:-$REPO/.holdout-runs/$(basename "$HOLDOUT")}"
: "${NEO4J_URI:?set NEO4J_URI to the scratch Neo4j, e.g. bolt://localhost:17687}"
case "$NEO4J_URI" in *:7687) echo "refusing: $NEO4J_URI is the dev graph" >&2; exit 1 ;; esac

if [ -x "$REPO/.venv/Scripts/python.exe" ]; then PY="$REPO/.venv/Scripts/python.exe"; LINEAGE="$REPO/.venv/Scripts/lineage.exe"
else PY="$REPO/.venv/bin/python"; LINEAGE="$REPO/.venv/bin/lineage"; fi

mkdir -p "$OUT"
cat "$HOLDOUT"/schema/0*.sql > "$OUT/schema_all.sql"
"$LINEAGE" schema from-ddl "$OUT/schema_all.sql" --out "$OUT/dictionary.json" > /dev/null
"$PY" "$REPO/scripts/holdout/check.py" "$HOLDOUT" "$OUT/dictionary.json" --out "$OUT" | tee "$OUT/check.txt"
"$LINEAGE" graph build "$HOLDOUT/sql" --dictionary "$OUT/dictionary.json" > "$OUT/build.txt"
for mode in "" "--keys"; do
  echo "== recall ${mode:-default}"
  "$PY" "$REPO/scripts/holdout/score.py" "$HOLDOUT/expectations.md" $mode --detail > "$OUT/score${mode}.txt"
  tail -6 "$OUT/score${mode}.txt"
done
echo "outputs in $OUT"
