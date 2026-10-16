"""A reader for PL/pgSQL bodies (batch B8, option A of its report).

A PL/pgSQL body arrives as a dollar-quoted string, so SQLGlot never sees inside it and every
routine was refused by name (B8-01). This module reads the body's PROCEDURAL structure -
blocks, declarations, IF, loops, exception handlers, and the PL/pgSQL statements
(assignment, EXECUTE, FETCH, RETURN, PERFORM, RAISE, GET DIAGNOSTICS ...) - and leaves every
embedded SQL statement as text, for SQLGlot to parse like any other.

**One rule above all: what it does not know, it refuses.** An unknown statement or form
raises `UnsupportedError`, and the caller keeps the routine refused by name exactly as before.
A routine half-read is worse than one declared unreadable: the def-use facts after an
unread statement would be stated with confidence and be wrong.

Statements are found with the B10 lexer (`sqlscript.skip_lexeme`), so a `;` inside a
literal, a dollar quote or a comment never ends one. Offsets are absolute in the source,
so every statement keeps its line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lineage.parsing.sqlscript import skip_lexeme


class UnsupportedError(Exception):
    """A form this reader does not know. The routine stays refused by name."""


# --- the tree -----------------------------------------------------------------------------


@dataclass
class Stmt:
    """One PL/pgSQL statement. `kind` names the form; `text` is its source, unparsed."""

    kind: str  # sql | select_into | assign | execute | fetch | return | return_query |
    #            return_next | diagnostics | open | noop | opaque (plan step 5: a statement
    #            this reader cannot follow, kept in place - `expression` says why)
    text: str
    start: int  # absolute offset of its first character
    targets: tuple[str, ...] = ()  # assignment / INTO / FETCH targets, as written
    expression: str = ""  # assignment RHS, RETURN expression, EXECUTE text
    using: str = ""  # EXECUTE ... USING
    cursor: str = ""  # FETCH cursor
    at: int = 0  # dml_returning_into: absolute offset of its RETURNING keyword


@dataclass
class Loop:
    start: int
    kind: str  # query | cursor | range | foreach | while | loop | execute | opaque
    variable: str = ""  # the record or index variable
    query: str = ""  # FOR rec IN <query>
    query_start: int = 0
    cursor: str = ""
    body: list[Node] = field(default_factory=list)
    reason: str = ""  # opaque: why the header could not be followed (its body still is)


@dataclass
class If:
    start: int
    arms: list[list[Node]] = field(default_factory=list)


@dataclass
class Handler:
    start: int
    names: str
    body: list[Node] = field(default_factory=list)


@dataclass
class Declaration:
    name: str
    start: int
    default: str = ""  # the initial value's text, if any
    cursor_query: str = ""  # `c CURSOR FOR <query>`


@dataclass
class Block:
    start: int
    declarations: list[Declaration] = field(default_factory=list)
    body: list[Node] = field(default_factory=list)
    handlers: list[Handler] = field(default_factory=list)


Node = Stmt | Loop | If | Block


# --- the reader ---------------------------------------------------------------------------

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_ASSIGN = re.compile(r"^([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)\s*(?::=|=)\s*(.*)$", re.DOTALL)
_SQL_START = {"select", "with", "insert", "update", "delete", "merge", "values", "table"}
_DDL = {
    "truncate", "drop", "alter", "create", "analyze", "analyse", "vacuum", "grant", "revoke",
    "comment", "lock", "set", "reset", "notify", "listen", "unlisten", "discard", "refresh",
    "cluster", "reindex",
}  # fmt: skip
_NOOPS = {
    "raise",
    "null",
    "commit",
    "rollback",
    "close",
    "call",
    "assert",
    "move",
    "open",
    "exit",
    "continue",
}
"""Statements that move no data. EXIT / CONTINUE end an iteration or a loop early; the
loop's own reads and writes are where its lineage is."""


