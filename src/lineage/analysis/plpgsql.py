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

**By meaning, not by form (semantic-ir, docs/semantic-ir-plan.md).** Every form the reader
names is lowered to a primitive (`routine_ir.LOWERING`), and each primitive has ONE
mechanism here: EMIT - rows go into the routine's output relation, or a RETURN QUERY routine
is inlined where it is called; DYN - `routine_ir.dynamic_text` in every position; ASSIGN /
BIND - to variables, record fields and NEW fields (an UPDATE of the trigger's table); CALL -
`_bind_call` binds a call's arguments to the callee's parameters. A variable is a graph node
of its routine (`<routine>.<name>`), never shared.

**What it cannot read stays refused by name.** A routine `parsing.plpgsql` rejects is left
in the source untouched, so the refusal register refuses it exactly as before B8.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from lineage.analysis import routine_ir
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

BIND_USING = False
"""D-42 holds: a dynamic statement's USING values are external, `$n` reads no column. True
would bind `$n` to the USING expressions (a variable or record field passed there is then
traced). A lineage-contract decision pending with semantic-ir T0 - flip it with the keys."""

_NAME = r"[A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?"
_INTO_TARGETS = re.compile(
    rf"(?is)\bINTO\s+(?:STRICT\s+)?({_NAME}(?:\s*,\s*{_NAME})*)"
    r"(?=\s+(?:FROM|WHERE|GROUP|HAVING|ORDER|LIMIT|OFFSET|FOR|WINDOW|UNION)\b|\s*;?\s*$)"
)
"""The target list of `SELECT ... INTO [STRICT] a, b ...` - several targets, which SQLGlot
cannot parse (holdout3 R4)."""


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
    binding: bool = False  # a caller's arguments bound to a routine's parameters (step 8)
    scope: str = ""  # whose variables its symbols are: the routine (T2b); "" = `unit`
    targets_scope: str = ""  # whose variables its INTO targets are, when not `scope` (a call)


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
    params: dict[str, list[str]] = field(default_factory=dict)  # function -> its IN params
    trigger_filters: dict[str, list[Use]] = field(default_factory=dict)
    trigger_tables: dict[str, list[str]] = field(default_factory=dict)
    unsupported: dict[int, str] = field(default_factory=dict)
    """Routine line -> why the reader gave up on it. The refusal says so, rather than the
    generic "no grammar" - the cause is what a person needs to act on."""
    opaque: list[tuple[int, int, str, str]] = field(default_factory=list)
    """Plan step 5: (line, end line, unit, why) of each statement INSIDE a routine the reader
    could not follow. The statement is refused alone; the routine around it is read."""
    functions: dict[str, Any] = field(default_factory=dict)
    """Every routine read so far, this script's and those handed in (D-65): a folder run
    gives them to the next file, so a call there reads the routine an earlier file defined."""
    synthetic: int = 0
    """Statements placed that the script does not hold (T3: a routine's output relation and
    its emits) - band 0 counts them, so `finish` takes them back out."""


@dataclass
class _Function:
    """A routine as later statements see it."""

    routine: pl.Routine
    queries: list[str] = field(default_factory=list)  # RETURN QUERY texts (D-40)
    procedural_rows: bool = False  # RETURN NEXT: rows built in a loop
    from_arguments: set[int] = field(default_factory=set)
    """A scalar function's argument positions its return value is computed from (D-66): a
    variable or record field passed there is a value of the call's result."""
    output: str = ""
    """semantic-ir T3: the relation its rows are emitted into (its own name), when they are
    built by RETURN NEXT or OUT parameters; a call reads that relation."""
    columns: list[str] = field(default_factory=list)  # the output relation's columns


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
    def __init__(
        self, source: str, dictionary: Dictionary, functions: dict[str, _Function] | None = None
    ) -> None:
        self.source = source
        self.dictionary = dictionary
        self.out = _Lines(source)
        self.result = Desugared(source=source)
        self.functions: dict[str, _Function] = dict(functions or {})
        self.phantoms = 0
        self.loops = 0
        self.bindings: dict[str, list[pl.TriggerBinding]] = {}
        self.trigger_functions: set[str] = set()
        self.transitions: dict[str, str] = {}  # in a trigger body: transition table -> table

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
        self.result.functions = dict(self.functions)
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
            if routine.language not in ("plpgsql", "sql"):
                return
            self.routine(routine, start, end, previous)
            return
        self._bind_arguments(text, start)
        rewritten = self._inline_reads(text, start)
        if rewritten is not None:
            self.out.blank(start, end + 1)  # its `;` too: the rewrite carries its own
            self.out.put(start, rewritten)

    def _bind_arguments(self, text: str, start: int) -> None:
        """A call to a routine read earlier binds the caller's arguments to its parameters.

        Plan step 8: `SELECT tgt.load_one(a.v) FROM src.a AS a` with `load_one(p_v)` writing
        `p_v` into a table - the value written is `a.v`. Each argument is read as the value
        of its parameter (`variable:p_v`), from the caller's own FROM, so a path runs from
        the caller's column through the parameter to what the routine writes. Only a routine
        called for what it DOES (void): a scalar function's value already flows through its
        summary (`Summary.from_arguments`).
        """
        if not self.functions or not any(
            name.split(".")[-1] in text.lower() for name in self.functions
        ):
            return
        parsed = _parse(text)
        if parsed is None:
            return
        for call in list(parsed.find_all(exp.Anonymous)):
            qualified = call.parent if isinstance(call.parent, exp.Dot) else None
            name = pl.fold_name(
                f"{qualified.this.name}.{call.name}" if qualified is not None else call.name
            )
            function = self.functions.get(name)
            if function is None or function.routine.returns not in ("void", ""):
                continue
            self._bind_call(call, function, call.find_ancestor(exp.Select), start, None)

    def _bind_call(
        self, call: Any, function: _Function, select: Any, start: int, context: _Context | None
    ) -> None:
        """CALL (semantic-ir): each argument is the value of the callee's parameter, read
        from the caller's own FROM - so a path runs from the caller's column through the
        parameter to what the routine writes or filters. One binding for every call site:
        a void routine (plan step 8) and a routine read in FROM / LATERAL (T3)."""
        params = function.routine.params
        bound = _call_arguments(call, params)
        names = [param for param in params if param in bound]
        if not names or select is None:
            return
        query = select.copy()
        query.set(
            "expressions",
            [exp.alias_(bound[param].copy(), f"__c{i}") for i, param in enumerate(names)],
        )
        for clause in ("order", "limit", "offset", "group", "having"):
            query.set(clause, None)
        caller = context or _Context(unit=function.routine.name, variables=set(params), band=1)
        try:
            emitted = self._phantom(query.sql(dialect=DIALECT), start, caller, "into", tuple(names))
        except pl.UnsupportedError:
            return
        emitted.binding = True
        emitted.targets_scope = function.routine.name  # the callee's parameters (T2b)

    def _sql_body(self, routine: pl.Routine) -> pl.Block:
        """A LANGUAGE sql routine's body, as the block a PL/pgSQL one would be (step 8).

        Each statement is read as SQL, `$1`-style references as the parameters they name.
        The LAST statement, when it is a query, is what a function returns: its value for a
        scalar function, its rows for a SETOF / TABLE one (as RETURN QUERY, D-40); a void
        function or a procedure returns nothing, so a final query there is read for nothing.
        """
        body: list[pl.Node] = []
        for first, last in pl._chunks(self.source, routine.body_start, routine.body_end):
            text = self.source[first:last]
            if not text.strip():
                continue
            text = re.sub(
                r"\$(\d+)",
                lambda m: (
                    routine.params[int(m.group(1)) - 1]
                    if int(m.group(1)) <= len(routine.params)
                    else m.group(0)
                ),
                text,
            )
            lead = len(text) - len(text.lstrip())
            body.append(
                pl.classify(text, first + lead) if text.strip() else pl.Stmt("noop", "", first)
            )
        last_query = next(
            (
                index
                for index in range(len(body) - 1, -1, -1)
                if getattr(body[index], "kind", "noop") != "noop"
            ),
            None,
        )
        if last_query is not None:
            stmt = body[last_query]
            assert isinstance(stmt, pl.Stmt)
            is_query = stmt.kind == "sql" and re.match(
                r"(?is)\s*(SELECT|WITH|VALUES|TABLE)\b", stmt.text
            )
            if is_query and routine.kind == "function":
                if routine.returns in ("table", "setof"):
                    body[last_query] = pl.Stmt(
                        "return_query", stmt.text, stmt.start, expression=stmt.text
                    )
                elif routine.returns == "scalar" and pl.find_word(stmt.text, "from") < 0:
                    # `SELECT $1 * 2`: an expression of the parameters - its RETURN.
                    expression = re.sub(r"(?is)^\s*SELECT\s+", "", stmt.text).strip()
                    body[last_query] = pl.Stmt(
                        "return", stmt.text, stmt.start, expression=expression
                    )
                elif routine.returns == "scalar":
                    body[last_query] = pl.Stmt(
                        "select_into",
                        re.sub(
                            r"(?is)^(\s*SELECT\b.*?)(\s+FROM\b|$)",
                            r"\1 INTO __result\2",
                            stmt.text,
                            count=1,
                        ),
                        stmt.start,
                    )
                    body.append(
                        pl.Stmt("return", "RETURN __result", stmt.start, expression="__result")
                    )
                else:
                    body[last_query] = pl.Stmt("noop", stmt.text, stmt.start)
            elif is_query:
                body[last_query] = pl.Stmt("noop", stmt.text, stmt.start)
        block = pl.Block(start=routine.body_start, body=body)
        block.declarations.append(pl.Declaration(name="__result", start=routine.body_start))
        return block

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
            if routine.language == "sql":
                block = self._sql_body(routine)
            else:
                block = pl.read_body(self.source, routine.body_start, routine.body_end)
        except pl.UnsupportedError as error:
            self._gave_up(start, error)
            return
        unit = routine.name
        bindings = self.bindings.get(routine.name, []) if routine.returns == "trigger" else []
        saved = (
            len(self.result.emitted), len(self.result.boundaries), self.phantoms,
            self.result.synthetic,
        )  # fmt: skip
        backup = (list(self.out.lines), {k: list(v) for k, v in self.out.placed.items()})
        context = _Context(
            unit=unit,
            variables=set(routine.params) | set(routine.out_columns) | set(routine.out_params),
            band=2 if routine.returns == "trigger" else 0,
            trigger=unit if routine.returns == "trigger" else "",
            scope=_scope_of(routine, self.source, start),
        )
        if bindings:
            context.records["new"] = []
            context.records["old"] = []
            context.tables = tuple(dict.fromkeys(binding.table for binding in bindings))
        # Plan step 8: a transition table (`REFERENCING NEW TABLE AS new_rows`) holds rows of
        # the table the trigger is on - read from that table.
        self.transitions = {
            name: binding.table for binding in bindings for name in binding.transitions
        }
        # The comments above the routine go too: band 0 numbers a statement from the first
        # content after the previous `;`, and the routine's first body statement must be
        # numbered from its own line.
        self.out.blank(previous, end + 1)
        function = _Function(routine=routine)
        columns = routine_ir.output_columns(routine, self.dictionary)
        if (
            columns
            and routine.language == "plpgsql"
            and routine_ir.yields_rows(routine)
            and (routine.returns == "record" or "return_next" in _forms(block.body))
        ):
            # semantic-ir T3, EMIT: rows built by RETURN NEXT or OUT parameters go into the
            # routine's OUTPUT, a relation named after it; every emit is an INSERT into it,
            # and a call reads it (holdout4 H5, H6). A routine of RETURN QUERY alone is still
            # inlined where it is called (D-40).
            function.output, function.columns = routine.name, columns
            self._synthetic(
                start, f"CREATE TABLE {routine.name} ({', '.join(f'{c} text' for c in columns)});"
            )
        try:
            if routine.returns == "trigger" and not bindings:
                pass  # D-41: never bound, so NEW has no table and nothing is emitted
            else:
                if routine.search_path:
                    self.out.put(start, f"SET search_path TO {routine.search_path};")
                self.block(block, context, function)
                if function.output and routine.returns == "record":
                    # Falling off the end returns the OUT parameters as they stand.
                    self._emit_out_params(routine.body_end, context, function)
                if routine.search_path:
                    self.out.put(end, "RESET search_path;")
        except pl.UnsupportedError as error:
            self._gave_up(start, error)
            # Undo this routine: it stays refused by name, never half-read.
            del self.result.emitted[saved[0] :]
            del self.result.boundaries[saved[1] :]
            self.phantoms = saved[2]
            self.result.synthetic = saved[3]
            self.out.lines, self.out.placed = backup
            return
        finally:
            self.transitions = {}  # a transition table is named only inside its trigger
        if routine.returns == "scalar" and len(routine.out_params) == 1:
            # One OUT parameter: its final value is what the function returns.
            returned = routine.out_params[0]
            self.result.returns.setdefault(unit, []).append(
                (returned, [(("", returned), Transform.IDENTITY)])
            )
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
            self.result.params[routine.name] = list(routine.params)
            if routine.returns == "scalar":
                assigned = _assignments(self.result.emitted)
                function.from_arguments = {
                    routine.params.index(name)
                    for _, symbols in self.result.returns.get(unit, [])
                    for (record, returned), outer in symbols
                    if not record
                    for name, _ in _reaching(unit, returned, outer, assigned)
                    if name in routine.params
                }

    def block(self, block: pl.Block, context: _Context, function: _Function) -> None:
        for declaration in block.declarations:
            name = declaration.name.lower()
            context.variables.add(name)
            if declaration.cursor_query:
                context.cursors[name] = declaration.cursor_query
            constant = routine_ir.fold(declaration.default, context.constants)
            if constant is not None:
                context.constants[name] = constant
            elif declaration.default and not declaration.cursor_query:
                # D-66: `v date := p_date` is an assignment like any other; it was dropped,
                # so whatever `v` carried from it was lost.
                assignment = pl.Stmt(
                    kind="assign",
                    text=f"{name} := {declaration.default}",
                    start=declaration.start,
                    targets=(name,),
                    expression=declaration.default,
                )
                # An initial value this reader cannot follow stays unread, as before.
                with contextlib.suppress(pl.UnsupportedError):
                    self._assign(assignment, context)
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
                self._isolated(node, context, function)

    def _isolated(self, stmt: pl.Stmt, context: _Context, function: _Function) -> None:
        """One statement, refused ALONE if it cannot be followed (plan step 5).

        Whatever it had placed is taken back, its INTO targets become plain variables - so a
        later use is traced from a variable with no named source rather than misread - and it
        is recorded at its own lines for `procedure` to refuse by name.
        """
        if stmt.kind == "opaque":
            self._opaque(stmt.start, stmt.text, stmt.expression, context)
            return
        saved = (len(self.result.emitted), len(self.result.boundaries), self.phantoms)
        backup = (list(self.out.lines), {k: list(v) for k, v in self.out.placed.items()})
        try:
            self.simple(stmt, context, function)
        except pl.UnsupportedError as error:
            del self.result.emitted[saved[0] :]
            del self.result.boundaries[saved[1] :]
            self.phantoms = saved[2]
            self.out.lines, self.out.placed = backup
            self._opaque(stmt.start, stmt.text, str(error), context)

    def _opaque(self, start: int, text: str, reason: str, context: _Context) -> None:
        line = self._line(start)
        self.result.opaque.append((line, self._line(start + len(text)), context.unit, reason))
        into = re.search(r"(?is)\bINTO\s+(?:STRICT\s+)?([\w$.]+(?:\s*,\s*[\w$.]+)*)", text)
        for target in into.group(1).split(",") if into else ():
            name = target.strip().lower()
            if name and "." not in name:
                context.variables.add(name)
                context.constants.pop(name, None)

    def loop(self, loop: pl.Loop, context: _Context, function: _Function) -> None:
        self.loops += 1
        number = self.loops
        inner = context.inside_loop(number)
        if loop.kind == "execute":
            folded = self._dynamic(loop.query, loop.using, loop.start, context, "loops over")
            if folded is None:
                inner.variables.add(loop.variable.lower())
            else:
                record = loop.variable.lower()
                phantom = self._phantom(
                    folded,
                    loop.query_start or loop.start,
                    inner.dynamic(),
                    "loop",
                    (),
                    names=True,
                    record=record,
                )
                inner.records[record] = [phantom.phantom]
                inner.loop_phantoms[number] = phantom.phantom
            self.nodes(loop.body, inner, function)
            return
        if loop.kind == "opaque":
            # A header the reader cannot follow: refused at its own line; the body is read.
            self._opaque(loop.start, "FOR", loop.reason, context)
            if loop.variable:
                inner.variables.add(loop.variable.lower())
            self.nodes(loop.body, inner, function)
            return
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
            if loop.kind == "foreach" and loop.query:
                # `FOREACH x IN ARRAY ks`: each x is an element of ks (plan step 5).
                variable = loop.variable.lower()
                uses = self._uses(f"SELECT {loop.query}", context, "into", targets=(variable,))
                self.result.emitted.append(
                    Emitted(
                        line=self._line(loop.query_start or loop.start),
                        unit=context.unit,
                        scope=context.scope_name,
                        band=inner.band,
                        kind="assign",
                        loops=inner.loop_stack,
                        trigger=context.trigger,
                        uses=uses,
                    )
                )
        self.nodes(loop.body, inner, function)

    def simple(self, stmt: pl.Stmt, context: _Context, function: _Function) -> None:
        kind = stmt.kind
        if kind == "noop":
            return
        if routine_ir.Prim.OPAQUE in routine_ir.lower(stmt):
            # A form the lowering table does not map: refused alone (plan step 5), by name.
            raise pl.UnsupportedError(f"a PL/pgSQL form no primitive covers yet ({kind})")
        if kind == "sql":
            self._sql(stmt.text, stmt.start, context)
        elif kind == "select_into":
            self._select_into(stmt.text, stmt.start, context)
        elif kind == "dml_returning_into":
            self._dml_returning_into(stmt, context)
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
            self._into(query, stmt.start, context, targets)
        elif kind == "execute":
            self._execute(stmt, context)
        elif kind == "open":
            # `OPEN c FOR <query>`: from here on, FETCH c reads that query.
            context.cursors[stmt.cursor.lower()] = stmt.expression
        elif kind == "open_execute":
            folded = self._dynamic(stmt.expression, stmt.using, stmt.start, context, "opens")
            if folded is not None:
                context.cursors[stmt.cursor.lower()] = folded
        elif kind == "return_query":
            self._emit_query(stmt.expression, stmt.start, context, function)
        elif kind == "return_query_execute":
            # holdout4 H7: the rows of constant dynamic text are the rows of that query.
            folded = self._dynamic(stmt.expression, stmt.using, stmt.start, context, "returns")
            if folded is not None:
                self._emit_query(folded, stmt.start, context, function)
        elif kind == "return_next":
            if function.output:
                self._emit_next(stmt, context, function)
            else:
                function.procedural_rows = True
        elif kind == "return" and function.output and function.routine.returns == "record":
            self._emit_out_params(stmt.start, context, function)
        elif kind == "return" and stmt.expression and function.routine.returns == "scalar":
            symbols = self._expression_symbols(stmt.expression, context)
            self.result.returns.setdefault(context.unit, []).append((stmt.expression, symbols))

    # --- statement forms --------------------------------------------------------------------

    def _sql(self, text: str, start: int, context: _Context, written_hint: str = "") -> None:
        parsed = _parse(text)
        if parsed is None:
            raise pl.UnsupportedError("an embedded statement SQLGlot cannot parse")
        self._inline_tree(parsed, start, context)
        uses = self._statement_uses(parsed, context)
        _null_symbols(parsed, context)
        line = self.out.put(start, parsed.sql(dialect=DIALECT) + ";")
        self.result.emitted.append(
            Emitted(
                line=line,
                unit=context.unit,
                scope=context.scope_name,
                band=context.band,
                kind="sql",
                loops=context.loop_stack,
                trigger=context.trigger,
                uses=uses,
                written=_written(parsed),
            )
        )

    def _dml_returning_into(self, stmt: pl.Stmt, context: _Context) -> None:
        """`INSERT ... RETURNING id INTO v_id` (production pilot: the load-log pattern).

        Read as the DML itself, then `SELECT <returning> INTO <targets> FROM <written>`:
        the variables hold values of the row just written, so their source is that
        relation's columns. The SELECT goes on the RETURNING keyword's line - its own
        statement - so it is never mistaken for the DML when edges are matched by line.
        """
        parsed = _parse(stmt.text)
        written = _written(parsed) if parsed is not None else ""
        if not written:
            raise pl.UnsupportedError("DML ... RETURNING ... INTO on an unknown relation")
        self._sql(stmt.text, stmt.start, context)
        targets = tuple(t.lower() for t in stmt.targets)
        self._phantom(f"SELECT {stmt.expression} FROM {written}", stmt.at, context, "into", targets)

    def _set_new(self, fields: dict[str, str], start: int, context: _Context) -> None:
        """NEW fields set in a BEFORE trigger: `UPDATE <trigger table> SET f = <value>` - an
        ordinary EFFECT, so the value's columns and the variables it filters on are traced."""
        sets = ", ".join(f"{name} = {value}" for name, value in fields.items())
        for table in context.tables:
            self.result.synthetic += 1
            self._sql(f"UPDATE {table} SET {sets}", start, context)

    def _bind_fields(self, query: Any, targets: list[str], start: int, context: _Context) -> None:
        """`SELECT a, b INTO NEW.x, v ...`: each NEW field takes its column of the query (a
        scalar subquery of it), each plain target is a BIND as before (semantic-ir T4)."""
        selects = query.selects
        if len(selects) != len(targets):
            raise pl.UnsupportedError("INTO targets that do not match the select list")
        fields: dict[str, str] = {}
        plain: list[tuple[Any, str]] = []
        for select, target in zip(selects, targets, strict=True):
            record, _, name = target.partition(".")
            if name and record == "new":
                single = query.copy()
                single.set(
                    "expressions",
                    [(select.this if isinstance(select, exp.Alias) else select).copy()],
                )
                fields[name] = f"({single.sql(dialect=DIALECT)})"
            elif name and record != "old":
                raise pl.UnsupportedError("INTO a field of a record other than NEW")
            elif not name:
                plain.append((select, target))
        if fields:
            self._set_new(fields, start, context)
        if plain:
            rest = query.copy()
            rest.set("expressions", [select.copy() for select, _ in plain])
            self._into(rest.sql(dialect=DIALECT), start, context, tuple(t for _, t in plain))

    def _select_into(self, text: str, start: int, context: _Context) -> None:
        listed = _INTO_TARGETS.search(text)
        written = [t.strip().lower() for t in listed.group(1).split(",")] if listed else []
        if any(t.startswith(("new.", "old.")) for t in written):
            assert listed is not None
            query = _parse(text[: listed.start()] + text[listed.end() :])
            if not isinstance(query, exp.Select):
                raise pl.UnsupportedError("SELECT INTO that SQLGlot reads otherwise")
            self._bind_fields(query, written, start, context)
            return
        # `INTO STRICT` only adds a run-time check that exactly one row came back.
        parsed = _parse(re.sub(r"(?i)\bINTO\s+STRICT\b", "INTO", text))
        if not isinstance(parsed, exp.Select) or parsed.args.get("into") is None:
            # `SELECT a, b INTO v1, v2 FROM ...`: several targets is PL/pgSQL, not SQL, and
            # SQLGlot cannot parse it (holdout3 R4). The list is read from the text and the
            # query analysed without it.
            listed = _INTO_TARGETS.search(text)
            query = _parse(text[: listed.start()] + text[listed.end() :]) if listed else None
            if not isinstance(query, exp.Select):
                raise pl.UnsupportedError("SELECT INTO that SQLGlot reads otherwise")
            targets = tuple(t.strip().split(".")[-1].lower() for t in listed.group(1).split(","))
            self._into(query.sql(dialect=DIALECT), start, context, targets)
            return
        into = parsed.args["into"]
        names = [into.this, *(into.expressions or [])] if into.this else list(into.expressions)
        targets = tuple(_name_of(n) for n in names)
        parsed.set("into", None)
        self._into(parsed.sql(dialect=DIALECT), start, context, targets)

    def _into(self, query: str, start: int, context: _Context, targets: tuple[str, ...]) -> None:
        """`<query> INTO targets` - SELECT INTO and FETCH alike.

        One target for `*`, or for several columns, is a record (holdout gap G2: `SELECT *
        INTO rec`, a `%ROWTYPE`, `FETCH c INTO rec`) - its fields are the query's columns,
        read as a FOR loop's record is. Otherwise each target takes one column.
        """
        parsed = _parse(query)
        selects = parsed.selects if isinstance(parsed, exp.Query) else []
        star = any(isinstance(s, exp.Star) or isinstance(s.this, exp.Star) for s in selects)
        if len(targets) == 1 and selects and (star or len(selects) != 1):
            record = targets[0]
            phantom = self._phantom(query, start, context, "record", (), names=True, record=record)
            context.records[record] = [*context.records.get(record, []), phantom.phantom]
            return
        self._phantom(query, start, context, "into", targets)

    def _assign(self, stmt: pl.Stmt, context: _Context) -> None:
        target = stmt.targets[0].lower()
        expression = stmt.expression
        query = _parse(f"SELECT {expression}")
        if query is None:
            raise pl.UnsupportedError("an assignment SQLGlot cannot parse")
        if "." in target:
            # `NEW.updated_at := now()` - a field set from nothing the analysis can name (a
            # clock, a literal) carries no lineage, so it is passed over.
            if query.find(exp.Column) is None and query.find(exp.Subquery) is None:
                return
            record, _, name = target.partition(".")
            if record == "old":
                return  # OLD is the row as it was: setting it writes nothing
            if record == "new":
                # semantic-ir T4 (holdout4 H8): in a BEFORE trigger NEW is the row being
                # written - `NEW.f := e` is a write of e into the trigger table's column f.
                self._set_new({name: expression}, stmt.start, context)
                return
            # Plan step 8: `r.v := <expr>` is a one-field record value, read as `SELECT * INTO
            # rec` is (G2) - a later `r.v` is traced from it.
            phantom = self._phantom(
                f"SELECT {expression} AS {name}", stmt.start, context, "record", (),
                names=True, record=record,
            )  # fmt: skip
            context.records[record] = [*context.records.get(record, []), phantom.phantom]
            return
        # A text that folds to a constant (a literal, `format()` of constants ...) is kept, so
        # a later EXECUTE of it is read (holdout4 H7: `v_sql := format(...)`).
        constant = routine_ir.fold(expression, context.constants)
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
                scope=context.scope_name,
                band=context.band,
                kind="assign",
                loops=context.loop_stack,
                trigger=context.trigger,
                uses=uses,
            )
        )

    def _dynamic(
        self, expression: str, using: str, start: int, context: _Context, verb: str = "executes"
    ) -> str | None:
        """DYN, in every position (semantic-ir T5): the SQL the text runs, or a boundary.

        `$n` is NULL - a USING value is an external value (D-42) - unless `BIND_USING`, which
        binds it to the n-th USING expression so a variable passed there stays traced.
        """
        bound = using if BIND_USING else ""
        folded = routine_ir.dynamic_text(expression, context.constants, bound)
        if folded is None:
            line = self._line(start)
            self.result.boundaries.append(
                Boundary(
                    kind=BoundaryKind.DYNAMIC_SQL,
                    subject=f"EXECUTE:{line}",
                    detail=f"{context.unit}: line {line} {verb} text that does not fold "
                    "to a constant, so its lineage is not decidable (D-42)",
                    unit=context.unit,
                    line=line,
                )
            )
        return folded

    # --- EMIT (semantic-ir T3) ---------------------------------------------------------------

    def _synthetic(self, start: int, text: str) -> None:
        """Place a statement the script does not hold; `finish` takes it out of the count."""
        self.out.put(start, text)
        self.result.synthetic += 1

    def _emit_query(self, query: str, start: int, context: _Context, function: _Function) -> None:
        """RETURN QUERY [EXECUTE]: inlined where the routine is called (D-40), or - in a
        routine that also builds rows - an INSERT of the query into its output."""
        if not function.output:
            function.queries.append(query)
            return
        columns = ", ".join(function.columns)
        self.result.synthetic += 1
        self._sql(f"INSERT INTO {function.output} ({columns}) {query}", start, context)

    def _emit_next(self, stmt: pl.Stmt, context: _Context, function: _Function) -> None:
        """RETURN NEXT [e]: one row of the output - a record's fields, the OUT parameters,
        or (SETOF a base type) the value of `e` itself."""
        record = stmt.expression.strip().lower()
        if not record:
            self._emit_out_params(stmt.start, context, function)
            return
        if record in context.records:
            values = ", ".join(f"{record}.{column}" for column in function.columns)
        elif len(function.columns) == 1:
            values = stmt.expression.strip()  # SETOF numeric: `RETURN NEXT r.v`
        else:
            return  # a record nothing assigned yet: a row of nothing traceable
        self.result.synthetic += 1
        self._sql(
            f"INSERT INTO {function.output} ({', '.join(function.columns)}) SELECT {values}",
            stmt.start,
            context,
        )

    def _emit_out_params(self, start: int, context: _Context, function: _Function) -> None:
        """RETURN (or the end) of an OUT-parameter routine: the row its OUT values make."""
        columns = ", ".join(function.columns)
        self.result.synthetic += 1
        self._sql(f"INSERT INTO {function.output} ({columns}) SELECT {columns}", start, context)

    def _execute(self, stmt: pl.Stmt, context: _Context) -> None:
        folded = self._dynamic(stmt.expression, stmt.using, stmt.start, context)
        if folded is None:
            return
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
        self._inline_tree(parsed, start, context)
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
            scope=context.scope_name,
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

    def _read_output(
        self, node: Any, call: Any, function: _Function, start: int, context: _Context | None
    ) -> None:
        """`f(args) AS a` in FROM / LATERAL -> `<f's output> AS a` (semantic-ir T3); the
        call's arguments bind f's parameters, so what they filter inside f is traced."""
        alias = node.args.get("alias")
        name = alias.this if alias is not None else exp.to_identifier(call.name)
        renamed = [c.name.lower() for c in alias.columns] if alias is not None else []
        if renamed:
            # `f(...) AS f(x)`: the call's own column names go on projections - band 0 does
            # not read an alias column list (as for an inlined routine, H9).
            query = exp.select(
                *(
                    exp.alias_(exp.column(column), new)
                    for column, new in zip(function.columns, renamed, strict=False)
                )
            ).from_(exp.to_table(function.output))
            replacement: Any = exp.Subquery(this=query, alias=exp.TableAlias(this=name))
        else:
            replacement = exp.to_table(function.output)
            replacement.set("alias", exp.TableAlias(this=name))
        node.replace(replacement)
        self._bind_call(call, function, replacement.find_ancestor(exp.Select), start, context)

    def _inline_tree(self, tree: Any, start: int, context: _Context | None = None) -> bool:
        changed = False
        for table in list(tree.find_all(exp.Table)):
            bound = self.transitions.get(table.name.lower()) if not table.db else None
            if bound is not None:
                schema, _, name = bound.rpartition(".")
                table.set("this", exp.to_identifier(name))
                table.set("db", exp.to_identifier(schema) if schema else None)
                if not table.alias:
                    table.set("alias", exp.TableAlias(this=exp.to_identifier(table.name)))
                changed = True
        # `FROM f(...) AS x` parses as a Table over the call; `[LEFT JOIN] LATERAL f(...) AS x`
        # as a Lateral over it (holdout3 R5: a calendar lookup through a LANGUAGE sql
        # function, defined in another file, read this way). Both are the function's query.
        calls: list[tuple[Any, Any, str]] = []
        for table in tree.find_all(exp.Table):
            if isinstance(table.this, exp.Anonymous):
                calls.append((table, table.this, str(table.db or "")))
        for lateral in tree.find_all(exp.Lateral):
            call = lateral.this
            if isinstance(call, exp.Dot) and isinstance(call.expression, exp.Anonymous):
                calls.append((lateral, call.expression, call.this.name))
            elif isinstance(call, exp.Anonymous):
                calls.append((lateral, call, ""))
        for node, call, schema in calls:
            name = pl.fold_name(f"{schema}.{call.name}" if schema else call.name)
            function = self.functions.get(name)
            if function is None:
                continue
            if function.output:
                self._read_output(node, call, function, start, context)
                changed = True
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
            bound = _call_arguments(call, function.routine.params)
            parts = []
            for text in function.queries:
                query = _parse(text)
                if not isinstance(query, exp.Query):
                    break
                for column in list(query.find_all(exp.Column)):
                    if not column.table and column.name.lower() in bound:
                        column.replace(bound[column.name.lower()].copy())
                parts.append(query)
            else:
                union = parts[0]
                for part in parts[1:]:
                    union = exp.union(union, part, distinct=False)
                alias = node.args.get("alias")
                alias_name = alias.this if alias is not None else exp.to_identifier(call.name)
                # The output names go on the projections themselves: band 0 does not read an
                # alias column list over a LATERAL subquery ("no matching projection",
                # holdout4 H9). The call's own list wins over the routine's signature.
                names = (
                    [column.name.lower() for column in alias.columns]
                    if alias is not None and alias.columns
                    else routine_ir.output_columns(function.routine, self.dictionary)
                )
                for part in parts:
                    _name_projections(part, names)
                named = exp.TableAlias(this=alias_name)
                if isinstance(node, exp.Lateral):
                    node.set("this", exp.Subquery(this=union))
                    node.set("alias", named)
                else:
                    node.replace(exp.Subquery(this=union, alias=named))
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
            if not _is_symbol(column, context):
                continue
            if _inside_routine_call(column, self.functions) and not _a_returned_argument(
                column, self.functions
            ):
                continue
            slot = next(((root, target) for root, target in slots if _within(column, root)), None)
            if slot is not None and _in_predicate_below(column, slot[0]):
                # `SET f = (SELECT r.x FROM r WHERE NEW.k LIKE r.p)`: the symbol chooses the
                # row the value is read from - a filter of the write, not its value (T4).
                read = _relations_beside(column, parsed) if (into or record_fields) else ()
                uses.append(Use(symbol=_symbol(column), role="filter", read=read))
            elif slot is not None:
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
            elif slots and _in_from_function(column):
                # `FROM generate_series(date_trunc('month', p_from), ...) AS gs`: the
                # variable decides the rows the function produces, and the projections
                # read those rows - a derived value of every target (production pilot).
                for _root, target in slots:
                    uses.append(
                        Use(
                            symbol=_symbol(column),
                            role="value",
                            target=target,
                            transform=Transform.DERIVED,
                        )
                    )
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
    tables: tuple[str, ...] = ()  # in a trigger: the tables NEW is a row of (semantic-ir T4)
    scope: str = ""
    """semantic-ir T2b: the routine whose variables these are - its name, or `<do>:<line>:<hash>`
    for a DO block - so two routines' `p_run_id` are two graph nodes, never one."""

    @property
    def scope_name(self) -> str:
        return self.scope or self.unit

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
            tables=self.tables,
            scope=self.scope,
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
            tables=self.tables,
            scope=self.scope,
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


