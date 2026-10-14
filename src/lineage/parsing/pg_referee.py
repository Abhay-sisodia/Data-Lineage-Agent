"""PostgreSQL's own parser as the referee for every PostgreSQL statement (plan step 2).

SQLGlot is the column-lineage workhorse, but its grammar is an approximation of
PostgreSQL's: it rejects some valid statements (`SET b = x AND y < 1`) and accepts some
invalid ones (`TIMESTAMP('1900-01-01')`, a Spark spelling). libpg_query - PostgreSQL's
grammar compiled out of the server, through pglast - settles both questions exactly:

* `verdict(text)` - is this statement PostgreSQL at all, what kind of statement is it, which
  tables does it read and write, and which grammar constructs does it contain;
* `set_aside(source)` - blank every top-level statement PostgreSQL rejects, so nothing is
  ever claimed from SQL the server would not run, and return them to be refused by name;
* `constructs(text)` - the grammar checklist's construct ids in a statement, PL/pgSQL bodies
  and their embedded SQL included (`lineage.coverage.grammar` measures what we do with each).

Only the top-level parse decides validity. A PL/pgSQL body is checked by PostgreSQL when
the function is created, against a live catalogue: `%ROWTYPE` of a table this process cannot
see fails offline while being perfectly valid, so a body that does not parse here is never
treated as invalid - only its constructs go unread.
"""

from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pglast import ast, parse_plpgsql
from pglast import parser as pg_parser

from lineage.parsing.sqlscript import split_statements

# The five statements that move data: their clauses are checklist items of their own.
DML = ("SelectStmt", "InsertStmt", "UpdateStmt", "DeleteStmt", "MergeStmt")
CLAUSE_SKIP = {"location", "stmt_location", "stmt_len"}

_WRITES = (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt, ast.MergeStmt)
_DROPPED_RELATIONS = {"OBJECT_TABLE", "OBJECT_VIEW", "OBJECT_MATVIEW", "OBJECT_FOREIGN_TABLE"}


def _slots(cls: Any) -> dict[str, Any]:
    """A pglast node class's fields, each with its C type (`SlotTypeInfo`)."""
    return dict(cls.__slots__)


def _name(rel: Any) -> str:
    return f"{rel.schemaname}.{rel.relname}" if rel.schemaname else str(rel.relname)


# --- constructs ----------------------------------------------------------------------------


def _walk(node: Any, out: set[str], ctes: set[str], tables: set[str]) -> None:
    if isinstance(node, (list, tuple)):
        for item in node:
            _walk(item, out, ctes, tables)
        return
    if not isinstance(node, ast.Node):
        return
    name = type(node).__name__
    out.add(f"stmt:{name}" if name.endswith("Stmt") else f"node:{name}")
    if isinstance(node, ast.RangeVar):
        tables.add(_name(node))
    elif isinstance(node, ast.CommonTableExpr):
        ctes.add(str(node.ctename))
    elif isinstance(node, ast.UpdateStmt) and any(
        isinstance(t.val, ast.BoolExpr) for t in node.targetList or ()
    ):
        # Holdout gap G1: a boolean expression as a SET value - SQLGlot cannot parse it.
        out.add("extra:boolean_value_in_update_set")
    for field_name in _slots(type(node)):
        value = getattr(node, field_name, None)
        if value is None:
            continue
        if isinstance(value, enum.Enum):
            out.add(f"variant:{name}.{field_name}={value.name}")
        elif name in DML and field_name not in CLAUSE_SKIP and value not in (False, 0, "", ()):
            out.add(f"clause:{name}.{field_name}")
        _walk(value, out, ctes, tables)


