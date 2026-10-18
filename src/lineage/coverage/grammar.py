"""The PostgreSQL grammar checklist: every construct PostgreSQL can parse, and what we do with it.

Every earlier probe of PostgreSQL coverage was a hand-picked list - `scripts/probe_postgres.py`
ran 44 constructs - and every new corpus found a construct nobody had listed. A list somebody
writes is a list of what they thought of. So the INVENTORY here is not written; it is read
out of PostgreSQL's own parser (libpg_query, through pglast):

* `stmt:<Node>`     - every statement node the grammar produces (`*Stmt`);
* `node:<Node>`     - every other raw-parse node (expressions, FROM items, clauses);
* `variant:<Node>.<field>=<VALUE>` - every value of a kind-deciding enum field on a node
                      lineage can depend on (`JoinExpr.jointype=JOIN_FULL`, ...);
* `clause:<Stmt>.<field>` - every clause of the five data-moving statements;
* `plpgsql:<stmt>`  - every PL/pgSQL statement type.

A node is a raw-parse node when pglast can print it back as SQL - the planner's nodes
(`Var`, `Aggref`, ...) cannot, and never appear in a parse tree. What is left out is left
out by name, with the reason, in `EXCLUDED` / `UNREACHABLE` / `DDL_DETAIL_FIELDS`.

Each checklist row (`stress/pg/grammar/checklist.yaml`) gives one inventory item a minimal
SQL snippet and a TARGET - what the analyser should do with it. The snippet is checked
twice by the tests: PostgreSQL must accept it, and its parse tree must contain the item it
claims to test. Its STATUS is then measured by running it through `analyse_source`, never
typed by hand, and a generated snapshot catches any change in what we do.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pglast import ast, enums
from pglast.printers import NODE_PRINTERS

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.parsing.pg_referee import CLAUSE_SKIP, DML, _slots
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parents[3]
GRAMMAR_DIR = ROOT / "stress" / "pg" / "grammar"
CHECKLIST = GRAMMAR_DIR / "checklist.yaml"
SNAPSHOT = GRAMMAR_DIR / "measured.json"
REPORT = ROOT / "docs" / "pg-coverage.md"

# --- the inventory -------------------------------------------------------------------------

EXCLUDED = {
    "stmt:SetOperationStmt": "built by PostgreSQL's analyser, never by its parser",
    "stmt:PLAssignStmt": "PL/pgSQL's internal form of `x := expr` - plpgsql:PLpgSQL_stmt_assign",
    "stmt:RawStmt": "the wrapper around every parsed statement",
}

# Enum fields that only describe DDL - which constraint, which ALTER TABLE action, which
# role, which partition strategy. None of their values moves a column; each node is still
# an inventory item of its own (`node:Constraint`, ...), only its variants are not.
DDL_DETAIL_FIELDS = {
    "AlterTableCmd.subtype": "ALTER TABLE actions - the ones that change data are extra rows",
    "AlterTableCmd.behavior": "CASCADE / RESTRICT",
    "Constraint.contype": "constraint kinds",
    "DefElem.defaction": "option list actions",
    "FunctionParameter.mode": "parameter modes - IN/OUT/INOUT are PL/pgSQL rows",
    "IndexElem.ordering": "index column order",
    "IndexElem.nulls_ordering": "index NULLS order",
    "IntoClause.onCommit": "ON COMMIT of a temporary table",
    "PartitionRangeDatum.kind": "MINVALUE / MAXVALUE bounds",
    "PartitionSpec.strategy": "RANGE / LIST / HASH partitioning",
    "PublicationObjSpec.pubobjtype": "logical replication publications",
    "RoleSpec.roletype": "role names",
    "LockingClause.waitPolicy": "NOWAIT / SKIP LOCKED",
}

# Enum values the SQL grammar never produces - the planner's join types, the analyser's
# sublink kinds. Listed so a missing row can never hide behind "unreachable".
UNREACHABLE = {
    "variant:JoinExpr.jointype=JOIN_SEMI": "planner only",
    "variant:JoinExpr.jointype=JOIN_ANTI": "planner only",
    "variant:JoinExpr.jointype=JOIN_RIGHT_SEMI": "planner only",
    "variant:JoinExpr.jointype=JOIN_RIGHT_ANTI": "planner only",
    "variant:JoinExpr.jointype=JOIN_UNIQUE_OUTER": "planner only",
    "variant:JoinExpr.jointype=JOIN_UNIQUE_INNER": "planner only",
    "variant:SubLink.subLinkType=CTE_SUBLINK": "planner only",
    "variant:SubLink.subLinkType=ROWCOMPARE_SUBLINK": "built by the analyser from EXPR_SUBLINK",
    "variant:MergeWhenClause.commandType=CMD_UNKNOWN": "not a MERGE action",
    "variant:MergeWhenClause.commandType=CMD_SELECT": "not a MERGE action",
    "variant:MergeWhenClause.commandType=CMD_MERGE": "not a MERGE action",
    "variant:MergeWhenClause.commandType=CMD_UTILITY": "not a MERGE action",
    "variant:FuncCall.funcformat=COERCE_EXPLICIT_CAST": "set on casts by the analyser",
    "variant:FuncCall.funcformat=COERCE_IMPLICIT_CAST": "set on casts by the analyser",
    "variant:RowExpr.row_format=COERCE_EXPLICIT_CAST": "not a row constructor form",
    "variant:RowExpr.row_format=COERCE_SQL_SYNTAX": "not a row constructor form",
    "variant:SelectStmt.op=SETOP_NONE": "the default - every plain SELECT",
    "variant:SelectStmt.limitOption=LIMIT_OPTION_DEFAULT": "the default - no LIMIT",
    "variant:InsertStmt.override=OVERRIDING_NOT_SET": "the default - no OVERRIDING",
    "variant:MergeWhenClause.override=OVERRIDING_NOT_SET": "the default - no OVERRIDING",
    "variant:OnConflictClause.action=ONCONFLICT_NONE": "the default - no ON CONFLICT",
    "variant:CommonTableExpr.ctematerialized=CTEMaterializeDefault": "the default",
    "variant:SortBy.sortby_dir=SORTBY_DEFAULT": "the default - no ASC/DESC",
    "variant:SortBy.sortby_nulls=SORTBY_NULLS_DEFAULT": "the default - no NULLS",
    "variant:GroupingSet.kind=GROUPING_SET_SIMPLE": "built by the analyser from a plain list",
    "variant:SubLink.subLinkType=MULTIEXPR_SUBLINK": "built by the analyser from EXPR_SUBLINK",
    "variant:XmlExpr.op=IS_DOCUMENT": "`x IS DOCUMENT` is parsed as a function call",
    "variant:JsonFormat.format_type=JS_FORMAT_DEFAULT": "the default - no FORMAT",
    "variant:JsonFormat.encoding=JS_ENC_DEFAULT": "the default - no ENCODING",
    "variant:JsonFuncExpr.wrapper=JSW_UNSPEC": "the default - no WRAPPER",
    "variant:LockingClause.strength=LCS_NONE": "the default - no locking clause",
    "variant:JsonBehavior.btype=JSON_BEHAVIOR_EMPTY": "bare EMPTY parses as EMPTY ARRAY",
    "variant:JsonFormat.format_type=JS_FORMAT_JSONB": "no SQL spelling - set internally",
    "variant:JsonFuncExpr.op=JSON_TABLE_OP": "JSON_TABLE parses as its own node, JsonTable",
    "variant:XmlExpr.op=IS_XMLSERIALIZE": "XMLSERIALIZE parses as its own node, XmlSerialize",
    "node:PartitionRangeDatum": "MINVALUE / MAXVALUE parse as names; the analyser builds it",
    "variant:JsonFuncExpr.quotes=JS_QUOTES_UNSPEC": "the default - no QUOTES",
    "variant:JsonTableColumn.wrapper=JSW_UNSPEC": "the default - no WRAPPER",
    "variant:JsonTableColumn.quotes=JS_QUOTES_UNSPEC": "the default - no QUOTES",
}

# Every PL/pgSQL statement type (PostgreSQL's plpgsql.h, `PLpgSQL_stmt_type`). pglast has
# no enum for these, so the list is written here and checked: every snippet's parse must
# name only types in it, and every type must have a row.
PLPGSQL_STATEMENTS = (
    "block", "assign", "if", "case", "loop", "while", "fori", "fors", "forc", "foreach_a",
    "exit", "return", "return_next", "return_query", "raise", "assert", "execsql",
    "dynexecute", "dynfors", "getdiag", "open", "fetch", "close", "perform", "call",
    "commit", "rollback",
)  # fmt: skip


def _printable() -> list[type]:
    return sorted((k for k in NODE_PRINTERS if isinstance(k, type)), key=lambda c: c.__name__)


def _enum_of(info: Any) -> type[enum.Enum] | None:
    kind = getattr(enums, info.c_type, None)
    return kind if isinstance(kind, type) and issubclass(kind, enum.Enum) else None


def inventory() -> dict[str, str]:
    """Every construct PostgreSQL's parser can produce, as `id -> area`."""
    items: dict[str, str] = {}
    statements = sorted(
        name
        for name, cls in vars(ast).items()
        if isinstance(cls, type) and issubclass(cls, ast.Node) and name.endswith("Stmt")
    )
    for name in statements:
        items[f"stmt:{name}"] = "statement"
    for cls in _printable():
        name = cls.__name__
        if not name.endswith("Stmt"):
            items[f"node:{name}"] = "expression / clause"
        for field_name, info in _slots(cls).items():
            kind = _enum_of(info)
            if kind is None or f"{name}.{field_name}" in DDL_DETAIL_FIELDS:
                continue
            if name.endswith("Stmt") and name not in DML:
                continue
            for value in kind:
                items[f"variant:{name}.{field_name}={value.name}"] = "variant"
    for name in DML:
        for field_name, info in _slots(getattr(ast, name)).items():
            if field_name not in CLAUSE_SKIP and _enum_of(info) is None:
                items[f"clause:{name}.{field_name}"] = "clause"
    for name in PLPGSQL_STATEMENTS:
        items[f"plpgsql:PLpgSQL_stmt_{name}"] = "PL/pgSQL"
    for key in (*EXCLUDED, *UNREACHABLE):
        items.pop(key, None)
    return items


