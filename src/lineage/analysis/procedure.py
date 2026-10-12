"""The band-1 analyser: set-based lineage plus dataflow (T2.3).

Combines two analyses that see different things and must not be confused with each other:

* **Set-based** (`band0`) reads one statement at a time. It resolves column-to-column
  lineage through projections, views and alias chains, and it cannot see a variable.
* **Def-use** (`defuse`) reads the program. It follows values through variables, across
  statements, and across procedure calls in a package body.

Neither subsumes the other. A statement's projections are band-0 work even inside a
procedure; a variable governing which rows that statement loads is band-1 work. Keeping
them separate is what lets the score say which one broke.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from lineage.analysis import band0
from lineage.analysis.band0 import AnalysisResult, Refusal
from lineage.analysis.cfg import Cfg, build_all
from lineage.analysis.cfg import NodeKind as CfgNodeKind
from lineage.analysis.defuse import (
    UnitScope,
    analyse_unit,
    collect_scopes,
    definitions_and_uses,
)
from lineage.analysis.document_tables import restate as restate_document_tables
from lineage.analysis import plpgsql
from lineage.analysis.dynamic import Resolution, resolve_dynamic_sql
from lineage.analysis.interproc import build_summaries
from lineage.analysis.reaching import reaching_definitions, uninitialised_uses
from lineage.analysis.refusal import classify_program, enclosing_unit_at
from lineage.analysis.relations import is_path_change, search_path_change
from lineage.analysis.scratch import find_fusion_hazards
from lineage.analysis.triggers import inherited_edges
from lineage.config import AnalysisConfig
from lineage.dialects import resolve_dialect
from lineage.evidence.witness import ExecutionWitness
from lineage.ir.model import Boundary, BoundaryKind, IREdge, Node, NodeKind
from lineage.parsing import pg_grammar, psql
from lineage.parsing.plsql import Program, parse_program, source_slice
from lineage.resolution.dictionary import Dictionary
from lineage.resolution.views import resolve_views

__all__ = ["AnalysisResult", "Refusal", "analyse_source"]


def analyse_source(
    source: str,
    dictionary: Dictionary,
    config: AnalysisConfig | None = None,
    witness: ExecutionWitness | None = None,
    result_target: str | None = None,
) -> AnalysisResult:
    """Analyse one PL/SQL source unit for band-0 and band-1 lineage.

    ``witness`` supplies execution evidence (T3.5). Omit it and every edge carries
    ``unexercised=None`` - nothing was observed, so nothing is claimed either way.

    ``result_target`` names the relation a TARGETLESS query writes into (PG-09) - a dbt or
    Databricks model's own table. Omit it and such a query's projections land on
    ``<result>``.
    """
    settings = config or AnalysisConfig()
    dialect = resolve_dialect(settings, dictionary)

    includes: list[psql.Include] = []
    desugared: plpgsql.Desugared | None = None
    if dialect.name == "postgres":
        # D-36, D-37: a psql script restated as the SQL the server receives - meta-commands
        # blanked, `:'v'` substituted, `U&""` decoded - before anything splits or parses
        # it (B10-03, B10-05). Same lines, so every later line number holds.
        source, includes = psql.restate(source)
        # D-30: JSON_TABLE / XMLTABLE restated as LATERAL subqueries in the SOURCE, before
        # any front end, classifier or SQLGlot reads it - SQLGlot cannot parse PASSING
        # (B5-04), and the refusal register scans the raw text. Line counts are kept.
        source = restate_document_tables(source)
        # B7-07: SQLGlot cannot parse OVERRIDING {SYSTEM|USER} VALUE. It decides whether an
        # identity column takes the supplied value and changes nothing about lineage, so it
        # is blanked - same length, so every line and column stays where it was.
        source = re.sub(
            r"\bOVERRIDING\s+(?:SYSTEM|USER)\s+VALUE\b",
            lambda m: re.sub(r"\S", " ", m.group(0)),
            source,
            flags=re.IGNORECASE,
        )
        # TT-5: PostgreSQL grammar SQLGlot has no rule for (SEARCH / CYCLE, SQL/JSON and XML
        # keyword forms, typed and radix literals, geometric operators ...), restated as SQL
        # that reads the same columns. Same lines, so every later line number holds.
        source = pg_grammar.restate(source)
        # B8 (D-39..D-42): every PL/pgSQL routine and DO block the reader can read becomes
        # the SQL its body runs, at its own lines; what it cannot read stays as written and
        # is refused by name, as before. `plpgsql.finish` restates the result afterwards.
        desugared = plpgsql.desugar(source, dictionary)
        source = desugared.source

    program = dialect.frontend.parse(source)

    # Callees are summarised before the call sites are analysed, so a scalar UDF inside a
    # SELECT contributes the columns its return value depends on rather than the columns
    # in its arguments.
    summaries = build_summaries(program, dictionary, settings.budgets.interprocedural_depth_cap)

    result = band0.analyse_source(
        source, dictionary, settings, summaries=summaries, result_target=result_target
    )
    if desugared is not None:
        # Rule 6: a scalar PL/pgSQL function's summary comes from its own body, which band
        # 0 has just traced. A function calling a function needs its callee's summary first,
        # so band 0 runs again until the summaries stop changing (bounded by the depth cap).
        result = plpgsql.finish(result, desugared, dictionary)
        known = dict(summaries)
        for _ in range(settings.budgets.interprocedural_depth_cap):
            found = plpgsql.summaries(result, desugared)
            if not found or all(known.get(name) == summary for name, summary in found.items()):
                break
            known.update(found)
            result = plpgsql.finish(
                band0.analyse_source(
                    source, dictionary, settings, summaries=known, result_target=result_target
                ),
                desugared,
                dictionary,
            )

    # The unanalysable-construct classifier (T3.1), run over the whole program rather than
    # over one module's statement list. A `MODEL` clause is refused whether it appears in a
    # band-0 INSERT or inside a cursor a band-1 loop drives, and a refusal that depended on
    # which analyser happened to look first would not be a property of the statement.
    #
    # Only flagged statements are counted, and each one costs exactly one statement of
    # parse coverage. Refusing is never free: a classifier that abstained from everything
    # would report 0% coverage, not 100% precision.
    # `scoped_statements` carries the statements band 0 refused only in PART (PG-20). Their
    # refusals are addressed at a CTE's line rather than the statement's, so the dedup key
    # below cannot see them, and this pass would re-refuse the whole statement: a second
    # `statements_seen`, a refusal spanning lineage that was legitimately produced, and a
    # `violations` contradiction for every recovered edge. Measured before it was fixed -
    # the ungated probe reported `seen 2, refusals 1, violations 1` on four correct edges.
    # D-37: `\i` runs another file, whose lineage this analysis does not have.
    for include in includes:
        result.boundaries.append(
            Boundary(
                kind=BoundaryKind.SOURCE_UNAVAILABLE,
                subject=include.path,
                detail=f"\\i {include.path}: an included script's lineage is not in this "
                "analysis (D-37)",
                unit=band0.SCRIPT_UNIT,
                line=include.line,
            )
        )

    already_refused = {(refusal.unit, refusal.line) for refusal in result.refusals}
    already_refused |= result.scoped_statements
    standing = list(result.refusals)
    for candidate in classify_program(program):
        # Addressed already, either exactly or by falling inside a standing refusal's SPAN.
        # The span test is the part PG-20 needed. `classify_program`'s third pass reports a
        # construct at the line the construct sits on, while its first pass and band 0
        # report the line the STATEMENT starts on, so the two addresses for one construct
        # differ by however long the statement is - 795 lines, in the torture file. Without
        # it the same `JSON_TABLE` was refused twice at two addresses, which cost a second
        # `statements_seen` and reported coverage 0.5 for a statement that was analysed and
        # produced 329 edges.
        if (candidate.unit, candidate.line) in already_refused:
            continue
        if any(prior.covers(candidate.unit, candidate.line) for prior in standing):
            continue
        result.statements_seen += 1
        result.refusals.append(candidate)
        already_refused.add((candidate.unit, candidate.line))
        # Appended, so the span test also holds WITHIN this pass. Two constructs in one
        # statement otherwise produce two refusals and two `statements_seen` for one
        # statement - which contradicts `_deduplicate`'s stated policy of one refusal per
        # statement, and which band 0 never noticed because it only ever reaches statements
        # its own front end could parse.
        standing.append(candidate)
    for refusal in result.refusals:
        refused = Boundary(
            kind=BoundaryKind.REFUSAL,
            subject=f"{refusal.unit}:{refusal.line}",
            detail=f"{refusal.unit}: line {refusal.line} refused "
            f"[{refusal.code.value}] - {refusal.reason}",
            unit=refusal.unit,
            line=refusal.line,
        )
        if refused not in result.boundaries:
            result.boundaries.append(refused)

    scopes = collect_scopes(program, dictionary.dialect)
    graphs = build_all(program, dictionary.dialect)

    # Resolved once for the whole source, not per unit: a carrier variable is a carrier
    # everywhere, and the constant environment is built in source order.
    dynamic = resolve_dynamic_sql(program)

    for summary in summaries.values():
        boundary = summary.boundary()
        if boundary is None:
            continue
        capped = Boundary(
            kind=BoundaryKind.DEPTH_CAP,
            subject=summary.unit,
            detail=boundary,
            unit=summary.unit,
        )
        if capped not in result.boundaries:
            result.boundaries.append(capped)

    for unit, cfg in graphs.items():
        scope = scopes.get(unit)
        if scope is None:
            continue
        dataflow = analyse_unit(cfg, scope, dictionary, dynamic)
        result.edges.extend(dataflow.edges)
        for item in dataflow.unresolved:
            # TT-4: band 0 now counts and refuses a script statement SQLGlot cannot parse,
            # so def-use's parse failure for the same statement would declare it twice.
            if (
                isinstance(item, Boundary)
                and item.kind is BoundaryKind.PARSE_FAILURE
                and (unit.upper(), item.line) in _refused_at(result)
            ):
                continue
            unresolved = _in_unit(unit, item, BoundaryKind.UNRESOLVED_IDENTIFIER)
            if unresolved not in result.boundaries:
                result.boundaries.append(unresolved)

        for text in _reaching_findings(cfg, scope, settings):
            finding = _in_unit(unit, text, BoundaryKind.CROSS_UNIT_STATE)
            if finding not in result.boundaries:
                result.boundaries.append(finding)

    # Triggers fire invisibly. Nothing in the source mentions them, so their edges are
    # inherited from the DICTIONARY by whichever relations this source writes - the
    # register's rule, and the reason a trigger belongs to its table rather than to any
    # caller. Added before the merge below so they are deduplicated and ordered like any
    # other edge.
    trigger_edges, trigger_boundaries = inherited_edges(
        _written_relations(program, dynamic), dictionary
    )
    result.edges.extend(trigger_edges)

    # S2-10: a trigger body is analysed out of the DICTIONARY, wrapped in a synthetic
    # procedure, so its edges carry lines relative to that wrapper while a refusal on the
    # same statement carries the line in the FILE. Recording the units here is what lets
    # `refusals_not_cross_checkable` say "unknown" instead of letting `covers()` answer
    # "no" for a reason that has nothing to do with the truth.
    result.incomparable_units.update(
        qualified.rpartition(".")[2].upper() for qualified in dictionary.triggers
    )
    for note in trigger_boundaries:
        trigger_note = Boundary(
            kind=BoundaryKind.SOURCE_UNAVAILABLE,
            subject=str(note).split(":")[0],
            detail=str(note),
        )
        if trigger_note not in result.boundaries:
            result.boundaries.append(trigger_note)

    for suppressed in _suppressed_errors(program):
        if suppressed not in result.boundaries:
            result.boundaries.append(suppressed)

    for contextual in _context_dependent_names(program, result, dictionary.dialect):
        if contextual not in result.boundaries:
            result.boundaries.append(contextual)

    # Silent failure s6, closed corpus-wide rather than per analyser (T3.4c). Band 0
    # inlines view text before analysing a projection and the trigger analyser rewrites an
    # INSTEAD OF trigger's :NEW row, but an ordinary `UPDATE v_customer_editable` went
    # through neither and kept the view on both ends of its filter edge - naming an object
    # that stores nothing, while dim_customer appeared to have no writer.
    #
    # Run over every edge from every analyser, because a view is a naming fact and the
    # third copy of a naming rule is the one that disagrees with the other two.
    result.edges, view_notes = resolve_views(
        result.edges, dictionary, _defined_views(source, dictionary.dialect)
    )
    for note in view_notes:
        view_note = Boundary(
            kind=BoundaryKind.ROW_CORRESPONDENCE,
            subject=str(note).split(" ")[0],
            detail=str(note),
        )
        if view_note not in result.boundaries:
            result.boundaries.append(view_note)

    # The set-based analyser works one statement at a time and never sees control flow,
    # so its edges arrive unguarded even when the statement sits inside a branch or a
    # loop. An unguarded edge claims the write always happens, which for a conditional
    # write is a wrong answer rather than an incomplete one - so guards are applied here,
    # from the CFG, once both analyses have run.
    result.edges = [_with_guard(edge, graphs) for edge in result.edges]

    # Merge on ledger identity so two facts differing only by guard or origin both
    # survive, while the same fact found by both analyses appears once.
    unique: dict[tuple[str, ...], object] = {}
    merged = []
    for edge in result.edges:
        key = edge.identity()
        if key in unique:
            continue
        unique[key] = edge
        merged.append(edge)

    # `unexercised` is applied last, over the finished edge set (T3.5). It is not an
    # analysis result - it is a fact about the running system laid alongside one - so it
    # must not be able to change which edges exist or what they say. An edge whose unit the
    # witness cannot attribute keeps None, and the harness counts those separately: a
    # statement the log could not tie to a unit is UNCOVERED, not exercised.
    if witness is not None:
        result.edges = [
            edge.model_copy(update={"unexercised": _unexercised(edge, witness)}) for edge in merged
        ]
        result.boundaries.append(
            Boundary(
                kind=BoundaryKind.SOURCE_UNAVAILABLE,
                subject="EXECUTION WITNESS",
                detail=f"execution witness covers {len(witness.units)} unit(s) over "
                f"{witness.window}; edges outside those units carry no execution "
                f"evidence either way",
            )
        )
    else:
        result.edges = merged

    result.edges = sorted(
        result.edges, key=lambda e: (e.band, e.flow.value, str(e.source), str(e.target))
    )

    # Declared, not silently resolved. Nothing composes paths yet, so nothing is fused -
    # but the hazard is recorded now so the constraint exists before the code that would
    # violate it.
    for hazard in find_fusion_hazards(result.edges, dictionary):
        entry = Boundary(
            kind=BoundaryKind.FUSION_HAZARD,
            subject=hazard.relation,
            detail=f"fusion hazard: {hazard.describe()}",
            attaches_to=Node(kind=NodeKind.RELATION, name=hazard.relation),
        )
        if entry not in result.boundaries:
            result.boundaries.append(entry)

    if desugared is not None:
        result = plpgsql.explain_refusals(result, desugared)
    return result


def _refused_at(result: AnalysisResult) -> set[tuple[str, int]]:
    """`(UNIT, line)` of every statement refused whole, units upper-cased to compare."""
    return {
        ((refusal.unit or "").upper(), refusal.line)
        for refusal in result.refusals
        if refusal.scope == "statement"
    }


def _in_unit(unit: str, item: Boundary | str, default: BoundaryKind) -> Boundary:
    """Prefix a boundary with its unit without losing how it was classified.

    An f-string over a ``Declared`` returns a plain ``str`` and drops kind and subject
    silently, which would make the axis unscoreable again one interpolation at a time.
    """
    prefix = f"{unit}: "
    if isinstance(item, Boundary):
        # A parse failure knows its line and not its unit, and a bare line number is not an
        # identity - so the unit qualifies the subject here, where it is known.
        qualify = f"{unit}:" if item.kind is BoundaryKind.PARSE_FAILURE else ""
        return item.prefixed(prefix, subject_prefix=qualify).model_copy(
            update={"unit": item.unit or unit}
        )
    return Boundary(kind=default, subject=unit, detail=f"{prefix}{item}", unit=unit)


def _unexercised(edge: IREdge, witness: ExecutionWitness) -> bool | None:
    """Whether this edge's statement was seen running, from the witness's point of view.

    Inverted from `ran()` because the axis is named for the interesting case: an edge that
    is provably in the code and has never fired is a finding, and an edge that fired is
    just an ordinary edge.
    """
    ran = witness.ran(edge.origin.unit, edge.origin.line)
    return None if ran is None else not ran


def _written_relations(program: Program, dynamic: Resolution) -> dict[str, str]:
    """Which relations this source writes, and by what event.

    The event matters: a trigger fires on `INSERT` or on `UPDATE`, and inheriting an
    `AFTER INSERT` trigger from an `UPDATE` statement would be an invented edge - the
    trigger genuinely does not run.

    Recovered dynamic statements are included. `b2_01` inserts into `tmp_recent` through
    `EXECUTE IMMEDIATE`, and the trigger fires just the same; a source that only looked at
    written SQL would miss it, which is two invisible mechanisms compounding.
    """
    written: dict[str, str] = {}
    texts = [statement.text for statement in program.statements]
    texts += [statement.text for statement in dynamic.statements]

    for text in texts:
        try:
            parsed = sqlglot.parse_one(text, dialect="oracle")
        except Exception:
            continue
        if isinstance(parsed, exp.Insert):
            event = "INSERT"
        elif isinstance(parsed, exp.Update):
            event = "UPDATE"
        elif isinstance(parsed, exp.Delete):
            event = "DELETE"
        elif isinstance(parsed, exp.Merge):
            # A MERGE both inserts and updates, and which one a given row takes is not
            # statically decidable. Both events are claimed, because a trigger that might
            # fire is a real edge and dropping it would be a silent miss.
            event = "INSERT UPDATE"
        else:
            continue

        target = parsed.this
        if isinstance(target, exp.Schema):
            target = target.this
        if isinstance(target, exp.Table):
            name = target.name.upper()
            written[name] = event if name not in written else f"{written[name]} {event}"

    return written


def _suppressed_errors(program: Program) -> list[Boundary]:
    """Exception handlers that swallow the failure whole.

    `WHEN OTHERS THEN NULL` is not a style complaint here, it is a coverage fact. The
    handler catches every error and does nothing, so a mapping that was enabled and failed
    leaves the estate looking exactly like one that ran and wrote nothing. **Absence of an
    edge stops being evidence** anywhere downstream of it - which is precisely the claim a
    coverage statement exists to qualify.

    Detected structurally rather than by matching source text, because `WHEN OTHERS THEN
    NULL;` and a handler whose body is a bare NULL across three lines are the same fact.
    """
    found: list[Boundary] = []
    for ctx in program.frontend.exception_handlers(program.tree):
        body = " ".join(source_slice(ctx).split()).upper()
        if "OTHERS" not in body.split("THEN")[0]:
            continue
        statements = body.split("THEN", 1)[1] if "THEN" in body else ""
        if re.fullmatch(r"\s*NULL\s*;?\s*", statements) is None:
            continue
        line = ctx.start.line
        unit = enclosing_unit_at(program, line)
        found.append(
            Boundary(
                kind=BoundaryKind.SUPPRESSED_ERROR,
                subject=f"{unit}:{line}",
                detail=f"{unit}: WHEN OTHERS THEN NULL at line {line} swallows every "
                f"failure - a write that was attempted and rejected is indistinguishable "
                f"from one that never ran, so a missing edge below here proves nothing",
                unit=unit,
                line=line,
            )
        )
    return found


_TEMP_TABLE = re.compile(
    r"(?i)(?:CREATE\s+(?:LOCAL\s+|GLOBAL\s+)?TEMP(?:ORARY)?\s+TABLE|INTO\s+TEMP(?:ORARY)?(?:\s+TABLE)?)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)"
)
"""A temp table's name where a statement creates one (D-31)."""


