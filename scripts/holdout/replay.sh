#!/usr/bin/env bash
# Replay holdouts through several commits, one git worktree each, sequentially (one scratch
# Neo4j). Measures what each step changed on the SAME input - see docs/handoff-2026-10-16.md.
#
#   NEO4J_URI=bolt://localhost:17687 bash scripts/holdout/replay.sh OUT_DIR \
#       "pre:b7eea40 s5:e77636a s8:beed7a0" \
#       holdout3="<path>/holdout3::<run dir>/dictionary.json" [...]
#
# The ANTLR parser under src/lineage/parsing/generated/ is untracked; it is copied into each
# worktree. Never use PYTHONPATH for this: pytest and the scripts would load the working tree.
set -u
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$1"; COMMITS="$2"; shift 2
: "${NEO4J_URI:?set NEO4J_URI to the scratch Neo4j}"
case "$NEO4J_URI" in *:7687) echo "refusing: $NEO4J_URI is the dev graph" >&2; exit 1 ;; esac
if [ -x "$REPO/.venv/Scripts/python.exe" ]; then PY="$REPO/.venv/Scripts/python.exe"; else PY="$REPO/.venv/bin/python"; fi
mkdir -p "$OUT"
for pair in $COMMITS; do
  label=${pair%%:*}; sha=${pair##*:}
  wt="$OUT/wt_$label"
  [ -d "$wt" ] || git -C "$REPO" worktree add -q --detach "$wt" "$sha"
  mkdir -p "$wt/src/lineage/parsing/generated"
  cp -r "$REPO/src/lineage/parsing/generated/." "$wt/src/lineage/parsing/generated/"
  echo "== $label $sha"
  (cd "$wt" && "$PY" "$REPO/scripts/holdout/replay.py" "$wt" "$label" "$OUT/$label.json" "$@" 2>&1 | grep -v "^ \|^$" | tail -4)
done
echo "remove the worktrees with: git worktree remove <dir> (one per commit)"