def _in_predicate_below(node: Any, root: Any) -> bool:
    """Is `node` in a WHERE / HAVING / JOIN / QUALIFY that sits inside `root`?"""
    current = node.parent
    while current is not None and current is not root:
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


def _a_returned_argument(column: Any, functions: dict[str, _Function]) -> bool:
    """A symbol passed, directly or in an expression, as an argument the called scalar
    function's return value is computed from (D-66) - so it is a value of the call.

    Band 0 sees the symbol as NULL, so the function's summary has nothing to map; without
    this the value passed by a variable or record field was lost (holdout3:
    `fn_next_business_day(cal.due_date, ...)` with `cal` a loop record).
    """
    child, current = column, column.parent
    while current is not None:
        if isinstance(current, exp.Anonymous):
            function = next(
                (f for n, f in functions.items() if n.split(".")[-1] == current.name.lower()),
                None,
            )
            if function is not None:
                if function.routine.returns != "scalar":
                    return False
                position = next(
                    (i for i, arg in enumerate(current.expressions) if arg is child), -1
                )
                return position in function.from_arguments
        child, current = current, current.parent
    return False


def _scope_of(routine: pl.Routine, source: str, start: int) -> str:
    """Whose variables a routine's are (T2b): its name, or - for a DO block, which has none -
    `<do>:<line>:<hash of its body>`, so two DO blocks never share a variable node."""
    if routine.kind != "do":
        return routine.name
    line = source.count("\n", 0, start) + 1
    body = source[routine.body_start : routine.body_end]
    return f"<do>:{line}:{hashlib.sha1(body.encode('utf-8')).hexdigest()[:6]}"


