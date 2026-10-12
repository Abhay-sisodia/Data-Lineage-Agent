"""PL/pgSQL routines, DO blocks and triggers, analysed through band 0 (batch B8, D-39..D-42).

**How it works: desugar, analyse, then put the procedural facts back.**

`desugar` rewrites the script, before anything parses it. Every routine and DO block that
`parsing.plpgsql` can read is replaced by the SQL its body runs, each statement at its own
lines:

* an INSERT / UPDATE / DELETE / MERGE stays itself, with every variable and record field
  replaced by NULL. Band 0 then analyses the column-to-column lineage exactly as it does
  at top level, with the B7 catalog, D-23 paths, views and every earlier fix;
* `SELECT … INTO v`, a FETCH, an assignment that reads a table, and a FOR loop's query each
  become a PHANTOM `CREATE TEMP TABLE "__plN" AS <query>`. Band 0 traces it like any CTAS,
  and `finish` turns its columns back into the variables or record fields they really are;
* constant dynamic SQL is replaced by the text it folds to (D-42);
* a trigger function is emitted once, only when a CREATE TRIGGER binds it (D-41).

`finish` takes band 0's result and restates it in procedural terms:
* phantom columns become `variable:` nodes (Oracle's convention), and phantom filters
  target the relation read (rule 2);
* every use of a variable, a loop record or NEW/OLD in a statement becomes an edge, from
  `parsing`-free SQLGlot positions: a VALUES slot, a projection, a SET, or a predicate;
* a loop's WHERE becomes a filter on every relation its body writes (rule 4);
* bands and units are set per edge (rule 5), and phantoms are removed from the count.

`summaries` turns each scalar function's RETURN into an interprocedural summary (rule 6),
in the same shape the Oracle path feeds band 0.

**What it cannot read stays refused by name.** A routine `parsing.plpgsql` rejects is left
in the source untouched, so the refusal register refuses it exactly as before B8.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from lineage.analysis.interproc import ReturnSource, Summary, _combine
from lineage.analysis.transforms import transform_of_path
from lineage.harness.scoring import PredictedEdge
from lineage.ir.model import (
    Boundary,
    BoundaryKind,
    FilterPhase,
    Flow,
    Mechanism,
    Node,
    NodeKind,
    Origin,
    Tier,
    Transform,
)
from lineage.parsing import plpgsql as pl
from lineage.parsing.sqlscript import skip_lexeme
from lineage.resolution.dictionary import Dictionary

PHANTOM = "__pl"
DIALECT = "postgres"


# --- what desugaring records ----------------------------------------------------------------


@dataclass
class Use:
    """One variable, record field or NEW/OLD reference, and what it feeds."""

    symbol: tuple[str, str]  # ("", var) or (record, field)
    role: str  # value | filter
    target: str = ""  # value: `schema.table.column`, or `var:<name>`; filter: unused
    transform: Transform = Transform.IDENTITY
    read: tuple[str, ...] = ()  # filter in a statement that writes a variable: relations read


@dataclass
class Emitted:
    """One statement `desugar` placed in the source, and how `finish` restates it."""

    line: int
    unit: str
    band: int  # the context's band: 0, 1 in a loop, 2 dynamic or in a trigger
    kind: str  # sql | into | loop | assign | none
    loops: tuple[int, ...] = ()  # the loops it sits in, innermost last
    trigger: str = ""  # the trigger function, when in one
    targets: tuple[str, ...] = ()  # into: the variables, in projection order
    phantom: str = ""  # the phantom table's name
    record: str = ""  # loop: the record variable
    uses: list[Use] = field(default_factory=list)
    written: str = ""  # sql: the relation written, as the statement names it


@dataclass
class Desugared:
    source: str
    emitted: list[Emitted] = field(default_factory=list)
    boundaries: list[Boundary] = field(default_factory=list)
    routines_seen: int = 0
    returns: dict[str, list[tuple[str, list[tuple[tuple[str, str], Transform]]]]] = field(
        default_factory=dict
    )  # scalar function -> its RETURN expressions' symbols, per RETURN
    units: dict[str, str] = field(default_factory=dict)  # scalar function -> unit
    trigger_filters: dict[str, list[Use]] = field(default_factory=dict)
    trigger_tables: dict[str, list[str]] = field(default_factory=dict)
    unsupported: dict[int, str] = field(default_factory=dict)
    """Routine line -> why the reader gave up on it. The refusal says so, rather than the
    generic "no grammar" - the cause is what a person needs to act on."""


@dataclass
class _Function:
    """A routine as later statements see it."""

    routine: pl.Routine
    queries: list[str] = field(default_factory=list)  # RETURN QUERY texts (D-40)
    procedural_rows: bool = False  # RETURN NEXT: rows built in a loop


# --- desugaring -----------------------------------------------------------------------------


def _code_spans(source: str) -> list[tuple[int, int]]:
    """`(start, end)` of each top-level statement's code, `end` at its `;` (exclusive)."""
    spans: list[tuple[int, int]] = []
    index, begin, length = 0, None, len(source)
    while index < length:
        if source.startswith(("--", "/*"), index):
            index = skip_lexeme(source, index) or length
            continue
        skipped = skip_lexeme(source, index)
        if skipped is not None:
            if begin is None:
                begin = index
            index = skipped
            continue
        char = source[index]
        if char == ";":
            if begin is not None:
                spans.append((begin, index))
            begin = None
        elif not char.isspace() and begin is None:
            begin = index
        index += 1
    if begin is not None and source[begin:].strip():
        spans.append((begin, length))
    return spans


