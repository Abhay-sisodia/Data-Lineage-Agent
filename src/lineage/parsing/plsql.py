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

from lineage.parsing.frontend import Assignment, CursorDecl, Declared, Fetch, LoopParam, UnitRef

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
    # The front end that produced `tree`, and therefore the only thing entitled to read
    # it. The analysis asks structural questions here rather than of a grammar class, so
    # a second dialect's program answers them with its own parser (A3). Optional and
    # last for backward compatibility with every existing constructor call.
    frontend: Any = field(default=None, repr=False)

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


def iter_contexts(node: Any, wanted: type) -> list[Any]:
    """Public: every context of a given type, depth first."""
    return _iter_contexts(node, wanted)


def most_specific(ctx: Any) -> Any:
    """Public: descend single-child wrapper rules to the rule that actually matched."""
    return _most_specific(ctx)


def rule_name(ctx: Any) -> str:
    """Public: the grammar rule name for a context, e.g. ``insert_statement``."""
    return _rule_name(ctx)


def source_slice(ctx: Any) -> str:
    """Public: the original source text a context spans.

    Taken from the input stream rather than reconstructed from tokens, so whitespace,
    casing and comments survive - which matters for evidence and for guard text.
    """
    return _source_slice(ctx)


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
        frontend=ORACLE_FRONTEND,
    )


# --- the Oracle front end ---------------------------------------------------------------


_UNIT_SPECS: tuple[tuple[str, str, str], ...] = (
    # (context class, unit kind, accessor for the name node) - THE list that was copied
    # into four modules before A3. It is here once now, and `units()` is the only reader.
    ("Create_procedure_bodyContext", "procedure", "procedure_name"),
    ("Create_function_bodyContext", "function", "function_name"),
    ("Procedure_bodyContext", "packaged_procedure", "identifier"),
    ("Function_bodyContext", "packaged_function", "identifier"),
)


def _name_of(ctx: Any, accessor: str) -> str | None:
    """The name node's text, or None.

    ANTLR returns a list when a rule allows the sub-rule more than once (`identifier` also
    matches parameter names); the unit's own name is the first occurrence.
    """
    node = getattr(ctx, accessor, lambda: None)()
    if isinstance(node, list):
        node = node[0] if node else None
    return None if node is None else str(node.getText())


def _nested_unit_classes() -> tuple[type, ...]:
    kinds = ("Procedure_bodyContext", "Function_bodyContext")
    return tuple(c for c in (getattr(PlSqlParser, k, None) for k in kinds) if c is not None)


def _inside_nested_unit(ctx: Any, root: Any) -> bool:
    """True if ctx sits inside a procedure/function declared within root."""
    nested = _nested_unit_classes()
    parent = ctx.parentCtx
    while parent is not None and parent is not root:
        if isinstance(parent, nested):
            return True
        parent = parent.parentCtx
    return False


