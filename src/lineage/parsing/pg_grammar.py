"""PostgreSQL grammar SQLGlot cannot parse, restated as SQL it can (finding TT-5).

The shipment torture query - a fixture written for PostgreSQL 16/17 parser coverage - failed
to parse WHOLE: 18 of its 81 CTE bodies, both recursive-CTE decorations and the final
SELECT each held a construct SQLGlot's grammar does not have. One unknown spelling cost the
lineage of 2,963 lines.

Each construct is restated in the SOURCE, before anything splits or parses it, as SQL that
reads exactly the same columns, so the lineage is unchanged and only the spelling moves.
The rule is the one `rewrite.py` states, widened by one step: a clause that names no column
may be dropped; one that names columns must be restated so that every column it reads is
still read, in an expression of the same kind (a value stays a value, an aggregate stays an
aggregate). A construct that cannot be restated that way is left alone and still fails to
parse - counted and refused (TT-4), never guessed.

Written -> restated as (why it is lineage-neutral):

* `ORDER BY x USING >` -> `ORDER BY x` (the operator is a sort direction);
* `JOIN t USING (k) AS j` with `j.k` -> `JOIN t USING (k)` with `k` (`j.k` IS the merged
  column);
* `SEARCH ... SET s`, `CYCLE ... SET m USING p` -> `s`, `m`, `p` added to every arm, built
  from the BY / CYCLE columns as PostgreSQL builds them; CYCLE's implicit guard -> a
  predicate on the CYCLE columns in every recursive arm (D-54: it stops rows, a filter);
* SQL/JSON constructors and queries -> `jsonb_build_array`, `jsonb_agg`, ... over the same
  arguments, an aggregate staying an aggregate (D-29);
* `XMLPI`, `XMLEXISTS`, `XMLSERIALIZE`, `XMLPARSE`, `XMLROOT` keyword forms -> the plain
  call over the same expressions (D-29);
* `SUBSTRING(x SIMILAR p ESCAPE e)` -> `SUBSTRING(x, p, e)` (the same operands);
* `TABLE name` -> `SELECT * FROM name` (its definition; also after `CREATE ... AS`), and a
  bare `VALUES ... ORDER BY 1` -> `SELECT * FROM (VALUES ...) AS v ORDER BY 1`;
* CREATE options SQLGlot cannot read (finding F0-01) - `CREATE RECURSIVE VIEW` -> its
  documented `CREATE VIEW ... AS WITH RECURSIVE` equivalent; a materialized view's `WITH
  [NO] DATA`, `WITH ... CHECK OPTION`, a view's `WITH (options)`, `TABLESPACE`,
  `ON COMMIT DROP`, `WITHOUT OIDS` -> blanked, `WITH NO DATA` leaving a marker (no rows);
* `x AT LOCAL` -> `x AT TIME ZONE 'localtime'`;
* `0o17`, `0x1F`, `0b101`, `1_000` -> `15`, `31`, `5`, `1000`;
* `national character(5)`, `nchar varying(5)`, `bit varying` -> plain type names, and
  `integer[2][2]` -> `integer[][]` (PostgreSQL ignores declared sizes);
* `box '...'`, `pg_catalog.int4 '7'` -> `CAST('...' AS box)` (a typed literal);
* `COLLATION FOR (x)` -> `pg_collation_for(x)` (its documented equivalent);
* `x IS NFC NORMALIZED`, `IS DOCUMENT`, `IS JSON ...` -> `x IS NOT NULL` (a test of `x`);
* `a OPERATOR(pg_catalog.+) b` -> `a + b`; geometric, network and range operators SQLGlot
  has no rule for -> `+`, and unreadable prefix operators -> unary `+` (the same operands,
  read as a derived value).

Every rewrite keeps the line count of what it replaces, so every later line number holds.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from lineage.parsing.sqlscript import skip_lexeme


def restate(source: str) -> str:
    """Restate every construct in the table above. Idempotent; PostgreSQL only."""
    for rewrite in _REWRITES:
        source = rewrite(source)
    return source


# --- shared machinery ---------------------------------------------------------------------


def _mask(source: str) -> str:
    """`source` with every literal, quoted identifier and comment blanked (newlines kept).

    A literal keeps its first character, so a rewrite can tell `box '...'` (a string follows)
    from `box` alone; everything else inside it becomes `\x00`.
    """
    out = list(source)
    index = 0
    while index < len(source):
        end = skip_lexeme(source, index)
        if end is None:
            index += 1
            continue
        for position in range(index + 1 if source[index] in "'\"" else index, end):
            if out[position] != "\n":
                out[position] = "\x00"
        index = max(end, index + 1)
    return "".join(out)


def _closing(masked: str, opening: int) -> int | None:
    """The index of the `)` matching the `(` at `opening`, in masked text."""
    depth = 0
    for index in range(opening, len(masked)):
        char = masked[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _keep_lines(old: str, new: str) -> str:
    """`new`, padded with newlines so it spans as many lines as `old` did."""
    missing = old.count("\n") - new.count("\n")
    return new + "\n" * missing if missing > 0 else new


def _blank(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def _apply(source: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply non-overlapping `(start, end, replacement)` edits, last first."""
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + _keep_lines(source[start:end], replacement) + source[end:]
    return source