def _context_dependent_names(
    program: Program, result: AnalysisResult, dialect: str
) -> list[Boundary]:
    """Units whose lineage is only valid for the schema that executed them.

    Silent failure s2, the half that survives a correct analyser. An unqualified name binds
    through the EXECUTING user's schema, so two jobs running identical code against
    different schemas touch different tables - and a single observation shows one of them
    and looks definitive. The resolution is not a defect to fix; it is a condition on the
    answer, and a condition nobody states is a condition nobody knows about.

    One boundary per unit, at the first unqualified reference. Per-reference would be
    accurate and unreadable - the same reason fusion hazards are declared per relation.
    """
    seen: dict[str, int] = {}
    path: list[str] | None = None
    temps: set[str] = set()
    for statement in program.statements:
        # D-31: a temp table lives in pg_temp, which is searched first - an unqualified name
        # that a temp table holds binds with certainty, so it assumes nothing.
        if dialect == "postgres":
            temps |= {
                name.upper() for name in _TEMP_TABLE.findall(statement.text)
            }
        # D-23: under a `SET search_path` the binding is certain, so nothing is assumed.
        change = search_path_change(statement.text, dialect)
        if is_path_change(change):
            path = change
            continue
        if path is not None:
            continue
        # TE-2: a top-level bare SELECT is analysed into `<result>` (D-8), so its unqualified
        # names bind through the search path like any write's and the assumption is declared.
        if (
            statement.kind not in band0.SUPPORTED
            and not (dialect == "postgres" and statement.kind == "update_statement")
            and not band0._is_standalone_query(program, statement)
        ):
            continue
        try:
            parsed = sqlglot.parse_one(statement.text, dialect=dialect)
        except Exception:
            continue
        # A reference to a CTE is a Table node with no schema, and it resolves to the
        # statement's own WITH - wherever it runs. Counting it made every CTE query carry a
        # search-path warning about names that never touch the search path (finding B1-06).
        local = {(cte.alias_or_name or "").upper() for cte in parsed.find_all(exp.CTE)}
        for table in parsed.find_all(exp.Table):
            if table.text("db") or not table.name or table.name.upper() in local | temps:
                continue
            unit = enclosing_unit_at(program, statement.line)
            seen.setdefault(unit, statement.line)

    return [
        Boundary(
            kind=BoundaryKind.CONTEXT_DEPENDENT_BINDING,
            subject=f"{unit}:{line}",
            detail=f"{unit}: unqualified names from line {line} resolve through whichever "
            f"schema executes this unit, so the lineage below is valid for the schema it "
            f"was bound against and is a different answer under another",
            unit=unit,
            line=line,
        )
        for unit, line in sorted(seen.items())
    ]


