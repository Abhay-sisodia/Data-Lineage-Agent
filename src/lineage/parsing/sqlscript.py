"""A front end for plain SQL scripts, used by PostgreSQL until it has a PL/pgSQL parser.

`lineage.parsing.frontend.Frontend` has ~24 methods. This implements the handful that a
SET-BASED analysis needs and answers the procedural ones with nothing — deliberately, and
this docstring is the declaration of that.

**Why this is a coherent thing to ship rather than a stub.** Band 0 has 71 SQLGlot
references and zero references to any grammar (ADR-0002 §1): given statement boundaries and
a dialect name, it resolves column lineage through projections, CTEs, joins, views and
alias chains without knowing what parsed the file. So statement boundaries are the *only*
thing band 0 needs a front end for, and finding them in a SQL script is lexing, not parsing.

WHAT IT DOES
  * splits a script into top-level statements, respecting string literals, quoted
    identifiers, line and block comments, and **dollar-quoted bodies**
  * names each statement's kind the way the Oracle front end does - `insert_statement`,
    `update_statement`, `merge_statement`, `delete_statement`, `query_block` - by asking
    SQLGlot what it parsed, rather than by matching the leading word
  * reports ONE SYNTHETIC UNIT for the whole script, whose body is the statement list

WHY THE SYNTHETIC UNIT MATTERS, and it is not cosmetic. Band 0's `SUPPORTED` set is
`{insert, merge, delete}` - **an `UPDATE` is analysed by `defuse`, which runs per unit.**
With no units, every `UPDATE` in a PostgreSQL script would be skipped before
`statements_seen` was even incremented: not counted, not refused, not visible in parse
coverage. That is the S2-04 shape exactly - two passes each correctly deciding a statement
is not theirs, and nobody owning the result. A script IS a unit of execution, so reporting
it as one is honest and it is what keeps `UPDATE` owned.

WHAT IT DOES NOT DO, AND SAYS SO
  A PL/pgSQL body arrives as a dollar-quoted string inside `CREATE FUNCTION … AS $$ … $$`.
  It is not part of the SQL parse tree, and reading it needs a grammar this project has not
  chosen yet (ADR-0002 §5). Such a routine is located and **refused by name**, so it is
  counted against parse coverage rather than silently absent. A body we can see and cannot
  read is a boundary, not an absence.
"""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from sqlglot import exp

from lineage.parsing.frontend import (
    Assignment,
    BodyShape,
    CursorDecl,
    Declared,
    Fetch,
    IfShape,
    LoopParam,
    LoopShape,
    UnitRef,
)
from lineage.parsing.plsql import ParsedStatement, ParsedUnit, Program

# What SQLGlot parsed -> the kind name the Oracle front end would have reported. Keeping the
# vocabulary identical is what lets `band0.SUPPORTED` and every refusal check work unchanged.
_KINDS: tuple[tuple[type[exp.Expression], str], ...] = (
    (exp.Insert, "insert_statement"),
    (exp.Update, "update_statement"),
    (exp.Delete, "delete_statement"),
    (exp.Merge, "merge_statement"),
    (exp.Select, "query_block"),
    # Every set operation, not `Union` alone: a bare EXCEPT or INTERSECT was no kind band 0
    # knows, so it was skipped before it was counted (finding FH-05).
    (exp.SetOperation, "query_block"),
)

SCRIPT_UNIT = "<script>"