def _forms(nodes: list[pl.Node]) -> set[str]:
    """Every statement form in a body, nested blocks, branches, loops and handlers included."""
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, pl.Stmt):
            found.add(node.kind)
        elif isinstance(node, pl.Block):
            found |= _forms(node.body)
            for handler in node.handlers:
                found |= _forms(handler.body)
        elif isinstance(node, pl.If):
            for arm in node.arms:
                found |= _forms(arm)
        elif isinstance(node, pl.Loop):
            found |= _forms(node.body)
    return found


def _name_projections(query: Any, names: list[str]) -> None:
    """Alias each top-level projection of `query` (every branch of a set operation) to
    `names`, by position - what a routine's rows are called where it is read."""
    if not names:
        return
    selects = (
        [query]
        if isinstance(query, exp.Select)
        else [s for s in query.find_all(exp.Select) if s.parent_select is None]
    )
    for select in selects:
        if any(isinstance(e, exp.Star) or isinstance(e.this, exp.Star) for e in select.expressions):
            continue  # `*` is not positional text; band 0 expands it itself
        select.set(
            "expressions",
            [
                exp.alias_(e.this if isinstance(e, exp.Alias) else e, names[i])
                if i < len(names)
                else e
                for i, e in enumerate(select.expressions)
            ],
        )