def _reaching_findings(cfg: Cfg, scope: UnitScope, config: AnalysisConfig) -> list[str]:
    """Variables read before anything assigns them, and non-convergence if it happens."""
    definitions: dict[int, set[str]] = {}
    uses: dict[int, set[str]] = {}

    for node in cfg.nodes.values():
        if node.kind is CfgNodeKind.LOOP:
            # A FOR loop's index or record is defined by the loop header, not by any
            # statement. Without this it looks like a variable read before assignment.
            for name, row in scope.row_sources.items():
                if row.line == node.line:
                    definitions.setdefault(node.id, set()).add(name)
            for name, declaration in scope.variables.items():
                if declaration.line == node.line and declaration.scope == "local":
                    definitions.setdefault(node.id, set()).add(name)
            continue
        if node.kind is not CfgNodeKind.STATEMENT:
            continue
        defined, used = definitions_and_uses(node, scope, config.dialect)
        if defined:
            definitions[node.id] = defined
        if used:
            uses[node.id] = used

    # `v_total NUMBER := 0` is assigned before the first statement runs. Treating the
    # declaration as a definition at entry stops every later read looking uninitialised.
    initialised = {name for name, declaration in scope.variables.items() if declaration.has_default}
    if initialised:
        definitions.setdefault(cfg.entry, set()).update(initialised)

    cap = config.budgets.loop_fixpoint_iteration_cap
    result = reaching_definitions(cfg, definitions, cap)

    findings: list[str] = []
    if not result.converged:
        # A limit that silently truncates is a hidden defect; declared, it is a
        # specification.
        findings.append(
            f"reaching definitions did not converge within the configured cap of {cap} "
            f"iterations - results below that point are incomplete"
        )

    parameters = {name for name, decl in scope.variables.items() if decl.scope == "parameter"}
    package_state = {name for name, decl in scope.variables.items() if decl.scope == "package"}

    for finding in uninitialised_uses(
        cfg,
        definitions,
        uses,
        result,
        parameters=parameters,
        external=package_state,
        iteration_cap=cap,
    ):
        findings.append(finding.describe())

    return findings