# --- what the analyser does with it --------------------------------------------------------


def dictionary() -> Dictionary:
    return Dictionary.load(GRAMMAR_DIR / "dictionary.json")


def measure(sql: str, book: Dictionary) -> tuple[str, list[str]]:
    """The analyser's outcome for `sql`, and the `source -> target` column pairs it found.

    LINEAGE      edges came out, nothing refused
    PARTIAL:c    edges came out, and a part was refused with code c
    REFUSED:c    refused, code c
    UNCLASSIFIED:c  refused, but its columns are kept as `mention` edges (D-60)
    TABLE_LEVEL:c   refused, but its tables are kept as `opaque` edges (D-60)
    DECLARED:k   no edge, no refusal, a boundary names what stopped it
    COUNTED      nothing to trace, and the statement ledger says why (DDL, settings ...)
    EFFECT       the ledger lists it as an effect (DROP, TRUNCATE ...) - not modelled yet
    NOT_ANALYSED it can move data and nothing came of it - listed loudly by the ledger
    SILENT       not even in the ledger - cannot happen for PostgreSQL since plan step 3
    CRASH        the analyser raised
    HANG         the analyser did not finish within the timeout (`measure_all` only)
    """
    try:
        result = analyse_source(sql, book, AnalysisConfig(dialect="postgres"))
    except Exception as error:  # a crash is a measured outcome here, not an error
        return f"CRASH:{type(error).__name__}", []
    pairs = sorted(
        {
            f"{str(e.source).removeprefix('column:')} -> {str(e.target).removeprefix('column:')}"
            for e in [*result.edges, *result.join_keys]
        }
    )
    if {entry.status for entry in result.ledger} == {"effect"} and not result.refusals:
        # A rename's identity edges carry the contents over (holdout5); it is an effect.
        return ("EFFECT" if result.operations else "EFFECT_UNRECORDED"), pairs
    if result.edges or result.join_keys:
        if result.refusals:
            return f"PARTIAL:{result.refusals[0].code.value}", pairs
        return "LINEAGE", pairs
    if result.refusals:
        code = result.refusals[0].code.value
        statuses = {entry.status for entry in result.ledger}
        if "unclassified" in statuses:  # refused, columns kept as mentions (D-60)
            return f"UNCLASSIFIED:{code}", pairs
        if "table_level" in statuses:  # refused, tables kept as opaque links (D-60)
            return f"TABLE_LEVEL:{code}", pairs
        return f"REFUSED:{code}", pairs
    if result.boundaries:
        return f"DECLARED:{result.boundaries[0].kind.value}", pairs
    statuses = {entry.status for entry in result.ledger}
    if not statuses:
        return "SILENT", pairs
    if "not_analysed" in statuses:
        return "NOT_ANALYSED", pairs
    if statuses == {"effect"}:
        # Recorded only if an Operation fact names it (D-62); otherwise labelled, not modelled.
        return ("EFFECT" if result.operations else "EFFECT_UNRECORDED"), pairs
    return "COUNTED", pairs


