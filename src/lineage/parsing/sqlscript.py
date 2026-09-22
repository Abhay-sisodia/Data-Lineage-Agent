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
    (exp.Union, "query_block"),
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
    start_line = 1
    length = len(source)

    def flush() -> None:
        text = "".join(buffer).strip()
        if text:
            statements.append((start_line, text))
        buffer.clear()

    while index < length:
        char = source[index]
        two = source[index : index + 2]

        if two == "--":
            end = source.find("\n", index)
            end = length if end == -1 else end
            buffer.append(source[index:end])
            index = end
            continue

        if two == "/*":
            end = source.find("*/", index + 2)
            end = length if end == -1 else end + 2
            chunk = source[index:end]
            buffer.append(chunk)
            line += chunk.count("\n")
            index = end
            continue

        if char in ("'", '"'):
            closing = source.find(char, index + 1)
            while closing != -1 and source[closing : closing + 2] == char * 2:
                closing = source.find(char, closing + 2)
            end = length if closing == -1 else closing + 1
            chunk = source[index:end]
            buffer.append(chunk)
            line += chunk.count("\n")
            index = end
            continue

        if char == "$":
            tag = _dollar_tag(source, index)
            if tag is not None:
                closing = source.find(tag, index + len(tag))
                end = length if closing == -1 else closing + len(tag)
                chunk = source[index:end]
                buffer.append(chunk)
                line += chunk.count("\n")
                index = end
                continue

        if char == ";":
            flush()
            index += 1
            # A statement starts at the first line with content after the separator.
            start_line = line
            continue

        if char == "\n":
            line += 1
            if not "".join(buffer).strip():
                start_line = line
        buffer.append(char)
        index += 1

    flush()
    return statements


def _dollar_tag(source: str, index: int) -> str | None:
    """`$$` or `$tag$` starting at `index`, or None if this `$` opens neither."""
    end = source.find("$", index + 1)
    if end == -1:
        return None
    body = source[index + 1 : end]
    if body and not body.replace("_", "").isalnum():
        return None
    return source[index : end + 1]


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