def _chunks(source: str, start: int, end: int) -> list[tuple[int, int]]:
    """`(start, end)` of each `;`-terminated piece, at bracket depth 0, outside lexemes."""
    pieces: list[tuple[int, int]] = []
    index, begin, depth = start, start, 0
    while index < end:
        skipped = skip_lexeme(source, index)
        if skipped is not None:
            index = min(skipped, end)
            continue
        char = source[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == ";" and depth == 0:
            pieces.append((begin, index))
            begin = index + 1
        index += 1
    if source[begin:end].strip():
        pieces.append((begin, end))
    return pieces


def find_word(text: str, word: str, start: int = 0) -> int:
    """Offset of `word` as a keyword at bracket depth 0, outside lexemes; -1 if absent."""
    index, depth, length = start, 0, len(text)
    pattern = re.compile(rf"(?i)\b{word}\b")
    while index < length:
        skipped = skip_lexeme(text, index)
        if skipped is not None:
            index = skipped
            continue
        char = text[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif (
            depth == 0
            and pattern.match(text, index)
            and (index == 0 or not (text[index - 1].isalnum() or text[index - 1] in "_$."))
        ):
            return index
        index += 1
    return -1


class _Reader:
    def __init__(self, source: str, start: int, end: int) -> None:
        self.source = source
        self.pieces = _chunks(source, start, end)
        self.piece = 0
        self.position = self.pieces[0][0] if self.pieces else end

    # -- low level ------------------------------------------------------------------------

    def _skip_space(self) -> None:
        while self.piece < len(self.pieces):
            _, end = self.pieces[self.piece]
            while self.position < end:
                char = self.source[self.position]
                if char.isspace():
                    self.position += 1
                    continue
                if self.source.startswith(("--", "/*"), self.position):
                    self.position = min(skip_lexeme(self.source, self.position) or end, end)
                    continue
                return
            self.piece += 1
            if self.piece < len(self.pieces):
                self.position = self.pieces[self.piece][0]

    def done(self) -> bool:
        self._skip_space()
        return self.piece >= len(self.pieces)

    def peek(self) -> str:
        if self.done():
            return ""
        if self.source.startswith("<<", self.position):
            return "<<"
        match = _WORD.match(self.source, self.position)
        return match.group(0).lower() if match else self.source[self.position]

    def take(self, word: str) -> None:
        if self.peek() != word:
            raise UnsupportedError(f"expected {word.upper()} at offset {self.position}")
        self.position += len(word)

    def rest(self) -> tuple[int, str]:
        """The remainder of the current piece, which ends at its `;`, and move past it."""
        self._skip_space()
        start, end = self.position, self.pieces[self.piece][1]
        self.piece += 1
        if self.piece < len(self.pieces):
            self.position = self.pieces[self.piece][0]
        return start, self.source[start:end]

    def until(self, word: str) -> tuple[int, str]:
        """Text up to the keyword `word` within the current piece; positioned after it."""
        self._skip_space()
        start, end = self.position, self.pieces[self.piece][1]
        found = find_word(self.source[start:end], word)
        if found < 0:
            raise UnsupportedError(f"no {word.upper()} where one was expected")
        self.position = start + found + len(word)
        return start, self.source[start : start + found]

    def end_of_piece(self) -> None:
        """The current piece must have nothing left (`END IF;`, `END LOOP;` ...)."""
        # Within THIS piece only: skipping whitespace may not carry into the next one, whose
        # `END` would otherwise be taken for a label and lost.
        if self.piece >= len(self.pieces):
            return
        leftover = self.source[self.position : self.pieces[self.piece][1]]
        leftover = re.sub(r"--[^\n]*|/\*.*?\*/", " ", leftover, flags=re.DOTALL).strip()
        if leftover and not _WORD.fullmatch(leftover):  # an END's label is allowed
            raise UnsupportedError(f"unexpected text {leftover[:30]!r}")
        self.piece += 1
        if self.piece < len(self.pieces):
            self.position = self.pieces[self.piece][0]

    # -- structure ------------------------------------------------------------------------

    def block(self) -> Block:
        self._skip_space()
        block = Block(start=self.position)
        if self.peek() == "declare":
            self.take("declare")
            while self.peek() not in ("begin", ""):
                block.declarations.append(self.declaration())
        self.take("begin")
        block.body = self.statements({"end", "exception"})
        if self.peek() == "exception":
            self.take("exception")
            while self.peek() == "when":
                self._skip_space()
                at = self.position
                self.take("when")
                _, names = self.until("then")
                handler = Handler(start=at, names=names.strip())
                handler.body = self.statements({"when", "end"})
                block.handlers.append(handler)
        self.take("end")
        self.end_of_piece()
        return block

    def declaration(self) -> Declaration:
        start, text = self.rest()
        words = text.split()
        if not words:
            raise UnsupportedError("empty declaration")
        name = words[0]
        if len(words) > 1 and words[1].lower() in ("cursor", "no", "scroll"):
            found = find_word(text, "for")
            if found < 0:
                found = find_word(text, "is")
                skip = 2
            else:
                skip = 3
            if found < 0:
                raise UnsupportedError("a cursor declaration without FOR / IS")
            return Declaration(name=name, start=start, cursor_query=text[found + skip :].strip())
        if len(words) > 1 and words[1].lower() == "alias":
            raise UnsupportedError("ALIAS FOR declarations")
        for marker in (":=", "="):
            depth_free = _top_level(text, marker)
            if depth_free >= 0:
                return Declaration(
                    name=name, start=start, default=text[depth_free + len(marker) :].strip()
                )
        found = find_word(text, "default")
        if found >= 0:
            return Declaration(name=name, start=start, default=text[found + 7 :].strip())
        return Declaration(name=name, start=start)

    def statements(self, stop: set[str]) -> list[Node]:
        nodes: list[Node] = []
        while True:
            word = self.peek()
            if word == "" or word in stop:
                return nodes
            if word == "<<":
                end = self.source.find(">>", self.position)
                self.position = end + 2
                continue
            if word in ("declare", "begin"):
                nodes.append(self.block())
            elif word == "if":
                nodes.append(self.if_())
            elif word in ("for", "foreach", "while", "loop"):
                nodes.append(self.loop())
            elif word == "case":
                nodes.append(self.case_())
            else:
                nodes.append(self.simple())

    def if_(self) -> If:
        self._skip_space()
        node = If(start=self.position)
        self.take("if")
        self.until("then")
        node.arms.append(self.statements({"elsif", "elseif", "else", "end"}))
        while self.peek() in ("elsif", "elseif"):
            self.take(self.peek())
            self.until("then")
            node.arms.append(self.statements({"elsif", "elseif", "else", "end"}))
        if self.peek() == "else":
            self.take("else")
            node.arms.append(self.statements({"end"}))
        self.take("end")
        self.take("if")
        self.end_of_piece()
        return node

    def case_(self) -> If:
        """`CASE [x] WHEN ... THEN stmts ... [ELSE stmts] END CASE;` - one arm per WHEN, as an
        IF reads: which arm runs is decided at run time, so each arm's writes are guarded."""
        self._skip_space()
        node = If(start=self.position)
        self.take("case")
        self.until("when")
        self.until("then")
        node.arms.append(self.statements({"when", "else", "end"}))
        while self.peek() == "when":
            self.take("when")
            self.until("then")
            node.arms.append(self.statements({"when", "else", "end"}))
        if self.peek() == "else":
            self.take("else")
            node.arms.append(self.statements({"end"}))
        self.take("end")
        self.take("case")
        self.end_of_piece()
        return node

    def loop(self) -> Loop:
        self._skip_space()
        start = self.position
        word = self.peek()
        if word == "loop":
            self.take("loop")
            node = Loop(start=start, kind="loop")
        elif word == "while":
            self.take("while")
            self.until("loop")
            node = Loop(start=start, kind="while")
        else:
            self.take(word)
            header_start, header = self.until("loop")
            try:
                node = _loop_header(word, header, header_start)
            except UnsupportedError as error:
                # Plan step 5: a header this reader cannot follow costs the header only - the
                # body is read as usual, and the loop is refused at its own line.
                node = Loop(start=start, kind="opaque", reason=str(error))
            node.start = start
        node.body = self.statements({"end"})
        self.take("end")
        self.take("loop")
        self.end_of_piece()
        return node

    def simple(self) -> Stmt:
        start, text = self.rest()
        try:
            return classify(text, start)
        except UnsupportedError as error:
            # Plan step 5: one statement this reader cannot follow is refused alone, at its
            # own line; the routine around it is still read.
            return Stmt("opaque", text.strip(), start, expression=str(error))


def _top_level(text: str, marker: str) -> int:
    """Offset of `marker` at bracket depth 0 outside lexemes, or -1."""
    index, depth = 0, 0
    while index < len(text):
        skipped = skip_lexeme(text, index)
        if skipped is not None:
            index = skipped
            continue
        char = text[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and text.startswith(marker, index):
            if marker == "=" and text[index - 1 : index] in (":", "<", ">", "!"):
                index += 1
                continue
            return index
        index += 1
    return -1


def _loop_header(word: str, header: str, header_start: int) -> Loop:
    if word == "foreach":
        found = find_word(header, "in")
        if found < 0:
            raise UnsupportedError("FOREACH without IN")
        array = re.sub(r"(?is)^\s*ARRAY\b", "", header[found + 2 :]).strip()
        return Loop(
            start=header_start,
            kind="foreach",
            variable=header[:found].split()[0],
            query=array,
            query_start=header_start + found + 2,
        )
    found = find_word(header, "in")
    if found < 0:
        raise UnsupportedError("FOR without IN")
    variable = header[:found].strip()
    if not _WORD.fullmatch(variable):
        raise UnsupportedError("a FOR over several targets")
    body = header[found + 2 :]
    lead = body.lstrip()
    offset = header_start + found + 2 + (len(body) - len(lead))
    first = _WORD.match(lead) or re.match(r"\(", lead)
    first_word = first.group(0).lower() if first else ""
    if first_word in ("select", "with", "("):
        return Loop(
            start=header_start,
            kind="query",
            variable=variable,
            query=lead.strip(),
            query_start=offset,
        )
    if first_word == "execute":
        text = lead[len("execute") :]
        using = find_word(text, "using")
        return Loop(
            start=header_start,
            kind="execute",
            variable=variable,
            query=(text[:using] if using >= 0 else text).strip(),
            query_start=offset,
        )
    if _top_level(lead, "..") >= 0:
        return Loop(start=header_start, kind="range", variable=variable)
    if first_word and _WORD.fullmatch(lead.split("(")[0].strip()):
        return Loop(
            start=header_start, kind="cursor", variable=variable, cursor=lead.split("(")[0].strip()
        )
    raise UnsupportedError(f"FOR ... IN {lead[:20]!r}")


def classify(text: str, start: int) -> Stmt:
    """One simple statement, by its leading word."""
    stripped = text.strip()
    match = _WORD.match(stripped)
    word = match.group(0).lower() if match else ""
    if word in _SQL_START:
        if word == "select" and find_word(stripped, "into") >= 0:
            return Stmt("select_into", stripped, start)
        returning = find_word(stripped, "returning")
        if returning >= 0 and word != "select":
            into_at = find_word(stripped[returning:], "into")
            if into_at >= 0:
                # `INSERT ... RETURNING id INTO v_id`: the DML, then the variables taken from
                # the row it wrote (see `_Desugarer._dml_returning_into`).
                into_at += returning
                returned_into = tuple(
                    t.strip() for t in stripped[into_at + 4 :].rstrip(";").split(",") if t.strip()
                )
                if not returned_into:
                    raise UnsupportedError("DML ... RETURNING ... INTO with no target")
                lead = len(text) - len(text.lstrip())
                return Stmt(
                    "dml_returning_into",
                    stripped[:returning].rstrip(),
                    start,
                    targets=returned_into,
                    expression=stripped[returning + len("returning") : into_at].strip(),
                    at=start + lead + returning,
                )
        return Stmt("sql", stripped, start)
    if word == "perform":
        return Stmt("noop", stripped, start)
    if word == "create" and re.search(r"(?is)\bAS\s*\(?\s*(SELECT|WITH|VALUES|TABLE)\b", stripped):
        return Stmt("sql", stripped, start)  # CREATE [TEMP] TABLE ... AS <query>: lineage
    if word in _DDL:
        # Plan step 6 (D-62): TRUNCATE / DROP / ALTER ... move no column; their effect on a
        # relation is recorded from PostgreSQL's own parse (`analysis.effects`).
        return Stmt("noop", stripped, start)
    if word in _NOOPS:
        found = find_word(stripped, "for") if word == "open" else -1
        if found >= 0:
            query = stripped[found + 3 :].strip()
            opener = _WORD.match(query)
            if opener is not None and opener.group(0).lower() == "execute":
                raise UnsupportedError("OPEN ... FOR EXECUTE")
            head = stripped[len("open") : found].split()
            cursor = head[0] if head else ""
            return Stmt("open", stripped, start, cursor=cursor, expression=query)
        return Stmt("noop", stripped, start)
    if word == "execute":
        body = stripped[len("execute") :]
        using = ""
        found = find_word(body, "using")
        if found >= 0:
            body, using = body[:found], body[found + 5 :]
        targets: tuple[str, ...] = ()
        found = find_word(body, "into")
        if found >= 0:
            into = body[found + 4 :]
            body = body[:found]
            into = re.sub(r"(?i)^\s*strict\b", "", into)
            targets = tuple(t.strip() for t in into.split(","))
        return Stmt(
            "execute",
            stripped,
            start,
            targets=targets,
            expression=body.strip(),
            using=using.strip(),
        )
    if word == "return":
        rest = stripped[len("return") :].strip()
        after = _WORD.match(rest)
        second = after.group(0).lower() if after else ""
        if second == "query":
            query = rest[len("query") :].strip()
            if _WORD.match(query) and _WORD.match(query).group(0).lower() == "execute":
                raise UnsupportedError("RETURN QUERY EXECUTE")
            return Stmt("return_query", stripped, start, expression=query)
        if second == "next":
            return Stmt("return_next", stripped, start, expression=rest[4:].strip())
        return Stmt("return", stripped, start, expression=rest)
    if word == "fetch":
        found = find_word(stripped, "into")
        if found < 0:
            raise UnsupportedError("FETCH without INTO")
        head = stripped[len("fetch") : found].split()
        targets = tuple(t.strip() for t in stripped[found + 4 :].split(","))
        return Stmt("fetch", stripped, start, targets=targets, cursor=head[-1] if head else "")
    if word == "get":
        found = find_word(stripped, "diagnostics")
        if found < 0:
            raise UnsupportedError("GET without DIAGNOSTICS")
        targets = tuple(
            part.split("=")[0].replace(":", "").strip()
            for part in stripped[found + len("diagnostics") :].split(",")
        )
        return Stmt("diagnostics", stripped, start, targets=targets)
    assigned = _ASSIGN.match(stripped)
    if assigned:
        return Stmt(
            "assign",
            stripped,
            start,
            targets=(assigned.group(1),),
            expression=assigned.group(2).strip(),
        )
    raise UnsupportedError(f"PL/pgSQL statement {word.upper() or stripped[:20]!r}")


def read_body(source: str, start: int, end: int) -> Block:
    """The block a body holds, between its dollar tags (`start`/`end` are offsets)."""
    reader = _Reader(source, start, end)
    block = reader.block()
    if not reader.done():
        raise UnsupportedError("text after the body's END")
    return block


# --- routine headers ------------------------------------------------------------------------

_ROUTINE = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?P<kind>FUNCTION|PROCEDURE)\s+(?P<name>[\w.\"$]+)\s*\(",
    re.IGNORECASE,
)
_DO = re.compile(r"^\s*DO\s+(?:LANGUAGE\s+(?P<lang>\w+)\s+)?(?P<tag>\$[A-Za-z_]*\$)", re.IGNORECASE)
_TRIGGER = re.compile(
    r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\s+(?P<name>[\w\"]+)\s+"
    r"(?:BEFORE|AFTER|INSTEAD\s+OF)\s+.*?\bON\s+(?P<table>[\w.\"]+)(?P<rest>.*?)"
    r"\bEXECUTE\s+(?:FUNCTION|PROCEDURE)\s+(?P<function>[\w.\"]+)\s*\(",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class Routine:
    kind: str  # function | procedure | do
    name: str  # folded, schema-qualified as written
    params: list[str]
    returns: str  # trigger | void | table | setof | scalar | "" (procedure, DO)
    out_columns: list[str]
    language: str
    search_path: str | None
    body_start: int
    body_end: int
    out_params: list[str] = field(default_factory=list)
    """OUT / INOUT parameters: variables the body sets; with one, it is the value returned."""


def _matching(text: str, opening: int) -> int:
    depth = 0
    index = opening
    while index < len(text):
        skipped = skip_lexeme(text, index)
        if skipped is not None:
            index = skipped
            continue
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise UnsupportedError("unbalanced parentheses")


def _split_top(text: str) -> list[str]:
    parts, depth, begin, index = [], 0, 0, 0
    while index < len(text):
        skipped = skip_lexeme(text, index)
        if skipped is not None:
            index = skipped
            continue
        char = text[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[begin:index])
            begin = index + 1
        index += 1
    if text[begin:].strip():
        parts.append(text[begin:])
    return [p.strip() for p in parts]


def fold_name(text: str) -> str:
    return ".".join(
        part[1:-1].replace('""', '"') if part.startswith('"') else part.lower()
        for part in text.split(".")
    )


def read_routine(source: str, start: int, end: int) -> Routine | None:
    """A CREATE FUNCTION / PROCEDURE or a DO block between `start` and `end`, or None."""
    text = source[start:end]
    do = _DO.match(text)
    if do:
        tag = do.group("tag")
        body_start = start + do.end()
        closing = source.find(tag, body_start, end)
        if closing < 0:
            raise UnsupportedError("unterminated DO body")
        tail = source[closing + len(tag) : end]
        language = (do.group("lang") or "").lower() or (
            re.search(r"(?i)\bLANGUAGE\s+(\w+)", tail).group(1).lower()
            if re.search(r"(?i)\bLANGUAGE\s+(\w+)", tail)
            else "plpgsql"
        )
        return Routine("do", "<do>", [], "", [], language, None, body_start, closing)
    routine = _ROUTINE.match(text)
    if not routine:
        return None
    opening = routine.end() - 1
    closing = _matching(text, opening)
    params: list[str] = []
    out_params: list[str] = []
    for part in _split_top(text[opening + 1 : closing]):
        words = part.split()
        if not words:
            continue
        if words[0].lower() in ("out", "inout") and len(words) >= 2:
            # Plan step 5: an OUT parameter is a variable the routine sets and returns.
            out_params.append(words[1].lower())
            if words[0].lower() == "out":
                continue
            words = words[1:]
        if words[0].lower() in ("in", "variadic"):
            words = words[1:]
        if len(words) >= 2 and words[1].lower() not in ("default",):
            params.append(words[0].lower())
    header = text[closing + 1 :]
    dollar = re.search(r"\bAS\s+(\$[A-Za-z_]*\$)", header, re.IGNORECASE)
    if not dollar:
        raise UnsupportedError("a routine body that is not dollar-quoted")
    tag = dollar.group(1)
    body_start = start + closing + 1 + dollar.end()
    body_end = source.find(tag, body_start, end)
    if body_end < 0:
        raise UnsupportedError("unterminated routine body")
    options = header[: dollar.start()] + source[body_end + len(tag) : end]
    language = re.search(r"(?i)\bLANGUAGE\s+(\w+)", options)
    path = re.search(r"(?i)\bSET\s+search_path\s*(?:=|TO)\s*([\w\"]+)", options)
    returns_match = re.search(r"(?i)\bRETURNS\s+(SETOF\s+|TABLE\s*\()?\s*([\w.\"]*)", options)
    out_columns: list[str] = []
    returns = ""
    if returns_match:
        head = (returns_match.group(1) or "").strip().lower()
        if head.startswith("table"):
            returns = "table"
            open_at = options.lower().index("table", returns_match.start()) + len("table")
            open_at = options.index("(", open_at)
            close_at = _matching(options, open_at)
            out_columns = [
                p.split()[0].lower() for p in _split_top(options[open_at + 1 : close_at])
            ]
        elif head.startswith("setof"):
            returns = "setof"
        else:
            kind = returns_match.group(2).lower()
            returns = kind if kind in ("trigger", "void") else "scalar"
    elif out_params:
        returns = "scalar" if len(out_params) == 1 else "record"
    return Routine(
        kind=routine.group("kind").lower(),
        name=fold_name(routine.group("name")),
        params=params,
        returns=returns,
        out_columns=out_columns,
        language=language.group(1).lower() if language else "sql",
        search_path=fold_name(path.group(1)) if path else None,
        body_start=body_start,
        body_end=body_end,
        out_params=out_params,
    )


@dataclass
class TriggerBinding:
    name: str
    table: str
    function: str
    when: str  # the WHEN condition's text, or ""
    transitions: tuple[str, ...] = ()  # REFERENCING NEW / OLD TABLE AS name (plan step 8)


def read_trigger(text: str) -> TriggerBinding | None:
    match = _TRIGGER.match(text)
    if not match:
        return None
    when = ""
    found = re.search(r"(?i)\bWHEN\s*\(", match.group("rest"))
    if found:
        rest = match.group("rest")
        opening = found.end() - 1
        when = rest[opening + 1 : _matching(rest, opening)]
    transitions = tuple(
        fold_name(name)
        for name in re.findall(
            r"(?i)\b(?:NEW|OLD)\s+TABLE\s+(?:AS\s+)?([\w\"]+)", match.group("rest")
        )
    )
    return TriggerBinding(
        name=fold_name(match.group("name")),
        table=fold_name(match.group("table")),
        function=fold_name(match.group("function")),
        when=when.strip(),
        transitions=transitions,
    )
