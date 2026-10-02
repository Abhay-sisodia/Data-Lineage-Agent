"""JSON_TABLE and XMLTABLE, restated in the statement text before anything parses it (D-30).

Both were refused SHAPE_NOT_DECIDABLE - "columns are projected out of a document by path
expression". But the COLUMNS clause states the output shape, so the shape is decided;
what the analyser lacks is only the document's internal structure, and D-29 says lineage
does not need it: a column projected out of a document reads the document, whole.

So each one becomes the LATERAL subquery with the same columns:

    JSON_TABLE(o.payload, '$.items[*]' PASSING o.code AS s
               COLUMNS (n FOR ORDINALITY, qty int PATH '$.qty')) AS jt
    LATERAL (SELECT ROW_NUMBER() OVER (ORDER BY o.payload) AS "n",
                    concat(o.payload, o.code) AS "qty") AS jt

Every declared column reads the document and every PASSING expression, derived (D-30,
D-22). An ordinality column is a ROW_NUMBER ordered by the document, which is D-26's
influence through D-4. NESTED PATH columns are flattened into the same list - each still
reads the document. Paths are literals and vanish, as D-29 says they should.

**Why text, not the tree:** SQLGlot cannot parse a JSON_TABLE with a PASSING clause at all
(finding B5-04), so a tree-level rewrite would never see the statements that need it most.
The rewrite keeps the statement's line count, so every origin still points where it did. A
shape this scanner cannot read is left untouched - and then refused by name, as before.
"""

from __future__ import annotations

import re

_START = re.compile(r"\b(JSON_TABLE|XMLTABLE)\s*\(", re.IGNORECASE)
_ALIAS = re.compile(r"\s*(?:AS\s+)?((?:\"[^\"]+\")|(?:[A-Za-z_][A-Za-z0-9_$]*))", re.IGNORECASE)
_KEYWORDS = {"WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "FULL", "CROSS", "ON", "GROUP",
             "ORDER", "LIMIT", "UNION", "EXCEPT", "INTERSECT", "WINDOW", "HAVING"}


def restate(text: str) -> str:
    """Every JSON_TABLE / XMLTABLE in `text` as a LATERAL subquery; unreadable ones kept."""
    out: list[str] = []
    position = 0
    while True:
        match = _next_start(text, position)
        if match is None:
            break
        open_at = match.end() - 1
        close_at = _matching(text, open_at)
        if close_at is None:
            break
        alias_match = _ALIAS.match(text, close_at + 1)
        alias = alias_match.group(1) if alias_match else None
        if alias is None or alias.upper() in _KEYWORDS:
            position = close_at + 1
            continue
        body = text[open_at + 1 : close_at]
        replacement = _restated(match.group(1).upper(), body, alias)
        if replacement is None:
            position = close_at + 1
            continue
        before = text[position : match.start()]
        lateral = "" if re.search(r"\bLATERAL\s*$", before, re.IGNORECASE) else "LATERAL "
        original = text[match.start() : alias_match.end()]
        replacement = f"{lateral}{replacement}" + "\n" * max(
            0, original.count("\n") - replacement.count("\n")
        )
        out.append(before)
        out.append(replacement)
        position = alias_match.end()
    out.append(text[position:])
    return "".join(out)


def _restated(kind: str, body: str, alias: str) -> str | None:
    if kind == "JSON_TABLE":
        parts = _split_top(body, ",", limit=1)
        if len(parts) != 2:
            return None
        document, rest = parts[0].strip(), parts[1]
        passing = _between(rest, "PASSING", "COLUMNS")
        arguments = [_strip_as(p) for p in _split_top(passing, ",")] if passing else []
        columns_text = _after(rest, "COLUMNS")
        if columns_text is None:
            return None
        inner = _parenthesised(columns_text)
        if inner is None:
            return None
        columns = _columns(inner)
    else:  # XMLTABLE
        passing = _between(body, "PASSING", "COLUMNS")
        if passing is None:
            return None
        document = re.sub(r"^\s*BY\s+(?:REF|VALUE)\s+|\s+BY\s+(?:REF|VALUE)\s*$", "", passing,
                          flags=re.IGNORECASE).strip()
        arguments = []
        columns_text = _after(body, "COLUMNS")
        if columns_text is None:
            return None
        columns = _columns(columns_text)
    if not columns or not document:
        return None
    reads = ", ".join([document, *arguments])
    projections = []
    for name, ordinal in columns:
        if ordinal:
            projections.append(f"ROW_NUMBER() OVER (ORDER BY {document}) AS {name}")
        else:
            projections.append(f"concat({reads}) AS {name}")
    return f"(SELECT {', '.join(projections)}) AS {alias}"


