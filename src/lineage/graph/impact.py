"""The impact report: what breaks if a column or table changes (MVP plan F3).

Deterministic, from the graph alone - no LLM. For a column (or every column of a table) and
a change - `drop`, `rename` or `type` - it lists:

* **affected**: every downstream column or table, by hop, and HOW it is reached - as a
  `value`, an `influence` (grouping, ordering) or a `filter` (which rows exist). This is
  exactly `GraphTools.downstream`: a filter ends at a whole table and is reported there,
  not fanned out to every column built from that table.
* **edit**: the statements whose SQL must change, with the lines that name the column:
  - statements that READ it into lineage (its outgoing edges),
  - statements that WRITE it (its incoming edges),
  - statements that NAME it with no lineage edge at all - a join key's equality is
    plumbing and gives no edge (D-5), yet dropping that column still breaks the SQL. These
    come from the statement text, so they are marked `possible`: a word match, not proof.
* **unknowns**: boundaries on any statement involved, and refused statements that name the
  subject's table or an affected table - where the impact cannot be known.
"""

from __future__ import annotations

import csv
import io
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lineage.graph.tools import GraphTools

CHANGES = ("drop", "rename", "type")

FLOW = {"FEEDS": "value", "INFLUENCES": "influence", "FILTERS": "filter"}

CONSEQUENCE = {
    "drop": "Every statement in 'edit' fails until it is changed; every affected column "
    "loses this input.",
    "rename": "Every statement in 'edit' fails until it uses the new name; values downstream "
    "are unchanged once it does.",
    "type": "Statements in 'edit' may fail or change meaning (casts, comparisons, arithmetic); "
    "affected values may change.",
}

REASONS = {
    "reads": "reads it into lineage",
    "writes": "writes it",
    "names": "names it with no lineage edge (e.g. a join key) - possible",
    "reads_table": "reads the table",
    "writes_table": "writes the table",
}


def impact(tools: GraphTools, node: str, change: str = "drop", depth: int = 15) -> dict[str, Any]:
    """The impact report for `node` (a column or table id) under `change`."""
    if change not in CHANGES:
        raise ValueError(f"change must be one of {', '.join(CHANGES)}")
    found = tools._read(
        """
        MATCH (n {id: $id}) WHERE n:Column OR n:Table
        OPTIONAL MATCH (n)-[:HAS_COLUMN]->(c:Column)
        OPTIONAL MATCH (t:Table)-[:HAS_COLUMN]->(n)
        RETURN labels(n)[0] AS label, collect(DISTINCT c.id) AS columns, t.id AS owner
        """,
        id=node,
    )
    notes: list[str] = []
    if not found:
        # A column in no lineage edge is no graph node - a join key is the usual case
        # (D-5) - but SQL that touches its table can still name it, so look there.
        owner = node.rpartition(".")[0]
        if not owner or not tools._read("MATCH (t:Table {id: $id}) RETURN 1", id=owner):
            return {"subject": node, "change": change, "found": False}
        found = [{"label": "Column", "columns": [], "owner": owner}]
        notes.append(f"{node} is in no lineage edge: only SQL that names it can break.")
    label = found[0]["label"]
    is_table = label == "Table"
    columns = sorted(found[0]["columns"]) if is_table else [node]
    table = node if is_table else found[0]["owner"]

    affected = _affected(tools, columns, depth)
    edit = _edit(tools, columns, table, is_table)
    reached = {_table_of(a["node"], a["flow"]) for a in affected}
    tables = sorted(t for t in {table, *reached} if t)
    statements = sorted({e["statement"] for e in edit} | {a["statement"] for a in affected})
    unknowns = {
        "boundaries": tools._read(
            """
            MATCH (s:Statement)-[:BLOCKED_BY]->(b:Boundary) WHERE s.id IN $statements
            RETURN DISTINCT b.id AS id, b.kind AS kind, b.subject AS subject, s.id AS statement
            ORDER BY id, statement
            """,
            statements=statements,
        ),
        "refusals": tools._read(
            """
            MATCH (x:Refusal)-[:MENTIONS]->(t:Table) WHERE t.id IN $tables
            WITH x, collect(DISTINCT t.id) AS tables
            RETURN x.id AS id, x.code AS code, x.file AS file, x.line AS line,
                   x.reason AS reason, tables ORDER BY file, line
            """,
            tables=tables,
        ),
    }
    affected_tables = sorted(t for t in reached if t)
    return {
        "subject": node,
        "kind": "table" if is_table else "column",
        "change": change,
        "found": True,
        "columns": columns,
        "consequence": CONSEQUENCE[change],
        "summary": {
            "affected": len(affected),
            "affected_tables": len(affected_tables),
            "statements_to_edit": len(edit),
            "files_to_edit": len({e["file"] for e in edit}),
            "unknowns": len(unknowns["boundaries"]) + len(unknowns["refusals"]),
        },
        "affected": affected,
        "edit": edit,
        "unknowns": unknowns,
        "notes": [
            *notes,
            "A filter ends at a whole table: its rows may change, and so may everything "
            "built from that table. Run the report on that table's columns to follow it.",
            "'possible' edits are word matches in the statement text, not lineage facts.",
        ],
    }