def split_statements(source: str) -> list[tuple[int, str]]:
    """`(line, text)` for each top-level statement, by lexing rather than parsing.

    Splitting on `;` is wrong in four ways and each of them is handled: a semicolon inside
    a string literal, inside a quoted identifier, inside a comment, or inside a
    dollar-quoted body is not a statement boundary. **The dollar-quoted case is the one
    that matters most here**, because a PL/pgSQL body is full of semicolons and splitting
    on them would shred one routine into a dozen fragments, each of which would then be
    mis-parsed into confident nonsense.
    """
    statements: list[tuple[int, str]] = []
    buffer: list[str] = []
    index = 0
    line = 1
    # A statement starts at its first CODE token, line and text alike (finding F0-08).
    # Starting at the comments in front put a refusal on the first line of a comment
    # block, which can be far above the statement it describes. The text starts there too,
    # because everything that turns an offset in the text into a line - `last_line`, a
    # CTE's refusal - counts from the statement's line.
    start_line: int | None = None
    length = len(source)

    def flush() -> None:
        text = "".join(buffer).strip()
        if text:
            statements.append((start_line if start_line is not None else line, text))
        buffer.clear()

    while index < length:
        char = source[index]
        skipped = skip_lexeme(source, index)
        if skipped is not None:
            chunk = source[index:skipped]
            if start_line is None and not chunk.startswith(("--", "/*")):
                start_line = line
            if start_line is not None:
                buffer.append(chunk)
            line += chunk.count("\n")
            index = skipped
            continue

        if char == ";":
            flush()
            index += 1
            start_line = None
            continue

        if char == "\n":
            line += 1
        elif start_line is None and not char.isspace():
            start_line = line
        if start_line is not None:
            buffer.append(char)
        index += 1

    flush()
    return statements


_IDENT_CHAR = re.compile(r"[\w$]")
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_\x80-\uffff][\w\x80-\uffff]*)?\$")


def skip_lexeme(source: str, index: int) -> int | None:
    """The end of the literal, quoted identifier or comment starting at `index`, or None.

    One lexer for every pass that must not mistake text for code (B10). Each rule is
    PostgreSQL's own, and each one missing cost a run of statements:

    * `--` runs to the end of the line; `/* */` NESTS (B10-02);
    * `'...'` doubles its quote and has no backslash escape (standard_conforming_strings),
      but `E'...'` does: `E'it\\'s'` is one literal (B10-01). `U&'...'`, `B'...'`,
      `X'...'` and `N'...'` are standard strings;
    * `"..."` and `U&"..."` are identifiers, doubling their quote;
    * `$tag$...$tag$` is a literal, but only where a token can start: `a$1` is a name, and
      a tag never starts with a digit, so `$1` is a parameter.
    """
    char = source[index]
    two = source[index : index + 2]
    if two == "--":
        end = source.find("\n", index)
        return len(source) if end == -1 else end
    if two == "/*":
        depth, cursor = 1, index + 2
        while depth and cursor < len(source):
            pair = source[cursor : cursor + 2]
            if pair == "/*":
                depth, cursor = depth + 1, cursor + 2
            elif pair == "*/":
                depth, cursor = depth - 1, cursor + 2
            else:
                cursor += 1
        return cursor
    if char in ("'", '"'):
        escaped = (
            char == "'"
            and index > 0
            and source[index - 1] in "Ee"
            and (index < 2 or not _IDENT_CHAR.match(source[index - 2]))
        )
        cursor = index + 1
        while cursor < len(source):
            current = source[cursor]
            if escaped and current == "\\":
                cursor += 2
                continue
            if current == char:
                if source[cursor + 1 : cursor + 2] == char:
                    cursor += 2
                    continue
                return cursor + 1
            cursor += 1
        return len(source)
    if char == "$" and not (index > 0 and _IDENT_CHAR.match(source[index - 1])):
        tag = _DOLLAR_TAG.match(source, index)
        if tag is not None:
            closing = source.find(tag.group(0), tag.end())
            return len(source) if closing == -1 else closing + len(tag.group(0))
    return None


def statement_kind(text: str, dialect: str) -> str:
    """The kind name for one statement, from what SQLGlot parsed.

    Asked of the parser rather than of the leading word, because `WITH x AS (...) INSERT`
    and `WITH x AS (...) SELECT` both begin `WITH` and are not the same statement - and a
    CTE in front of a DML statement is the ordinary shape of a PostgreSQL load.
    """
    stripped = text.lstrip().lower()
    if stripped.startswith(("create or replace function", "create function")) or (
        stripped.startswith(("create or replace procedure", "create procedure"))
    ):
        return "create_routine"
    try:
        parsed = sqlglot.parse_one(text, dialect=dialect)
    except Exception:
        return "unparsed_statement"
    for node_type, kind in _KINDS:
        if isinstance(parsed, node_type):
            return kind
    return f"{type(parsed).__name__.lower()}_statement"