def _call_arguments(call: Any, params: list[str]) -> dict[str, Any]:
    """Parameter name -> the argument expression a call binds to it.

    Positional arguments first, then named ones (`p => expr`, holdout4 H7's call), as
    PostgreSQL binds them. A `VARIADIC ARRAY(...)` argument binds to its parameter like any
    other; a variadic LIST of several arguments binds only its first (an under-approximation).
    """
    bound: dict[str, Any] = {}
    position = 0
    for argument in call.expressions:
        if isinstance(argument, exp.Kwarg):
            bound[argument.this.name.lower()] = argument.expression
        elif position < len(params):
            bound[params[position]] = argument
            position += 1
    return bound


def _qualified(table: exp.Table) -> str:
    parts = [p for p in (table.catalog, table.db, table.name) if p]
    return ".".join(p.lower() for p in parts)


def _in_from_function(column: exp.Column) -> bool:
    """Is `column` an argument of a function used as a relation in a FROM or JOIN?"""
    node: Any = column.parent
    inside_function = False
    while node is not None and not isinstance(node, exp.Select):
        if isinstance(node, exp.Func):
            inside_function = True
        if isinstance(node, (exp.From, exp.Join)):
            return inside_function
        node = node.parent
    return False


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


def _bind_trigger(uses: list[Use], table: str) -> list[Use]:
    """WHEN (NEW.x ...) filters, with NEW / OLD bound to the trigger's table."""
    return [Use(symbol=(f"@{table}", use.symbol[1]), role="filter") for use in uses]