def _affected(tools: GraphTools, columns: list[str], depth: int) -> list[dict[str, Any]]:
    """Each downstream node once, at its nearest hop, with the edge that reached it."""
    best: dict[str, dict[str, Any]] = {}
    for column in columns:
        for edge in tools.downstream(column, depth):
            row = {
                "node": edge["target"],
                "hops": edge["depth"],
                "flow": FLOW[edge["type"]],
                "transform": edge["transform"],
                "from": edge["source"],
                "edge": edge["id"],
                "statement": edge["statement"],
                "file": edge["file"],
                "line": edge["line"],
            }
            kept = best.get(row["node"])
            if kept is None or (row["hops"], row["edge"]) < (kept["hops"], kept["edge"]):
                best[row["node"]] = row
    return sorted(best.values(), key=lambda r: (r["hops"], r["node"]))


def _edit(
    tools: GraphTools, columns: list[str], table: str | None, is_table: bool
) -> list[dict[str, Any]]:
    """Statements to change, each with the lines that name the subject."""
    rows = tools._read(
        """
        UNWIND $columns AS cid
        MATCH (c:Column {id: cid})
        CALL (c) {
            MATCH (c)-[r:FEEDS|INFLUENCES|FILTERS]->() RETURN r.statement AS statement,
                  'reads' AS reason
            UNION
            MATCH ()-[r:FEEDS|INFLUENCES]->(c) RETURN r.statement AS statement,
                  'writes' AS reason
        }
        RETURN DISTINCT statement, reason
        UNION
        MATCH (s:Statement)-[:READS]->(:Table {id: $table})
        RETURN s.id AS statement, 'reads_table' AS reason
        UNION
        MATCH (s:Statement)-[:WRITES]->(:Table {id: $table})
        RETURN s.id AS statement, 'writes_table' AS reason
        """,
        columns=columns,
        table=table,
    )
    reasons: dict[str, set[str]] = {}
    for row in rows:
        reasons.setdefault(row["statement"], set()).add(row["reason"])

    details = {
        row["id"]: row
        for row in tools._read(
            """
            MATCH (s:Statement) WHERE s.id IN $ids
            OPTIONAL MATCH (f:File {id: s.file})
            RETURN s.id AS id, s.file AS file, s.line AS line, f.text AS text
            """,
            ids=sorted(reasons),
        )
    }
    # The lines to point at: the column's name, or the table's for a table.
    names = sorted({c.rsplit(".", 1)[-1] for c in columns})
    if is_table and table:
        names = [table.rsplit(".", 1)[-1]]
    shared = {} if is_table else _shared_names(tools, sorted(reasons), names, table)
    edit: list[dict[str, Any]] = []
    for statement, found in sorted(reasons.items()):
        info = details.get(statement)
        if info is None:
            continue
        lines = _mentions(info["text"], info["line"], names)
        lineage = found & {"reads", "writes"}
        if lineage:
            reason = ", ".join(REASONS[r] for r in sorted(lineage))
        elif is_table:
            reason = ", ".join(REASONS[r] for r in sorted(found))
        elif lines:
            reason = REASONS["names"]  # touches the table and names the column: no edge
            if shared.get(statement):
                reason += "; the name is also a column of " + ", ".join(shared[statement])
        else:
            continue  # touches the table, never names the column: unaffected
        edit.append(
            {
                "statement": statement,
                "file": info["file"],
                "line": info["line"],
                "reason": reason,
                "possible": not lineage and not is_table,
                "lines": lines,
            }
        )
    return sorted(edit, key=lambda e: (e["file"], e["line"]))


