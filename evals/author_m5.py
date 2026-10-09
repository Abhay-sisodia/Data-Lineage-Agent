"""Write evals/m5_questions.yaml: ten questions on the final suite's M5, answers from the graph.

M5 must be in the graph under the file name given (default pasted/query11111.sql, as
loaded from the Streamlit Parse SQL view):

    .venv/Scripts/python.exe evals/author_m5.py [FILE]
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

OUT = ROOT / "evals" / "m5_questions.yaml"


def main() -> None:
    file = sys.argv[1] if len(sys.argv) > 1 else "pasted/query11111.sql"
    r = f"<result:{file}>"
    with connect() as driver:
        t = GraphTools(driver)

        def into(col: str, *types: str) -> list[list[str]]:
            edges = t.upstream(f"{r}.{col}", 1)
            return sorted(
                [e["type"], e["source"], e["target"]]
                for e in edges
                if e["target"] == f"{r}.{col}" and (not types or e["type"] in types)
            )

        q: list[dict[str, Any]] = []

        def add(qid: str, question: str, intent: str, **expected: Any) -> None:
            q.append({"id": qid, "question": question, "intent": intent,
                      "expected": {"status": "answered", **expected}})

        add("M1", f"Where does {r}.ext come from, through all the CTE levels?",
            "value_sources", facts=into("ext", "FEEDS"), subject=f"{r}.ext")
        add("M2", f"Where does {r}.customer_name come from: crm.customer or crm.contact?",
            "value_sources", facts=into("customer_name", "FEEDS"), subject=f"{r}.customer_name")
        add("M3", f"Where does {r}.primary_email come from, and what decides which contact it is?",
            "value_sources", facts=into("primary_email"), subject=f"{r}.primary_email")
        add("M4", f"Where does {r}.carrier_name come from, including what picks the carrier?",
            "value_sources", facts=into("carrier_name"), subject=f"{r}.carrier_name")
        add("M5", f"What decides {r}.last_touch_ts and {r}.touch_sources? Give their value "
            "sources and what influences them.", "value_sources",
            facts=sorted(into("last_touch_ts") + into("touch_sources")),
            subject=f"{r}.last_touch_ts")
        add("M6", f"Which columns decide which rows appear in the result {r}?", "filters",
            facts=sorted([e["type"], e["source"], e["target"]] for e in t.coverage(r)["filters"]),
            subject=r)
        add("M7", f"If lgx.package_event.event_ts is wrong, which columns of {r} are affected?",
            "impact",
            facts=sorted([e["type"], e["source"], e["target"]]
                         for e in t.downstream("lgx.package_event.event_ts", 1)),
            subject="lgx.package_event.event_ts")
        add("M8", f"Does any column of crm.contact reach {r}.customer_status? List the crm.contact "
            "columns that do (none, if none do).", "value_sources", answer="nodes",
            nodes=[], subject=f"{r}.customer_status")
        add("M9", f"Where does {r}.site_segment come from?", "value_sources",
            facts=into("site_segment"), subject=f"{r}.site_segment")
        add("M10", f"Where does {r}.row_xmin come from?", "value_sources",
            facts=into("row_xmin"), subject=f"{r}.row_xmin")

    for item in q:
        item["expected"] = {k: v for k, v in item["expected"].items()
                            if v or k in ("status", "nodes") and item["expected"].get("answer")}
    OUT.write_text(
        f"# Ten questions on M5 as loaded into the graph ({file}); written by evals/author_m5.py.\n"
        + yaml.safe_dump({"questions": q}, sort_keys=False, width=110),
        encoding="utf-8", newline="\n",
    )
    for item in q:
        e = item["expected"]
        print(item["id"], "facts", len(e.get("facts", [])), "nodes", e.get("nodes"), e.get("answer", "edges"))


if __name__ == "__main__":
    main()
