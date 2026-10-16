#!/bin/sh
# Start-up for the review bundle's app container: seed PostgreSQL, build the lineage graph
# on first start, then serve the UI. Neo4j and PostgreSQL are healthy before this runs
# (depends_on in docker-compose.review.yml).
set -e
cd /app

echo "[lineage] loading the suites' DDL into PostgreSQL"
python scripts/pg_seed.py

files=$(python -c "
from lineage.graph.neo4j_conn import connect
with connect() as driver:
    print(driver.execute_query('MATCH (f:File) RETURN count(f) AS n').records[0]['n'])
")
if [ "$files" = "0" ]; then
    echo "[lineage] building the lineage graph from the final suite (first start only)"
    lineage graph build stress/pg/final --dictionary stress/pg/final/dictionary.json
else
    echo "[lineage] lineage graph already holds $files files"
fi

echo "[lineage] UI ready on http://localhost:8501"
exec python -m streamlit run frontend/app.py \
    --server.address=0.0.0.0 --server.port=8501 \
    --server.headless=true --browser.gatherUsageStats=false
