"""Thin wrapper over the ANTLR PL/SQL parser.

ANTLR's job is the *program*: where statements begin and end, what is declared, how
control flows. It captures each SQL fragment as text and makes no attempt to understand
it — that is SQLGlot's job, one statement at a time.

Keeping that boundary sharp matters. The moment this module starts interpreting SQL, the
two parsers begin disagreeing about the same statement and every downstream fact
inherits the ambiguity.

Two deliberate choices:

* Parse errors are *collected*, never printed and never raised. ANTLR's default listener
  writes to stderr and carries on, which would let an unparsed procedure look like a
  procedure with no lineage. Coverage has to be counted, so errors are returned as data.
* Statement text is sliced from the original input rather than reconstructed from tokens,
  so the exact source — whitespace, comments, casing — survives for evidence and for
  origin spans in the IR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from antlr4 import CommonTokenStream, InputStream, ParserRuleContext
from antlr4.error.ErrorListener import ErrorListener

try:
    from lineage.parsing.generated.PlSqlLexer import PlSqlLexer
    from lineage.parsing.generated.PlSqlParser import PlSqlParser
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise ModuleNotFoundError(
        "The ANTLR PL/SQL parser has not been generated. Run:\n"
        "    .\\.venv\\Scripts\\python.exe scripts\\generate_parser.py"
    ) from exc


@dataclass(frozen=True)
class ParseError:
    """One syntax error, with the position needed to report it against the source."""

    line: int
    column: int
    message: str

    def __str__(self) -> str:
        return f"{self.line}:{self.column} {self.message}"


@dataclass(frozen=True)
class ParsedStatement:
    """One statement, located but not interpreted.

    ``kind`` is the most specific grammar rule that matched — ``insert_statement``
    rather than the ``sql_statement`` wrapper — because that is what decides which band
    a statement belongs to and which analysis path it takes.
    """

    kind: str
    line: int
    column: int
    text: str


@dataclass(frozen=True)
class ParsedUnit:
    """One named program unit — a procedure, function, package or trigger.

    Standalone and packaged units are distinguished because the grammar does:
    ``create_procedure_body`` is a schema-level procedure, ``procedure_body`` is one
    declared inside a package. They never nest, so counting both is safe.
    """

    kind: str
    name: str
    line: int


@dataclass(frozen=True)
class Program:
    """The result of parsing one source unit."""

    source: str
    tree: Any = field(repr=False)
    errors: tuple[ParseError, ...]
    units: list[ParsedUnit]
    statements: list[ParsedStatement]

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def parsed_cleanly(self) -> bool:
        return not self.errors

    @property
    def procedure_names(self) -> list[str]:
        """Names of schema-level procedures, in source order."""
        return [unit.name for unit in self.units if unit.kind == "procedure"]

    def unit_counts(self) -> dict[str, int]:
        """How many of each unit kind this source declares."""
        counts: dict[str, int] = {}
        for unit in self.units:
            counts[unit.kind] = counts.get(unit.kind, 0) + 1
        return counts


class _CollectingErrorListener(ErrorListener):  # type: ignore[misc] # antlr4 ships no types
    """Records syntax errors instead of printing them.

    A procedure that fails to parse must be counted as unparsed, not silently treated as
    a procedure containing no lineage. That distinction is the difference between a
    declared boundary and a silent hole.
    """

    def __init__(self) -> None:
        self.errors: list[ParseError] = []

    def syntaxError(  # noqa: N802 - ANTLR interface
        self,
        recognizer: Any,
        offendingSymbol: Any,  # noqa: N803 - ANTLR interface
        line: int,
        column: int,
        msg: str,
        e: Any,
    ) -> None:
        self.errors.append(ParseError(line=line, column=column, message=msg))


def _iter_contexts(node: Any, wanted: type) -> list[Any]:
    """Depth-first collection of every context of a given type."""
    found: list[Any] = []
    if isinstance(node, wanted):
        found.append(node)
    for index in range(node.getChildCount()):
        child = node.getChild(index)
        if isinstance(child, ParserRuleContext):
            found.extend(_iter_contexts(child, wanted))
    return found


def _most_specific(ctx: ParserRuleContext) -> ParserRuleContext:
    """Descend through single-child wrapper rules to the rule that actually matched.

    ``statement -> sql_statement -> data_manipulation_language_statements ->
    insert_statement`` collapses to ``insert_statement``.
    """
    current = ctx
    while current.getChildCount() == 1:
        child = current.getChild(0)
        if not isinstance(child, ParserRuleContext):
            break
        current = child
    return current


def _rule_name(ctx: ParserRuleContext) -> str:
    return type(ctx).__name__.removesuffix("Context").lower()


def _source_slice(ctx: ParserRuleContext) -> str:
    """The original text this context spans, taken from the input stream."""
    start_token, stop_token = ctx.start, ctx.stop
    if start_token is None or stop_token is None:
        return str(ctx.getText())
    stream = start_token.getInputStream()
    if stream is None:
        return str(ctx.getText())
    return str(stream.getText(start_token.start, stop_token.stop))


def _collect_units(tree: Any) -> list[ParsedUnit]:
    """Find every named program unit in the parse tree.

    Standalone units (``create_procedure_body``) and packaged ones (``procedure_body``)
    are separate grammar rules that never nest, so both can be collected without
    double-counting.
    """
    # (context class name, unit kind, accessor for the name node)
    specs = [
        ("Create_procedure_bodyContext", "procedure", "procedure_name"),
        ("Create_function_bodyContext", "function", "function_name"),
        ("Create_packageContext", "package_spec", "package_name"),
        ("Create_package_bodyContext", "package_body", "package_name"),
        ("Create_triggerContext", "trigger", "trigger_name"),
        ("Procedure_bodyContext", "packaged_procedure", "identifier"),
        ("Function_bodyContext", "packaged_function", "identifier"),
    ]

    units: list[ParsedUnit] = []
    for context_name, kind, accessor in specs:
        context_class = getattr(PlSqlParser, context_name, None)
        if context_class is None:  # grammar changed upstream
            continue
        for ctx in _iter_contexts(tree, context_class):
            name_node = getattr(ctx, accessor, lambda: None)()
            # ANTLR returns a list when a rule allows the sub-rule more than once
            # (`identifier` also matches parameter names). The unit's own name is the
            # first occurrence.
            if isinstance(name_node, list):
                name_node = name_node[0] if name_node else None
            if name_node is None:
                continue
            units.append(ParsedUnit(kind=kind, name=str(name_node.getText()), line=ctx.start.line))

    units.sort(key=lambda unit: unit.line)
    return units


def parse_program(source: str) -> Program:
    """Parse one PL/SQL source unit into its program structure.

    Never raises on malformed input — errors come back on the ``Program`` so the caller
    can count them.
    """
    listener = _CollectingErrorListener()

    lexer = PlSqlLexer(InputStream(source))
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener)

    parser = PlSqlParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(listener)

    tree = parser.sql_script()

    units = _collect_units(tree)

    statements: list[ParsedStatement] = []
    for ctx in _iter_contexts(tree, PlSqlParser.StatementContext):
        specific = _most_specific(ctx)
        statements.append(
            ParsedStatement(
                kind=_rule_name(specific),
                line=ctx.start.line,
                column=ctx.start.column,
                text=_source_slice(ctx),
            )
        )

    return Program(
        source=source,
        tree=tree,
        errors=tuple(listener.errors),
        units=units,
        statements=statements,
    )
