"""A routine's statements by MEANING: eight primitives, one lowering table (semantic-ir T2).

The PL/pgSQL reader names a statement by its FORM (`return_query`, `return_next`, `fetch`
...). Each form used to have its own desugar branch, and a form without one was refused - so
every new corpus brought new refusals (docs/semantic-ir-plan.md). Here each form is lowered to
one of eight primitives, and the desugarer implements each primitive once:

    ASSIGN  target := expression          BIND    targets := a query's row
    EMIT    the routine yields rows/value  EFFECT  a statement writes a relation
    DYN     text that becomes SQL          CALL    another routine
    BRANCH  decides rows or writes         OPAQUE  not mapped yet - refused alone

A new form then needs one row in `LOWERING`, not a new lineage rule.

Two helpers serve every position a primitive appears in:

* `output_columns` - what a routine yields, from its signature: RETURNS TABLE (...), RETURNS
  SETOF t (t's columns), OUT / INOUT parameters (a record), or a scalar.
* `dynamic_text` - the SQL a DYN expression runs, when it folds to a constant: `||`,
  `format()` with sequential or positional (`%1$I`) arguments, and `$n` placeholders bound
  to the USING expressions. EXECUTE, RETURN QUERY EXECUTE, FOR ... IN EXECUTE and OPEN ...
  FOR EXECUTE all go through it.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

import sqlglot
from sqlglot import exp

from lineage.parsing import plpgsql as pl
from lineage.parsing.sqlscript import skip_lexeme
from lineage.resolution.dictionary import Dictionary

DIALECT = "postgres"


class Prim(StrEnum):
    ASSIGN = "assign"
    BIND = "bind"
    EMIT = "emit"
    EFFECT = "effect"
    DYN = "dyn"
    CALL = "call"
    BRANCH = "branch"
    OPAQUE = "opaque"
    NONE = "none"  # moves no data: RAISE, COMMIT, CLOSE, NULL ...


LOWERING: dict[str, tuple[Prim, ...]] = {
    # reader form             primitive(s) it means
    "sql": (Prim.EFFECT,),
    "select_into": (Prim.BIND,),
    "dml_returning_into": (Prim.EFFECT, Prim.BIND),
    "fetch": (Prim.BIND,),
    "assign": (Prim.ASSIGN,),
    "execute": (Prim.DYN,),  # then EFFECT, or BIND with INTO
    "open": (Prim.BIND,),  # binds the cursor's query; FETCH reads it
    "open_execute": (Prim.DYN, Prim.BIND),
    "return": (Prim.EMIT,),
    "return_query": (Prim.EMIT,),
    "return_query_execute": (Prim.DYN, Prim.EMIT),
    "return_next": (Prim.EMIT,),
    "diagnostics": (Prim.ASSIGN,),  # a count: no column flows
    "noop": (Prim.NONE,),
    "opaque": (Prim.OPAQUE,),
}
"""Every form the reader names, and what it means. A form missing here is a test failure
(`tests/test_routine_ir.py`), never a silent refusal."""


def lower(stmt: pl.Stmt) -> tuple[Prim, ...]:
    return LOWERING.get(stmt.kind, (Prim.OPAQUE,))


# --- EMIT: what a routine yields ---------------------------------------------------------------


def output_columns(routine: pl.Routine, dictionary: Dictionary | None = None) -> list[str]:
    """The columns of the rows a routine yields, in order; [] for a scalar or void one.

    RETURNS TABLE (a, b) -> a, b. OUT / INOUT parameters with no RETURNS (or RETURNS record /
    SETOF record) -> those parameters, in declaration order: PostgreSQL's own row type for
    the call. RETURNS SETOF t -> t's columns from the dictionary.
    """
    if routine.returns == "table":
        return list(routine.out_columns)
    if routine.returns == "record" or (routine.out_params and routine.returns != "scalar"):
        return list(routine.out_params)
    if routine.returns == "setof" and routine.setof:
        if _BASE_TYPE.fullmatch(routine.setof):
            # SETOF a base type: one column, which PostgreSQL names after the function.
            return [routine.name.split(".")[-1]]
        if dictionary is not None:
            try:
                return [c.lower() for c in dictionary.columns_of(routine.setof)]
            except Exception:
                return []
    return []


_BASE_TYPE = re.compile(
    r"(?:pg_catalog\.)?(?:numeric|decimal|int[248]?|integer|smallint|bigint|real|float[48]?"
    r"|double|money|text|varchar|char|character|bpchar|name|bool|boolean|date|time|timetz"
    r"|timestamp|timestamptz|interval|uuid|json|jsonb|bytea|xml|inet|cidr|oid)"
)


def yields_rows(routine: pl.Routine) -> bool:
    return routine.kind == "function" and (
        routine.returns in ("table", "setof", "record")
        or (len(routine.out_params) > 1 and routine.returns != "scalar")
    )


# --- DYN: the SQL a dynamic statement runs ------------------------------------------------------


def dynamic_text(expression: str, constants: dict[str, str], using: str = "") -> str | None:
    """The SQL `expression` evaluates to, if it folds to a constant (D-42); else None.

    `$n` placeholders are replaced by the n-th USING expression, as PostgreSQL binds them -
    a parameter or variable there stays a symbol of the statement, so what it filters is
    still traced. Without USING (or past its end) `$n` is NULL: an external value.
    """
    text = fold(expression, constants)
    if text is None:
        return None
    bound = [part.strip() for part in pl._split_top(using)] if using.strip() else []

    def placeholder(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        return f"({bound[index]})" if 0 <= index < len(bound) else "NULL"

    return _outside_literals(text, re.compile(r"\$(\d+)"), placeholder)


def fold(text: str, constants: dict[str, str]) -> str | None:
    """A text expression's constant value: literals, constant variables, `||`, format()."""
    try:
        parsed = sqlglot.parse_one(f"SELECT {text}", read=DIALECT)
    except Exception:
        return None
    if not isinstance(parsed, exp.Select) or not parsed.selects:
        return None
    return _value(parsed.selects[0], constants)