def _split_top(masked: str, start: int, end: int, separator: str = ",") -> list[tuple[int, int]]:
    """Spans of `masked[start:end]` between top-level separators."""
    spans, depth, begin = [], 0, start
    for index in range(start, end):
        char = masked[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == separator and depth == 0:
            spans.append((begin, index))
            begin = index + 1
    spans.append((begin, end))
    return spans


_NAME = r'(?:"(?:[^"]|"")*"|[A-Za-z_][\w$]*)'
_QUALIFIED = rf"{_NAME}(?:\s*\.\s*{_NAME})*"


# --- ORDER BY x USING op ------------------------------------------------------------------

_USING_OPERATOR = re.compile(
    r"\bUSING\s+(?:OPERATOR\s*\([^)]*\)|[<>=~!@#%^&|`?*/+-]+)", re.IGNORECASE
)


def _order_using(source: str) -> str:
    masked = _mask(source)
    edits = [(m.start(), m.end(), _blank(m.group(0))) for m in _USING_OPERATOR.finditer(masked)]
    return _apply(source, edits)


# --- JOIN ... USING (k) AS j --------------------------------------------------------------

# Only a plain column list: `MERGE ... USING (SELECT ...) AS s` has an alias too, and is a
# relation, not a join alias.
_JOIN_ALIAS = re.compile(
    rf"\bUSING\s*\(\s*{_NAME}(?:\s*,\s*{_NAME})*\s*\)(\s+AS\s+({_NAME}))", re.IGNORECASE
)


def _join_using_alias(source: str) -> str:
    while True:
        masked = _mask(source)
        match = _JOIN_ALIAS.search(masked)
        if match is None:
            return source
        alias = source[match.start(2) : match.end(2)]
        # The alias is visible in the query that holds the join: its enclosing parentheses.
        start, end = _enclosing(masked, match.start())
        references = re.compile(rf"(?<![\w$.\"]){re.escape(alias)}\s*\.\s*(?=[A-Za-z_\"])")
        edits = [(match.start(1), match.end(1), _blank(match.group(1)))]
        edits += [
            (ref.start(), ref.end(), "")
            for ref in references.finditer(masked, start, end)
            if not (match.start(1) <= ref.start() < match.end(1))
        ]
        source = _apply(source, edits)


def _enclosing(masked: str, position: int) -> tuple[int, int]:
    """The span of the innermost parentheses around `position`, or the whole text."""
    depth = 0
    for index in range(position - 1, -1, -1):
        char = masked[index]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth == 0:
                closing = _closing(masked, index)
                return index, closing if closing is not None else len(masked)
            depth -= 1
    return 0, len(masked)


# --- recursive CTE SEARCH / CYCLE ---------------------------------------------------------

_COLUMNS = rf"{_NAME}(?:\s*,\s*{_NAME})*"
_SEARCH = re.compile(
    rf"\s*SEARCH\s+(?:DEPTH|BREADTH)\s+FIRST\s+BY\s+(?P<by>{_COLUMNS})\s+SET\s+(?P<seq>{_NAME})",
    re.IGNORECASE,
)
_CYCLE = re.compile(
    rf"\s*CYCLE\s+(?P<cyc>{_COLUMNS})\s+SET\s+(?P<mark>{_NAME})"
    rf"(?:\s+TO\s+(?P<to>\S+)\s+DEFAULT\s+(?P<default>\S+))?\s+USING\s+(?P<path>{_NAME})",
    re.IGNORECASE,
)
_SET_OPERATOR = re.compile(r"\b(?:UNION|INTERSECT|EXCEPT)(?:\s+(?:ALL|DISTINCT))?\b", re.I)
_HEADER = re.compile(
    rf"({_NAME})\s*(\([^()]*\))?\s*AS\s+(?:NOT\s+)?(?:MATERIALIZED\s+)?$", re.IGNORECASE
)


def _search_cycle(source: str) -> str:
    while True:
        masked = _mask(source)
        found = None
        for closing in (m.start() for m in re.finditer(r"\)", masked)):
            search = _SEARCH.match(masked, closing + 1)
            cycle = _CYCLE.match(masked, search.end() if search else closing + 1)
            if search or cycle:
                found = (closing, search, cycle)
                break
        if found is None:
            return source
        restated = _restate_search_cycle(source, masked, *found)
        if restated is None:
            return source  # a shape not understood: left to fail, counted and refused
        source = restated


def _restate_search_cycle(source, masked, closing, search, cycle) -> str | None:
    opening = _opening(masked, closing)
    if opening is None:
        return None
    header = _HEADER.search(masked[:opening])
    if header is None:
        return None
    names = _names(source[header.start(2) + 1 : header.end(2) - 1]) if header.group(2) else None
    arms = _arms(masked, opening + 1, closing)
    if not arms:
        return None

    def group(match: re.Match[str], name: str) -> str:
        """A group's text from the SOURCE: in the masked text a literal is blanked."""
        start, end = match.span(name)
        return source[start:end] if start >= 0 else ""

    added: list[tuple[str, list[str], str]] = []  # (name, columns, kind)
    if search:
        added.append((group(search, "seq"), _names(group(search, "by")), "search"))
    if cycle:
        columns = _names(group(cycle, "cyc"))
        to, default = group(cycle, "to"), group(cycle, "default")
        added.append((group(cycle, "mark"), columns, f"mark\x01{to}\x01{default}"))
        added.append((group(cycle, "path"), columns, "path"))

    anchor_names = names or [_output_name(source, s, e) for s, e in arms[0][1]]
    edits: list[tuple[int, int, str]] = []
    for position, (select_end, items, arm_end) in enumerate(arms):
        expressions = [_without_alias(source[s:e]).strip() for s, e in items]
        if cycle and position > 0:
            # D-54 (finding FM-02): the recursive term stops extending a row whose CYCLE
            # columns are already on its path, so those columns decide which rows the CTE
            # holds. Restated as a predicate reading them as this arm computes them, as the
            # mark above reads them; the cycle test itself has no other operand.
            try:
                guarded = [expressions[anchor_names.index(c)] for c in _names(group(cycle, "cyc"))]
            except ValueError:
                return None
            edits.extend(
                _guard(masked, select_end, arm_end, f"ROW({', '.join(guarded)}) IS NOT NULL")
            )
        pieces = []
        for name, columns, kind in added:
            try:
                operands = [expressions[anchor_names.index(c.lower())] for c in columns]
            except ValueError:
                return None
            row = f"ROW({', '.join(operands)})"
            if kind == "search":
                expression = row
            elif kind == "path":
                expression = f"ARRAY[{row}]"
            else:
                _, to, default = kind.split("\x01", 2)
                expression = (
                    f"CASE WHEN {row} IS NULL THEN {to} ELSE {default} END"
                    if to
                    else f"({row} IS NULL)"
                )
            pieces.append(f"{expression} AS {name}")
        edits.append((select_end, select_end, ", " + ", ".join(pieces) + " "))
    end = (cycle or search).end()
    edits.append((closing + 1, end, _blank(source[closing + 1 : end])))
    if names is not None:
        list_end = header.end(2) - 1
        extra = ", ".join(name for name, _, _ in added)
        edits.append((list_end, list_end, f", {extra}"))
    return _apply(source, edits)


def _opening(masked: str, closing: int) -> int | None:
    depth = 0
    for index in range(closing, -1, -1):
        char = masked[index]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                return index
    return None


def _names(text: str) -> list[str]:
    return [part.strip().strip('"').lower() for part in text.split(",") if part.strip()]


_ARM_TAIL = ("GROUP", "HAVING", "WINDOW", "ORDER", "LIMIT", "OFFSET", "FETCH")


def _guard(masked: str, list_end: int, arm_end: int, predicate: str) -> list[tuple[int, int, str]]:
    """Edits that AND `predicate` into one arm's WHERE, or give the arm one."""
    tails = [_top_keyword(masked, list_end, arm_end, word) for word in _ARM_TAIL]
    at = min((tail for tail in tails if tail is not None), default=arm_end)
    where = _top_keyword(masked, list_end, at, "WHERE")
    if where is None:
        return [(at, at, f" WHERE {predicate} ")]
    after = where + len("WHERE")
    return [(after, after, " ("), (at, at, f") AND {predicate} ")]


def _arms(masked: str, start: int, end: int) -> list[tuple[int, list[tuple[int, int]], int]]:
    """Each top-level SELECT arm of a CTE body: where its select list ends, its items, and
    where the arm ends."""
    bounds, depth, begin = [], 0, start
    index = start
    while index < end:
        char = masked[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0:
            operator = _SET_OPERATOR.match(masked, index)
            if operator and (index == 0 or not masked[index - 1].isalnum()):
                bounds.append((begin, index))
                begin = operator.end()
                index = operator.end()
                continue
        index += 1
    bounds.append((begin, end))
    arms = []
    for arm_start, arm_end in bounds:
        select = re.compile(r"\bSELECT\b(\s+(?:ALL|DISTINCT)\b)?", re.I).search(
            masked, arm_start, arm_end
        )
        if select is None:
            return []
        list_end = _top_keyword(masked, select.end(), arm_end, "FROM") or arm_end
        arms.append((list_end, _split_top(masked, select.end(), list_end), arm_end))
    return arms


def _top_keyword(masked: str, start: int, end: int, word: str) -> int | None:
    depth = 0
    pattern = re.compile(rf"\b{word}\b", re.IGNORECASE)
    for index in range(start, end):
        char = masked[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and pattern.match(masked, index) and not masked[index - 1].isalnum():
            return index
    return None


_ALIAS = re.compile(rf"\s+AS\s+({_NAME})\s*$", re.IGNORECASE)


def _without_alias(item: str) -> str:
    return _ALIAS.sub("", item)


def _output_name(source: str, start: int, end: int) -> str:
    item = source[start:end].strip()
    alias = _ALIAS.search(item)
    if alias:
        return alias.group(1).strip('"').lower()
    return re.split(r"[.\s]", item)[-1].strip('"').lower()


# --- SQL/JSON and XML keyword forms -------------------------------------------------------

_SQLJSON = re.compile(
    r"(?<![\w$.])(JSON_VALUE|JSON_QUERY|JSON_EXISTS|JSON_ARRAYAGG|JSON_OBJECTAGG|JSON_ARRAY"
    r"|JSON_OBJECT|JSON_SCALAR|JSON_SERIALIZE|JSON|XMLPI|XMLEXISTS|XMLSERIALIZE|XMLPARSE"
    r"|XMLROOT)\s*\(",
    re.IGNORECASE,
)


def _sqljson(source: str) -> str:
    offset = 0
    while True:
        masked = _mask(source)
        match = _SQLJSON.search(masked, offset)
        if match is None:
            return source
        opening = match.end() - 1
        closing = _closing(masked, opening)
        if closing is None:
            return source
        restated = _restate_call(
            match.group(1).upper(), source[opening + 1 : closing], masked[opening + 1 : closing]
        )
        offset = match.start() + 1
        if restated is None:
            continue
        source = _apply(source, [(match.start(), closing + 1, restated)])


def _restate_call(name: str, inner: str, masked: str) -> str | None:
    """One SQL/JSON or XML call in plain-function form, or None to leave it as written."""
    tokens = _tokens(inner, masked)
    if name == "XMLPI":
        # XMLPI(NAME target [, content])
        rest = _after_word(tokens, "NAME", skip=1)
        args, _ = _arguments(rest)  # the target is skipped already: these are the content
        return f"xmlpi({', '.join(args)})"
    if name == "XMLROOT":
        # XMLROOT(xml, VERSION {text | NO VALUE} [, STANDALONE {YES | NO | NO VALUE}])
        keywords = ("VERSION", "STANDALONE", "YES", "NO", "VALUE")
        kept = [t for t in tokens if _upper(t) not in keywords]
        parts = [part.strip() for part in _text(kept).split(",")]
        return f"xmlconcat({', '.join(part for part in parts if part)})"
    if name == "XMLPARSE":
        # XMLPARSE({DOCUMENT | CONTENT} value [{PRESERVE | STRIP} WHITESPACE])
        words = [t for t in tokens if t[0] != "space"]
        if words and _upper(words[0]) in ("DOCUMENT", "CONTENT"):
            body = tokens[tokens.index(words[0]) + 1 :]
            body = [t for t in body if _upper(t) not in ("PRESERVE", "STRIP", "WHITESPACE")]
            return f"CAST({_text(body).strip()} AS xml)"
        return None
    if name == "XMLSERIALIZE":
        words = [t for t in tokens if t[0] != "space"]
        if words and words[0][1].upper() in ("DOCUMENT", "CONTENT"):
            body = _until_word(tokens[tokens.index(words[0]) + 1 :], "AS")
            return f"xmlserialize({_text(body).strip()})"
        return None
    if name == "XMLEXISTS":
        head = _until_word(tokens, "PASSING")
        tail = tokens[len(head) + 1 :]
        tail = [t for t in tail if t[1].upper() not in ("BY", "REF", "VALUE")]
        return f"xmlexists({_text(head).strip()}, {_text(tail).strip()})"
    args, order = _arguments(tokens)
    if not args and name not in ("JSON_ARRAY", "JSON_OBJECT"):
        return None
    if name == "JSON_ARRAYAGG":
        return f"jsonb_agg({args[0]}{' ORDER BY ' + order if order else ''})"
    if name == "JSON_OBJECTAGG":
        return f"jsonb_object_agg({', '.join(args[:2])})"
    if name == "JSON_ARRAY":
        if len(args) == 1 and re.match(r"(?is)\s*SELECT\b", args[0]):
            return f"to_jsonb(ARRAY({args[0]}))"
        return f"jsonb_build_array({', '.join(args)})"
    if name == "JSON_OBJECT":
        return f"jsonb_build_object({', '.join(args)})"
    if name in ("JSON", "JSON_SCALAR", "JSON_SERIALIZE"):
        return f"to_jsonb({args[0]})"
    return f"jsonb_path_query_first({', '.join(args)})"  # JSON_VALUE / _QUERY / _EXISTS


Token = tuple[str, str]  # (kind, text): word, group, other, space, comma, colon


def _tokens(inner: str, masked: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    while index < len(inner):
        char = masked[index]
        if char.isspace():
            end = index
            while end < len(inner) and masked[end].isspace():
                end += 1
            tokens.append(("space", inner[index:end]))
            index = end
        elif char == "(" or char == "[":
            closing = _closing(masked.replace("[", "(").replace("]", ")"), index)
            end = (closing if closing is not None else len(inner) - 1) + 1
            tokens.append(("group", inner[index:end]))
            index = end
        elif char == ",":
            tokens.append(("comma", ","))
            index += 1
        elif char == ":" and masked[index : index + 2] != "::" and masked[index - 1] != ":":
            tokens.append(("colon", ":"))
            index += 1
        elif char.isalpha() or char == "_":
            end = index
            while end < len(inner) and (masked[end].isalnum() or masked[end] in "_$"):
                end += 1
            tokens.append(("word", inner[index:end]))
            index = end
        else:
            end = index + 1
            while end < len(inner) and masked[end] == "\x00":
                end += 1
            tokens.append(("other", inner[index:end]))
            index = end
    return tokens


def _text(tokens: list[Token]) -> str:
    return "".join(text for _, text in tokens)


def _upper(token: Token | None) -> str:
    return token[1].upper() if token is not None and token[0] == "word" else ""


def _after_word(tokens: list[Token], word: str, skip: int = 0) -> list[Token]:
    for index, token in enumerate(tokens):
        if _upper(token) == word:
            rest = tokens[index + 1 :]
            while skip and rest:
                if rest[0][0] != "space":
                    skip -= 1
                rest = rest[1:]
            return rest
    return tokens


def _until_word(tokens: list[Token], word: str) -> list[Token]:
    for index, token in enumerate(tokens):
        if _upper(token) == word:
            return tokens[:index]
    return tokens


def _arguments(tokens: list[Token]) -> tuple[list[str], str | None]:
    """The value expressions of a SQL/JSON call, and its ORDER BY, clause keywords removed."""
    # Each non-space token carries the space after it, so an expression is rebuilt exactly as
    # written (`100`, `x::text`, `a . b` all stay what they were).
    words: list[Token] = []
    trailing: list[str] = []
    for token in tokens:
        if token[0] == "space":
            if trailing:
                trailing[-1] += token[1]
            continue
        words.append(token)
        trailing.append("")
    args: list[str] = []
    current: list[str] = []
    order: list[str] | None = None
    index = 0

    def peek(offset: int = 0) -> str:
        return _upper(words[index + offset]) if index + offset < len(words) else ""

    def finish() -> None:
        text = "".join(current).strip()
        if text:
            args.append(text)
        current.clear()

    while index < len(words):
        kind = words[index][0]
        text = words[index][1] + trailing[index]
        word = _upper(words[index])
        if kind in ("comma", "colon") and order is None:
            finish()
        elif word == "RETURNING":
            index += 1
            while index < len(words) and not _clause_starts(words, index):
                index += 1
            continue
        elif word == "FORMAT" and peek(1) == "JSON":
            index += 2
            if peek() == "ENCODING":
                index += 2
            continue
        elif peek(1) == "ON" and word in ("ERROR", "NULL", "ABSENT", "TRUE", "FALSE", "UNKNOWN"):
            # `NULL ON NULL`, `ABSENT ON NULL`, `ERROR ON EMPTY`, `NULL ON ERROR` ...
            index += 3
            continue
        elif word == "EMPTY" and peek(1) in ("ARRAY", "OBJECT") and peek(2) == "ON":
            index += 4
            continue
        elif word == "EMPTY" and peek(1) == "ON":
            index += 3
            continue
        elif word == "DEFAULT":
            finish()
            index += 1
            while index < len(words) and not (peek() == "ON" and peek(1) in ("EMPTY", "ERROR")):
                current.append(words[index][1] + trailing[index])
                index += 1
            finish()
            index += 2
            continue
        elif word == "PASSING":
            finish()
        elif word == "AS" and order is None:
            # `PASSING expr AS name`, or the path name of JSON_TABLE: the name is no value
            finish()
            index += 2
            continue
        elif word in ("WITH", "WITHOUT") and peek(1) in (
            "UNIQUE",
            "WRAPPER",
            "CONDITIONAL",
            "UNCONDITIONAL",
            "ARRAY",
        ):
            index += 1
            while peek() in ("UNIQUE", "KEYS", "WRAPPER", "CONDITIONAL", "UNCONDITIONAL", "ARRAY"):
                index += 1
            continue
        elif word in ("KEEP", "OMIT") and peek(1) == "QUOTES":
            index += 2
            if peek() == "ON":
                index += 3
            continue
        elif word in ("KEY", "VALUE") and order is None:
            finish()
        elif word == "ORDER" and peek(1) == "BY":
            finish()
            order = []
            index += 2
            continue
        elif order is not None:
            order.append(text)
        else:
            current.append(text)
        index += 1
    finish()
    return args, ("".join(order).strip() if order else None)


def _clause_starts(words: list[Token], index: int) -> bool:
    word = _upper(words[index])
    following = _upper(words[index + 1]) if index + 1 < len(words) else ""
    if word in ("FORMAT", "DEFAULT", "ERROR", "EMPTY", "ABSENT", "KEEP", "OMIT", "PASSING"):
        return True
    if word in ("NULL", "TRUE", "FALSE", "UNKNOWN") and following == "ON":
        return True
    if word in ("WITH", "WITHOUT") and following in (
        "UNIQUE",
        "WRAPPER",
        "CONDITIONAL",
        "UNCONDITIONAL",
        "ARRAY",
    ):
        return True
    return words[index][0] in ("comma", "colon")


# --- small lexical forms ------------------------------------------------------------------

_SUBSTRING_SIMILAR = re.compile(r"(?<![\w$.])SUBSTRING\s*\(", re.IGNORECASE)


def _substring_similar(source: str) -> str:
    offset = 0
    while True:
        masked = _mask(source)
        match = _SUBSTRING_SIMILAR.search(masked, offset)
        if match is None:
            return source
        offset = match.end()
        closing = _closing(masked, match.end() - 1)
        if closing is None:
            continue
        inner = masked[match.end() : closing]
        similar = re.search(r"\bSIMILAR\b", inner, re.I)
        if similar is None:
            continue
        escape = re.search(r"\bESCAPE\b", inner, re.I)
        start = match.end()
        edits = [(start + similar.start(), start + similar.end(), ",")]
        if escape:
            edits.append((start + escape.start(), start + escape.end(), ","))
        source = _apply(source, edits)


_TABLE = re.compile(rf"\bTABLE\s+({_QUALIFIED})", re.IGNORECASE)
_TABLE_CONTEXT = re.compile(
    r"(?:\(|\bUNION|\bINTERSECT|\bEXCEPT|\bALL|\bDISTINCT|\bAS"
    # `INSERT INTO t [AS a] [(cols)] TABLE src` (plan step 8)
    r"|\bINSERT\s+INTO\s+[\w.\"]+(?:\s+AS\s+\w+)?\s*(?:\([^()]*\))?)\s*$",
    re.I,
)


def _table_command(source: str) -> str:
    masked = _mask(source)
    edits = []
    for match in _TABLE.finditer(masked):
        if _TABLE_CONTEXT.search(masked[max(0, match.start() - 200) : match.start()]):
            name = source[match.start(1) : match.end(1)]
            edits.append((match.start(), match.end(), f"SELECT * FROM {name}"))
    return _apply(source, edits)


def _at_local(source: str) -> str:
    masked = _mask(source)
    pattern = re.compile(r"\bAT\s+LOCAL\b", re.IGNORECASE)
    edits = [(m.start(), m.end(), "AT TIME ZONE 'localtime'") for m in pattern.finditer(masked)]
    return _apply(source, edits)


_NUMBER = re.compile(
    r"(?<![\w$.])(0[xX][0-9A-Fa-f_]+|0[oO][0-7_]+|0[bB][01_]+|\d+(?:_\d+)+(?:\.\d+(?:_\d+)*)?)(?![\w$])"
)


def _numeric_literals(source: str) -> str:
    masked = _mask(source)
    edits = []
    for match in _NUMBER.finditer(masked):
        text = match.group(1).replace("_", "")
        bases = {"x": 16, "o": 8, "b": 2}
        prefix = text[1:2].lower() if text[:1] == "0" and len(text) > 2 else ""
        value = str(int(text[2:], bases[prefix])) if prefix in bases else text
        edits.append((match.start(), match.end(), value))
    return _apply(source, edits)


_NATIONAL = [
    (re.compile(r"\bbit\s+varying\b", re.I), "varbit"),
    (re.compile(r"\bnational\s+(character|char)\s+varying\b", re.I), "varchar"),
    (re.compile(r"\bnational\s+(character|char)\b", re.I), "character"),
    (re.compile(r"\bnchar\s+varying\b", re.I), "varchar"),
    (re.compile(r"\bnchar\b", re.I), "character"),
]


def _national_types(source: str) -> str:
    for pattern, replacement in _NATIONAL:
        masked = _mask(source)
        source = _apply(
            source, [(m.start(), m.end(), replacement) for m in pattern.finditer(masked)]
        )
    return source


_TYPED_LITERAL = re.compile(
    # `path` only where a value can start: `PATH '$.x'` after a column type is the column-path
    # keyword of JSON_TABLE and XMLTABLE (`_is_value_position`).
    r"(?<![\w$.])(box|circle|lseg|point|path|polygon|line|inet|cidr|macaddr8?|tsquery|tsvector"
    r"|uuid|money|int4range|int8range|numrange|tsrange|tstzrange|daterange|varbit)\s+(?=')",
    re.IGNORECASE,
)


# A schema-qualified type before a literal (`pg_catalog.int4 '7'`). A qualified name directly
# followed by a string is no other construct: a column cannot be followed by a literal.
_QUALIFIED_TYPED_LITERAL = re.compile(
    r"(?<![\w$.])([A-Za-z_][\w$]*\s*\.\s*[A-Za-z_][\w$]*)\s+(?=')", re.IGNORECASE
)


def _is_value_position(masked: str, position: int) -> bool:
    """Does a value start here - after an operator, `(`, `,` or a keyword, not after a type?

    A column type ends in a word or `)` (`bigint`, `numeric(12,3)`), and `PATH` after one is
    JSON_TABLE's keyword; `@-@ path '...'` or `, path '...'` is a geometric literal.
    """
    before = masked[:position].rstrip()
    if not before:
        return True
    if before[-1] == ")":
        return False
    if before[-1].isalnum() or before[-1] in '_$"':
        word = re.search(r"[A-Za-z_]\w*$", before)
        return bool(word) and word.group(0).upper() in (
            "SELECT",
            "WHERE",
            "AND",
            "OR",
            "NOT",
            "WHEN",
            "THEN",
            "ELSE",
            "ON",
            "BY",
            "RETURN",
        )
    return True


def _typed_literals(source: str) -> str:
    masked = _mask(source)
    edits = []
    for match in [*_TYPED_LITERAL.finditer(masked), *_QUALIFIED_TYPED_LITERAL.finditer(masked)]:
        end = skip_lexeme(source, match.end())
        if end is None:
            continue
        if match.group(1).lower() == "path" and not _is_value_position(masked, match.start()):
            continue
        literal = source[match.end() : end]
        edits.append((match.start(), end, f"CAST({literal} AS {match.group(1)})"))
    return _apply(source, edits)


_IS_PREDICATE = re.compile(
    r"\bIS\s+(?:NOT\s+)?(?:(?:NFC|NFD|NFKC|NFKD)\s+)?NORMALIZED\b"
    r"|\bIS\s+(?:NOT\s+)?DOCUMENT\b"
    r"|\bIS\s+(?:NOT\s+)?JSON(?:\s+(?:VALUE|SCALAR|ARRAY|OBJECT))?"
    r"(?:\s+(?:WITH|WITHOUT)\s+UNIQUE(?:\s+KEYS)?)?",
    re.IGNORECASE,
)


def _is_predicates(source: str) -> str:
    """`x IS [NFC] NORMALIZED`, `IS DOCUMENT`, `IS JSON ...`: a test of `x` alone.

    SQLGlot reads the first as `x IS NFC` aliased `NORMALIZED` and then fails on the real
    alias. Each is a boolean of its one operand, which `x IS NOT NULL` is too.
    """
    masked = _mask(source)
    return _apply(
        source, [(m.start(), m.end(), "IS NOT NULL") for m in _IS_PREDICATE.finditer(masked)]
    )


_OPERATOR_RUN = re.compile(r"[+\-*/<>=~!@#%^&|`?]+")
# Measured against SQLGlot's PostgreSQL reader: the geometric, network and range operators it
# has no grammar for. Restated as `+`, which reads the same two operands as a derived value;
# precedence cannot move an operand out of the expression it is written in.
_UNREADABLE_BINARY = {
    "?||",
    "?-|",
    "<#>",
    "@-@",
    "##",
    "<<|",
    "|>>",
    "&<|",
    "|&>",
    "<^",
    ">^",
    "~=",
    "<<=",
    ">>=",
    "!!",
}
# Prefix operators it reads as something else or not at all: absolute value, length, point
# count, centre, square and cube root. Restated as unary `+`.
_UNREADABLE_PREFIX = {"@", "@-@", "#", "@@", "|/", "||/", "!!", "?-", "?|"}
_OPERATOR_SYNTAX = re.compile(
    r"\bOPERATOR\s*\(\s*(?:pg_catalog\s*\.\s*)?([+\-*/<>=~!@#%^&|`?]+)\s*\)", re.IGNORECASE
)


def _operator_syntax(source: str) -> str:
    """`a OPERATOR(pg_catalog.+) b` is `a + b`: the schema only says where `+` is defined.

    Only the built-in schema: `OPERATOR(mart.===)` is a user's operator, which means nothing
    as `===`, and it keeps the handling a user routine gets (B10).
    """
    masked = _mask(source)
    edits = [(m.start(), m.end(), m.group(1)) for m in _OPERATOR_SYNTAX.finditer(masked)]
    return _apply(source, edits)


_ARRAY_DIMENSIONS = re.compile(
    r"((?:\bAS|::)\s*[A-Za-z_][\w. ]*?(?:\([^()]*\))?)((?:\s*\[\s*\d+\s*\])+)", re.IGNORECASE
)


def _array_dimensions(source: str) -> str:
    """`integer[2][2]` is `integer[][]`: PostgreSQL does not enforce declared sizes."""
    masked = _mask(source)
    edits = [
        (m.start(2), m.end(2), "[]" * m.group(2).count("["))
        for m in _ARRAY_DIMENSIONS.finditer(masked)
    ]
    return _apply(source, edits)


_PREFIX_CONTEXT = re.compile(
    r"(?:^|[(,=<>+\-*/]|\b(?:SELECT|WHERE|AND|OR|NOT|WHEN|THEN|ELSE|ON|BY|HAVING|RETURN))\s*$",
    re.IGNORECASE,
)


def _operators(source: str) -> str:
    masked = _mask(source)
    edits = []
    for match in _OPERATOR_RUN.finditer(masked):
        run = match.group(0)
        before = masked[max(0, match.start() - 12) : match.start()]
        prefix = bool(_PREFIX_CONTEXT.search(before))
        if (prefix and run in _UNREADABLE_PREFIX) or (not prefix and run in _UNREADABLE_BINARY):
            edits.append((match.start(), match.end(), "+"))
    return _apply(source, edits)


_VALUES_HEAD = re.compile(r"\(\s*VALUES\b", re.IGNORECASE)
_ROW_LIMITS = re.compile(r"\b(?:ORDER\s+BY|LIMIT|OFFSET|FETCH)\b", re.IGNORECASE)


def _ordered_values(source: str) -> str:
    """`(VALUES (...), (...) ORDER BY 1 LIMIT 2)` as a SELECT over the same VALUES.

    PostgreSQL lets a bare VALUES list carry ORDER BY / LIMIT; SQLGlot parses it but cannot
    resolve `ORDER BY 1` against a VALUES ("Unknown output column: 1") and the whole
    statement was refused. Restated as `SELECT * FROM (VALUES ...) AS v ORDER BY 1 ...`:
    the same rows, read from literals only.
    """
    masked = _mask(source)
    edits = []
    for head in _VALUES_HEAD.finditer(masked):
        closing = _closing(masked, head.start())
        if closing is None:
            continue
        values_start = head.end() - len("VALUES")
        limit = None
        depth = 0
        for index in range(values_start, closing):
            char = masked[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif depth == 0 and _ROW_LIMITS.match(masked, index):
                limit = index
                break
        if limit is not None:
            values = source[values_start:limit].rstrip()
            edits.append((values_start, limit, f"SELECT * FROM ({values}) AS v "))
    return _apply(source, edits)


def _collation_for(source: str) -> str:
    masked = _mask(source)
    pattern = re.compile(r"\bCOLLATION\s+FOR\s*\(", re.IGNORECASE)
    edits = [(m.start(), m.end(), "pg_collation_for(") for m in pattern.finditer(masked)]
    return _apply(source, edits)


# --- DDL options SQLGlot keeps as a raw Command (finding F0-01) -----------------------------
#
# A CREATE that SQLGlot cannot read whole is kept as an opaque `Command`, which carried no
# kind band 0 handles: the statement was not even counted, and a whole view's or temp
# table's lineage vanished. Each clause below names no column, so it is blanked; the two
# with a meaning of their own are restated instead.

NO_DATA_MARKER = "/*lineage:with-no-data*/"
"""Left where `WITH NO DATA` stood on a materialized view: it holds no rows until REFRESH
(B7-06, D-32), which the catalog reads from this marker."""

_QUERY_START = r"(?:\(|SELECT\b|WITH\b|VALUES\b|TABLE\b)"
_RECURSIVE_VIEW = re.compile(
    rf"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:TEMP|TEMPORARY)\s+)?(?P<recursive>RECURSIVE\s+)VIEW\s+"
    rf"(?P<name>{_QUALIFIED})\s*\((?P<columns>[^()]*)\)\s*AS\s+",
    re.IGNORECASE,
)
_TRAILING_DATA = re.compile(r"\bWITH\s+(?P<no>NO\s+)?DATA\s*$", re.IGNORECASE)
_CHECK_OPTION = re.compile(r"\bWITH\s+(?:CASCADED\s+|LOCAL\s+)?CHECK\s+OPTION\s*$", re.I)
_TABLESPACE = re.compile(rf"\bTABLESPACE\s+{_NAME}", re.IGNORECASE)
_ON_COMMIT_DROP = re.compile(r"\bON\s+COMMIT\s+DROP\b", re.IGNORECASE)
_WITHOUT_OIDS = re.compile(r"\bWITHOUT\s+OIDS\b", re.IGNORECASE)
_VIEW_OPTIONS = re.compile(r"\bWITH\s*\(", re.IGNORECASE)


def _create_options(source: str) -> str:
    """Restate, per CREATE statement, the options that make SQLGlot give up.

    * `CREATE RECURSIVE VIEW v (cols) AS q` -> `CREATE VIEW v AS WITH RECURSIVE v (cols) AS
      (q) SELECT cols FROM v`, PostgreSQL's documented equivalent;
    * a materialized view's `WITH DATA` (the default) is blanked, `WITH NO DATA` becomes
      `NO_DATA_MARKER`;
    * `WITH [CASCADED | LOCAL] CHECK OPTION`, a view's `WITH (options)`, `TABLESPACE t`,
      `ON COMMIT DROP` and `WITHOUT OIDS` in a CREATE header are blanked.
    """
    masked = _mask(source)
    edits: list[tuple[int, int, str]] = []
    start = 0
    for end in [i for i, char in enumerate(masked) if char == ";"] + [len(masked)]:
        _create_statement(source, masked, start, end, edits)
        start = end + 1
    return _apply(source, edits)


def _create_statement(
    source: str, masked: str, start: int, end: int, edits: list[tuple[int, int, str]]
) -> None:
    lead = len(masked[start:end]) - len(masked[start:end].lstrip(" \t\r\n\x00"))
    begin = start + lead
    text = masked[begin:end].rstrip()
    stop = begin + len(text)
    head = re.match(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:GLOBAL|LOCAL)\s+)?"
        r"(?:(?:TEMP|TEMPORARY|UNLOGGED)\s+)?(?:RECURSIVE\s+)?(?P<kind>MATERIALIZED\s+VIEW|VIEW|TABLE)\b",
        text,
        re.IGNORECASE,
    )
    if head is None:
        return
    kind = " ".join(head.group("kind").upper().split())
    body = re.search(rf"\bAS\s+{_QUERY_START}", text[head.end() :], re.IGNORECASE)
    if body is None:
        return  # not CREATE ... AS <query>: nothing of this rewrite's concern
    header_end = head.end() + body.start()

    for pattern in (_TABLESPACE, _ON_COMMIT_DROP, _WITHOUT_OIDS):
        for match in pattern.finditer(text, head.end(), header_end):
            edits.append((begin + match.start(), begin + match.end(), ""))
    if kind == "VIEW":
        options = _VIEW_OPTIONS.search(text, head.end(), header_end)
        if options is not None:
            closing = _closing(masked, begin + options.end() - 1)
            if closing is not None and closing < begin + header_end:
                edits.append((begin + options.start(), closing + 1, ""))
        check = _CHECK_OPTION.search(text)
        if check is not None:
            edits.append((begin + check.start(), begin + check.end(), ""))
    if kind == "MATERIALIZED VIEW":
        data = _TRAILING_DATA.search(text)
        if data is not None:
            marker = NO_DATA_MARKER if data.group("no") else ""
            edits.append((begin + data.start(), begin + data.end(), marker))

    recursive = _RECURSIVE_VIEW.match(text)
    if recursive is not None:
        written = source[begin + recursive.start("name") : begin + recursive.end("name")]
        name = written.rsplit(".", 1)[-1].strip()  # a CTE name is never qualified
        columns = source[begin + recursive.start("columns") : begin + recursive.end("columns")]
        edits.append((begin + recursive.start("recursive"), begin + recursive.end("recursive"), ""))
        edits.append(
            (
                begin + recursive.end("name"),
                begin + recursive.end(),
                f" AS WITH RECURSIVE {name} ({columns}) AS (",
            )
        )
        edits.append((stop, stop, f") SELECT {columns} FROM {name}"))


_REWRITES: list[Callable[[str], str]] = [
    _create_options,
    _search_cycle,
    _order_using,
    _join_using_alias,
    _sqljson,
    _substring_similar,
    _table_command,
    _ordered_values,
    _at_local,
    _numeric_literals,
    _national_types,
    _typed_literals,
    _collation_for,
    _is_predicates,
    _array_dimensions,
    _operator_syntax,
    _operators,
]