def _shared_names(
    tools: GraphTools, statements: list[str], names: list[str], table: str | None
) -> dict[str, list[str]]:
    """Per statement: the OTHER tables it reads that have a column of the same name.

    A word match cannot tell `o.customer_id` from `c.customer_id`; saying which other
    tables share the name is what makes a `possible` edit checkable by a person.
    """
    if not names:
        return {}
    rows = tools._read(
        """
        MATCH (s:Statement)-[:READS]->(t:Table)-[:HAS_COLUMN]->(c:Column)
        WHERE s.id IN $statements AND t.id <> $table
          AND toLower(split(c.id, '.')[size(split(c.id, '.')) - 1]) IN $names
        RETURN s.id AS statement, collect(DISTINCT t.id) AS tables
        """,
        statements=statements,
        names=[n.lower() for n in names],
        table=table or "",
    )
    return {row["statement"]: sorted(row["tables"]) for row in rows}


def _mentions(text: str | None, start: int | None, names: list[str]) -> list[int]:
    """File line numbers, within the statement starting at `start`, that name a column."""
    if not text or not start or not names:
        return []
    pattern = re.compile(
        r"(?<![\w$])\"?(" + "|".join(re.escape(n) for n in names) + r")\"?(?![\w$])", re.I
    )
    found: list[int] = []
    for offset, line in enumerate(text.splitlines()[start - 1 :]):
        code = line.split("--", 1)[0]
        if pattern.search(code):
            found.append(start + offset)
        if line.rstrip().endswith(";"):
            break
    return found


def _table_of(node: str, flow: str) -> str | None:
    if flow == "filter":
        return node  # a filter's target is the table itself
    if node.startswith("<result:"):
        return node.split(">", 1)[0] + ">"
    head, _, _ = node.rpartition(".")
    return head or None


# --- rendering ---------------------------------------------------------------------------


def to_markdown(report: dict[str, Any]) -> str:
    """A ticket-ready report."""
    if not report.get("found"):
        return f"# Impact of changing `{report['subject']}`\n\nNot found in the lineage graph.\n"
    s = report["summary"]
    out = [
        f"# Impact of {report['change']} `{report['subject']}`",
        "",
        f"**{report['consequence']}**",
        "",
        f"- Affected: **{s['affected']}** downstream columns or tables, in "
        f"{_n(s['affected_tables'], 'table')}",
        f"- To edit: **{_n(s['statements_to_edit'], 'statement')}** in "
        f"{_n(s['files_to_edit'], 'file')}",
        f"- Unknowns: {s['unknowns']}",
        "",
        "## Statements to edit",
        "",
        "| File | Statement line | Lines naming it | Why |",
        "|---|---|---|---|",
    ]
    for e in report["edit"]:
        lines = ", ".join(str(n) for n in e["lines"]) or "-"
        out.append(f"| `{e['file']}` | {e['line']} | {lines} | {e['reason']} |")
    if not report["edit"]:
        out.append("| - | - | - | nothing in the analysed SQL names it |")
    out += [
        "",
        "## Affected downstream",
        "",
        "| Hops | Column or table | How | Transform | From | Where |",
        "|---|---|---|---|---|---|",
    ]
    for a in report["affected"]:
        out.append(
            f"| {a['hops']} | `{a['node']}` | {a['flow']} | {a['transform']} | "
            f"`{a['from']}` | `{a['file']}`:{a['line']} |"
        )
    if not report["affected"]:
        out.append("| - | nothing downstream | | | | |")
    out += ["", "## Unknowns", ""]
    for b in report["unknowns"]["boundaries"]:
        out.append(f"- Boundary `{b['kind']}` on `{b['subject']}` in `{b['statement']}`")
    for x in report["unknowns"]["refusals"]:
        out.append(
            f"- Refused statement `{x['file']}`:{x['line']} ({x['code']}) names "
            f"{', '.join(f'`{t}`' for t in x['tables'])}: its effect is unknown"
        )
    if not s["unknowns"]:
        out.append("- None: the analysed SQL is fully covered on this path.")
    out += ["", "## Notes", "", *(f"- {n}" for n in report["notes"]), ""]
    return "\n".join(out)


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def to_csv(report: dict[str, Any]) -> str:
    """One row per item: section, then the fields a spreadsheet filters on."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["section", "node_or_file", "line", "hops", "how", "transform", "detail"])
    for e in report.get("edit", []):
        writer.writerow(
            ["edit", e["file"], e["line"], "", "", "", f"{e['reason']}; lines {e['lines']}"]
        )
    for a in report.get("affected", []):
        writer.writerow(
            ["affected", a["node"], a["line"], a["hops"], a["flow"], a["transform"], a["file"]]
        )
    for b in report.get("unknowns", {}).get("boundaries", []):
        writer.writerow(["unknown", b["statement"], "", "", "boundary", "", b["kind"]])
    for x in report.get("unknowns", {}).get("refusals", []):
        writer.writerow(["unknown", x["file"], x["line"], "", "refusal", "", x["code"]])
    return buffer.getvalue()