def _value(node: Any, constants: dict[str, str]) -> str | None:
    if isinstance(node, exp.Alias):
        return _value(node.this, constants)
    if isinstance(node, exp.Literal) and node.is_string:
        return str(node.this)
    if isinstance(node, exp.RawString):  # a dollar-quoted text: `$q$ ... $q$`
        return str(node.this)
    if isinstance(node, exp.Paren):
        return _value(node.this, constants)
    if isinstance(node, exp.Column) and not node.table:
        return constants.get(node.name.lower())
    if isinstance(node, exp.DPipe):
        left, right = _value(node.this, constants), _value(node.expression, constants)
        return None if left is None or right is None else left + right
    if isinstance(node, exp.Concat):
        parts = [_value(part, constants) for part in node.expressions]
        return None if any(p is None for p in parts) else "".join(parts)  # type: ignore[arg-type]
    if isinstance(node, exp.Format) or (
        isinstance(node, exp.Anonymous) and node.name.lower() == "format"
    ):
        pieces = (
            [node.this, *node.expressions] if isinstance(node, exp.Format) else node.expressions
        )
        values = [_value(piece, constants) for piece in pieces]
        if not values or values[0] is None:
            return None
        return format_text(values[0], values[1:])
    if isinstance(node, exp.Anonymous) and node.name.lower() in ("quote_ident", "quote_literal"):
        inner = _value(node.expressions[0], constants) if node.expressions else None
        if inner is None:
            return None
        return _identifier(inner) if node.name.lower() == "quote_ident" else _quoted(inner)
    return None


_SPEC = re.compile(r"%(?:(\d+)\$)?([-0-9]*)([sIL%])")


def format_text(template: str, arguments: list[str | None]) -> str | None:
    """PostgreSQL's format(): `%s` `%I` `%L` `%%`, sequential or positional (`%2$I`).

    An argument used that is not a constant makes the whole text unknown (None).
    """
    out: list[str] = []
    position, cursor = 0, 0
    for match in _SPEC.finditer(template):
        out.append(template[position : match.start()])
        position = match.end()
        explicit, _flags, kind = match.groups()
        if kind == "%":
            out.append("%")
            continue
        index = int(explicit) - 1 if explicit else cursor
        cursor = index + 1
        if not 0 <= index < len(arguments) or arguments[index] is None:
            return None
        argument = str(arguments[index])
        out.append(
            _identifier(argument) if kind == "I" else _quoted(argument) if kind == "L" else argument
        )
    out.append(template[position:])
    return "".join(out)


def _identifier(name: str) -> str:
    return name if re.fullmatch(r"[a-z_][a-z0-9_$]*", name) else '"' + name.replace('"', '""') + '"'


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _outside_literals(text: str, pattern: re.Pattern[str], repl: Any) -> str:
    """`pattern.sub(repl, text)` on code only - never inside a literal or comment."""
    out, index = [], 0
    while index < len(text):
        skipped = skip_lexeme(text, index)
        if skipped is not None:
            out.append(text[index:skipped])
            index = skipped
            continue
        match = pattern.match(text, index)
        if match is not None:
            out.append(repl(match))
            index = match.end()
            continue
        out.append(text[index])
        index += 1
    return "".join(out)
