"""Write evals/hard_questions.yaml: ten harder questions on M5 - multi-hop, filters, aggregation.

Every expected answer is computed from the graph (set operations over its edges), never typed
by hand. M5 must be in the graph under the file name given (default pasted/query11111.sql):

    .venv/Scripts/python.exe evals/author_hard.py [FILE]
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

OUT = ROOT / "evals" / "hard_questions.yaml"

LEFT_JOINED = ("crm.customer_site.country_code", "crm.contact.is_primary",
               "gold.dim_status.datasource_num_id")
"""Filter columns inside the LEFT-JOINed CTEs site, nested and status (M5 lines 67, 78, 96)."""


def main() -> None:
    file = sys.argv[1] if len(sys.argv) > 1 else "pasted/query11111.sql"
    r = f"<result:{file}>"
    with connect() as driver:
        t = GraphTools(driver)
        edges: list[list[str | None]] = [
            [row["t"], row["s"], row["x"], row["tr"]]
            for row in t._read(
                "MATCH (s)-[e:FEEDS|FILTERS|INFLUENCES]->(x) WHERE e.file = $file "
                "RETURN type(e) AS t, s.id AS s, x.id AS x, e.transform AS tr",
                file=file,
            )
        ]

    def facts(pred: Any) -> list[list[str]]:
        return sorted([e[0], e[1], e[2]] for e in edges if pred(*e))  # type: ignore[misc]

    filter_sources = {s for typ, s, _, _ in edges if typ == "FILTERS"}
    value_sources = {s for typ, s, _, _ in edges if typ == "FEEDS"}
    aggregated = sorted({x for typ, _, x, tr in edges if typ == "FEEDS" and tr == "aggregated"})

    q: list[dict[str, Any]] = []

    def add(qid: str, question: str, intent: str, **expected: Any) -> None:
        q.append({"id": qid, "question": question, "intent": intent,
                  "expected": {"status": "answered", **expected}})

    add("H1", f"Which columns of {r} are computed by an aggregate function (MAX, COUNT, SUM ...)? "
        "List the result columns only.", "aggregation", answer="nodes", nodes=aggregated, subject=r)
    add("H2", f"{r}.touch_sources counts distinct event sources per shipment. Which source column, "
        "filtered before the aggregation, restricts which activity rows are counted, and which "
        "columns decide the grouping?", "aggregation",
        facts=facts(lambda typ, s, x, tr: x == f"{r}.touch_sources"), subject=f"{r}.touch_sources")
    add("H3", f"Which source columns restrict which rows appear in {r} but never supply a value to "
        "any of its columns?", "filters", answer="nodes",
        nodes=sorted(filter_sources - value_sources), subject=r)
    add("H4", f"Which source columns both restrict the rows of {r} and supply a value to one of "
        "its columns?", "filters", answer="nodes", nodes=sorted(filter_sources & value_sources),
        subject=r)
    add("H5", f"Some columns of {r} come from LEFT-JOINed CTEs that filter their own rows, so "
        "those columns are NULL when the filter fails. Which result columns are affected that "
        "way, and by which filter column?", "filters",
        facts=facts(lambda typ, s, x, tr: typ == "FEEDS" and s in LEFT_JOINED), subject=r)
    add("H6", f"If evt.activity.kind is mis-tagged, which columns of {r} change, and do they "
        "change through an aggregate?", "impact",
        facts=facts(lambda typ, s, x, tr: s == "evt.activity.kind"), subject="evt.activity.kind")
    add("H7", f"Which source columns decide how events are grouped into {r}.last_touch_ts - the "
        "grouping keys, not the aggregated values?", "aggregation",
        facts=facts(lambda typ, s, x, tr: typ == "INFLUENCES" and x == f"{r}.last_touch_ts"),
        subject=f"{r}.last_touch_ts")
    add("H8", f"Which columns of {r} does any column of lgx.package_event affect, as a value or as "
        "a grouping key?", "impact",
        facts=facts(lambda typ, s, x, tr: s.startswith("lgx.package_event.")),
        subject="lgx.package_event")
    add("H9", f"{r}.ext is filtered (ext > 0) on its way to the result. Which source columns does "
        "that row filter rely on, and do the same columns supply ext's value?", "filters",
        facts=facts(lambda typ, s, x, tr: s in ("lgx.shipment_line.shipped_qty",
                                                "lgx.shipment_line.unit_selling_price")
                    and x in (r, f"{r}.ext")),
        subject=f"{r}.ext")
    add("H10", f"Is any column of {r} an aggregate over lgx.shipment_line (e.g. SUM of quantity or "
        "value)? List the result columns that are (none, if none are).", "aggregation",
        answer="nodes",
        nodes=sorted({x for typ, s, x, tr in edges if typ == "FEEDS" and tr == "aggregated"
                      and s.startswith("lgx.shipment_line.")}),
        subject="lgx.shipment_line")

    for item in q:
        item["expected"] = {k: v for k, v in item["expected"].items()
                            if v or (k in ("status", "nodes") and item["expected"].get("answer"))}
    OUT.write_text(
        f"# Ten harder questions on M5 ({file}): multi-hop, filters, aggregation;\n"
        "# written by evals/author_hard.py from the graph.\n"
        + yaml.safe_dump({"questions": q}, sort_keys=False, width=110),
        encoding="utf-8", newline="\n",
    )
    for item in q:
        e = item["expected"]
        print(item["id"], "facts", len(e.get("facts", [])), "nodes", e.get("nodes"))


if __name__ == "__main__":
    main()