TARGETS = ("lineage", "no_lineage", "effect", "refused", "declared")


def _reaches(pair: str, pairs: list[str]) -> bool:
    """`source -> target` as a path through the edges - a PL/pgSQL value passes through
    `variable:` nodes, a view or CTE through its own columns, before it lands."""
    source, _, target = pair.partition(" -> ")
    successors: dict[str, set[str]] = {}
    for edge in pairs:
        a, _, b = edge.partition(" -> ")
        successors.setdefault(a, set()).add(b)
    seen, frontier = {source}, [source]
    while frontier:
        node = frontier.pop()
        for nxt in successors.get(node, ()):
            if nxt == target:
                return True
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return False


def _worker(connection: Any) -> None:  # pragma: no cover - runs in a child process
    import logging

    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    book = dictionary()
    while True:
        sql = connection.recv()
        connection.send(measure(sql, book))


def measure_all(sqls: list[str], timeout: float = 20.0) -> list[tuple[str, list[str]]]:
    """`measure` every snippet in one child process, each with a time limit.

    A hang is a finding, not a reason for the checklist to stop: the child is killed, the
    snippet is recorded as HANG and a fresh child carries on with the next one.
    """
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    results: list[tuple[str, list[str]]] = []
    process, parent = None, None
    for sql in sqls:
        if process is None:
            parent, child = context.Pipe()
            process = context.Process(target=_worker, args=(child,), daemon=True)
            process.start()
        assert parent is not None
        parent.send(sql)
        if parent.poll(timeout):
            results.append(parent.recv())
        else:
            process.kill()
            process.join()
            process = None
            results.append(("HANG", []))
    if process is not None:
        process.kill()
        process.join()
    return results