class SqlScriptFrontend:
    """A `Frontend` over a SQL script, with no procedural analysis.

    Holds its dialect only to classify statement kinds; everything else it does is lexical.
    """

    def __init__(self, dialect: str) -> None:
        self._dialect = dialect

    def parse(self, source: str) -> Program:
        statements = [
            ParsedStatement(
                kind=statement_kind(text, self._dialect),
                line=line,
                column=0,
                text=text,
            )
            for line, text in split_statements(source)
        ]
        # One synthetic unit: see the module docstring for why this is not cosmetic.
        units = [ParsedUnit(kind="script", name=SCRIPT_UNIT, line=1)] if statements else []
        return Program(
            source=source,
            tree=statements,
            errors=(),
            units=units,
            statements=statements,
            frontend=self,
            dialect=self._dialect,
        )

    # ---- units --------------------------------------------------------------------

    def units(self, tree: Any) -> list[UnitRef]:
        if not tree:
            return []
        return [UnitRef(kind="script", name=SCRIPT_UNIT, line=1, ctx=tree)]

    def package_bodies(self, tree: Any) -> list[tuple[str, Any]]:
        return []  # PostgreSQL has no packages (ADR-0002 §4)

    def enclosing_package(self, ctx: Any) -> str | None:
        return None

    # ---- declarations -------------------------------------------------------------
    #
    # A SQL script declares nothing. These are empty because there is nothing to find, not
    # because finding it was skipped - a PL/pgSQL body would declare plenty, and such a
    # body is refused by name rather than being read and found empty.

    def parameters(self, unit_ctx: Any) -> list[Declared]:
        return []

    def variables(self, unit_ctx: Any) -> list[Declared]:
        return []

    def cursors(self, unit_ctx: Any) -> list[CursorDecl]:
        return []

    def loop_params(self, unit_ctx: Any) -> list[LoopParam]:
        return []

    # ---- statements ---------------------------------------------------------------

    def assignment(self, statement_ctx: Any) -> Assignment | None:
        return None  # `:=` lives only inside a routine body

    def fetch(self, statement_ctx: Any) -> Fetch | None:
        return None

    def returns(self, unit_ctx: Any) -> list[str]:
        return []

    def exception_handlers(self, tree: Any) -> list[Any]:
        return []

    # ---- control flow ---------------------------------------------------------------

    def body(self, unit_ctx: Any) -> BodyShape | None:
        """The script itself, as a linear sequence with no handlers."""
        if not unit_ctx:
            return None
        return BodyShape(sequence=unit_ctx, end_line=unit_ctx[-1].line, handlers=())

    def statements_of(self, sequence_ctx: Any) -> list[Any]:
        return list(sequence_ctx or [])

    def statement_kind(self, statement_ctx: Any) -> str:
        return str(statement_ctx.kind)

    def shape(self, statement_ctx: Any) -> IfShape | LoopShape | None:
        return None  # a script has no branches or loops of its own

    def line(self, ctx: Any) -> int:
        if isinstance(ctx, list):
            return ctx[0].line if ctx else 1
        return int(ctx.line)

    def text(self, ctx: Any) -> str:
        if isinstance(ctx, list):
            return "\n".join(statement.text for statement in ctx)
        return str(ctx.text)

    def is_conditional(self, ctx: Any) -> bool:
        return False

    def assignments(self, tree: Any) -> list[Any]:
        return []

    def execute_immediates(self, tree: Any) -> list[tuple[Any, str | None]]:
        return []

    def statements(self, tree: Any) -> list[Any]:
        return list(tree or [])