def _columns(text: str) -> list[tuple[str, bool]] | None:
    found: list[tuple[str, bool]] = []
    for definition in _split_top(text, ","):
        definition = definition.strip()
        if not definition:
            continue
        if re.match(r"NESTED\b", definition, re.IGNORECASE):
            nested = _after(definition, "COLUMNS")
            inner = _parenthesised(nested) if nested is not None else None
            if inner is None:
                return None
            more = _columns(inner)
            if more is None:
                return None
            found.extend(more)
            continue
        name_match = re.match(r'("[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)', definition)
        if name_match is None:
            return None
        ordinal = bool(re.search(r"\bFOR\s+ORDINALITY\b", definition, re.IGNORECASE))
        found.append((name_match.group(1), ordinal))
    return found


def _next_start(text: str, position: int) -> re.Match[str] | None:
    """The next JSON_TABLE( / XMLTABLE( outside a string literal or comment."""
    for match in _START.finditer(text, position):
        if not _in_literal(text, match.start()):
            return match
    return None


def _in_literal(text: str, index: int) -> bool:
    quoted = False
    i = 0
    while i < index:
        c = text[i]
        if c == "'":
            quoted = not quoted
        elif not quoted and text.startswith("--", i):
            end = text.find("\n", i)
            if end == -1 or end >= index:
                return True
            i = end
        i += 1
    return quoted


def _matching(text: str, open_at: int) -> int | None:
    depth, quoted, quote = 0, False, ""
    for i in range(open_at, len(text)):
        c = text[i]
        if quoted:
            if c == quote:
                quoted = False
            continue
        if c in ("'", '"'):
            quoted, quote = True, c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def _split_top(text: str, separator: str, limit: int = -1) -> list[str]:
    parts, depth, quoted, quote, start = [], 0, False, "", 0
    for i, c in enumerate(text):
        if quoted:
            if c == quote:
                quoted = False
            continue
        if c in ("'", '"'):
            quoted, quote = True, c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == separator and depth == 0 and (limit < 0 or len(parts) < limit):
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _top_keyword(text: str, keyword: str) -> int | None:
    depth, quoted, quote = 0, False, ""
    pattern = re.compile(rf"\b{keyword}\b", re.IGNORECASE)
    for i, c in enumerate(text):
        if quoted:
            if c == quote:
                quoted = False
            continue
        if c in ("'", '"'):
            quoted, quote = True, c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0 and pattern.match(text, i) and (i == 0 or not text[i - 1].isalnum()):
            return i
    return None


def _after(text: str, keyword: str) -> str | None:
    at = _top_keyword(text, keyword)
    return None if at is None else text[at + len(keyword) :]


def _between(text: str, first: str, second: str) -> str | None:
    start = _top_keyword(text, first)
    end = _top_keyword(text, second)
    if start is None or end is None or end < start:
        return None
    return text[start + len(first) : end].strip()


def _parenthesised(text: str) -> str | None:
    stripped = text.lstrip()
    if not stripped.startswith("("):
        return None
    offset = len(text) - len(stripped)
    close = _matching(text, offset)
    return None if close is None else text[offset + 1 : close]


def _strip_as(argument: str) -> str:
    return re.sub(r"\s+AS\s+[A-Za-z_][A-Za-z0-9_$]*\s*$", "", argument.strip(), flags=re.IGNORECASE)