def _with_guard(edge: IREdge, graphs: dict[str, Cfg]) -> IREdge:
    """Attach the governing condition to an edge that does not already carry one."""
    if edge.guard is not None:
        return edge

    cfg = graphs.get(edge.origin.unit)
    if cfg is None:
        return edge

    for node in cfg.statement_nodes():
        if node.line != edge.origin.line:
            continue
        guards = cfg.guards_reaching(node.id)
        if guards:
            return edge.model_copy(update={"guard": " AND ".join(guards)})
        return edge
    return edge


_CREATE_VIEW = re.compile(
    r"(?is)^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?(?:RECURSIVE\s+)?"
    r"(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"$]+)"
)


def _defined_views(source: str, dialect: str) -> set[tuple[str, int]]:
    """(view, line) for every `CREATE VIEW` in the script - its edges define the view."""
    from lineage.dialects import fold
    from lineage.parsing.sqlscript import split_statements

    found: set[tuple[str, int]] = set()
    try:
        statements = split_statements(source)
    except Exception:
        return found
    for line, text in statements:
        match = _CREATE_VIEW.match(text)
        if match:
            name = ".".join(
                part[1:-1] if part.startswith('"') else part for part in match.group(1).split(".")
            )
            found.add((fold(name, dialect), line))
    return found