class _Lines:
    """The rewritten source, line by line: blanked ranges, and text placed on a line."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.lines = source.split("\n")
        self.placed: dict[int, list[str]] = {}
        self.starts = [0]
        for line in self.lines[:-1]:
            self.starts.append(self.starts[-1] + len(line) + 1)

    def line_of(self, offset: int) -> int:
        """0-based line of an offset."""
        low, high = 0, len(self.starts) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if self.starts[middle] <= offset:
                low = middle
            else:
                high = middle - 1
        return low

    def blank(self, start: int, end: int) -> None:
        first, last = self.line_of(start), self.line_of(max(start, end - 1))
        for number in range(first, last + 1):
            line = self.lines[number]
            begin = max(start - self.starts[number], 0)
            stop = min(end - self.starts[number], len(line))
            self.lines[number] = line[:begin] + " " * (stop - begin) + line[stop:]

    def put(self, offset: int, text: str) -> int:
        """Place `text` on the line of `offset`; returns that line, 1-based.

        Kept apart from the blanked original until `text()`: a later blank that runs to the
        end of this line (the next routine's leading gap) must not erase it.
        """
        number = self.line_of(offset)
        self.placed.setdefault(number, []).append(text)
        return number + 1

    def text(self) -> str:
        return "\n".join(
            " ".join([line.rstrip(), *self.placed[number]]) if number in self.placed else line
            for number, line in enumerate(self.lines)
        )


class _Desugarer:
    def __init__(self, source: str, dictionary: Dictionary) -> None:
        self.source = source
        self.dictionary = dictionary
        self.out = _Lines(source)
        self.result = Desugared(source=source)
        self.functions: dict[str, _Function] = {}
        self.phantoms = 0
        self.loops = 0
        self.bindings: dict[str, list[pl.TriggerBinding]] = {}
        self.trigger_functions: set[str] = set()

    # --- the script -------------------------------------------------------------------------

    def run(self) -> Desugared:
        spans = _code_spans(self.source)
        for start, end in spans:
            binding = pl.read_trigger(self.source[start:end])
            if binding is not None:
                self.bindings.setdefault(binding.function, []).append(binding)
        previous = 0
        for start, end in spans:
            self.statement(start, end, previous)
            previous = end + 1
        self.result.source = self.out.text()
        return self.result

    def statement(self, start: int, end: int, previous: int = 0) -> None:
        text = self.source[start:end]
        if pl.read_trigger(text) is not None:
            if self._trigger_known(text):
                self.out.blank(start, end + 1)
                self.result.routines_seen += 1
            return
        drop = re.match(
            r"(?i)^\s*DROP\s+(FUNCTION|PROCEDURE)\s+(?:IF\s+EXISTS\s+)?([\w.\"]+)", text
        )
        if drop:
            self.functions.pop(pl.fold_name(drop.group(2)), None)
            self.out.blank(start, end + 1)
            return
        try:
            routine = pl.read_routine(self.source, start, end)
        except pl.UnsupportedError as error:
            self._gave_up(start, error)
            return  # left as written: refused by name, as before B8
        if routine is not None:
            if routine.language != "plpgsql":
                return
            self.routine(routine, start, end, previous)
            return
        rewritten = self._inline_reads(text, start)
        if rewritten is not None:
            self.out.blank(start, end + 1)  # its `;` too: the rewrite carries its own
            self.out.put(start, rewritten)

    def _gave_up(self, start: int, error: Exception) -> None:
        """Record why the routine starting at `start` stays refused, by its CREATE line."""
        found = re.compile(r"(?i)\bCREATE\b|\bDO\b").search(self.source, start)
        at = found.start() if found else start
        self.result.unsupported[self.source.count("\n", 0, at) + 1] = str(error)

    def _trigger_known(self, text: str) -> bool:
        binding = pl.read_trigger(text)
        return binding is not None and binding.function in self.trigger_functions

    # --- one routine ------------------------------------------------------------------------

    def routine(self, routine: pl.Routine, start: int, end: int, previous: int = 0) -> None:
        try:
            block = pl.read_body(self.source, routine.body_start, routine.body_end)
        except pl.UnsupportedError as error:
            self._gave_up(start, error)
            return
        unit = routine.name
        bindings = self.bindings.get(routine.name, []) if routine.returns == "trigger" else []
        saved = (len(self.result.emitted), len(self.result.boundaries), self.phantoms)
        backup = (list(self.out.lines), {k: list(v) for k, v in self.out.placed.items()})
        context = _Context(
            unit=unit,
            variables=set(routine.params) | set(routine.out_columns),
            band=2 if routine.returns == "trigger" else 0,
            trigger=unit if routine.returns == "trigger" else "",
        )
        if bindings:
            context.records["new"] = []
            context.records["old"] = []
        # The comments above the routine go too: band 0 numbers a statement from the first
        # content after the previous `;`, and the routine's first body statement must be
        # numbered from its own line.
        self.out.blank(previous, end + 1)
        function = _Function(routine=routine)
        try:
            if routine.returns == "trigger" and not bindings:
                pass  # D-41: never bound, so NEW has no table and nothing is emitted
            else:
                if routine.search_path:
                    self.out.put(start, f"SET search_path TO {routine.search_path};")
                self.block(block, context, function)
                if routine.search_path:
                    self.out.put(end, "RESET search_path;")
        except pl.UnsupportedError as error:
            self._gave_up(start, error)
            # Undo this routine: it stays refused by name, never half-read.
            del self.result.emitted[saved[0] :]
            del self.result.boundaries[saved[1] :]
            self.phantoms = saved[2]
            self.out.lines, self.out.placed = backup
            return
        if bindings:
            self.result.trigger_tables[unit] = [b.table for b in bindings]
            for binding in bindings:
                if binding.when:
                    uses = self._uses(f"SELECT 1 WHERE {binding.when}", context, "filter_only")
                    self.result.trigger_filters.setdefault(unit, []).extend(
                        _bind_trigger(uses, binding.table)
                    )
        self.result.routines_seen += 1
        if routine.returns == "trigger":
            self.trigger_functions.add(routine.name)
        elif routine.kind == "function":
            self.functions[routine.name] = function
            self.result.units[routine.name] = unit

    def block(self, block: pl.Block, context: _Context, function: _Function) -> None:
        for declaration in block.declarations:
            name = declaration.name.lower()
            context.variables.add(name)
            if declaration.cursor_query:
                context.cursors[name] = declaration.cursor_query
            constant = _literal(declaration.default)
            if constant is not None:
                context.constants[name] = constant
        self.nodes(block.body, context, function)
        for handler in block.handlers:
            if re.search(r"(?i)\bothers\b", handler.names) and all(
                isinstance(node, pl.Stmt) and node.text.strip().lower() == "null"
                for node in handler.body
            ):
                self.result.boundaries.append(
                    Boundary(
                        kind=BoundaryKind.SUPPRESSED_ERROR,
                        subject=f"{context.unit}:{self._line(handler.start)}",
                        detail=f"{context.unit}: WHEN OTHERS THEN NULL swallows every failure "
                        "above it, so the absence of a write proves nothing",
                        unit=context.unit,
                        line=self._line(handler.start),
                    )
                )
            self.nodes(handler.body, context, function)

    def nodes(self, nodes: list[pl.Node], context: _Context, function: _Function) -> None:
        for node in nodes:
            if isinstance(node, pl.Block):
                self.block(node, context, function)
            elif isinstance(node, pl.If):
                for arm in node.arms:
                    self.nodes(arm, context, function)
            elif isinstance(node, pl.Loop):
                self.loop(node, context, function)
            else:
                self.simple(node, context, function)

    def loop(self, loop: pl.Loop, context: _Context, function: _Function) -> None:
        self.loops += 1
        number = self.loops
        inner = context.inside_loop(number)
        if loop.kind in ("query", "cursor"):
            query = loop.query if loop.kind == "query" else context.cursors.get(loop.cursor.lower())
            if query is None:
                raise pl.UnsupportedError(f"FOR over an unknown cursor {loop.cursor}")
            record = loop.variable.lower()
            phantom = self._phantom(
                query, loop.query_start or loop.start, inner, "loop", (), names=True, record=record
            )
            inner.records[record] = [phantom.phantom]
            inner.loop_phantoms[number] = phantom.phantom
        else:
            inner.variables.add(loop.variable.lower())
            context.constants.pop(loop.variable.lower(), None)
        self.nodes(loop.body, inner, function)

    def simple(self, stmt: pl.Stmt, context: _Context, function: _Function) -> None:
        kind = stmt.kind
        if kind == "noop":
            return
        if kind == "sql":
            self._sql(stmt.text, stmt.start, context)
        elif kind == "select_into":
            self._select_into(stmt.text, stmt.start, context)
        elif kind == "assign":
            self._assign(stmt, context)
        elif kind == "diagnostics":
            for target in stmt.targets:
                context.constants.pop(target.lower(), None)
        elif kind == "fetch":
            query = context.cursors.get(stmt.cursor.lower())
            if query is None:
                raise pl.UnsupportedError(f"FETCH from an unknown cursor {stmt.cursor}")
            targets = tuple(t.lower() for t in stmt.targets)
            self._phantom(query, stmt.start, context, "into", targets)
        elif kind == "execute":
            self._execute(stmt, context)
        elif kind == "return_query":
            function.queries.append(stmt.expression)
        elif kind == "return_next":
            function.procedural_rows = True
        elif kind == "return" and stmt.expression and function.routine.returns == "scalar":
            symbols = self._expression_symbols(stmt.expression, context)
            self.result.returns.setdefault(context.unit, []).append((stmt.expression, symbols))

    # --- statement forms --------------------------------------------------------------------

    def _sql(self, text: str, start: int, context: _Context, written_hint: str = "") -> None:
        parsed = _parse(text)
        if parsed is None:
            raise pl.UnsupportedError("an embedded statement SQLGlot cannot parse")
        self._inline_tree(parsed, start)
        uses = self._statement_uses(parsed, context)
        _null_symbols(parsed, context)
        line = self.out.put(start, parsed.sql(dialect=DIALECT) + ";")
        self.result.emitted.append(
            Emitted(
                line=line,
                unit=context.unit,
                band=context.band,
                kind="sql",
                loops=context.loop_stack,
                trigger=context.trigger,
                uses=uses,
                written=_written(parsed),
            )
        )

    def _select_into(self, text: str, start: int, context: _Context) -> None:
        parsed = _parse(text)
        if not isinstance(parsed, exp.Select) or parsed.args.get("into") is None:
            raise pl.UnsupportedError("SELECT INTO that SQLGlot reads otherwise")
        into = parsed.args["into"]
        names = [into.this, *(into.expressions or [])] if into.this else list(into.expressions)
        targets = tuple(_name_of(n) for n in names)
        parsed.set("into", None)
        self._phantom(parsed.sql(dialect=DIALECT), start, context, "into", targets)

    def _assign(self, stmt: pl.Stmt, context: _Context) -> None:
        target = stmt.targets[0].lower()
        if "." in target:
            raise pl.UnsupportedError("assignment to a record field")
        expression = stmt.expression
        query = _parse(f"SELECT {expression}")
        if query is None:
            raise pl.UnsupportedError("an assignment SQLGlot cannot parse")
        constant = _literal(expression)
        if constant is not None:
            context.constants[target] = constant
        else:
            context.constants.pop(target, None)
        reads_tables = (
            any(not _is_symbol(column, context) for column in query.find_all(exp.Column))
            or query.find(exp.Subquery) is not None
        )
        if reads_tables:
            self._phantom(f"SELECT {expression}", stmt.start, context, "into", (target,))
            return
        uses = self._uses(f"SELECT {expression}", context, "into", targets=(target,))
        self.result.emitted.append(
            Emitted(
                line=self._line(stmt.start),
                unit=context.unit,
                band=context.band,
                kind="assign",
                loops=context.loop_stack,
                trigger=context.trigger,
                uses=uses,
            )
        )

    def _execute(self, stmt: pl.Stmt, context: _Context) -> None:
        folded = _fold(stmt.expression, context.constants)
        line = self._line(stmt.start)
        if folded is None:
            self.result.boundaries.append(
                Boundary(
                    kind=BoundaryKind.DYNAMIC_SQL,
                    subject=f"EXECUTE:{line}",
                    detail=f"{context.unit}: line {line} executes text that does not fold "
                    "to a constant, so its lineage is not decidable (D-42)",
                    unit=context.unit,
                    line=line,
                )
            )
            return
        # USING values are external values (D-42): `$n` reads no column.
        folded = re.sub(r"\$\d+", "NULL", folded)
        dynamic = context.dynamic()
        if stmt.targets:
            targets = tuple(t.lower() for t in stmt.targets)
            self._phantom(folded, stmt.start, dynamic, "into", targets)
        else:
            self._sql(folded, stmt.start, dynamic)

    def _phantom(
        self,
        query: str,
        start: int,
        context: _Context,
        kind: str,
        targets: tuple[str, ...],
        names: bool = False,
        record: str = "",
    ) -> Emitted:
        parsed = _parse(query)
        if not isinstance(parsed, exp.Query):
            raise pl.UnsupportedError("a query SQLGlot cannot parse")
        self._inline_tree(parsed, start)
        selects = parsed.selects
        if kind == "into" and any(
            isinstance(s, exp.Star) or isinstance(s.this, exp.Star) for s in selects
        ):
            raise pl.UnsupportedError("SELECT * INTO a record")
        if kind == "into" and len(selects) != len(targets):
            raise pl.UnsupportedError("INTO targets that do not match the select list")
        uses = self._statement_uses(
            parsed, context, into=targets if kind == "into" else (), record_fields=record
        )
        _null_symbols(parsed, context)
        if not names:
            _alias_columns(parsed)
        self.phantoms += 1
        phantom = f"{PHANTOM}{self.phantoms}"
        line = self.out.put(start, f"CREATE TEMP TABLE {phantom} AS {parsed.sql(dialect=DIALECT)};")
        emitted = Emitted(
            line=line,
            unit=context.unit,
            band=context.band,
            kind=kind,
            loops=context.loop_stack,
            trigger=context.trigger,
            targets=targets,
            phantom=phantom,
            record=record,
            uses=uses,
        )
        self.result.emitted.append(emitted)
        return emitted

    # --- RETURN QUERY functions read in FROM (D-40) -----------------------------------------

    def _inline_reads(self, text: str, start: int) -> str | None:
        if not any(name.split(".")[-1] in text.lower() for name in self.functions):
            return None
        parsed = _parse(text)
        if parsed is None:
            return None
        if not self._inline_tree(parsed, start):
            return None
        return parsed.sql(dialect=DIALECT) + ";"

    def _inline_tree(self, tree: Any, start: int) -> bool:
        changed = False
        for table in list(tree.find_all(exp.Table)):
            call = table.this
            if not isinstance(call, exp.Anonymous):
                continue
            name = pl.fold_name(f"{table.db}.{call.name}" if table.db else call.name)
            function = self.functions.get(name)
            if function is None:
                continue
            if function.procedural_rows or not function.queries:
                line = self._line(start)
                self.result.boundaries.append(
                    Boundary(
                        kind=BoundaryKind.SOURCE_UNAVAILABLE,
                        subject=name,
                        detail=f"{name}: its rows are built procedurally (RETURN NEXT), so "
                        "what it returns cannot be traced to a query (D-40)",
                        unit="<script>",
                        line=line,
                    )
                )
                continue
            arguments = list(call.expressions)
            parts = []
            for text in function.queries:
                query = _parse(text)
                if not isinstance(query, exp.Query):
                    break
                for column in list(query.find_all(exp.Column)):
                    if column.table:
                        continue
                    position = (
                        function.routine.params.index(column.name.lower())
                        if column.name.lower() in function.routine.params
                        else -1
                    )
                    if 0 <= position < len(arguments):
                        column.replace(arguments[position].copy())
                parts.append(query)
            else:
                union = parts[0]
                for part in parts[1:]:
                    union = exp.union(union, part, distinct=False)
                alias = table.args.get("alias")
                alias_name = alias.this if alias is not None else exp.to_identifier(call.name)
                columns = (
                    list(alias.columns)
                    if alias is not None and alias.columns
                    else [exp.to_identifier(c) for c in function.routine.out_columns]
                )
                table.replace(
                    exp.Subquery(this=union, alias=exp.TableAlias(this=alias_name, columns=columns))
                )
                changed = True
        return changed

    # --- where symbols are used -------------------------------------------------------------

    def _uses(
        self, text: str, context: _Context, mode: str, targets: tuple[str, ...] = ()
    ) -> list[Use]:
        parsed = _parse(text)
        if parsed is None:
            raise pl.UnsupportedError("an expression SQLGlot cannot parse")
        if mode == "filter_only":
            return [
                Use(symbol=_symbol(c), role="filter")
                for c in parsed.find_all(exp.Column)
                if _is_symbol(c, context)
            ]
        return self._statement_uses(parsed, context, into=targets)

    def _statement_uses(
        self, parsed: Any, context: _Context, into: tuple[str, ...] = (), record_fields: str = ""
    ) -> list[Use]:
        uses: list[Use] = []
        slots = _value_slots(parsed, into, record_fields, self.dictionary)
        for column in list(parsed.find_all(exp.Column)):
            if not _is_symbol(column, context) or _inside_routine_call(column, self.functions):
                continue
            slot = next(((root, target) for root, target in slots if _within(column, root)), None)
            if slot is not None:
                root, target = slot
                uses.append(
                    Use(
                        symbol=_symbol(column),
                        role="value",
                        target=target,
                        transform=transform_of_path(root, column),
                    )
                )
            elif _in_predicate(column):
                read = _relations_beside(column, parsed) if (into or record_fields) else ()
                uses.append(Use(symbol=_symbol(column), role="filter", read=read))
            else:
                raise pl.UnsupportedError("a variable in a position this reader does not trace")
        return uses

    def _expression_symbols(
        self, text: str, context: _Context
    ) -> list[tuple[tuple[str, str], Transform]]:
        parsed = _parse(f"SELECT {text}")
        if parsed is None:
            raise pl.UnsupportedError("a RETURN expression SQLGlot cannot parse")
        root = parsed.selects[0]
        found = []
        for column in root.find_all(exp.Column):
            if _is_symbol(column, context):
                found.append((_symbol(column), transform_of_path(root, column)))
            else:
                raise pl.UnsupportedError("a RETURN that reads a table directly")
        return found

    def _line(self, offset: int) -> int:
        return self.out.line_of(offset) + 1


@dataclass
class _Context:
    unit: str
    variables: set[str]
    band: int
    trigger: str = ""
    records: dict[str, list[str]] = field(default_factory=dict)  # record -> phantom(s)
    cursors: dict[str, str] = field(default_factory=dict)
    constants: dict[str, str] = field(default_factory=dict)
    loop_stack: tuple[int, ...] = ()
    loop_phantoms: dict[int, str] = field(default_factory=dict)

    def inside_loop(self, number: int) -> _Context:
        return _Context(
            unit=self.unit,
            variables=self.variables,
            band=max(self.band, 1),
            trigger=self.trigger,
            records=dict(self.records),
            cursors=self.cursors,
            constants=self.constants,
            loop_stack=(*self.loop_stack, number),
            loop_phantoms=self.loop_phantoms,
        )

    def dynamic(self) -> _Context:
        return _Context(
            unit=self.unit,
            variables=self.variables,
            band=2,
            trigger=self.trigger,
            records=self.records,
            cursors=self.cursors,
            constants=self.constants,
            loop_stack=self.loop_stack,
            loop_phantoms=self.loop_phantoms,
        )


# --- SQLGlot helpers --------------------------------------------------------------------------


def _parse(text: str) -> Any:
    try:
        return sqlglot.parse_one(text, read=DIALECT)
    except Exception:
        return None


def _name_of(node: Any) -> str:
    return (node.name if hasattr(node, "name") else str(node)).lower()


def _is_symbol(column: exp.Column, context: _Context) -> bool:
    table = column.table.lower() if column.table else ""
    if table:
        return table in context.records and not column.args.get("db")
    return column.name.lower() in context.variables


def _symbol(column: exp.Column) -> tuple[str, str]:
    return (
        (column.table.lower(), column.name.lower()) if column.table else ("", column.name.lower())
    )


def _null_symbols(tree: Any, context: _Context) -> None:
    for column in list(tree.find_all(exp.Column)):
        if _is_symbol(column, context):
            column.replace(exp.Null())


def _alias_columns(query: Any) -> None:
    for select in [query] if isinstance(query, exp.Select) else list(query.find_all(exp.Select)):
        if select.parent_select is not None:
            continue
        select.set(
            "expressions",
            [
                exp.alias_(e.this if isinstance(e, exp.Alias) else e, f"__c{i}")
                for i, e in enumerate(select.expressions)
            ],
        )


def _within(node: Any, root: Any) -> bool:
    current = node
    while current is not None:
        if current is root:
            return True
        current = current.parent
    return False


def _in_predicate(node: Any) -> bool:
    current = node.parent
    while current is not None:
        if isinstance(current, (exp.Where, exp.Having, exp.Join, exp.Qualify)):
            return True
        current = current.parent
    return False


def _inside_routine_call(node: Any, functions: dict[str, _Function]) -> bool:
    current = node.parent
    while current is not None:
        if isinstance(current, exp.Anonymous) and any(
            name.split(".")[-1] == current.name.lower() for name in functions
        ):
            return True
        current = current.parent
    return False


def _qualified(table: exp.Table) -> str:
    parts = [p for p in (table.catalog, table.db, table.name) if p]
    return ".".join(p.lower() for p in parts)


def _written(parsed: Any) -> str:
    target = (
        parsed.this if isinstance(parsed, (exp.Insert, exp.Update, exp.Delete, exp.Merge)) else None
    )
    if isinstance(target, exp.Schema):
        target = target.this
    return _qualified(target) if isinstance(target, exp.Table) else ""


def _value_slots(
    parsed: Any, into: tuple[str, ...], record: str, dictionary: Dictionary
) -> list[tuple[Any, str]]:
    """`(expression, target)` for every position whose value lands in a named target."""
    slots: list[tuple[Any, str]] = []
    if into:
        for index, select in enumerate(parsed.selects):
            if index < len(into):
                slots.append((select, f"var:{into[index]}"))
        return slots
    if record:
        for select in parsed.selects:
            slots.append((select, f"field:{record}.{select.alias_or_name.lower()}"))
        return slots
    if isinstance(parsed, exp.Insert):
        target = parsed.this
        columns = (
            [c.name.lower() for c in target.expressions] if isinstance(target, exp.Schema) else []
        )
        table = _qualified(target.this if isinstance(target, exp.Schema) else target)
        if not columns:
            try:
                columns = list(dictionary.columns_of(table))
            except Exception:
                columns = []
        source = parsed.expression
        rows: list[list[Any]] = []
        if isinstance(source, exp.Values):
            rows = [list(row.expressions) for row in source.expressions]
        elif isinstance(source, exp.Query):
            selects = (
                [source]
                if isinstance(source, exp.Select)
                else [s for s in source.find_all(exp.Select) if s.parent_select is None]
            )
            rows = [list(s.expressions) for s in selects]
        for row in rows:
            for index, value in enumerate(row):
                if index < len(columns):
                    slots.append((value, f"{table}.{columns[index]}"))
    elif isinstance(parsed, exp.Update):
        table = _qualified(parsed.this)
        for setter in parsed.expressions:
            if isinstance(setter, exp.EQ):
                slots.append((setter.expression, f"{table}.{setter.this.name.lower()}"))
    return slots


def _relations_beside(column: exp.Column, parsed: Any) -> tuple[str, ...]:
    """The relations a filter on a variable-writing statement acts on (rule 2)."""
    aliases = {}
    for table in parsed.find_all(exp.Table):
        aliases[(table.alias_or_name or table.name).lower()] = _qualified(table)
    comparison = column.parent
    while comparison is not None and not isinstance(comparison, exp.Predicate):
        comparison = comparison.parent
    near = [
        c
        for c in (comparison.find_all(exp.Column) if comparison is not None else [])
        if c is not column and c.table and c.table.lower() in aliases
    ]
    if near:
        return tuple(dict.fromkeys(aliases[c.table.lower()] for c in near))
    return tuple(dict.fromkeys(aliases.values()))


def _literal(text: str) -> str | None:
    parsed = _parse(f"SELECT {text}") if text else None
    if parsed is None or not parsed.selects:
        return None
    value = parsed.selects[0]
    return value.this if isinstance(value, exp.Literal) and value.is_string else None


def _fold(text: str, constants: dict[str, str]) -> str | None:
    """The text an EXECUTE runs, if it is a constant (D-42); otherwise None."""
    parsed = _parse(f"SELECT {text}")
    if parsed is None or not parsed.selects:
        return None

    def value(node: Any) -> str | None:
        if isinstance(node, exp.Literal) and node.is_string:
            return str(node.this)
        if isinstance(node, exp.Paren):
            return value(node.this)
        if isinstance(node, exp.Column) and not node.table:
            return constants.get(node.name.lower())
        if isinstance(node, exp.DPipe):
            left, right = value(node.this), value(node.expression)
            return None if left is None or right is None else left + right
        if isinstance(node, exp.Format) or (
            isinstance(node, exp.Anonymous) and node.name.lower() == "format"
        ):
            pieces = (
                [node.this, *node.expressions]
                if isinstance(node, exp.Format)
                else list(node.expressions)
            )
            values = [value(piece) for piece in pieces]
            if any(v is None for v in values):
                return None
            return _format(values[0], values[1:])
        return None

    return value(parsed.selects[0])


def _format(template: str, arguments: list[str | None]) -> str | None:
    out, index, position = [], 0, 0
    while position < len(template):
        char = template[position]
        if char == "%" and position + 1 < len(template):
            spec = template[position + 1]
            position += 2
            if spec == "%":
                out.append("%")
                continue
            if index >= len(arguments) or arguments[index] is None:
                return None
            argument = str(arguments[index])
            index += 1
            if spec == "I":
                plain = re.fullmatch(r"[a-z_][a-z0-9_$]*", argument)
                out.append(argument if plain else '"' + argument.replace('"', '""') + '"')
            elif spec == "L":
                out.append("'" + argument.replace("'", "''") + "'")
            elif spec == "s":
                out.append(argument)
            else:
                return None
            continue
        out.append(char)
        position += 1
    return "".join(out)


def _bind_trigger(uses: list[Use], table: str) -> list[Use]:
    """WHEN (NEW.x ...) filters, with NEW / OLD bound to the trigger's table."""
    return [Use(symbol=(f"@{table}", use.symbol[1]), role="filter") for use in uses]


# --- the entry points --------------------------------------------------------------------------


def desugar(source: str, dictionary: Dictionary) -> Desugared:
    return _Desugarer(source, dictionary).run()


def _edge(
    source: Node, target: Node, flow: Flow, transform: Transform, band: int, origin: Origin
) -> PredictedEdge:
    return PredictedEdge(
        source=source,
        target=target,
        flow=flow,
        transform=transform,
        phase=FilterPhase.PRE_AGGREGATION,
        band=band,
        mechanism=Mechanism.DEF_USE,
        tier=Tier.A,
        origin=origin,
    )


def _column(name: str) -> Node:
    return Node(kind=NodeKind.COLUMN, name=name)


def _variable(name: str) -> Node:
    return Node(kind=NodeKind.VARIABLE, name=name)


def _relation_of(column: str) -> str:
    return column.rpartition(".")[0]


def finish(result: Any, desugared: Desugared, dictionary: Dictionary) -> Any:
    """Restate band 0's result in procedural terms (see the module docstring)."""
    by_line: dict[int, list[Emitted]] = {}
    for emitted in desugared.emitted:
        by_line.setdefault(emitted.line, []).append(emitted)
    phantoms = {e.phantom: e for e in desugared.emitted if e.phantom}

    kept: list[PredictedEdge] = []
    statement_edges: dict[int, list[PredictedEdge]] = {}
    fields: dict[str, list[tuple[Node, Transform, Flow]]] = {}  # "phantom.field" -> sources
    loop_filters: dict[str, list[Node]] = {}
    into_edges: list[tuple[Emitted, PredictedEdge]] = []

    for edge in result.edges:
        target_name = edge.target.name
        owner = target_name.split(".")[1] if target_name.startswith("pg_temp.") else ""
        if owner in phantoms:
            emitted = phantoms[owner]
            if emitted.kind == "loop":
                if edge.flow is Flow.FILTER:
                    loop_filters.setdefault(owner, []).append(edge.source)
                else:
                    key = f"{owner}.{target_name.split('.')[-1]}"
                    fields.setdefault(key, []).append((edge.source, edge.transform, edge.flow))
            else:
                into_edges.append((emitted, edge))
            continue
        here = by_line.get(edge.origin.line)
        if here:
            emitted = here[0]
            edge = edge.model_copy(
                update={
                    "band": max(edge.band, emitted.band),
                    "origin": Origin(unit=emitted.unit, line=edge.origin.line),
                }
            )
            statement_edges.setdefault(id(emitted), []).append(edge)
        kept.append(edge)

    # Phantom INTO columns are variables; their filters act on the relation read (rule 2).
    provenance: dict[tuple[str, str], list[tuple[str, Transform]]] = {}
    for emitted, edge in into_edges:
        origin = Origin(unit=emitted.unit, line=emitted.line)
        band = max(1, emitted.band)
        if edge.flow is Flow.FILTER:
            kept.append(
                _edge(
                    edge.source,
                    Node(kind=NodeKind.RELATION, name=_relation_of(edge.source.name)),
                    Flow.FILTER,
                    Transform.IDENTITY,
                    band,
                    origin,
                )
            )
            continue
        index = int(edge.target.name.rsplit("__c", 1)[-1]) if "__c" in edge.target.name else -1
        if not 0 <= index < len(emitted.targets):
            continue
        variable = emitted.targets[index]
        kept.append(
            _edge(edge.source, _variable(variable), edge.flow, edge.transform, band, origin)
        )
        if edge.flow is Flow.VALUE and edge.source.kind is NodeKind.COLUMN:
            provenance.setdefault((emitted.unit, variable), []).append(
                (edge.source.name, edge.transform)
            )

    def written_of(emitted: Emitted) -> list[str]:
        names = []
        for edge in statement_edges.get(id(emitted), []):
            name = edge.target.name if edge.flow is Flow.FILTER else _relation_of(edge.target.name)
            if edge.target.kind is not NodeKind.VARIABLE and name not in names:
                names.append(name)
        if not names and emitted.written:
            names.append(_resolve(emitted.written, dictionary))
        return names

    # Symbol uses: variables, loop records, NEW / OLD.
    def sources(emitted: Emitted, symbol: tuple[str, str]) -> list[tuple[Node, Transform, Flow]]:
        record, name = symbol
        if not record:
            return [(_variable(name), Transform.IDENTITY, Flow.VALUE)]
        if record.startswith("@"):
            return [(_column(f"{record[1:]}.{name}"), Transform.IDENTITY, Flow.VALUE)]
        if record in ("new", "old") and emitted.trigger:
            return [
                (_column(f"{table}.{name}"), Transform.IDENTITY, Flow.VALUE)
                for table in desugared.trigger_tables.get(emitted.trigger, [])
                if name in _columns(dictionary, table)
            ]
        found = []
        for phantom in _record_phantoms(desugared, emitted, record):
            found.extend(fields.get(f"{phantom}.{name}", []))
        return found

    for emitted in desugared.emitted:
        origin = Origin(unit=emitted.unit, line=emitted.line)
        written = written_of(emitted) if emitted.kind == "sql" else []
        for use in emitted.uses:
            for source, transform, flow in sources(emitted, use.symbol):
                band = max(1, emitted.band)
                if use.role == "value":
                    if use.target.startswith("var:"):
                        target = _variable(use.target[4:])
                    elif use.target.startswith("field:"):
                        _, _, name = use.target[6:].partition(".")
                        key = f"{emitted.phantom}.{name}"
                        fields.setdefault(key, []).append(
                            (source, _combine(transform, use.transform), flow)
                        )
                        continue
                    else:
                        target = _column(_resolve_column(use.target, dictionary))
                    kept.append(
                        _edge(
                            source,
                            target,
                            flow,
                            _combine(transform, use.transform)
                            if flow is Flow.VALUE
                            else Transform.IDENTITY,
                            band,
                            origin,
                        )
                    )
                    if target.kind is NodeKind.VARIABLE and flow is Flow.VALUE:
                        provenance_key = (emitted.unit, target.name)
                        if source.kind is NodeKind.COLUMN:
                            provenance.setdefault(provenance_key, []).append(
                                (source.name, _combine(transform, use.transform))
                            )
                        else:
                            for column, inner in provenance.get((emitted.unit, source.name), []):
                                provenance.setdefault(provenance_key, []).append(
                                    (column, _combine(inner, use.transform))
                                )
                    continue
                if flow is not Flow.VALUE:
                    continue
                if emitted.kind == "loop":
                    loop_filters.setdefault(emitted.phantom, []).append(source)
                    continue
                relations = (
                    written
                    if emitted.kind == "sql"
                    else [_resolve(r, dictionary) for r in use.read]
                )
                for relation in relations:
                    kept.append(
                        _edge(
                            source,
                            Node(kind=NodeKind.RELATION, name=relation),
                            Flow.FILTER,
                            Transform.IDENTITY,
                            band,
                            origin,
                        )
                    )

    # Rule 4: a loop's WHERE filters every relation its body writes; D-41: so does WHEN.
    phantom_of_loop: dict[int, str] = {}
    for emitted in desugared.emitted:
        if emitted.kind == "loop" and emitted.loops:
            phantom_of_loop[emitted.loops[-1]] = emitted.phantom
    for emitted in desugared.emitted:
        if emitted.kind != "sql":
            continue
        origin = Origin(unit=emitted.unit, line=emitted.line)
        band = max(1, emitted.band)
        governing: list[Node] = []
        for loop in emitted.loops:
            phantom = phantom_of_loop.get(loop)
            if phantom:
                governing.extend(loop_filters.get(phantom, []))
        if emitted.trigger:
            for use in desugared.trigger_filters.get(emitted.trigger, []):
                governing.append(_column(f"{use.symbol[0][1:]}.{use.symbol[1]}"))
        for relation in written_of(emitted):
            for source in dict.fromkeys(governing):
                kept.append(
                    _edge(
                        source,
                        Node(kind=NodeKind.RELATION, name=relation),
                        Flow.FILTER,
                        Transform.IDENTITY,
                        band,
                        origin,
                    )
                )

    result.edges = _deduplicate(kept)
    phantom_count = sum(1 for e in desugared.emitted if e.phantom)
    result.statements_seen = (
        max(0, result.statements_seen - phantom_count) + desugared.routines_seen
    )
    for boundary in desugared.boundaries:
        if boundary not in result.boundaries:
            result.boundaries.append(boundary)
    result.provenance = provenance  # type: ignore[attr-defined]
    return result


def _record_phantoms(desugared: Desugared, emitted: Emitted, record: str) -> list[str]:
    found = []
    for candidate in desugared.emitted:
        if (
            candidate.kind == "loop"
            and candidate.record == record
            and candidate.unit == emitted.unit
            and candidate.loops
            and candidate.loops[-1] in emitted.loops
        ):
            found.append(candidate.phantom)
    return found


def _columns(dictionary: Dictionary, table: str) -> list[str]:
    try:
        return list(dictionary.columns_of(table))
    except Exception:
        return []


def _resolve(relation: str, dictionary: Dictionary) -> str:
    if "." in relation:
        return relation
    owners = [name for name in dictionary.objects if name.split(".")[-1] == relation]
    return owners[0] if len(owners) == 1 else relation


def _resolve_column(column: str, dictionary: Dictionary) -> str:
    relation, _, name = column.rpartition(".")
    return f"{_resolve(relation, dictionary)}.{name}"


def _deduplicate(edges: list[PredictedEdge]) -> list[PredictedEdge]:
    seen: set[tuple[Any, ...]] = set()
    out = []
    for edge in edges:
        key = (
            edge.source,
            edge.target,
            edge.flow,
            edge.transform,
            edge.phase,
            edge.origin.line,
            edge.band,
        )
        if key not in seen:
            seen.add(key)
            out.append(edge)
    return out


def summaries(result: Any, desugared: Desugared) -> dict[str, Summary]:
    """Rule 6: each scalar function's RETURN, as the columns its value depends on."""
    provenance = getattr(result, "provenance", {})
    out: dict[str, Summary] = {}
    for unit in desugared.units:
        summary = Summary(unit=unit.split(".")[-1].upper())
        for _, symbols in desugared.returns.get(unit, []):
            for (record, name), transform in symbols:
                if record:
                    continue
                for column, inner in provenance.get((unit, name), []):
                    summary.returns_from.append(ReturnSource(column, _combine(inner, transform)))
        out[summary.unit] = summary
    return out


def explain_refusals(result: Any, desugared: Desugared) -> Any:
    """Name what stopped the reader on each routine it gave up on.

    A routine the reader could not follow stays refused by name; the generic "no grammar"
    reason hid the actual cause (`DML ... RETURNING ... INTO`, an assignment to a record
    field), which is what a person needs to act on. Run once the refusals are final.
    """
    if not desugared.unsupported:
        return result
    import dataclasses

    result.refusals = [
        dataclasses.replace(
            refusal,
            reason=(
                "PL/pgSQL routine the reader could not follow - it stopped at: "
                f"{desugared.unsupported[refusal.line]}. Refused whole, so nothing half-read "
                "is reported"
            ),
        )
        if refusal.line in desugared.unsupported
        and refusal.reason.startswith("PL/pgSQL routine body")
        else refusal
        for refusal in result.refusals
    ]
    return result
