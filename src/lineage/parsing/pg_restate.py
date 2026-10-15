"""Rung 1 of the fallback ladder: PostgreSQL that SQLGlot cannot parse, restated by the TREE.

`pg_grammar` restates constructs SQLGlot has no rule for by pattern. Some cannot be found by
pattern at all, because what SQLGlot trips on is a COMBINATION that only a parse can see:

* **a boolean SET value** (holdout gap G1) - `SET is_closed = c.is_closed AND abs(b.r) <
  0.01`. SQLGlot reads `SET x = a AND b` as `(SET x = a) AND b` and fails, in UPDATE, in
  `ON CONFLICT DO UPDATE` and in `MERGE ... UPDATE` alike. PostgreSQL's tree says the whole
  `a AND b` is the value; wrapping exactly that span in parentheses changes nothing about
  its meaning or the columns it reads.
* **an alias-less JSON_TABLE** - PostgreSQL names it `json_table` implicitly; the D-30
  restatement needs a name, and without one the statement was dropped in silence. The
  implicit name is written out: `JSON_TABLE(...) AS json_table`.

Plan step 8 adds four more, each lineage-neutral:

* `SET col = DEFAULT` -> `SET col = NULL`: no source either way, and SQLGlot read DEFAULT as
  a column it then could not resolve;
* `EXPLAIN ANALYZE <stmt>` -> `<stmt>`: ANALYZE RUNS the statement, so its write happens
  (a plain EXPLAIN does not, and is left alone);
* `RETURNING WITH (OLD AS o, NEW AS n) ...` (PostgreSQL 18) -> the clause blanked: it only
  hands rows back to the client, and SQLGlot cannot parse the options;
* `ALTER TABLE t ALTER COLUMN c TYPE ... USING expr` -> `UPDATE t SET c = (expr)`: USING
  rewrites every row's value from its old one, which is exactly that UPDATE's lineage;
* `FROM t AS x (a, b, c)` -> `FROM (SELECT * FROM t) AS x (a, b, c)`: SQLGlot applies a
  column alias list to a derived table, by position, but not to a table;
* `FOR UPDATE OF a, b` -> `FOR UPDATE`: which rows are locked moves no value, and SQLGlot
  read the `OF` list as a second use of the alias;
* `CREATE RULE r AS ON <event> TO t DO [ALSO|INSTEAD] <one action>` -> the action as it runs:
  NEW and OLD are rows of `t`, so `INSERT INTO x (c) VALUES (NEW.v)` reads as
  `INSERT INTO x (c) SELECT new.v FROM t AS new`, and an UPDATE / DELETE action gains
  `FROM t AS new` (and `t AS old`). A rule with several actions is left as written.

Positions come from libpg_query (`pglast`), as byte offsets into the statement, and from its
own scanner, so string literals and comments can never be mistaken for structure. Nothing
adds or removes a newline, so every line number in the file holds.
"""

from __future__ import annotations

import re
from typing import Any

from pglast import ast
from pglast import parser as pg_parser
from pglast.visitors import Visitor

_SET_LIST_END = {",", "FROM", "WHERE", "RETURNING", "WHEN", "ON", ";"}


def _token_text(data: bytes, token: Any) -> str:
    return data[token.start : token.end + 1].decode("utf-8", errors="replace").upper()


class _Finder(Visitor):
    """The SET lists and alias-less JSON_TABLEs of one statement."""

    def __init__(self) -> None:
        super().__init__()
        self.set_lists: list[list[Any]] = []
        self.json_tables: list[Any] = []
        self.returning_with_options: list[Any] = []
        self.aliased_tables: list[Any] = []
        self.locked_rels = False

    def visit_UpdateStmt(self, ancestors: Any, node: Any) -> None:  # noqa: N802
        self.set_lists.append(list(node.targetList or ()))

    def visit_OnConflictClause(self, ancestors: Any, node: Any) -> None:  # noqa: N802
        self.set_lists.append(list(node.targetList or ()))

    def visit_MergeWhenClause(self, ancestors: Any, node: Any) -> None:  # noqa: N802
        if node.commandType is not None and node.commandType.name == "CMD_UPDATE":
            self.set_lists.append(list(node.targetList or ()))

    def visit_JsonTable(self, ancestors: Any, node: Any) -> None:  # noqa: N802
        if node.alias is None:
            self.json_tables.append(node)

    def visit_RangeVar(self, ancestors: Any, node: Any) -> None:  # noqa: N802
        if node.alias is not None and node.alias.colnames and node.location is not None:
            self.aliased_tables.append(node)

    def visit_LockingClause(self, ancestors: Any, node: Any) -> None:  # noqa: N802
        if node.lockedRels:
            self.locked_rels = True

    def visit_ReturningClause(self, ancestors: Any, node: Any) -> None:  # noqa: N802
        if node.options:
            self.returning_with_options.append(node)


