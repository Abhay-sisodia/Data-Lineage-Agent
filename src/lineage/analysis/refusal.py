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

from lineage.parsing.plsql import ParsedStatement, Program

__all__ = [
    "CONSTRUCTS",
    "Refusal",
    "RefusalCode",
    "classify_program",
    "classify_statement",
    "conditional_compilation_spans",
    "enclosing_unit_at",
    "last_line",
    "violations",
]


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
    """One named construct the analyser refuses on sight."""

    name: str
    pattern: re.Pattern[str]
    code: RefusalCode
    reason: str


def last_line(statement: ParsedStatement) -> int:
    """Where a statement ends, so a refusal addresses the whole of it and not just its head."""
    return statement.line + statement.text.count("\n")


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE | re.DOTALL)


# The loud-failure register from the corpus notes, made executable. Order matters only
# where two patterns can match the same text: the first match wins, so the more specific
# construct is listed first.
CONSTRUCTS: tuple[Construct, ...] = (
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
    Construct(
        name="CONDITIONAL_COMPILATION",
        pattern=_rx(r"\$IF\b|\$\$[A-Za-z_]\w*"),
        code=RefusalCode.CONTEXT_DEPENDENT,
        reason="conditional compilation - which branch exists in the compiled unit depends "
        "on PLSQL_CCFLAGS at compile time, so the source alone describes several different "
        "programs",
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
            end = index + 1
            while end < length:
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


def classify_statement(text: str) -> Construct | None:
    """The first construct this text contains that the analyser cannot resolve.

    Returns the construct rather than a bare bool so the caller gets the code and the
    reason from one place - a reason written at the call site is a reason that will
    diverge from the code beside it.
    """
    clean = _strip_noise(text)
    for construct in CONSTRUCTS:
        if construct.pattern.search(clean):
            return construct
    return None


def _enclosing_unit(program: Program, statement: ParsedStatement) -> str:
    candidates = [unit for unit in program.units if unit.line <= statement.line]
    return candidates[-1].name.upper() if candidates else "<anonymous>"


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


def conditional_compilation_spans(source: str) -> list[tuple[int, int]]:
    """Line ranges governed by a `$IF ... $END` block.

    Conditional compilation is the one construct here that poisons *neighbouring*
    statements rather than itself. The `$IF` carries no lineage; the INSERTs inside its
    arms carry all of it, and which of them exists in the compiled unit is decided by
    `PLSQL_CCFLAGS` and is not in the file. Measured, not assumed: the first version
    flagged only the directive line and cheerfully emitted edges for **both** arms of
    `u1_conditional_compilation` - two contradictory answers, each stated as fact.
    """
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
        construct = classify_statement(statement.text)
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

    for first, last in conditional_compilation_spans(program.source):
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
    return candidates[-1].name.upper() if candidates else "<anonymous>"


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
    """
    found: list[tuple[Refusal, list]] = []  # type: ignore[type-arg]
    for refusal in refusals:
        clashing = [edge for edge in edges if refusal.covers(edge.origin.unit, edge.origin.line)]
        if clashing:
            found.append((refusal, clashing))
    return found
