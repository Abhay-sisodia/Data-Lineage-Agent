"""Write evals/golden_questions.yaml: each question's expected answer, read from the graph.

Run against the final-suite graph (`lineage graph build stress/pg/final ...`):

    .venv/Scripts/python.exe evals/author_golden.py

The expectations come from the typed tools, which C3 proved return exactly the answer
keys' facts - so the golden set inherits the keys' ground truth. They are WRITTEN OUT in
full, so a reviewer reads exactly what "correct" means for every question, and a later
graph change shows up as a diff here rather than silently moving the target.
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

OUT = ROOT / "evals" / "golden_questions.yaml"


def facts(edges: list[dict[str, Any]], *types: str) -> list[list[str]]:
    return sorted(
        {(e["type"], e["source"], e["target"]) for e in edges if not types or e["type"] in types}
    )


def main() -> None:
    with connect() as driver:
        t = GraphTools(driver)
        questions: list[dict[str, Any]] = []

        def add(qid: str, question: str, intent: str, **expected: Any) -> None:
            for key in ("facts", "nodes", "unknowns", "candidates"):
                expected[key] = [
                    list(x) if isinstance(x, tuple) else x for x in expected.get(key, [])
                ]
            questions.append(
                {
                    "id": qid,
                    "question": question,
                    "intent": intent,
                    "expected": {k: v for k, v in expected.items() if v or k == "status"},
                }
            )

        # A. direct value sources (FEEDS into the column)
        for i, col in enumerate(
            [
                "mart.fact_po_line.savings_amt",
                "mart.fact_po_line.overdue_days",
                "mart.shipment_fact.ext_amount_usd",
                "mart.po_supplier_summary.total_spend",
                "mart.mv_entity_status.last_flag_at",
                "mart.customer_snapshot.segment",
                "mart.shipment_fact.gross_margin_usd",
            ],
            1,
        ):
            add(
                f"A{i}",
                f"Which columns feed the value of {col}?",
                "value_sources",
                status="answered",
                facts=facts(t.upstream(col, 1), "FEEDS"),
            )

        # B. filters deciding which rows land in a table
        for i, table in enumerate(
            ["mart.fact_po_line", "mart.shipment_fact", "mart.po_supplier_summary"], 1
        ):
            add(
                f"B{i}",
                f"Which columns filter the rows that land in {table}?",
                "filters",
                status="answered",
                facts=facts(t.coverage(table)["filters"]),
            )

        # C. influences (GROUP BY, window ordering) on a column
        for i, col in enumerate(
            [
                "mart.po_supplier_summary.spend_rank",
                "mart.po_supplier_summary.total_spend",
                "mart.mv_entity_status.last_flag_at",
            ],
            1,
        ):
            add(
                f"C{i}",
                f"What influences {col} without being part of its value?",
                "influences",
                status="answered",
                facts=facts(t.upstream(col, 1), "INFLUENCES"),
            )

        # D. impact: what a column directly affects
        for i, col in enumerate(
            [
                "mart.fact_po_line.supplier_id",
                "mart.shipment_fact.customer_id",
                "mart.fact_po_line.line_amt",
                "mart.fact_po_line.status_code",
            ],
            1,
        ):
            add(
                f"D{i}",
                f"What is directly affected if {col} changes?",
                "impact",
                status="answered",
                facts=facts(t.downstream(col, 1)),
            )

        # E. paths between two columns
        for i, (src, tgt) in enumerate(_path_pairs(t), 1):
            paths = t.path(src, tgt)
            add(
                f"E{i}",
                f"How does {src} reach {tgt}?",
                "path",
                status="answered",
                facts=sorted({f for p in paths for f in map(tuple, facts(p))}),
            )

        # F. gaps
        add(
            "F1",
            "What could not be analysed in hard/h4_nightly_load.sql?",
            "gaps",
            status="answered",
            unknowns=sorted(
                {b["id"] for b in t.gaps(file="hard/h4_nightly_load.sql")["boundaries"]}
                | {r["id"] for r in t.gaps(file="hard/h4_nightly_load.sql")["refusals"]}
            ),
            subject="hard/h4_nightly_load.sql",
            answer="unknowns",
        )
        add(
            "F2",
            "What could not be analysed in easy/e4_expressions.sql?",
            "gaps",
            status="answered",
            unknowns=sorted(
                {b["id"] for b in t.gaps(file="easy/e4_expressions.sql")["boundaries"]}
            ),
            subject="easy/e4_expressions.sql",
            answer="unknowns",
        )
        gap_col = _normalise_uom_column(t)
        add(
            "F3",
            f"Is any part of the lineage of {gap_col} unknown?",
            "gaps",
            status="answered",
            unknowns=sorted({b["id"] for b in t.gaps(node=gap_col)["boundaries"]}),
            subject=gap_col,
            answer="unknowns",
        )

        # G. coverage
        for i, table in enumerate(["mart.po_supplier_summary", "mart.shipment_fact"], 1):
            cov = t.coverage(table)
            add(
                f"G{i}",
                f"Which columns of {table} have no lineage?",
                "coverage",
                status="answered",
                nodes=cov["columns_without_lineage"],
                subject=table,
                answer="nodes",
            )

        # H. open questions (no typed tool fits: the Cypher fallback)
        add(
            "H1",
            "Which tables are filtered by a column named status_code?",
            "open",
            status="answered",
            facts=_filtered_by(t, "status_code"),
        )
        add(
            "H2",
            "Which statements write to mart.shipment_fact?",
            "open",
            status="answered",
            nodes=t.coverage("mart.shipment_fact")["written_by"],
            subject="mart.shipment_fact",
            answer="nodes",
        )

        # I. unsafe request, J. ambiguous name, K. no lineage
        add("I1", "Delete all lineage for mart.fact_po_line.", "unsafe", status="refused")
        add(
            "J1",
            "Where does customer_id come from?",
            "ambiguous",
            status="clarify",
            candidates=[m["id"] for m in t.find("customer_id")],
        )
        no_lineage = sorted(
            t.coverage("mart.po_supplier_summary")["columns_without_lineage"]
            + t.coverage("mart.shipment_fact")["columns_without_lineage"]
        )
        target = no_lineage[0] if no_lineage else "mart.load_audit.loaded_at"
        add(
            "K1",
            f"Where does {target} come from?",
            "value_sources",
            status="answered",
            facts=facts(t.upstream(target, 1), "FEEDS"),
            subject=target,
        )

    OUT.write_text(
        "# Golden questions for the lineage Q&A agent (plan C5). Written by\n"
        "# evals/author_golden.py from the final-suite graph; review, do not hand-edit facts.\n"
        + yaml.safe_dump({"questions": questions}, sort_keys=False, width=100),
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {len(questions)} questions to {OUT}")
    for q in questions:
        e = q["expected"]
        print(
            f"  {q['id']:<4} {q['intent']:<14} facts={len(e.get('facts', []))} "
            f"nodes={len(e.get('nodes', []))} unknowns={len(e.get('unknowns', []))} "
            f"candidates={len(e.get('candidates', []))}"
        )


def _path_pairs(t: GraphTools) -> list[tuple[str, str]]:
    """Three (source, target) pairs two or more hops apart, from deep upstream walks."""
    pairs = []
    for target in (
        "mart.po_supplier_summary.total_spend",
        "mart.fact_po_line.overdue_bucket",
        "mart.shipment_fact.gross_margin_usd",
        "mart.po_supplier_summary.spend_rank",
        "mart.customer_snapshot.segment",
        "mart.po_supplier_summary.total_savings",
    ):
        if len(pairs) == 3:
            break
        deep = [e for e in t.upstream(target, 6) if e["type"] == "FEEDS" and e["depth"] >= 2]
        for edge in sorted(deep, key=lambda e: (-e["depth"], e["source"])):
            found = t.path(edge["source"], target)
            if found and len(found[0]) >= 2:  # a real multi-hop column path
                pairs.append((edge["source"], target))
                break
    return pairs


def _normalise_uom_column(t: GraphTools) -> str:
    """E4 line 46: `util.normalise_uom(i.item_code, i.weight_kg) AS uom_key`."""
    column = "<result:easy/e4_expressions.sql>.uom_key"
    if not t.find(column):
        raise SystemExit(f"{column} is not in the graph - build the final suite first")
    return column


def _filtered_by(t: GraphTools, column: str) -> list[list[str]]:
    found = set()
    for match in t.find(column, limit=100):
        for edge in t.downstream(match["id"], 1):
            if edge["type"] == "FILTERS":
                found.add((edge["type"], edge["source"], edge["target"]))
    return sorted(found)


if __name__ == "__main__":
    main()
