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

Positions come from libpg_query (`pglast`), as byte offsets into the statement, and from its
own scanner, so string literals and comments can never be mistaken for structure. Nothing
adds or removes a newline, so every line number in the file holds.
"""

from __future__ import annotations

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


def restate(text: str) -> str:
    """One PostgreSQL statement with the constructs above restated; unchanged if none apply."""
    try:
        parsed = pg_parser.parse_sql(text)
    except pg_parser.ParseError:
        return text
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
    for start, end, before, after in sorted(edits, reverse=True):
        data = data[:start] + before + data[start:end] + after + data[end:]
    return data.decode("utf-8")


def restate_source(source: str, verdicts: list[Any]) -> str:
    """Every statement of `source` that needs it restated, found by its PostgreSQL verdict."""
    wanted = {"extra:boolean_value_in_update_set", "extra:json_table_without_alias"}
    starts = [0] + [index + 1 for index, char in enumerate(source) if char == "\n"]
    for verdict in reversed(verdicts):
        if not verdict.valid or not verdict.constructs & wanted:
            continue
        at = source.find(verdict.text, starts[verdict.line - 1])
        if at < 0:
            continue
        source = source[:at] + restate(verdict.text) + source[at + len(verdict.text) :]
    return source
