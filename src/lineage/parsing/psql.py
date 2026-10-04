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
* `\\gset` sends the query before it into psql variables, so the query is blanked with it:
  it writes no column (D-37, and the B10 application that it gives no `<result>` either);
* `\\copy` becomes `COPY ...;`, on its own line, so the refusal register refuses it by name
  as it refuses COPY (B3) - and only it;
* `\\i` / `\\ir` (and the long spellings) are returned as includes, declared
  source_unavailable by the caller: another file's lineage is not in this analysis;
* `:'v'` and `:"v"` lose the colon and become the literal or identifier psql would have
  substituted - external values (D-36). `:v` is left alone: SQLGlot reads it as a
  placeholder, which is what it is;
* `U&"..."` identifiers are decoded to the plain quoted identifier they spell (B10-05),
  because SQLGlot read `U&"\\0069d"` as a column `u`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lineage.parsing.sqlscript import skip_lexeme

_INCLUDES = {"i", "ir", "include", "include_relative"}
_SENDS = {"g", "gx"}
_VARIABLE = re.compile(r":(['\"])[A-Za-z_][A-Za-z0-9_]*\1")
_UESCAPE = re.compile(r"\s*UESCAPE\s*'(.)'", re.IGNORECASE)
_UNICODE = re.compile(r"\\(\+[0-9A-Fa-f]{6}|[0-9A-Fa-f]{4}|\\)")


@dataclass(frozen=True)
class Include:
    """A `\\i` of another file: its lineage is not in this analysis."""

    path: str
    line: int


def restate(source: str) -> tuple[str, list[Include]]:
    out: list[str] = []
    includes: list[Include] = []
    statement_start = 0  # index in `out` (joined) where the current statement began
    index, length = 0, len(source)

    def written() -> int:
        return sum(len(part) for part in out)

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
            statement_start = written()
            continue

        if char == ":" and source[index - 1 : index] != ":":
            variable = _VARIABLE.match(source, index)
            if variable is not None:
                out.append(" " + variable.group(0)[1:])
                index = variable.end()
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
            elif command == "gset":
                # The query goes to psql variables: blank it, with the meta-command.
                joined = "".join(out)
                out[:] = [joined[:statement_start], _blank(joined[statement_start:])]
                out.append(_blank(source[index:end]))
            else:
                out.append(_blank(source[index:end]))
            index = end
            statement_start = written()
            continue

        out.append(char)
        index += 1

    return "".join(out), includes


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