def _walk_plpgsql(node: Any, out: set[str], sql: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_plpgsql(item, out, sql)
    elif isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("PLpgSQL_stmt_"):
                out.add(f"plpgsql:{key}")
                if key == "PLpgSQL_stmt_execsql" and isinstance(value, dict):
                    _into(value, out)
            if key == "PLpgSQL_stmt_assign" and isinstance(value, dict) and _assigns_a_field(value):
                out.add("extra:plpgsql_record_field_assignment")
            if key == "PLpgSQL_expr" and isinstance(value, dict):
                sql.append(_as_statement(value))
            _walk_plpgsql(value, out, sql)


def _assigns_a_field(assign: dict[str, Any]) -> bool:
    """`r.v := ...` - the target before `:=` is a field of a record, not a variable."""
    query = str(assign.get("expr", {}).get("PLpgSQL_expr", {}).get("query", ""))
    return "." in query.split(":=", 1)[0]


def _selects_star(query: str) -> bool:
    """`SELECT * ...` / `SELECT t.* ...` - every column, into whatever the target is."""
    try:
        statement = pg_parser.parse_sql(query)[0].stmt
    except (pg_parser.ParseError, IndexError):
        return False
    targets = getattr(statement, "targetList", None) or ()
    return any(
        isinstance(t.val, ast.ColumnRef) and isinstance((t.val.fields or (None,))[-1], ast.A_Star)
        for t in targets
    )


def _into(execsql: dict[str, Any], out: set[str]) -> None:
    """The INTO forms the PL/pgSQL reader treats differently, named for the checklist."""
    if not execsql.get("into"):
        return
    query = str(execsql.get("sqlstmt", {}).get("PLpgSQL_expr", {}).get("query", ""))
    if "PLpgSQL_rec" in (execsql.get("target") or {}) or _selects_star(query):
        out.add("extra:plpgsql_select_star_into_record")
    if query.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "MERGE")):
        out.add("extra:plpgsql_returning_into")


def _as_statement(expr: dict[str, Any]) -> str:
    """An embedded PL/pgSQL expression as a statement PostgreSQL's parser accepts."""
    query = str(expr.get("query", ""))
    mode = expr.get("parseMode", 0)
    if mode in (0, "RAW_PARSE_DEFAULT"):
        return query
    if mode in (2, "RAW_PARSE_PLPGSQL_EXPR"):
        return f"SELECT {query}"
    for operator in (":=", "="):  # RAW_PARSE_PLPGSQL_ASSIGNn - "target := expression"
        if operator in query:
            return f"SELECT {query.split(operator, 1)[1]}"
    return f"SELECT {query}"


def _language(statement: Any) -> str:
    return next(
        (str(o.arg.sval) for o in statement.options or () if o.defname == "language"), ""
    ).lower()


def _routine_body(statement: Any, text: str) -> str | None:
    """The CREATE FUNCTION/PROCEDURE text to hand `parse_plpgsql`, DO wrapped as one."""
    if isinstance(statement, ast.DoStmt):
        body = next((o.arg.sval for o in statement.args or () if o.defname == "as"), None)
        if body is None:
            return None
        return f"CREATE FUNCTION pg_temp.do_block() RETURNS void LANGUAGE plpgsql AS $do${body}$do$"
    if isinstance(statement, ast.CreateFunctionStmt) and _language(statement) == "plpgsql":
        return text
    return None


def _routine_constructs(statement: Any, text: str, out: set[str], tables: set[str]) -> None:
    if isinstance(statement, ast.CreateFunctionStmt):
        if _language(statement) == "sql" and not statement.sql_body:
            out.add("extra:sql_function_body")
        if _language(statement) == "plpgsql" and any(
            getattr(p, "mode", None) is not None and p.mode.name == "FUNC_PARAM_OUT"
            for p in statement.parameters or ()
        ):
            out.add("extra:plpgsql_out_parameter")
    routine = _routine_body(statement, text)
    if routine is None:
        return
    try:
        tree = json.loads(json.dumps(parse_plpgsql(routine)))
    except Exception:
        out.add("plpgsql:unreadable_offline")
        return
    embedded: list[str] = []
    _walk_plpgsql(tree, out, embedded)
    for sql in embedded:
        try:
            parsed = pg_parser.parse_sql(sql)
        except pg_parser.ParseError:
            continue  # a fragment (a cursor's name, a RAISE format) - not SQL
        for inner in parsed:
            if isinstance(inner.stmt, ast.TruncateStmt):
                out.add("extra:plpgsql_truncate_then_reload")
            _walk(inner.stmt, out, set(), tables)


def constructs(sql: str) -> set[str]:
    """Every checklist construct id `sql` contains - PL/pgSQL bodies and their SQL included.

    Raises `pglast.parser.ParseError` when PostgreSQL itself would reject `sql`.
    """
    out: set[str] = set()
    for raw in pg_parser.parse_sql(sql):
        location, length = raw.stmt_location or 0, raw.stmt_len or len(sql)
        _walk(raw.stmt, out, set(), set())
        _routine_constructs(raw.stmt, sql[location : location + length], out, set())
    return out