def _value_span(targets: list[Any], index: int, tokens: list[Any], data: bytes) -> tuple[int, int]:
    """Byte span of the value of `targets[index]`: from after its `=` to before the next item."""
    target = targets[index]
    after = [t for t in tokens if t.start > target.location]
    equals = next(t for t in after if _token_text(data, t) == "=")
    start = next(t for t in after if t.start > equals.start).start
    limit = targets[index + 1].location if index + 1 < len(targets) else len(data)
    depth, end = 0, start
    for token in after:
        if token.start < start or token.start >= limit:
            continue
        word = _token_text(data, token)
        if word in ("(", "["):
            depth += 1
        elif word in (")", "]"):
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and word in _SET_LIST_END:
            break
        end = token.end + 1
    return start, end


def _closing_paren(tokens: list[Any], data: bytes, after: int) -> int | None:
    """Byte offset just past the `)` closing the first `(` at or after `after`."""
    depth = 0
    for token in tokens:
        if token.start < after:
            continue
        word = _token_text(data, token)
        if word == "(":
            depth += 1
        elif word == ")":
            depth -= 1
            if depth == 0:
                return int(token.end) + 1
    return None


def _rule(statement: Any) -> str | None:
    """A one-action rule as the statement its action runs (see the module docstring)."""
    from pglast.stream import RawStream

    actions = list(statement.actions or ())
    if len(actions) != 1 or statement.relation is None:
        return None
    rel = statement.relation
    table = f"{rel.schemaname}.{rel.relname}" if rel.schemaname else str(rel.relname)
    action = actions[0]
    text = RawStream()(action)
    rows = [f"{table} AS new"] + ([f"{table} AS old"] if re.search(r"\bold\.", text) else [])
    if isinstance(action, ast.InsertStmt):
        values = getattr(action.selectStmt, "valuesLists", None)
        if not values or len(values) != 1 or action.relation is None:
            return None
        cols = ", ".join(str(c.name) for c in action.cols or ())
        exprs = ", ".join(RawStream()(v) for v in values[0])
        target = RawStream()(action.relation)
        listed = f" ({cols})" if cols else ""
        return f"INSERT INTO {target}{listed} SELECT {exprs} FROM {', '.join(rows)}"
    if isinstance(action, (ast.UpdateStmt, ast.DeleteStmt)):
        keyword = "FROM" if isinstance(action, ast.UpdateStmt) else "USING"
        where = re.search(r"\sWHERE\s", text)
        joined = f" {keyword} {', '.join(rows)}"
        return text[: where.start()] + joined + text[where.start() :] if where else text + joined
    return None


def _whole(text: str, statement: Any, tokens: list[Any], data: bytes) -> str | None:
    """A whole-statement restatement (EXPLAIN ANALYZE, ALTER ... USING, a rule), lines kept."""
    if isinstance(statement, ast.RuleStmt):
        action = _rule(statement)
        if action is not None:
            return action + "\n" * (text.count("\n") - action.count("\n"))
    if isinstance(statement, ast.ExplainStmt) and any(
        getattr(o, "defname", "") == "analyze" for o in statement.options or ()
    ):
        found = re.match(r"(?is)\s*EXPLAIN\s*(\([^)]*\)|(\s*\b(ANALYZE|ANALYSE|VERBOSE)\b)*)", text)
        if found:
            return " " * found.end() + text[found.end() :]
    commands = list(getattr(statement, "cmds", None) or ())
    rel = getattr(statement, "relation", None)
    if isinstance(statement, ast.AlterTableStmt) and len(commands) == 1 and rel is not None:
        command = commands[0]
        subtype = command.subtype.name if command.subtype is not None else ""
        using = getattr(command.def_, "raw_default", None)
        if subtype == "AT_AlterColumnType" and using is not None:
            at = next((t.end + 1 for t in tokens if _token_text(data, t) == "USING"), None)
            if at is not None:
                name = f"{rel.schemaname}.{rel.relname}" if rel.schemaname else rel.relname
                expression = data[at:].decode("utf-8").strip()
                update = f"UPDATE {name} SET {command.name} = ({expression})"
                return update + "\n" * (text.count("\n") - update.count("\n"))
    return None