def met(target: str, status: str, pairs: list[str], reach: list[str]) -> bool:
    """Does the measured outcome meet the row's target?

    lineage     column lineage, and every `reach` pair among it
    no_lineage  counted, nothing to trace (DDL, settings, grants ...)
    effect      the statement changes what a table holds or is (TRUNCATE, DROP ...) - an
                Operation fact on the relation (D-62)
    refused:C   refused with code C, honestly (dynamic SQL that is not constant ...)
    declared:K  a boundary of kind K names what stopped it
    """
    kind, _, detail = target.partition(":")
    if kind == "lineage":
        return status == "LINEAGE" and all(_reaches(pair, pairs) for pair in reach)
    if kind == "no_lineage":
        return status == "COUNTED"
    if kind == "refused":
        return status == f"REFUSED:{detail}"
    if kind == "declared":
        return status == f"DECLARED:{detail}"
    return status == "EFFECT"  # effect: an Operation fact on the relation (D-62)


@dataclass
class Row:
    id: str
    sql: str
    target: str
    reach: list[str] = field(default_factory=list)
    covers: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def items(self) -> list[str]:
        """Every inventory item this row stands for: its own id and those it `covers`."""
        return [self.id, *self.covers]


def load_rows(path: Path = CHECKLIST) -> list[Row]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [
        Row(
            id=row["id"],
            sql=row["sql"],
            target=row["target"],
            reach=list(row.get("reach") or []),
            covers=list(row.get("covers") or []),
            note=row.get("note", ""),
        )
        for row in data
    ]
