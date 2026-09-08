"""Dynamic SQL that only looks dynamic (T3.2).

Two jobs, and they pull in opposite directions.

**Recover what is knowable.** `EXECUTE IMMEDIATE 'INSERT INTO t SELECT ...'` is not
dynamic in any sense that matters — the statement is fixed before the program runs. So is
one assembled from string literals. Every edge recovered this way is ordinary AST
evidence, and treating it as unrecoverable would drop it a tier for no reason and inflate
the unresolved-dynamic-SQL count in the coverage statement, which is a number customers
read hard.

**Refuse everything else, loudly.** `'UPDATE ' || p_column || ' = 1'` has constant
fragments and a variable in the middle. Folding the parts that are available and guessing
the rest is precisely the plausible-wrong-answer failure this project keeps finding, so
constant-ness is a MUST property: every operand on every path, or nothing.

---

**The second job is subtler and it is what T3.0 exposed.** A variable holding statement
TEXT is not a variable carrying data, and def-use cannot tell them apart. Left alone it
produces:

    p_column -> v_sql      v_stmt -> v_stmt      v_cursor -> v_rows

Every one is a true def-use fact and none is a lineage fact. `p_column` supplies part of a
*statement*; nothing downstream carries its value into a column. The distinction is not
"variables are noise" — ADR-0001 §3 makes variables first-class, and
`ref_policy.window_days -> v_days -> v_cutoff -> row filter` is exactly the chain this
phase exists to trace. The line is **data-carrying versus text-carrying**: `v_cutoff` ends
up selecting rows, `v_sql` ends up at `EXECUTE IMMEDIATE`.

Carriers are identified by where they are USED, not by name or type, because a naming
convention is not a semantic fact — the same reasoning that stopped `tmp_recent` being
called temporary in T2.5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lineage.ir.model import BoundaryKind, Declared
from lineage.parsing.generated.PlSqlParser import PlSqlParser
from lineage.parsing.plsql import (
    ParsedStatement,
    Program,
    iter_contexts,
    most_specific,
    rule_name,
    source_slice,
)

__all__ = [
    "DynamicSite",
    "Resolution",
    "fold_constant",
    "resolve_dynamic_sql",
]

# `DBMS_SQL.PARSE(cursor, statement, mode)` — the statement is the second argument.
DBMS_SQL_PARSE = re.compile(r"^\s*(?:SYS\.)?DBMS_SQL\.PARSE\s*\((.*)\)\s*$", re.IGNORECASE | re.S)

# Any DBMS_SQL entry point. Its first argument is a cursor HANDLE — API plumbing that
# carries no data, and a def-use edge through it says nothing true.
DBMS_SQL_CALL = re.compile(r"^\s*(?:SYS\.)?DBMS_SQL\.(\w+)\s*\((.*)\)\s*$", re.IGNORECASE | re.S)
DBMS_SQL_OPEN = re.compile(r"(?:SYS\.)?DBMS_SQL\.OPEN_CURSOR", re.IGNORECASE)


@dataclass(frozen=True)
class DynamicSite:
    """One place a statement is handed to the database as text."""

    line: int
    unit: str
    kind: str
    carrier: str | None
    argument: str
    recovered: str | None = None
    refusal: str | None = None

    def describe(self) -> str:
        detail = self.refusal or "resolved by constant propagation"
        return f"{self.kind} at line {self.line}: {detail}"


@dataclass
class Resolution:
    """What the whole source's dynamic sites came to."""

    sites: list[DynamicSite] = field(default_factory=list)
    carriers: set[str] = field(default_factory=set)
    recovered: dict[int, str] = field(default_factory=dict)
    statements: list[ParsedStatement] = field(default_factory=list)

    @property
    def boundaries(self) -> list[str]:
        """Sites that could not be resolved — declared, counted, never guessed."""
        return [
            Declared(
                site.describe(),
                kind=BoundaryKind.DYNAMIC_SQL,
                subject=f"EXECUTE IMMEDIATE:{site.line}",
            )
            for site in self.sites
            if site.recovered is None
        ]


