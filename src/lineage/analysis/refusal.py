"""The unanalysable-construct classifier (T3.1).

The spike names this a deliverable in its own right: *"the classifier that says 'I cannot
resolve this construct' is itself a deliverable. never emit a guessed edge."*

**Why a refusal is worth more than an edge here.** An analyser that quietly produces
nothing for a `MODEL` clause and an analyser that correctly found nothing are byte for
byte the same output. Only one of them is safe to file. The refusal is what separates
them, so it is recorded as data - with a code, a unit, a line and an excerpt - rather than
left as an absence for a reader to notice.

**The taxonomy is closed on purpose.** Free-text reasons cannot be counted, cannot be
compared between runs, and drift into paraphrases of each other. One code per reason, and
a new reason means adding a member here and saying why - which is a decision, not a typo.

**Two-sided failure, and both sides get a number.** Refusing too little is a silent guess:
an edge exists that nothing corroborates. Refusing too much is a recall collapse that
*looks* like discipline - the score improves because the hard statements stopped
competing. So T3.1 measures both:

* the **cross-check** (`violations`): for every flagged statement, the edge set produced
  from it must be empty. Mechanical, because "I inspected the output and it looked right"
  is exactly how this passes while broken.
* the **false-abstention rate** (`measure.py`): a refused statement for which the ground
  truth holds edges was analysable, and the refusal cost us those edges.

**Refusing is never free.** Every classifier refusal counts one more statement seen and
one fewer analysed, so parse coverage falls by exactly the statements refused. A
classifier that abstained from everything would report 0% coverage rather than 100%
precision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from lineage.dialects import fold
from lineage.parsing.plsql import ParsedStatement, Program

__all__ = [
    "CONSTRUCTS",
    "PoisonedCte",
    "Refusal",
    "RefusalCode",
    "RefusalScope",
    "classify_program",
    "classify_statement",
    "conditional_compilation_spans",
    "cte_bodies",
    "cte_scoped_construct",
    "enclosing_unit_at",
    "last_line",
    "violations",
]

RefusalScope = Literal["statement", "cte"]
"""How much of a statement a refusal withdraws. See `Refusal.scope` (finding PG-20)."""


class RefusalCode(StrEnum):
    """Why the analyser declined. A closed list - one code per reason.

    The codes describe *what kind of knowledge is missing*, not which module gave up.
    Two constructs share a code when the same evidence would resolve both.
    """

    PARSE_FAILED = "PARSE_FAILED"
    """The text did not parse as SQL. Loud, and the least dangerous refusal there is."""

    UNSUPPORTED_CONSTRUCT = "UNSUPPORTED_CONSTRUCT"
    """It parses, and the analyser has no lineage semantics for it. `MODEL`,
    `MATCH_RECOGNIZE`, `CONNECT BY` - all real SQL, none of it implemented."""

    SHAPE_NOT_DECIDABLE = "SHAPE_NOT_DECIDABLE"
    """The statement's output columns are not fixed by the text. `PIVOT` with a subquery
    IN-list and `XMLTABLE` both decide their own column set at runtime, so any positional
    binding would be a guess dressed as an AST fact."""

    NAME_NOT_RESOLVED = "NAME_NOT_RESOLVED"
    """An object or column is absent from the dictionary. Resolvable by capturing more
    schema, which is what separates it from UNSUPPORTED_CONSTRUCT."""

    DYNAMIC_NOT_CONSTANT = "DYNAMIC_NOT_CONSTANT"
    """The executed text is not constant on every path (T3.2). Resolvable by observation
    - `V$SQL` holds what actually ran - which is why it is not SOURCE_UNAVAILABLE."""

    REMOTE_OBJECT = "REMOTE_OBJECT"
    """A referenced object lives across a database link. **This one attaches to the
    reference, not to the statement** - which the first draft of this classifier got
    wrong, and `b2_06` caught. Refusing `SELECT r.cust_id FROM remote_customer@crm_link`
    would throw away lineage that is fully provable on this side: the target columns, the
    projection and the filter are all in the local text. Only the far side is unknowable,
    so it is declared as a boundary node and counted, while the statement is analysed."""

    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    """There is no source to read: wrapped PL/SQL, an external or Java routine. No amount
    of parser work fixes this, only a different evidence type."""

    CONTEXT_DEPENDENT = "CONTEXT_DEPENDENT"
    """The same text means different things depending on state that is not in the text -
    conditional-compilation flags, the executing schema. The honest answer is per context,
    never one answer."""

    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    """A declared cap stopped the analysis. A limit that silently truncates is a hidden
    defect; declared and counted, it is a specification."""


@dataclass(frozen=True)
class Refusal:
    """A statement the analyser declined to interpret.

    A refusal is a declared boundary; a silent omission is a hole. ``unit`` and ``line``
    together address the statement, which is what makes the cross-check mechanical -
    an edge claiming that origin contradicts this refusal.
    """

    kind: str
    line: int
    reason: str
    excerpt: str
    code: RefusalCode = RefusalCode.UNSUPPORTED_CONSTRUCT
    unit: str = "<anonymous>"
    scope: RefusalScope = "statement"
    """How much of the statement this refusal withdraws (finding PG-20).

    `"statement"` is the original and still the default: nothing in the statement was
    analysed, so it costs one statement of parse coverage.

    `"cte"` says the construct was confined to one named CTE of a statement whose other
    CTEs were read normally. Such a refusal **does not** subtract from
    `statements_analysed`, because the statement WAS analysed - counting it as unanalysed
    while it contributes edges is an incoherence, not a conservatism. What it costs is
    recorded separately as `partial_statements`, so the new category is visible rather
    than folded into a number that already existed.
    """

    end_line: int | None = None
    """Last line of the refused statement.

    A refusal is recorded at the line the statement STARTS on, but a labelled edge
    carries the line its own expression sits on - `b2_06` labels its four projections at
    lines 21, 22 and 24 of a statement beginning at line 20. Addressing a refusal by its
    first line alone made the false-abstention rate read 0.0% while `b2_06` was refused
    and had seven labelled edges. The span is what makes both checks in T3.1 mean what
    they claim to mean.
    """

    @property
    def span(self) -> range:
        return range(self.line, (self.end_line or self.line) + 1)

    def covers(self, unit: str, line: int) -> bool:
        return unit == self.unit and line in self.span

    def __str__(self) -> str:
        return f"{self.unit}:{self.line} [{self.code.value}] {self.reason}"


@dataclass(frozen=True)
class Construct:
    """One named construct the analyser refuses on sight.

    `dialects` scopes a refusal to the dialects it is true for, and None means all of them.
    Most constructs here are refused because they are undecidable in principle - a MODEL
    clause computes by inter-row reference whoever wrote it - and those apply everywhere.
    Some are refused only because a particular front end cannot read them yet: `CREATE
    TRIGGER` is fully HANDLED in Oracle, through the dictionary, and unhandled in
    PostgreSQL, where the trigger's body lives in a function this project cannot parse.
    Refusing it for both would be false about Oracle and would move a signed measurement.
    """

    name: str
    pattern: re.Pattern[str]
    code: RefusalCode
    reason: str
    dialects: frozenset[str] | None = None

    def applies_to(self, dialect: str) -> bool:
        return self.dialects is None or dialect in self.dialects


def last_line(statement: ParsedStatement) -> int:
    """Where a statement ends, so a refusal addresses the whole of it and not just its head."""
    return statement.line + statement.text.count("\n")


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


# The loud-failure register from the corpus notes, made executable. Order matters only
# where two patterns can match the same text: the first match wins, so the more specific
# construct is listed first.
CONSTRUCTS: tuple[Construct, ...] = (
    # --- PostgreSQL only. Each of these was SILENT until 2026-09-23 - not counted,
    # not refused, not visible in parse coverage. Silence is the one outcome that is
    # never acceptable, in scope or out, so each became a refusal with its own reason.
    Construct(
        name="DO_BLOCK",
        pattern=_rx(r"\bDO\s*\$[A-Za-z_0-9]*\$"),
        code=RefusalCode.UNSUPPORTED_CONSTRUCT,
        reason="DO block - an anonymous PL/pgSQL body, dollar-quoted like a routine's and just as "
        "unreadable until a grammar is chosen (ADR-0002 section 5)",
        dialects=frozenset({"postgres"}),
    ),
    Construct(
        name="COPY",
        pattern=_rx(r"\bCOPY\s+[A-Za-z_0-9.$\"]+\s*(?:\([^)]*\)\s*)?(?:FROM|TO)\s"),
        code=RefusalCode.SOURCE_UNAVAILABLE,
        reason="COPY moves rows between a relation and something OUTSIDE the database - a file, a "
        "program, or the client. The relation's contents depend on data this analysis can never "
        "see, so the honest answer is to name the boundary rather than report the table as having "
        "no writer",
        dialects=frozenset({"postgres"}),
    ),
    Construct(
        name="CREATE_TRIGGER_PG",
        pattern=_rx(r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\b"),
        code=RefusalCode.UNSUPPORTED_CONSTRUCT,
        reason="CREATE TRIGGER - PostgreSQL triggers name a FUNCTION that holds the body, "
        "so the lineage "
        "is in a routine this project cannot yet parse. Oracle triggers are handled through the "
        "dictionary and are deliberately NOT refused, which is why this construct is scoped to "
        "one dialect",
        dialects=frozenset({"postgres"}),
    ),
    # TRUNCATE_PG lived here until D-62 (plan step 6): it empties a relation and moves no
    # column, so it is not refused but recorded as an `Operation` (RESETS) by
    # `analysis.effects`, with the relation and the line, from PostgreSQL's own parse.
    Construct(
        name="DOLLAR_QUOTED_ROUTINE_BODY",
        pattern=_rx(r"CREATE(?:\s+OR\s+REPLACE)?\s+(?:FUNCTION|PROCEDURE)\s[\s\S]{0,2000}?\bAS\s*\$[A-Za-z_0-9]*\$"),
        code=RefusalCode.UNSUPPORTED_CONSTRUCT,
        reason="PL/pgSQL routine body - the body after `AS $$` is a dollar-quoted string "
        "literal rather than part of the SQL parse tree, and no grammar has been chosen "
        "for it yet (ADR-0002 section 5). Refused by name so the cost is counted, never silent",
    ),
    Construct(
        name="MODEL",
        pattern=_rx(r"\bMODEL\b\s+(?:\w+\s+)*?(?:PARTITION\s+BY|DIMENSION\s+BY|MEASURES)\b"),
        code=RefusalCode.UNSUPPORTED_CONSTRUCT,
        reason="MODEL clause - spreadsheet-style cell rules compute values by inter-row "
        "reference, so a column's lineage is not a projection and cannot be read off the "
        "select list",
    ),
    Construct(
        name="MATCH_RECOGNIZE",
        pattern=_rx(r"\bMATCH_RECOGNIZE\b"),
        code=RefusalCode.UNSUPPORTED_CONSTRUCT,
        reason="MATCH_RECOGNIZE - measures are defined over pattern variables bound to "
        "row ranges, and which rows a variable covers is decided at runtime",
    ),
    Construct(
        name="PIVOT_SUBQUERY",
        pattern=_rx(r"\b(?:UN)?PIVOT\b.{0,400}?\bIN\s*\(\s*(?:SELECT\b|ANY\b)"),
        code=RefusalCode.SHAPE_NOT_DECIDABLE,
        reason="PIVOT with a subquery or ANY column list - the output columns are the "
        "DATA in the pivot column, so the statement's shape is not fixed by its text",
    ),
    # NOTE the absence of a blanket PIVOT entry, which this register carried until
    # 2026-09-09. Its own reason admitted the problem - "the literal-list form is decidable
    # in principle but is not implemented" - and stress 2 measured the cost: a static
    # `PIVOT (SUM(x) FOR c IN ('GBP' AS gbp))` and an `UNPIVOT (v FOR m IN (a, b))` were
    # both refused although their output columns are fixed by the text. That is a false
    # abstention (T3.1d), and it costs parse coverage on top of the lost edges.
    #
    # Now implemented in `band0._pivot_columns`. `PIVOT_SUBQUERY` above still catches the
    # undecidable form, where the output columns ARE the data.
    Construct(
        name="CONNECT_BY",
        pattern=_rx(r"\bCONNECT\s+BY\b|\bSTART\s+WITH\b"),
        code=RefusalCode.UNSUPPORTED_CONSTRUCT,
        reason="hierarchical query - CONNECT BY generates rows by walking a "
        "self-relationship, so a value in the result may have come from any depth and the "
        "path is not statically bounded",
    ),
    Construct(
        name="XMLTABLE",
        pattern=_rx(r"\b(?:XMLTABLE|JSON_TABLE)\s*\("),
        code=RefusalCode.SHAPE_NOT_DECIDABLE,
        reason="XMLTABLE/JSON_TABLE - columns are projected out of a document by path "
        "expression, and the document's structure is not in the dictionary",
    ),
    # Scoped to Oracle, and the scoping is the whole of finding PG-17. Conditional
    # compilation is an Oracle PL/SQL preprocessor feature; PostgreSQL has no equivalent,
    # so nothing in a PostgreSQL file can be one. Unscoped, BOTH alternatives collide with
    # ordinary PostgreSQL syntax, because `$` is a quoting character there rather than a
    # directive sigil:
    #
    #   `$$[A-Za-z_]\w*`  intends Oracle's `$$PLSQL_UNIT` inquiry directive, and also
    #                     matches the dollar-quoted string literal `$$it's a value$$`
    #   `\$IF\b`          intends the `$IF` directive, and also matches `$IF$`, which is a
    #                     legal dollar-quote TAG (`DO $IF$ ... $IF$`)
    #
    # Measured cost before the scoping, on two spellings PostgreSQL treats as identical:
    # `'manual review'` gave 3 edges at 100% coverage and `$$manual review$$` gave 0 edges,
    # 1 refusal, 0% coverage - a false abstention (T3.1d) on a completely readable INSERT,
    # and the lost edges included a filter edge with no connection to the literal.
    #
    # Narrowing the regex was the wrong repair and was considered: it would have left the
    # seam - an Oracle-only construct live against every dialect - intact, and the next
    # collision undiscovered.
    Construct(
        name="CONDITIONAL_COMPILATION",
        pattern=_rx(r"\$IF\b|\$\$[A-Za-z_]\w*"),
        code=RefusalCode.CONTEXT_DEPENDENT,
        reason="conditional compilation - which branch exists in the compiled unit depends "
        "on PLSQL_CCFLAGS at compile time, so the source alone describes several different "
        "programs",
        dialects=frozenset({"oracle"}),
    ),
    Construct(
        name="WRAPPED",
        pattern=_rx(r"\bWRAPPED\b\s*\r?\n|\bWRAPPED\b\s+a000000"),
        code=RefusalCode.SOURCE_UNAVAILABLE,
        reason="wrapped PL/SQL - the body is stored obfuscated and there is no source to "
        "read; only runtime evidence can say what it touches",
    ),
    # --- collections (stress finding S2-04) --------------------------------------------
    #
    # Added because these two were SILENT, not because they are hard. Neither band 0 nor
    # def-use claims them: `forall_statement` is not in band 0's SUPPORTED set, and the
    # collection is a memory location def-use does not model - so the statements were
    # skipped by both, produced no edges, and raised no refusal. Five labelled facts
    # vanished from `s2_bulk_limit` with no symptom anywhere in the report.
    #
    # A missing edge is survivable and a declared boundary is the product; an undeclared
    # absence is neither. Refusing is the honest floor here, not the ambition - both are
    # implementable, and the reasons say so rather than implying the language beat us.
    Construct(
        name="FETCH_BULK_COLLECT",
        pattern=_rx(r"\bFETCH\b[^;]{0,400}?\bBULK\s+COLLECT\b"),
        code=RefusalCode.UNSUPPORTED_CONSTRUCT,
        reason="FETCH ... BULK COLLECT INTO - the whole result set lands in a collection, "
        "and a collection of records is a memory location this analysis does not model; "
        "the cursor's select list is known, so this is implementable and simply is not "
        "implemented",
    ),
    Construct(
        name="FORALL",
        pattern=_rx(r"\bFORALL\s+\w+\s+IN\b"),
        code=RefusalCode.UNSUPPORTED_CONSTRUCT,
        reason="FORALL - the DML is driven by a collection subscript, so its values come "
        "from elements def-use does not track; SAVE EXCEPTIONS makes it worse, because "
        "the loop continues past failures and the absence of a row proves nothing",
    ),
)
# NOTE what is deliberately NOT here: `SELECT ... BULK COLLECT INTO`. It works today -
# stress 1 traces `stg_customer.cust_id -> v_ids` correctly - and a pattern matching BULK
# COLLECT generally would refuse a statement the analyser already gets right. Refusing too
# much looks like discipline, which is what makes it hard to notice; the FETCH form is
# named specifically for that reason.
# NOTE the absence of a DB-link entry, which the first draft had. `b2_06` refuted it:
# the local half of `INSERT INTO dim_customer ... FROM remote_customer@crm_link` is fully
# provable, and refusing the statement would have destroyed seven labelled edges to buy
# nothing. A remote reference is a boundary node, not a refused statement. Refusing too
# much looks like discipline, which is what makes it hard to notice.


def _strip_noise(text: str) -> str:
    """Blank out comments and string literals, preserving offsets and line structure.

    A `-- CONNECT BY is not used here` comment must not trip the classifier, and neither
    must a SQL fragment sitting inside a string being built for `EXECUTE IMMEDIATE` -
    that text is not this statement, and it gets classified on its own once T3.2 recovers
    it. Characters are replaced rather than removed so line numbers survive.
    """
    out = list(text)
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if char == "-" and text.startswith("--", index):
            end = text.find("\n", index)
            end = length if end == -1 else end
            for position in range(index, end):
                out[position] = " "
            index = end
        elif char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            for position in range(index, end):
                if out[position] != "\n":
                    out[position] = " "
            index = end
        elif char == "'":
            # PG-23: an `E'...'` literal also honours BACKSLASH escapes, so `E'O\'Reilly'`
            # is one string. Read as standard-conforming, `\'` closes it and every quote
            # after it in the file flips polarity - real code blanked, real literals left
            # visible. The prefix counts only as a whole token: `LIKE'...'` ends in an E
            # too. Oracle has no `E'...'` form, so this cannot change an Oracle reading.
            escaped = _is_escape_string_prefix(text, index)
            end = index + 1
            while end < length:
                if escaped and text[end] == "\\":
                    end += 2
                    continue
                if text[end] == "'":
                    if end + 1 < length and text[end + 1] == "'":
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
            for position in range(index, min(end, length)):
                if out[position] != "\n":
                    out[position] = " "
            index = end
        else:
            index += 1
    return "".join(out)


def _is_escape_string_prefix(text: str, quote: int) -> bool:
    """True when the quote at `quote` opens a PostgreSQL `E'...'` escape string."""
    if quote == 0 or text[quote - 1] not in "Ee":
        return False
    return quote == 1 or not (text[quote - 2].isalnum() or text[quote - 2] in "_$#\"")


def classify_statement(text: str, dialect: str = "oracle") -> Construct | None:
    """The first construct this text contains that the analyser cannot resolve.

    Returns the construct rather than a bare bool so the caller gets the code and the
    reason from one place - a reason written at the call site is a reason that will
    diverge from the code beside it.

    **`dialect` defaults to Oracle, and that default is a hazard worth naming.** It is what
    hid finding PG-17: `band0.analyse_source` called this without a dialect, so every
    PostgreSQL statement was classified against the Oracle register no matter what the
    config said, and scoping the `Construct` alone did not fix anything. The default is kept
    only because the Oracle call sites predate the parameter. **A new caller should pass
    `program.dialect` explicitly** - omitting it is silently wrong rather than loudly wrong,
    which is the shape this project treats as the worst kind of defect.
    """
    clean = _strip_noise(text)
    for construct in CONSTRUCTS:
        if construct.applies_to(dialect) and construct.pattern.search(clean):
            return construct
    return None


@dataclass(frozen=True)
class PoisonedCte:
    """One CTE of a statement whose construct the analyser refuses, located exactly.

    `line` and `end_line` are 1-based and relative to the START of the statement text, so
    a caller holding the statement's own line adds `statement.line - 1` to each. They
    bound the CTE's BODY - the parenthesised query - which is the span the construct
    actually sits in and therefore the span a refusal is entitled to withdraw.
    """

    name: str
    line: int
    end_line: int


_WITH_HEAD = re.compile(r"WITH\s+(?:RECURSIVE\s+)?", re.IGNORECASE)
_CTE_HEAD = re.compile(
    r"""\s*(?:"(?P<quoted>[^"]+)"|(?P<bare>[A-Za-z_]\w*))   # the CTE's name
        \s*(?:\([^()]*\)\s*)?                               # its optional column list
        AS\s+(?:NOT\s+)?(?:MATERIALIZED\s+)?\(              # PG's materialisation hints
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _matching_paren(clean: str, opening: int) -> int | None:
    """Offset of the `)` closing the `(` at `opening`, or None if it is never closed.

    `clean` must be noise-stripped, so a parenthesis inside a string literal or a comment
    cannot unbalance the count. Regex cannot do this - a CTE body contains `count(*)`,
    `coalesce(a, b)` and nested subqueries - and getting it wrong in the *silent*
    direction was the shape of two separate defects in this project's rewrite rules.
    """
    depth = 0
    for index in range(opening, len(clean)):
        if clean[index] == "(":
            depth += 1
        elif clean[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def cte_bodies(text: str) -> list[tuple[str, int, int]]:
    """Every top-level CTE in this statement, as `(name, body_start, body_end)` offsets.

    Body offsets are half-open over the text INSIDE the parentheses, so blanking
    `text[start:end]` removes the CTE's query and nothing else.

    **Text scanning rather than SQLGlot, deliberately.** This module classifies constructs
    the SQL parser may not be able to read at all - that is `classify_program`'s third
    pass, and a construct that fails to parse is *more* reason to refuse, not less. Asking
    SQLGlot to find the CTEs would make the refusal depend on the very parse whose failure
    is the thing being refused, and would put the answer in a different coordinate space
    from the line numbers every `Refusal` is addressed by.

    Returns `[]` for a statement with no `WITH` clause, which is the common case and not
    an error. Nested CTEs - a `WITH` inside a CTE body - are not returned: the outer body
    already contains them, and the poison is confined by the outermost one that holds it.
    """
    clean = _strip_noise(text)

    # The opening `WITH`, found at paren depth 0 so that a `WITH` inside a subquery does
    # not start the scan, and confirmed by the `name AS (` that must follow it - `WITH` is
    # also a modifier in `GROUP BY ... WITH ROLLUP` and `unnest(...) WITH ORDINALITY`.
    start: int | None = None
    depth = 0
    for index, char in enumerate(clean):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and (char in "Ww") and (head := _WITH_HEAD.match(clean, index)):
            if index and (clean[index - 1].isalnum() or clean[index - 1] == "_"):
                continue
            if _CTE_HEAD.match(clean, head.end()):
                start = head.end()
                break

    if start is None:
        return []

    bodies: list[tuple[str, int, int]] = []
    position = start
    while (head := _CTE_HEAD.match(clean, position)) is not None:
        opening = head.end() - 1
        closing = _matching_paren(clean, opening)
        if closing is None:
            break
        bodies.append((head.group("quoted") or head.group("bare"), opening + 1, closing))
        separator = re.compile(r"\s*,\s*").match(clean, closing + 1)
        if separator is None:
            break
        position = separator.end()

    return bodies


def cte_scoped_construct(
    text: str, dialect: str = "oracle"
) -> tuple[Construct, list[PoisonedCte]] | None:
    """The refused construct and the CTEs it is CONFINED to, or None if it is not confined.

    **Finding PG-20.** The refusal unit was the statement, and a modern analytical query is
    one statement with eighty-five CTEs in it. One `JSON_TABLE` in one CTE therefore
    discarded the other eighty-four, which are ordinary joins and aggregates the analyser
    reads perfectly well. Measured on a three-CTE reduction: four correct edges lost to
    abstain from one.

    None means "refuse the whole statement", and it is returned generously, because every
    case this cannot prove is a case where the old behaviour is still right:

    * the statement has no `WITH` clause, so there is nothing narrower to refuse;
    * the construct is `CONTEXT_DEPENDENT`, whose poison is not bounded by syntax at all -
      conditional compilation governs the statements AROUND it (see
      `conditional_compilation_spans`), so a syntactic span would be the wrong instrument;
    * no CTE body contains the construct, so it is in the main query and owns the statement;
    * **anything at all is still refused once the poisoned bodies are blanked out.** This
      is the load-bearing check and it is exact rather than inferential: the same
      classifier is re-run over the same text with the poison removed, so a second
      construct elsewhere - in the main query, or in a DIFFERENT CTE - falls back to the
      whole-statement refusal instead of being quietly analysed.

    That last clause means a statement poisoned by TWO different constructs is still
    refused whole. Narrowing it further would mean classifying every construct rather than
    the first, which is a larger change and a separate measurement; the cost of leaving it
    is one refusal that is broader than it needs to be, which is the safe direction.
    """
    construct = classify_statement(text, dialect)
    if construct is None or construct.code is RefusalCode.CONTEXT_DEPENDENT:
        return None

    bodies = cte_bodies(text)
    if not bodies:
        return None

    clean = _strip_noise(text)
    poisoned = [
        (name, start, end)
        for name, start, end in bodies
        if construct.pattern.search(clean, start, end)
    ]
    if not poisoned:
        return None

    blanked = list(clean)
    for _, start, end in poisoned:
        for position in range(start, end):
            if blanked[position] != "\n":
                blanked[position] = " "
    if classify_statement("".join(blanked), dialect) is not None:
        return None

    return construct, [
        PoisonedCte(name=name, line=_line_of(text, start), end_line=_line_of(text, end))
        for name, start, end in poisoned
    ]


def _enclosing_unit(program: Program, statement: ParsedStatement) -> str:
    candidates = [unit for unit in program.units if unit.line <= statement.line]
    return fold(candidates[-1].name, program.dialect) if candidates else "<anonymous>"


def _innermost(flagged: list[tuple[ParsedStatement, Construct]]) -> list[int]:
    """Indices to keep when several nested statements match the same construct.

    An `IF` statement's source span includes the statements inside it, so a `CONNECT BY`
    in a nested `SELECT` matches both. Flagging the outer one too would refuse a statement
    that carries genuine lineage of its own - refusing too much, which is the failure mode
    that looks like discipline. The smallest span containing the construct is the one that
    actually has it.
    """
    keep: list[int] = []
    for index, (statement, construct) in enumerate(flagged):
        nested = any(
            other is not statement
            and other_construct.name == construct.name
            and len(other.text) < len(statement.text)
            and other.text in statement.text
            for position, (other, other_construct) in enumerate(flagged)
            if position != index
        )
        if not nested:
            keep.append(index)
    return keep


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def conditional_compilation_spans(source: str, dialect: str = "oracle") -> list[tuple[int, int]]:
    """Line ranges governed by a `$IF ... $END` block. Oracle only.

    Conditional compilation is the one construct here that poisons *neighbouring*
    statements rather than itself. The `$IF` carries no lineage; the INSERTs inside its
    arms carry all of it, and which of them exists in the compiled unit is decided by
    `PLSQL_CCFLAGS` and is not in the file. Measured, not assumed: the first version
    flagged only the directive line and cheerfully emitted edges for **both** arms of
    `u1_conditional_compilation` - two contradictory answers, each stated as fact.

    **The dialect gate lives here rather than at the call sites** (finding PG-17). There are
    two callers - this module's own pass 2 and `band0.analyse_source` - and a gate written
    twice is a gate the third caller forgets. `$IF\\b` matches `$IF$`, which in PostgreSQL is
    a legal dollar-quote tag rather than a directive, so an unscoped span would refuse every
    statement inside `DO $IF$ ... $IF$` for a reason that cannot be true of PostgreSQL.
    """
    if dialect != "oracle":
        return []
    clean = _strip_noise(source)
    spans: list[tuple[int, int]] = []
    for opening in re.finditer(r"\$IF\b", clean, re.IGNORECASE):
        closing = re.search(r"\$END\b", clean[opening.end() :], re.IGNORECASE)
        end = len(clean) if closing is None else opening.end() + closing.end()
        spans.append((_line_of(clean, opening.start()), _line_of(clean, end)))
    return spans


def classify_program(program: Program) -> list[Refusal]:
    """Every statement in this source the analyser refuses on sight.

    Three passes, because a construct can hide from any one of them:

    1. **Statement level.** The normal case - the construct sits inside a statement ANTLR
       located, and the innermost such statement is the one that has it.
    2. **Conditional-compilation spans.** `$IF` poisons the statements around it rather
       than itself (see `conditional_compilation_spans`).
    3. **Source level.** A construct the grammar could not parse produces no statement at
       all, so it would be invisible to pass 1 - which is backwards, since failing to
       parse is *more* reason to refuse, not less. Wrapped PL/SQL is the case in hand:
       there is no statement, only a blob.

    Only *flagged* statements are returned. Statements this pass says nothing about are
    left to whichever analyser claims them - silence here is not an endorsement, it is an
    absence of objection, and conflating the two would make the count meaningless.
    """
    flagged: list[tuple[ParsedStatement, Construct]] = []
    for statement in program.statements:
        construct = classify_statement(statement.text, program.dialect)
        if construct is not None:
            flagged.append((statement, construct))

    refusals: list[Refusal] = []
    for index in _innermost(flagged):
        statement, construct = flagged[index]
        refusals.append(
            Refusal(
                kind=statement.kind,
                line=statement.line,
                reason=construct.reason,
                excerpt=" ".join(statement.text.split())[:80],
                code=construct.code,
                unit=_enclosing_unit(program, statement),
                end_line=last_line(statement),
            )
        )

    for first, last in conditional_compilation_spans(program.source, program.dialect):
        for statement in program.statements:
            if not first <= statement.line <= last:
                continue
            refusals.append(
                Refusal(
                    kind=statement.kind,
                    line=statement.line,
                    reason=f"inside the conditional-compilation block at line {first} - "
                    "which arm is compiled depends on PLSQL_CCFLAGS, so claiming this "
                    "statement's edges would state one of several possible programs as fact",
                    excerpt=" ".join(statement.text.split())[:80],
                    code=RefusalCode.CONTEXT_DEPENDENT,
                    unit=_enclosing_unit(program, statement),
                    end_line=last_line(statement),
                )
            )

    covered = {(refusal.line, refusal.code) for refusal in refusals}
    clean_source = _strip_noise(program.source)
    for construct in CONSTRUCTS:
        if not construct.applies_to(program.dialect):
            continue
        for match in construct.pattern.finditer(clean_source):
            line = _line_of(clean_source, match.start())
            if any(
                other.code is construct.code and other.line <= line <= other.line + 60
                for other in refusals
            ):
                continue
            if (line, construct.code) in covered:
                continue
            refusals.append(
                Refusal(
                    kind="<unparsed>",
                    line=line,
                    reason=construct.reason,
                    excerpt=" ".join(program.source.splitlines()[line - 1].split())[:80],
                    code=construct.code,
                    unit=enclosing_unit_at(program, line),
                )
            )

    return _deduplicate(refusals)


def _deduplicate(refusals: list[Refusal]) -> list[Refusal]:
    """One refusal per statement. The first reason found wins, and order is by specificity.

    A statement can trip several patterns - `XMLTABLE` also has an unresolvable column -
    and counting it twice would make the refusal total larger than the statement total,
    which is how a coverage figure becomes nonsense.
    """
    seen: dict[tuple[str, int], Refusal] = {}
    for refusal in refusals:
        seen.setdefault((refusal.unit, refusal.line), refusal)
    return sorted(seen.values(), key=lambda refusal: (refusal.line, refusal.code.value))


def enclosing_unit_at(program: Program, line: int) -> str:
    """The program unit a line falls inside. Public: procedure.py needs it too."""
    candidates = [unit for unit in program.units if unit.line <= line]
    return fold(candidates[-1].name, program.dialect) if candidates else "<anonymous>"


def violations(refusals: list[Refusal], edges: list) -> list[tuple[Refusal, list]]:  # type: ignore[type-arg]
    """Flagged statements that nonetheless produced edges. Must be empty.

    **This is the verification method, and it is the point of T3.1.** "I inspected the
    output and it looked right" is precisely how a broken refusal passes review. The check
    is mechanical: a refusal addresses a statement by (unit, line), and any edge claiming
    that origin contradicts it.

    One honest limitation, stated rather than engineered around: two statements on one
    physical line share an address, so an edge from the innocent one would read as a
    violation. Nothing in this corpus does that, and a false alarm here is the safe
    direction of the error.

    **A SECOND LIMITATION IS NOT SAFE IN THAT DIRECTION, AND IT IS WHY
    `not_cross_checkable` EXISTS** (stress finding S2-10). A trigger body is analysed from
    the DICTIONARY, wrapped in a synthetic procedure, so its edges carry lines relative to
    that wrapper - 3, 4, 7. A refusal raised over the same statement by the file pass
    carries the line in the FILE - 39. `covers()` compares two different coordinate spaces
    and can only ever answer "no", so for a trigger unit this function silently reports
    clean. That is a guard passing without looking, which is worse than a guard that fails.
    """
    found: list[tuple[Refusal, list]] = []  # type: ignore[type-arg]
    for refusal in refusals:
        clashing = [edge for edge in edges if refusal.covers(edge.origin.unit, edge.origin.line)]
        if clashing:
            found.append((refusal, clashing))
    return found


def not_cross_checkable(
    refusals: list[Refusal],
    edges: list,  # type: ignore[type-arg]
    incomparable_units: frozenset[str] | set[str],
) -> list[Refusal]:
    """Refusals whose contradiction check could not run, because the line spaces differ.

    Stress finding S2-10. `violations` above and `measure`'s `false_abstentions_recovered`
    both ask "is there an edge whose (unit, line) falls inside this refusal's span?" - and
    for a trigger unit the two sides are numbered in different spaces, so the answer is
    structurally "no" whatever the truth is.

    **This returns the refusals for which the answer is UNKNOWN rather than no.** Reporting
    that count is the whole fix: the numbers stay as they are, and they stop being read as
    evidence of something nobody checked. There is currently no such refusal in the corpus -
    the one that raised the finding, `s6`'s `INSERT ... VALUES`, was removed by D-1 - so this
    is a guard against the defect coming back rather than a repair of a live one.

    The larger fix is architectural and is recorded rather than taken: band 0 analyses trigger
    bodies out of the FILE while `triggers.py` analyses them out of the DICTIONARY, so the
    same statement is read twice in two coordinate spaces. Only the dictionary pass's edges
    are kept - band 0 contributes no trigger edges at all today - so the file pass's reading
    exists only to produce refusals and statement counts. Collapsing that is the right
    change and it moves parse coverage, which makes it a separate measurement.
    """
    units = {unit.upper() for unit in incomparable_units}
    if not units:
        return []
    return [
        refusal
        for refusal in refusals
        if refusal.unit
        and refusal.unit.upper() in units
        and any(edge.origin.unit.upper() in units for edge in edges)
    ]