def restate(text: str) -> str:
    """One PostgreSQL statement with the constructs above restated; unchanged if none apply."""
    try:
        parsed = pg_parser.parse_sql(text)
    except pg_parser.ParseError:
        return text
    if parsed:
        whole = _whole(text, parsed[0].stmt, list(pg_parser.scan(text)), text.encode("utf-8"))
        if whole is not None:
            return restate(whole) if whole != text else text
    finder = _Finder()
    finder(parsed)
    data = text.encode("utf-8")
    tokens = list(pg_parser.scan(text))
    edits: list[tuple[int, int, bytes, bytes]] = []  # (start, end, before, after)
    for targets in finder.set_lists:
        for index, target in enumerate(targets):
            value = target.val
            boolop = getattr(value, "boolop", None)
            if isinstance(value, ast.BoolExpr) and boolop is not None and boolop.name != "NOT_EXPR":
                start, end = _value_span(targets, index, tokens, data)
                edits.append((start, end, b"(", b")"))
    for table in finder.json_tables:
        closing = _closing_paren(tokens, data, table.location or 0)
        if closing is not None:
            edits.append((closing, closing, b"", b" AS json_table"))
    for targets in finder.set_lists:
        for target in targets:
            if isinstance(target.val, ast.SetToDefault) and target.val.location is not None:
                at = target.val.location
                data = data[:at] + b"NULL   " + data[at + len("DEFAULT") :]  # same length
    if finder.returning_with_options:
        top = [t for t in tokens if _token_text(data, t) == "RETURNING"]
        if top:
            at = top[-1].start
            data = data[:at] + bytes(b if b == 0x0A else 0x20 for b in data[at:])
    for table in finder.aliased_tables:
        name = _QUALIFIED_NAME.match(data, table.location)
        if name is not None:
            edits.append((name.start(), name.end(), b"(SELECT * FROM ", b")"))
    if finder.locked_rels:
        for found in _LOCKED_OF.finditer(data):
            span = range(found.start(1), found.end(1))
            data = data[: span.start] + b" " * len(span) + data[span.stop :]
    if parsed and isinstance(parsed[0].stmt, ast.InsertStmt):
        from lineage.parsing.pg_referee import ends_in_cross_join

        if ends_in_cross_join(parsed[0].stmt):
            # ` WHERE TRUE` ends the FROM where PostgreSQL ends it: names nothing, same line.
            words = [_token_text(data, t).upper() for t in tokens]
            ons = [i for i in range(len(words) - 1) if words[i : i + 2] == ["ON", "CONFLICT"]]
            if ons:
                at = tokens[ons[-1]].start
                edits.append((at, at, b"WHERE TRUE ", b""))
    for start, end, before, after in sorted(edits, reverse=True):
        data = data[:start] + before + data[start:end] + after + data[end:]
    return data.decode("utf-8")


_QUALIFIED_NAME = re.compile(
    rb'(?:"[^"]+"|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:"[^"]+"|[A-Za-z_][\w$]*))*'
)
_LOCKED_OF = re.compile(
    rb"(?i)\bFOR\s+(?:NO\s+KEY\s+UPDATE|KEY\s+SHARE|UPDATE|SHARE)\s+"
    rb'(OF\s+[\w$."]+(?:\s*,\s*[\w$."]+)*)'
)


def restate_source(source: str, verdicts: list[Any]) -> str:
    """Every statement of `source` that needs it restated, found by its PostgreSQL verdict."""
    wanted = {
        "extra:boolean_value_in_update_set",
        "extra:json_table_without_alias",
        "node:SetToDefault",
        "stmt:ExplainStmt",
        "node:ReturningOption",
        "variant:AlterTableCmd.subtype=AT_AlterColumnType",
        "extra:column_alias_list",
        "extra:locking_clause_of",
        "stmt:RuleStmt",
        "extra:on_conflict_after_cross_join",
    }
    starts = [0] + [index + 1 for index, char in enumerate(source) if char == "\n"]
    for verdict in reversed(verdicts):
        if not verdict.valid or not verdict.constructs & wanted:
            continue
        at = source.find(verdict.text, starts[verdict.line - 1])
        if at < 0:
            continue
        source = source[:at] + restate(verdict.text) + source[at + len(verdict.text) :]
    return source