# --- constant folding ------------------------------------------------------------------


def _split_concatenation(text: str) -> list[str] | None:
    """Split a PL/SQL expression on top-level `||`, respecting quotes and parentheses.

    Returns None if the text does not tokenise cleanly, which is itself a refusal — a
    string we cannot even split is certainly not one we can prove constant.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if char == "'":
            # Consume the whole literal, including '' escapes, so a `||` inside a string
            # is never mistaken for an operator.
            current.append(char)
            index += 1
            while index < length:
                current.append(text[index])
                if text[index] == "'":
                    if index + 1 < length and text[index + 1] == "'":
                        current.append(text[index + 1])
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            else:
                return None  # unterminated literal
            continue

        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
        elif char == "|" and depth == 0 and index + 1 < length and text[index + 1] == "|":
            parts.append("".join(current))
            current = []
            index += 2
            continue

        current.append(char)
        index += 1

    if depth != 0:
        return None
    parts.append("".join(current))
    return parts


def _literal_value(part: str) -> str | None:
    """The value of a quoted literal, or None if this is not one."""
    text = part.strip()
    if len(text) < 2 or not text.startswith("'") or not text.endswith("'"):
        return None
    inner = text[1:-1]
    # A lone quote inside means the literal ended early - not a single literal.
    if re.search(r"(?<!')'(?!')", inner.replace("''", "\x00\x00")):
        return None
    return inner.replace("''", "'")


def fold_constant(expression: str, environment: dict[str, str]) -> str | None:
    """The expression's value if every operand is known, else None.

    MUST, not may. One unknown operand and the whole expression is unknown — there is no
    partial answer here, because half a statement resolves to a different statement.
    """
    parts = _split_concatenation(expression)
    if parts is None:
        return None

    pieces: list[str] = []
    for part in parts:
        literal = _literal_value(part)
        if literal is not None:
            pieces.append(literal)
            continue
        name = part.strip().upper()
        if name in environment:
            pieces.append(environment[name])
            continue
        return None
    return "".join(pieces)


# --- finding the sites -----------------------------------------------------------------


def _unit_of(program: Program, line: int) -> str:
    candidates = [unit for unit in program.units if unit.line <= line]
    return candidates[-1].name.upper() if candidates else "<anonymous>"


def _is_conditional(ctx: Any) -> bool:
    """Is this context inside a branch or a loop?

    A definition that only sometimes runs cannot establish a constant. Reaching-definition
    analysis could be more precise; refusing is the conservative direction and this is the
    place to be conservative.
    """
    parent = ctx.parentCtx
    while parent is not None:
        if isinstance(parent, PlSqlParser.If_statementContext | PlSqlParser.Loop_statementContext):
            return True
        parent = parent.parentCtx
    return False


def _assignment_parts(ctx: Any) -> tuple[str, str, bool] | None:
    """(target, expression text, is conditional) for one assignment."""
    target_ctx = ctx.general_element()
    expression_ctx = ctx.expression()
    if target_ctx is None or expression_ctx is None:
        return None
    return (
        str(target_ctx.getText()).upper(),
        source_slice(expression_ctx),
        _is_conditional(ctx),
    )


def _execute_immediate_argument(ctx: Any) -> str | None:
    expression = ctx.expression()
    if expression is None:
        return None
    return source_slice(expression)


def _dbms_sql_argument(text: str) -> str | None:
    match = DBMS_SQL_PARSE.match(" ".join(text.split()))
    if match is None:
        return None
    arguments = _split_arguments(match.group(1))
    return arguments[1].strip() if len(arguments) >= 2 else None


def _split_arguments(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_quote = False
    for char in text:
        if char == "'":
            in_quote = not in_quote
        elif not in_quote and char == "(":
            depth += 1
        elif not in_quote and char == ")":
            depth -= 1
        elif not in_quote and char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _dbms_sql_handle(text: str) -> str | None:
    """The cursor handle a DBMS_SQL call operates on — its first argument."""
    match = DBMS_SQL_CALL.match(" ".join(text.split()))
    if match is None:
        return None
    arguments = _split_arguments(match.group(2))
    return _carrier_name(arguments[0]) if arguments else None


def _carrier_name(argument: str) -> str | None:
    """A bare identifier argument names the variable carrying the statement."""
    text = argument.strip().upper()
    return text if re.fullmatch(r"[A-Z_][A-Z0-9_$#]*", text) else None


def resolve_dynamic_sql(program: Program) -> Resolution:
    """Find every dynamic execution site and resolve the ones that are decidable.

    A single forward pass in source order builds the constant environment, which is what
    makes `v_stmt := v_stmt || '...'` work: by the time the self-reference is read, the
    accumulated value is already known. Any assignment inside a branch or a loop poisons
    its target permanently, because a constant that only sometimes holds is not a constant.
    """
    resolution = Resolution()
    environment: dict[str, str] = {}
    poisoned: set[str] = set()

    # Source order matters, so assignments and execution sites are collected together and
    # replayed in line order — an environment entry is never read before it is written.
    events: list[tuple[int, str, Any]] = []
    for ctx in iter_contexts(program.tree, PlSqlParser.Assignment_statementContext):
        events.append((ctx.start.line, "assign", ctx))
    for ctx in iter_contexts(program.tree, PlSqlParser.Execute_immediateContext):
        events.append((ctx.start.line, "execute", ctx))
    for ctx in iter_contexts(program.tree, PlSqlParser.StatementContext):
        inner = most_specific(ctx)
        if rule_name(inner) == "call_statement":
            events.append((ctx.start.line, "call", ctx))

    for line, kind, ctx in sorted(events, key=lambda item: (item[0], item[1])):
        if kind == "assign":
            parts = _assignment_parts(ctx)
            if parts is None:
                continue
            target, expression, conditional = parts
            if DBMS_SQL_OPEN.search(expression):
                resolution.carriers.add(target)
            if conditional:
                poisoned.add(target)
                environment.pop(target, None)
                continue
            value = fold_constant(expression, environment)
            if value is None or target in poisoned:
                environment.pop(target, None)
                poisoned.add(target)
            else:
                environment[target] = value
            continue

        if kind == "execute":
            argument = _execute_immediate_argument(ctx)
            site_kind = "EXECUTE IMMEDIATE"
        else:
            call_text = source_slice(ctx)
            handle = _dbms_sql_handle(call_text)
            if handle is not None:
                resolution.carriers.add(handle)
            argument = _dbms_sql_argument(call_text)
            site_kind = "DBMS_SQL.PARSE"
        if argument is None:
            continue

        carrier = _carrier_name(argument)
        if carrier is not None:
            resolution.carriers.add(carrier)
            text = environment.get(carrier)
            refusal = None if text is not None else _refusal_for(carrier, poisoned)
        else:
            text = fold_constant(argument, environment)
            refusal = None if text is not None else "statement text is not constant"

        site = DynamicSite(
            line=line,
            unit=_unit_of(program, line),
            kind=site_kind,
            carrier=carrier,
            argument=argument,
            recovered=text,
            refusal=refusal,
        )
        resolution.sites.append(site)
        if text is not None:
            resolution.recovered[line] = text
            resolution.statements.append(
                ParsedStatement(kind=_kind_of(text), line=line, column=0, text=text)
            )

    return resolution


def _refusal_for(carrier: str, poisoned: set[str]) -> str:
    if carrier in poisoned:
        return f"{carrier} is assembled from a value not known until runtime"
    return f"{carrier} has no definition that reaches this point on every path"


# Enough to route a recovered statement to the right analyser. The recovered text is
# re-parsed properly downstream; this only picks the door.
_LEADING = re.compile(r"^\s*([A-Za-z_]+)")


def _kind_of(text: str) -> str:
    match = _LEADING.match(text)
    word = match.group(1).upper() if match else ""
    return {
        "INSERT": "insert_statement",
        "MERGE": "merge_statement",
        "UPDATE": "update_statement",
        "DELETE": "delete_statement",
        "SELECT": "select_statement",
        "ALTER": "alter_statement",
    }.get(word, "unknown_statement")
