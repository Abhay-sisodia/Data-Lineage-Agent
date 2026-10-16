"""Write evals/business_questions.yaml: five multi-hop business questions, answers from the graph.

Run against the final-suite graph:  .venv/Scripts/python.exe evals/author_business.py

Each expectation is one fixed Cypher traversal, stated beside the question, so "correct"
is exactly reproducible. Answers are lists of NODES (columns), scored as exact sets.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lineage.graph.neo4j_conn import connect  # noqa: E402
from lineage.graph.tools import GraphTools  # noqa: E402

OUT = ROOT / "evals" / "business_questions.yaml"

IMPACT = """
MATCH (s:Column {id: $id})-[:FEEDS|INFLUENCES*1..15]->(t:Column)
WHERE t.id STARTS WITH 'mart.'
RETURN DISTINCT t.id AS id ORDER BY id
"""
ROOTS = """
MATCH (t:Column {id: $id})<-[:FEEDS*1..15]-(s)
WHERE NOT EXISTS { ()-[:FEEDS]->(s) }
RETURN DISTINCT s.id AS id ORDER BY id
"""
FILTERS_ON_LINEAGE = """
MATCH (t:Column {id: $id})
OPTIONAL MATCH (t)<-[:FEEDS*1..15]-(u:Column)
WITH t, collect(DISTINCT u) AS ups
UNWIND ([t] + ups) AS c
MATCH (tab:Table)-[:HAS_COLUMN]->(c)
MATCH (f)-[:FILTERS]->(tab)
RETURN DISTINCT f.id AS id ORDER BY id
"""


def main() -> None:
    with connect() as driver:
        t = GraphTools(driver)

        def nodes(query: str, node: str) -> list[str]:
            return [row["id"] for row in t._read(query, id=node)]

        fx = "gold.fx_rate.conversion_rate"
        dropped = "s2p.po_distribution.amount_cancelled"
        spend = "mart.po_supplier_summary.total_spend"
        margin = "mart.shipment_fact.gross_margin_usd"
        gaps = t.gaps(node=margin)
        questions: list[dict[str, Any]] = [
            {
                "id": "X1",
                "subject": fx,
                "question": f"If {fx} is wrong, which columns in mart tables are affected, "
                "directly or through intermediate tables (as a value or an influence)? "
                "List the affected mart columns.",
                "intent": "impact_multi_hop",
                "expected": {"status": "answered", "nodes": nodes(IMPACT, fx)},
                "note": "FEEDS|INFLUENCES closure downstream, mart.* columns only",
            },
            {
                "id": "X2",
                "subject": dropped,
                "question": f"We plan to drop {dropped}. Which mart columns would be affected, "
                "directly or indirectly (as a value or an influence)? List them.",
                "intent": "impact_multi_hop",
                "expected": {"status": "answered", "nodes": nodes(IMPACT, dropped)},
                "note": "FEEDS|INFLUENCES closure downstream, mart.* columns only",
            },
            {
                "id": "X3",
                "subject": spend,
                "question": f"Trace {spend} back through every intermediate table to its ultimate "
                "source columns (columns nothing feeds), following value flow only. List them.",
                "intent": "root_cause_multi_hop",
                "expected": {"status": "answered", "nodes": nodes(ROOTS, spend)},
                "note": "FEEDS closure upstream; roots have no incoming FEEDS",
            },
            {
                "id": "X4",
                "subject": spend,
                "question": f"Which columns filter the rows anywhere along the value lineage of "
                f"{spend} - on its own table and on every table whose columns feed it? "
                "List the filtering columns.",
                "intent": "governance_multi_hop",
                "expected": {"status": "answered", "nodes": nodes(FILTERS_ON_LINEAGE, spend)},
                "note": "FILTERS into the tables of every column on the FEEDS closure",
            },
            {
                "id": "X5",
                "subject": margin,
                "question": f"Is {margin} fully traceable to its ultimate source columns? List "
                "those source columns (value flow only) and report every boundary or "
                "refusal where knowledge stops.",
                "intent": "audit_multi_hop",
                "expected": {
                    "status": "answered",
                    "nodes": nodes(ROOTS, margin),
                    "unknowns": sorted(
                        {b["id"] for b in gaps["boundaries"]} | {r["id"] for r in gaps["refusals"]}
                    ),
                },
                "note": "FEEDS roots, plus gaps(node) unknowns",
            },
        ]
    for q in questions:
        q["expected"] = {k: v for k, v in q["expected"].items() if v or k == "status"}
        q["expected"]["subject"] = q.pop("subject")
        q["expected"]["answer"] = "nodes"
    OUT.write_text(
        "# Five multi-hop business questions (written by evals/author_business.py).\n"
        + yaml.safe_dump({"questions": questions}, sort_keys=False, width=100),
        encoding="utf-8",
        newline="\n",
    )
    for q in questions:
        e = q["expected"]
        print(f"{q['id']} nodes={len(e.get('nodes', []))} unknowns={len(e.get('unknowns', []))}")


if __name__ == "__main__":
    main()