# --- verdicts ------------------------------------------------------------------------------


@dataclass
class Verdict:
    """PostgreSQL's reading of one statement."""

    line: int
    end_line: int
    text: str
    valid: bool
    error: str = ""
    kind: str = ""
    """The statement node, `InsertStmt`, `CreateFunctionStmt` - exact, never guessed."""
    writes: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    constructs: set[str] = field(default_factory=set)


def _writes(statement: Any) -> list[str]:
    if isinstance(statement, _WRITES) and statement.relation is not None:
        return [_name(statement.relation)]
    if isinstance(statement, ast.CreateTableAsStmt) and statement.into is not None:
        return [_name(statement.into.rel)]
    if isinstance(statement, ast.SelectStmt) and statement.intoClause is not None:
        return [_name(statement.intoClause.rel)]
    if isinstance(statement, ast.ViewStmt):
        return [_name(statement.view)]
    if isinstance(statement, ast.TruncateStmt):
        return [_name(r) for r in statement.relations or ()]
    if isinstance(statement, ast.CopyStmt) and statement.is_from and statement.relation:
        return [_name(statement.relation)]
    if (
        isinstance(statement, ast.DropStmt)
        and statement.removeType is not None
        and statement.removeType.name in _DROPPED_RELATIONS
    ):
        return [".".join(str(part.sval) for part in name) for name in statement.objects or ()]
    return []


_PSQL_VARIABLE = re.compile(r"(?<![:\w]):(?!:)(['\"]?)([A-Za-z_]\w*)\1")


def _parse(text: str) -> tuple[Any, str]:
    """`text` parsed as PostgreSQL, and the text that was parsed.

    An unquoted psql variable (`WHERE id > :lo`, `FROM :tbl`) is substituted by psql before
    the server sees the statement, so it never makes a statement invalid: on failure the
    variables are tried as a value, then as a name. Validation only - the source is unchanged.
    """
    try:
        return pg_parser.parse_sql(text), text
    except pg_parser.ParseError:
        if not _PSQL_VARIABLE.search(text):
            raise
    for stand_in in ("NULL", "psql_variable"):

        def substitute(match: re.Match[str], stand_in: str = stand_in) -> str:
            return stand_in.ljust(len(match.group(0)))

        candidate = _PSQL_VARIABLE.sub(substitute, text)
        try:
            return pg_parser.parse_sql(candidate), candidate
        except pg_parser.ParseError:
            continue
    return pg_parser.parse_sql(text), text  # raises the original error


def verdict(text: str, line: int = 1) -> Verdict:
    """PostgreSQL's verdict on one statement starting at `line`."""
    end_line = line + text.count("\n")
    try:
        parsed, text_read = _parse(text)
    except pg_parser.ParseError as error:
        return Verdict(line, end_line, text, valid=False, error=str(error))
    if not parsed:
        return Verdict(line, end_line, text, valid=True, kind="")
    statement = parsed[0].stmt
    out: set[str] = set()
    ctes: set[str] = set()
    tables: set[str] = set()
    _walk(statement, out, ctes, tables)
    _routine_constructs(statement, text_read, out, tables)
    writes = _writes(statement)
    reads = sorted(t for t in tables if t not in writes and t not in ctes)
    return Verdict(
        line, end_line, text, valid=True, kind=type(statement).__name__,
        writes=writes, reads=reads, constructs=out,
    )  # fmt: skip


def verdicts(source: str) -> list[Verdict]:
    """A verdict for every top-level statement of a script, split the analyser's way."""
    return [verdict(text, line) for line, text in split_statements(source)]


def set_aside(source: str) -> tuple[str, list[Verdict]]:
    """`source` with every statement PostgreSQL rejects blanked, and those statements.

    Blanked to whitespace with its newlines kept, so every later line number holds and no
    front end, classifier or SQLGlot ever reads it. The caller refuses each one by name.
    """
    rejected = [v for v in verdicts(source) if not v.valid]
    starts = [0] + [index + 1 for index, char in enumerate(source) if char == "\n"]
    for item in rejected:
        at = source.find(item.text, starts[item.line - 1])
        if at < 0:
            continue
        blank = "".join(ch if ch == "\n" else " " for ch in item.text)
        source = source[:at] + blank + source[at + len(item.text) :]
    return source, rejected