# --- the entry points --------------------------------------------------------------------------


def desugar(
    source: str, dictionary: Dictionary, functions: dict[str, Any] | None = None
) -> Desugared:
    """`source` with every readable routine and DO block restated as the SQL it runs.

    `functions` are routines an earlier file of the same run defined (D-65), callable here.
    """
    return _Desugarer(source, dictionary, functions).run()


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


def _scoped(scope: str, name: str) -> str:
    """A variable's graph name: `<routine>.<name>` (T2b), so routines never share one."""
    return f"{scope}.{name}" if scope else name


def _raw(name: str) -> str:
    """The variable's own name, without its routine (a variable name holds no dot)."""
    return name.rpartition(".")[2]


def _variable(name: str) -> Node:
    return Node(kind=NodeKind.VARIABLE, name=name)


def _relation_of(column: str) -> str:
    return column.rpartition(".")[0]


def finish(result: Any, desugared: Desugared, dictionary: Dictionary) -> Any:
    """Restate band 0's result in procedural terms (see the module docstring)."""
    by_line: dict[int, list[Emitted]] = {}
    for emitted in desugared.emitted:
        if not emitted.binding:  # a binding shares its caller's line, not its statement
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
            if emitted.kind == "record" and edge.flow is Flow.FILTER:
                into_edges.append((emitted, edge))  # it chose the row read (rule 2)
            elif emitted.kind in ("loop", "record"):
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
            _edge(
                edge.source,
                _variable(_scoped(emitted.targets_scope or emitted.scope, variable)),
                edge.flow,
                edge.transform,
                band,
                origin,
            )
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
            return [(_variable(_scoped(emitted.scope, name)), Transform.IDENTITY, Flow.VALUE)]
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
                        target = _variable(_scoped(emitted.scope, use.target[4:]))
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
                        provenance_key = (emitted.unit, _raw(target.name))
                        if source.kind is NodeKind.COLUMN:
                            provenance.setdefault(provenance_key, []).append(
                                (source.name, _combine(transform, use.transform))
                            )
                        else:
                            # A copy: `x := x * 2` reads and writes the same list, and
                            # appending to what is being iterated never ended (step 8).
                            known = provenance.setdefault(provenance_key, [])
                            for column, inner in list(
                                provenance.get((emitted.unit, _raw(source.name)), [])
                            ):
                                entry = (column, _combine(inner, use.transform))
                                if entry not in known:
                                    known.append(entry)
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
                    if not relation:
                        # A guard in a routine whose statement names no relation of its own
                        # (`IF NEW IS DISTINCT FROM OLD` in a BEFORE trigger that only sets
                        # a NEW field): nothing written, so nothing filtered here.
                        continue
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
    phantom_count = sum(1 for e in desugared.emitted if e.phantom) + desugared.synthetic
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
        ) or (
            # `SELECT * INTO rec` earlier in the routine (G2): every one that may have run.
            candidate.kind == "record"
            and candidate.record == record
            and candidate.unit == emitted.unit
            and candidate.line <= emitted.line
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


def _assignments(emitted: list[Emitted]) -> dict[tuple[str, str], list[tuple[str, Transform]]]:
    """(unit, variable) -> the variables assigned into it, each with its transform."""
    assigned: dict[tuple[str, str], list[tuple[str, Transform]]] = {}
    for statement in emitted:
        for use in statement.uses:
            if use.role == "value" and use.target.startswith("var:") and not use.symbol[0]:
                key = (statement.unit, use.target[len("var:") :])
                assigned.setdefault(key, []).append((use.symbol[1], use.transform))
    return assigned


def _reaching(
    unit: str,
    returned: str,
    outer: Transform,
    assigned: dict[tuple[str, str], list[tuple[str, Transform]]],
) -> list[tuple[str, Transform]]:
    """Every variable a returned one was assigned from, transitively, with its transform.

    D-66: `v := p; v := v + 1; RETURN v` reaches parameter `p`, derived. Provenance holds
    only the table columns a variable read, so a function returning a local copy of its
    argument summarised to nothing, and every call to it lost its lineage without a word
    (holdout3: `fn_next_business_day(cal.due_date, ...)`).
    """
    todo, seen = [(returned, outer)], set()
    while todo:
        name, transform = todo.pop()
        if (name, transform) in seen:
            continue
        seen.add((name, transform))
        for source, inner in assigned.get((unit, name), []):
            if source != name:
                todo.append((source, _combine(inner, transform)))
            elif inner is not Transform.IDENTITY:  # `v := v + 1` derives v
                todo.append((name, _combine(inner, transform)))
    return sorted(seen, key=lambda item: (item[0], item[1].value))


def summaries(result: Any, desugared: Desugared) -> dict[str, Summary]:
    """Rule 6: each scalar function's RETURN, as the columns its value depends on."""
    provenance = getattr(result, "provenance", {})
    assigned = _assignments(desugared.emitted)
    out: dict[str, Summary] = {}
    for unit in desugared.units:
        summary = Summary(unit=unit.split(".")[-1].upper())
        params = desugared.params.get(unit, [])
        for _, symbols in desugared.returns.get(unit, []):
            for (record, returned), outer in symbols:
                if record:
                    continue
                for name, transform in _reaching(unit, returned, outer, assigned):
                    for column, inner in provenance.get((unit, name), []):
                        summary.returns_from.append(
                            ReturnSource(column, _combine(inner, transform))
                        )
                    if name in params:  # plan step 5: `RETURN x * 2` is computed from x
                        summary.from_arguments.append((params.index(name), transform))
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