class OracleFrontend:
    """`lineage.parsing.frontend.Frontend` for Oracle PL/SQL, over the vendored ANTLR grammar.

    Every method is the code that used to live at its call site, moved behind a name. The
    signed measurement must not move by an edge when this lands, and that is its only test.
    Contexts pass through untouched; names come back as written and the caller folds.
    """

    def parse(self, source: str) -> Program:
        return parse_program(source)

    # ---- units --------------------------------------------------------------------

    def units(self, tree: Any) -> list[UnitRef]:
        found: list[UnitRef] = []
        for context_name, kind, accessor in _UNIT_SPECS:
            context_class = getattr(PlSqlParser, context_name, None)
            if context_class is None:
                continue
            for ctx in _iter_contexts(tree, context_class):
                name = _name_of(ctx, accessor)
                if name is not None:
                    found.append(UnitRef(kind=kind, name=name, line=ctx.start.line, ctx=ctx))
        return found

    def package_bodies(self, tree: Any) -> list[tuple[str, Any]]:
        package_ctx = getattr(PlSqlParser, "Create_package_bodyContext", None)
        if package_ctx is None:
            return []
        found: list[tuple[str, Any]] = []
        for body in _iter_contexts(tree, package_ctx):
            name = _name_of(body, "package_name")
            if name is not None:
                found.append((name, body))
        return found

    def enclosing_package(self, ctx: Any) -> str | None:
        package_ctx = getattr(PlSqlParser, "Create_package_bodyContext", None)
        if package_ctx is None:
            return None
        parent = ctx.parentCtx
        while parent is not None:
            if isinstance(parent, package_ctx):
                return _name_of(parent, "package_name")
            parent = parent.parentCtx
        return None

    # ---- declarations -------------------------------------------------------------

    def parameters(self, unit_ctx: Any) -> list[Declared]:
        parameter_ctx = getattr(PlSqlParser, "Parameter_nameContext", None)
        if parameter_ctx is None:
            return []
        return [
            Declared(name=str(node.getText()), line=node.start.line)
            for node in _iter_contexts(unit_ctx, parameter_ctx)
            if not _inside_nested_unit(node, unit_ctx)
        ]

    def variables(self, unit_ctx: Any) -> list[Declared]:
        declaration_ctx = getattr(PlSqlParser, "Variable_declarationContext", None)
        if declaration_ctx is None:
            return []
        found: list[Declared] = []
        for node in _iter_contexts(unit_ctx, declaration_ctx):
            if _inside_nested_unit(node, unit_ctx):
                continue
            identifier = node.identifier()
            if identifier is None:
                continue
            default = getattr(node, "default_value_part", lambda: None)()
            found.append(
                Declared(
                    name=str(identifier.getText()),
                    line=node.start.line,
                    has_default=default is not None,
                )
            )
        return found

    def cursors(self, unit_ctx: Any) -> list[CursorDecl]:
        declaration = getattr(PlSqlParser, "Cursor_declarationContext", None)
        if declaration is None:
            return []
        found: list[CursorDecl] = []
        for node in _iter_contexts(unit_ctx, declaration):
            identifier = node.identifier()
            select = node.select_statement()
            if identifier is None or select is None:
                continue
            found.append(
                CursorDecl(
                    name=str(identifier.getText()),
                    query=_source_slice(select),
                    line=node.start.line,
                )
            )
        return found

    def loop_params(self, unit_ctx: Any) -> list[LoopParam]:
        loop_param = getattr(PlSqlParser, "Cursor_loop_paramContext", None)
        if loop_param is None:
            return []
        found: list[LoopParam] = []
        for param in _iter_contexts(unit_ctx, loop_param):
            record = param.record_name() if hasattr(param, "record_name") else None
            select = param.select_statement() if hasattr(param, "select_statement") else None
            cursor = param.cursor_name() if hasattr(param, "cursor_name") else None
            index = param.index_name() if hasattr(param, "index_name") else None
            line = param.start.line
            if record is not None and select is not None:
                found.append(
                    LoopParam(line=line, record=str(record.getText()), query=_source_slice(select))
                )
            elif record is not None and cursor is not None:
                found.append(
                    LoopParam(line=line, record=str(record.getText()), cursor=str(cursor.getText()))
                )
            elif index is not None:
                found.append(LoopParam(line=line, index=str(index.getText())))
        return found

    # ---- statements ---------------------------------------------------------------

    def assignment(self, statement_ctx: Any) -> Assignment | None:
        inner = next(
            iter(_iter_contexts(statement_ctx, PlSqlParser.Assignment_statementContext)), None
        )
        if inner is None:
            return None
        target = inner.general_element()
        if target is None:
            return None
        expression = inner.expression()
        return Assignment(
            target=str(target.getText()),
            expression=_source_slice(expression) if expression is not None else "",
        )

    def fetch(self, statement_ctx: Any) -> Fetch | None:
        fetch_ctx = getattr(PlSqlParser, "Fetch_statementContext", None)
        if fetch_ctx is None:
            return None
        inner = next(iter(_iter_contexts(statement_ctx, fetch_ctx)), None)
        if inner is None:
            return None
        cursor = inner.cursor_name()
        if cursor is None:
            return None
        return Fetch(
            cursor=str(cursor.getText()),
            targets=tuple(str(v.getText()) for v in inner.variable_or_collection()),
        )

    def returns(self, unit_ctx: Any) -> list[str]:
        return_ctx = getattr(PlSqlParser, "Return_statementContext", None)
        if return_ctx is None:
            return []
        texts: list[str] = []
        for node in _iter_contexts(unit_ctx, return_ctx):
            expression = node.expression() if hasattr(node, "expression") else None
            if expression is not None:
                texts.append(_source_slice(expression))
        return texts

    def exception_handlers(self, tree: Any) -> list[Any]:
        handler_ctx = getattr(PlSqlParser, "Exception_handlerContext", None)
        if handler_ctx is None:  # pragma: no cover - grammar always has it
            return []
        return _iter_contexts(tree, handler_ctx)


ORACLE_FRONTEND = OracleFrontend()
