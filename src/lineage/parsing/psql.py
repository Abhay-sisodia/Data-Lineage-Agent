"""A psql script restated as the SQL the server receives (D-36, D-37, batch B10).

A `.sql` file run by psql is not plain SQL. psql reads a backslash outside a literal or
comment as a META-COMMAND that runs to the end of its line, and substitutes `:'v'` and
`:"v"` before the server sees the statement. Without this pass, a `\\set` line was glued to
the statement after it, which then failed to parse: the meta-command cost a statement
of lineage (B10-03).

`restate` returns text of the same line structure, so every later line number is
unchanged, plus the meta-commands that mean something to lineage:

* every meta-command is blanked: skipped, not counted (D-37);
* `\\g` / `\\gx` end the query before them, as `;` does;
* `\\set v value` is substituted as psql substitutes it (semantic-ir T8, holdout4 H3):
  `:v` becomes the value, `:'v'` the literal, `:"v"` the identifier - a constant, no edge;
* `\\gset [prefix]` is a BIND (semantic-ir T0, decided 2026-10-16): the query's row goes
  into psql variables. When a later statement uses one, the query becomes
  `CREATE TEMP TABLE psql_gset_l<line>_<hash> AS <query, columns named as the variables>` and
  each use `(SELECT <variable> FROM psql_gset_...)` - so the variable carries the lineage
  of the columns it was read from, through a relation whose name says it is a psql
  variable (holdout4 H4). A `\\gset` nothing uses stays blanked: no `<result>` (D-37);
* `COPY ... FROM STDIN` keeps its statement and blanks the data rows after it, up to the
  `\\.` line - rows written in the script, not SQL (holdout4 H1);
* `\\copy` becomes `COPY ...;`, on its own line, so the refusal register refuses it by name
  as it refuses COPY (B3) - and only it;
* `\\i` / `\\ir` (and the long spellings) are returned as includes, declared
  source_unavailable by the caller: another file's lineage is not in this analysis;
* a variable set nowhere in the script (`psql -v`): `:'v'` and `:"v"` lose the colon and
  become the literal or identifier psql would have substituted - external values (D-36).
  `:v` is left alone: SQLGlot reads it as a placeholder, which is what it is;
* `U&"..."` identifiers are decoded to the plain quoted identifier they spell (B10-05),
  because SQLGlot read `U&"\\0069d"` as a column `u`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from lineage.parsing.sqlscript import skip_lexeme

_INCLUDES = {"i", "ir", "include", "include_relative"}
_SENDS = {"g", "gx"}
_VARIABLE = re.compile(r":(['\"])([A-Za-z_][A-Za-z0-9_]*)\1")
_BARE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
_COPY_FROM_STDIN = re.compile(r"(?is)^\s*COPY\b.*\bFROM\s+STDIN\b")
_COPY_END = re.compile(r"(?m)^\\\.[ \t\r]*$")
_UESCAPE = re.compile(r"\s*UESCAPE\s*'(.)'", re.IGNORECASE)
_UNICODE = re.compile(r"\\(\+[0-9A-Fa-f]{6}|[0-9A-Fa-f]{4}|\\)")


@dataclass(frozen=True)
class Include:
    """A `\\i` of another file: its lineage is not in this analysis."""

    path: str
    line: int


@dataclass
class _Gset:
    """A `\\gset` query, held until a variable of it is used."""

    relation: str
    statement: str  # CREATE TEMP TABLE <relation> AS <query>
    variables: set[str] = field(default_factory=set)
    used: bool = False


def restate(source: str) -> tuple[str, list[Include]]:
    out: list[str] = []
    includes: list[Include] = []
    constants: dict[str, str] = {}  # \set name -> value
    bound: dict[str, _Gset] = {}  # \gset variable -> its query
    gsets: list[_Gset] = []
    statement_start = 0  # index in `out` (joined) where the current statement began
    index, length = 0, len(source)

    def written() -> int:
        return sum(len(part) for part in out)

    def substitute(name: str, quote: str) -> str | None:
        """What psql pastes for `:name` / `:'name'` / `:"name"`, or None if unknown."""
        if name in bound:
            gset = bound[name]
            gset.used = True
            return f"(SELECT {name} FROM {gset.relation})"
        if name not in constants:
            return None
        value = constants[name]
        if quote == "'":
            return "'" + value.replace("'", "''") + "'"
        if quote == '"':
            return '"' + value.replace('"', '""') + '"'
        return value

    while index < length:
        char = source[index]

        if source.startswith(('U&"', 'u&"'), index) and not _ident_before(source, index):
            end = skip_lexeme(source, index + 2) or length
            decoded, end = _unicode_identifier(source, index, end)
            out.append(decoded)
            index = end
            continue

        skipped = skip_lexeme(source, index)
        if skipped is not None:
            out.append(source[index:skipped])
            index = skipped
            continue

        if char == ";":
            out.append(char)
            index += 1
            if _COPY_FROM_STDIN.match("".join(out)[statement_start:]):
                index = _skip_copy_data(source, index, out)
            statement_start = written()
            continue

        if char == ":" and source[index - 1 : index] != ":":
            variable = _VARIABLE.match(source, index)
            if variable is not None:
                pasted = substitute(variable.group(2), variable.group(1))
                out.append(pasted if pasted is not None else " " + variable.group(0)[1:])
                index = variable.end()
                continue
            bare = _BARE.match(source, index)
            if bare is not None:
                pasted = substitute(bare.group(1), "")
                if pasted is not None:
                    out.append(pasted)
                    index = bare.end()
                    continue

        if char == "\\":
            end = source.find("\n", index)
            end = length if end == -1 else end
            meta = source[index + 1 : end]
            command, _, argument = meta.strip().partition(" ")
            command = command.lower()
            line = source.count("\n", 0, index) + 1
            if command in _INCLUDES:
                includes.append(Include(path=argument.strip().strip("'\""), line=line))
                out.append(_blank(source[index:end]))
            elif command == "copy":
                out.append(" " + meta + ";")
            elif command in _SENDS:
                out.append(";" + _blank(source[index + 1 : end]))
            elif command == "set":
                name, _, value = argument.strip().partition(" ")
                if name:
                    constants[name] = _set_value(value)
                    bound.pop(name, None)
                out.append(_blank(source[index:end]))
            elif command == "gset":
                # The query goes to psql variables: blanked now, restated if one is used.
                joined = "".join(out)
                query = joined[statement_start:]
                lead = _sql_start(query)
                first_line = joined.count("\n", 0, statement_start + lead) + 1
                digest = hashlib.sha1(query[lead:].encode("utf-8")).hexdigest()[:6]
                relation = f"psql_gset_l{first_line}_{digest}"
                gset = _bind(query, argument.strip(), relation)
                marker = ""
                if gset is not None:
                    gsets.append(gset)
                    for name in gset.variables:
                        bound[name] = gset
                        constants.pop(name, None)
                    marker = f"\x00{len(gsets) - 1}\x00"
                # The restated query goes where its SQL starts, after any comment before it.
                out[:] = [joined[:statement_start], _blank(query[:lead]), marker]
                out.append(_blank(query[lead:]))
                out.append(_blank(source[index:end]))
            else:
                out.append(_blank(source[index:end]))
            index = end
            statement_start = written()
            continue

        out.append(char)
        index += 1

    text = "".join(out)
    for number, gset in enumerate(gsets):
        text = text.replace(f"\x00{number}\x00", gset.statement if gset.used else "")
    return text, includes


def _sql_start(text: str) -> int:
    """Offset of the first character of `text` that is neither whitespace nor a comment."""
    index = 0
    while index < len(text):
        if text.startswith(("--", "/*"), index):
            index = skip_lexeme(text, index) or len(text)
        elif text[index].isspace():
            index += 1
        else:
            break
    return index


def _set_value(text: str) -> str:
    """`\\set`'s value: its arguments joined, a single-quoted one unquoted ('' -> ')."""
    parts = re.findall(r"'((?:[^']|'')*)'|(\S+)", text)
    return "".join(bare if bare else quoted.replace("''", "'") for quoted, bare in parts)


def _bind(query: str, prefix: str, relation: str) -> _Gset | None:
    """`<query> \\gset prefix` as a relation whose columns are the variables it sets."""
    try:
        parsed = sqlglot.parse_one(query.strip().rstrip(";"), read="postgres")
    except Exception:
        return None
    if not isinstance(parsed, exp.Select) or not parsed.selects:
        return None
    names: list[str] = []
    renamed = []
    for projection in parsed.selects:
        if isinstance(projection, exp.Star) or isinstance(projection.this, exp.Star):
            return None
        name = f"{prefix}{projection.alias_or_name}".lower()
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            return None
        names.append(name)
        inner = projection.this if isinstance(projection, exp.Alias) else projection
        renamed.append(exp.alias_(inner.copy(), name))
    parsed.set("expressions", renamed)
    statement = f"CREATE TEMP TABLE {relation} AS {parsed.sql(dialect='postgres')};"
    return _Gset(relation=relation, statement=statement, variables=set(names))


def _skip_copy_data(source: str, index: int, out: list[str]) -> int:
    """After `COPY ... FROM STDIN;`: blank its data rows through the `\\.` line, if there."""
    line_end = source.find("\n", index)
    if line_end == -1:
        return index
    terminator = _COPY_END.search(source, line_end + 1)
    if terminator is None:
        return index
    out.append(_blank(source[index : terminator.end()]))
    return terminator.end()


def _blank(text: str) -> str:
    """Spaces for everything but newlines, so lines and columns stay where they were."""
    return re.sub(r"[^\n]", " ", text)


def _ident_before(source: str, index: int) -> bool:
    return index > 0 and (source[index - 1].isalnum() or source[index - 1] in "_$")


def _unicode_identifier(source: str, index: int, end: int) -> tuple[str, int]:
    """`U&"..."` [UESCAPE 'c'] -> the plain quoted identifier, padded to the same length."""
    body = source[index + 3 : end - 1]
    escape = "\\"
    clause = _UESCAPE.match(source, end)
    if clause is not None:
        escape, end = clause.group(1), clause.end()
    pattern = (
        _UNICODE
        if escape == "\\"
        else re.compile(
            re.escape(escape) + r"(\+[0-9A-Fa-f]{6}|[0-9A-Fa-f]{4}|" + re.escape(escape) + ")"
        )
    )

    def decode(match: re.Match[str]) -> str:
        code = match.group(1)
        if code == escape:
            return escape
        return chr(int(code.lstrip("+"), 16))

    plain = '"' + pattern.sub(decode, body) + '"'
    original = source[index:end]
    return plain + _blank(original[len(plain) :]), end
