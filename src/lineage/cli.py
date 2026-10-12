"""Command line entry point for the analyser and the scoring harness.

Phase 0 has exactly two deliverables, so this CLI stays small on purpose. Commands are
added as the tasks land; anything that looks like product surface does not belong here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from lineage import __version__
from lineage.config import AnalysisConfig
from lineage.ir.model import Boundary, BoundaryKind

app = typer.Typer(
    help="Phase 0 lineage spike - analyser and scoring harness.",
    no_args_is_help=True,
    add_completion=False,
)

config_app = typer.Typer(help="Inspect the analysis configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")

graph_app = typer.Typer(
    help="The lineage knowledge graph (Neo4j). Needs requirements-kg.txt.", no_args_is_help=True
)
app.add_typer(graph_app, name="graph")

schema_app = typer.Typer(
    help="Build a data dictionary from a live PostgreSQL or from DDL.", no_args_is_help=True
)
app.add_typer(schema_app, name="schema")


@schema_app.command("pull")
def schema_pull(
    out: Annotated[Path, typer.Option("--out", help="Where to write the dictionary JSON.")],
    dsn: Annotated[
        str | None,
        typer.Option("--dsn", help="postgresql://user:pass@host:port/db; default from PG_*."),
    ] = None,
    database: Annotated[
        str | None, typer.Option("--database", help="Database name, with PG_* settings.")
    ] = None,
    schemas: Annotated[
        str | None, typer.Option("--schemas", help="Comma-separated; default every user schema.")
    ] = None,
    default_schema: Annotated[
        str | None,
        typer.Option(
            "--default-schema",
            help="What an unqualified name means; default the database's search_path.",
        ),
    ] = None,
) -> None:
    """Read the dictionary out of a live PostgreSQL catalogue (read-only)."""
    from lineage.resolution.postgres_connect import PgSettings, pull

    conninfo = dsn or PgSettings.from_env(database).conninfo
    wanted = [s.strip() for s in schemas.split(",") if s.strip()] if schemas else None
    dictionary = pull(conninfo, wanted, default_schema)
    dictionary.save(out)
    _report_dictionary(dictionary, out)


@schema_app.command("from-ddl")
def schema_from_ddl(
    ddl: Annotated[Path, typer.Argument(help="A file of CREATE TABLE statements.")],
    out: Annotated[Path, typer.Option("--out", help="Where to write the dictionary JSON.")],
    default_schema: Annotated[
        str, typer.Option("--default-schema", help="What an unqualified name means.")
    ] = "public",
) -> None:
    """Build the dictionary from DDL (CREATE TABLE statements only)."""
    from lineage.resolution.ddl import from_ddl

    try:
        dictionary = from_ddl(ddl.read_text(encoding="utf-8"), default_schema)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    dictionary.save(out)
    _report_dictionary(dictionary, out)


def _report_dictionary(dictionary: Any, out: Path) -> None:
    schemas = sorted({info.owner for info in dictionary.objects.values()})
    typer.echo(f"wrote {out}")
    typer.echo(f"  schemas     {', '.join(schemas)}")
    typer.echo(f"  objects     {len(dictionary.objects)}")
    typer.echo(f"  relations   {len(dictionary.columns)} with columns")
    typer.echo(f"  views       {len(dictionary.view_text)}")
    typer.echo(f"  fingerprint {dictionary.fingerprint()}")


@graph_app.command("build")
def graph_build(
    folder: Annotated[Path, typer.Argument(help="Folder of .sql files, searched recursively.")],
    dictionary: Annotated[
        Path, typer.Option("--dictionary", help="Data dictionary for these files.")
    ] = Path("stress/pg/dictionary.json"),
    dialect: Annotated[str, typer.Option("--dialect", help="SQL dialect.")] = "postgres",
    keep: Annotated[
        bool,
        typer.Option("--keep", help="Merge into the existing graph instead of replacing it."),
    ] = False,
) -> None:
    """Analyse every SQL file in a folder and load the lineage into Neo4j."""
    from lineage.graph.build import analyse_folder, folder_sources, graph_counts, load
    from lineage.graph.neo4j_conn import Neo4jSettings, connect
    from lineage.resolution.dictionary import Dictionary

    config = AnalysisConfig.load().model_copy(update={"dialect": dialect})
    book = Dictionary.load(dictionary)
    results = analyse_folder(folder, book, config)
    settings = Neo4jSettings.from_env()
    with connect(settings) as driver:
        report = load(
            driver,
            results,
            database=settings.database,
            replace=not keep,
            sources=folder_sources(folder),
            default_schema=book.default_schema,
        )
        stored = graph_counts(driver, database=settings.database)
    typer.echo(f"snapshot {report.snapshot}: {report.files} files")
    for name, count in stored.items():
        typer.echo(f"  {name:<12} {count}")


@app.command("ask")
def ask(
    question: Annotated[str, typer.Argument(help="A lineage question.")],
    no_cache: Annotated[
        bool, typer.Option("--no-cache", help="Ask the model even if a cached answer exists.")
    ] = False,
) -> None:
    """Answer one lineage question from the graph, through the agent harness.

    Answers are cached by question + graph snapshot + model + prompt, and every answer's
    trace is kept in .lineage_cache/ (replay it with `lineage replay`).
    """
    import asyncio

    from lineage.agent.harness import Harness
    from lineage.agent.store import TraceStore
    from lineage.graph.neo4j_conn import Neo4jSettings, connect
    from lineage.graph.tools import GraphTools

    settings = Neo4jSettings.from_env()
    with connect(settings) as driver:
        harness = Harness(GraphTools(driver, settings), store=TraceStore(), read_cache=not no_cache)
        result = asyncio.run(harness.ask(question))
    typer.echo(result.answer.text)
    for label, ids in (
        ("edges", result.answer.cited_edges),
        ("nodes", result.answer.cited_nodes),
        ("unknowns", result.answer.unknowns),
        ("candidates", result.answer.candidates),
    ):
        if ids:
            typer.echo(f"  {label}: {', '.join(ids)}")
    typer.echo(
        f"  [{result.stage}; {len(result.tool_calls)} tool calls; {'; '.join(result.notes)}]"
    )
    if result.trace_path:
        typer.echo(f"  trace: {result.trace_path}")


@app.command("replay")
def replay_trace(
    trace: Annotated[Path, typer.Argument(help="A trace JSON from .lineage_cache/.")],
) -> None:
    """Re-run a trace's tool calls against the current graph; report any result that differs."""
    from lineage.agent.store import Trace, replay
    from lineage.graph.neo4j_conn import Neo4jSettings, connect
    from lineage.graph.tools import GraphTools

    recorded = Trace.from_json(trace.read_text(encoding="utf-8"))
    settings = Neo4jSettings.from_env()
    with connect(settings) as driver:
        report = replay(recorded, GraphTools(driver, settings))
    typer.echo(f"question: {recorded.question}")
    typer.echo(f"snapshot then {report.snapshot_then} / now {report.snapshot_now}")
    for line in report.mismatches:
        typer.echo(f"  MISMATCH {line}")
    verdict = "reproduced" if report.reproduced else f"{len(report.mismatches)} differ"
    typer.echo(f"{report.calls} tool calls replayed: {verdict}")
    raise typer.Exit(0 if report.reproduced else 1)


@app.command("eval")
def eval_answers(
    answers: Annotated[
        Path | None, typer.Option("--answers", help="Score an answers JSON (a list).")
    ] = None,
    run: Annotated[
        Path | None,
        typer.Option(
            "--run",
            help="Ask every golden question through the agent; write answers and traces "
            "into this folder, then score.",
        ),
    ] = None,
    concurrency: Annotated[int, typer.Option("--concurrency")] = 4,
    questions: Annotated[Path, typer.Option("--questions", help="Golden questions YAML.")] = Path(
        "evals/golden_questions.yaml"
    ),
) -> None:
    """Score answers to the golden questions against the graph, by what they cite."""
    from lineage.evals.golden import Answer, load_questions, score_all
    from lineage.graph.neo4j_conn import Neo4jSettings, connect
    from lineage.graph.tools import GraphTools

    golden = load_questions(questions)
    settings = Neo4jSettings.from_env()
    with connect(settings) as driver:
        tools = GraphTools(driver, settings)
        if run is not None:
            missing = _missing_golden_nodes(tools, golden)
            if missing:  # e.g. the tests replaced the graph: a run would score nothing real
                typer.echo(
                    f"the graph lacks {len(missing)} node(s) the golden questions expect, "
                    f"e.g. {missing[:3]} - rebuild it (lineage graph build ...) first"
                )
                raise typer.Exit(2)
            given = _run_agent(tools, golden, run, concurrency)
        elif answers is not None:
            given = [Answer.model_validate(r) for r in json.loads(answers.read_text("utf-8"))]
        else:
            raise typer.BadParameter("give --answers or --run")
        verdicts = score_all(golden, given, tools.resolve_edges)
    if run is not None:
        (run / "verdicts.json").write_text(
            json.dumps([v.__dict__ for v in verdicts], indent=2), encoding="utf-8"
        )
    for verdict in verdicts:
        mark = "PASS" if verdict.correct else "FAIL"
        typer.echo(f"{mark}  {verdict.question_id:<4} {'; '.join(verdict.reasons)}")
    wrong = sum(not v.correct for v in verdicts)
    typer.echo(f"\n{len(verdicts) - wrong}/{len(verdicts)} correct, {wrong} wrong")
    raise typer.Exit(1 if wrong else 0)


def _missing_golden_nodes(tools: Any, golden: list[Any]) -> list[str]:
    """Node ids the golden expectations name that the current graph does not hold."""
    wanted = sorted(
        {n for q in golden for f in q.expected.facts for n in f[1:]}
        | {n for q in golden for n in q.expected.candidates}
    )
    rows = tools._read(
        "UNWIND $ids AS id OPTIONAL MATCH (n {id: id}) WITH id, n WHERE n IS NULL RETURN id",
        ids=wanted,
    )
    return [row["id"] for row in rows]


def _run_agent(tools: Any, golden: list[Any], out: Path, concurrency: int) -> list[Any]:
    """Every golden question through the harness, `concurrency` at a time; traces kept."""
    import asyncio
    import time

    from lineage.agent.harness import Harness

    out.mkdir(parents=True, exist_ok=True)
    harness = Harness(tools)
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(q: Any) -> Any:
        async with gate:
            started = time.monotonic()
            try:
                result = await harness.ask(q.question, q.id)
            except Exception as error:  # a crash is a wrong answer, recorded
                typer.echo(f"  {q.id} crashed: {error}")
                return None
            seconds = round(time.monotonic() - started, 1)
            trace = {
                "question": q.question,
                "seconds": seconds,
                "stage": result.stage,
                "cost_usd": result.cost_usd,
                "turns": result.turns,
                "notes": result.notes,
                "answer": result.answer.model_dump(),
                "tool_calls": result.tool_calls,
            }
            (out / f"{q.id}.json").write_text(
                json.dumps(trace, indent=2, default=str), encoding="utf-8"
            )
            typer.echo(
                f"  {q.id} done in {seconds}s ({result.stage}, {len(result.tool_calls)} calls)"
            )
            return result.answer

    async def main() -> list[Any]:
        return [a for a in await asyncio.gather(*(one(q) for q in golden)) if a is not None]

    answers = asyncio.run(main())
    (out / "answers.json").write_text(
        json.dumps([a.model_dump() for a in answers], indent=2), encoding="utf-8"
    )
    return answers


@graph_app.command("trace")
def graph_trace(
    node: Annotated[str, typer.Argument(help="Column (or table) name; partial names allowed.")],
    depth: Annotated[int, typer.Option("--depth", help="Hops to walk.")] = 1,
    down: Annotated[
        bool, typer.Option("--down", help="Downstream impact instead of upstream lineage.")
    ] = False,
) -> None:
    """Upstream lineage (or downstream impact) of one column, from the graph."""
    from lineage.graph.neo4j_conn import Neo4jSettings, connect
    from lineage.graph.tools import GraphTools

    settings = Neo4jSettings.from_env()
    with connect(settings) as driver:
        tools = GraphTools(driver, settings)
        matches = tools.find(node)
        if len(matches) != 1:
            names = ", ".join(m["id"] for m in matches) or "nothing"
            typer.echo(f"'{node}' matches {names} - be more specific")
            raise typer.Exit(1)
        resolved = matches[0]["id"]
        edges = tools.downstream(resolved, depth) if down else tools.upstream(resolved, depth)
    typer.echo(f"{'impact of' if down else 'lineage of'} {resolved}: {len(edges)} edges")
    for edge in edges:
        detail = edge["transform"] if edge["type"] == "FEEDS" else edge["phase"] or ""
        typer.echo(
            f"  [{edge['depth']}] {edge['type']:<10} {edge['source']} -> {edge['target']}"
            f"  {detail}  ({edge['file']}:{edge['line']})"
        )


@graph_app.command("impact")
def graph_impact(
    node: Annotated[str, typer.Argument(help="Column or table name; partial names allowed.")],
    change: Annotated[
        str, typer.Option("--change", help="drop, rename or type (a type change).")
    ] = "drop",
    depth: Annotated[int, typer.Option("--depth", help="Hops to walk downstream.")] = 15,
    fmt: Annotated[str, typer.Option("--format", help="md, csv or json.")] = "md",
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the report here instead of stdout.")
    ] = None,
) -> None:
    """What breaks if a column or table changes: affected columns, statements to edit, unknowns."""
    from lineage.graph.impact import CHANGES, to_csv, to_markdown
    from lineage.graph.neo4j_conn import Neo4jSettings, connect
    from lineage.graph.tools import GraphTools

    if change not in CHANGES:
        raise typer.BadParameter(f"--change must be one of {', '.join(CHANGES)}")
    if fmt not in ("md", "csv", "json"):
        raise typer.BadParameter("--format must be md, csv or json")
    settings = Neo4jSettings.from_env()
    with connect(settings) as driver:
        tools = GraphTools(driver, settings)
        matches = tools.find(node)
        if len(matches) > 1:
            names = ", ".join(m["id"] for m in matches)
            typer.echo(f"'{node}' matches {names} - be more specific")
            raise typer.Exit(1)
        # No match can still be a real column: one in no lineage edge (a join key).
        report = tools.impact(matches[0]["id"] if matches else node, change, depth)
        if not report["found"]:
            typer.echo(f"'{node}' is not in the lineage graph - give schema.table.column")
            raise typer.Exit(1)
    if fmt == "md":
        rendered = to_markdown(report)
    elif fmt == "csv":
        rendered = to_csv(report)
    else:
        rendered = json.dumps(report, indent=2) + "\n"
    if out is None:
        typer.echo(rendered, nl=False)
        return
    out.write_text(rendered, encoding="utf-8", newline="\n")
    summary = report.get("summary", {})
    typer.echo(
        f"wrote {out}: {summary.get('affected', 0)} affected, "
        f"{summary.get('statements_to_edit', 0)} statements to edit, "
        f"{summary.get('unknowns', 0)} unknowns"
    )


@app.command()
def version() -> None:
    """Print the analyser version."""
    typer.echo(__version__)


@config_app.command("show")
def config_show(
    path: Annotated[
        Path | None,
        typer.Option("--path", help="Configuration file. Defaults to config/analysis.yaml."),
    ] = None,
) -> None:
    """Print the limits in force and the configuration fingerprint.

    This is the same content that goes into a coverage statement. If a setting can change
    an answer and does not appear here, that is a defect.
    """
    config = AnalysisConfig.load(path)
    payload = {
        "declared_limits": config.declared_limits(),
        "config_fingerprint": config.fingerprint(),
    }
    typer.echo(json.dumps(payload, indent=2))


@app.command()
def analyse(
    package: Annotated[Path, typer.Argument(help="Corpus .sql file to analyse.")],
    dictionary: Annotated[
        Path, typer.Option("--dictionary", help="Captured data dictionary.")
    ] = Path("corpus/dictionary.json"),
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            help="Write the whole result (lineage-result/1 JSON: edges, "
            "boundaries, refusals, statement counts, provenance).",
        ),
    ] = None,
    dialect: Annotated[
        str | None,
        typer.Option("--dialect", help="SQL dialect. Defaults to the configured one."),
    ] = None,
) -> None:
    """Extract lineage from one package — set-based and dataflow."""
    from lineage.analysis.procedure import analyse_source
    from lineage.export import to_document
    from lineage.resolution.dictionary import Dictionary

    config = AnalysisConfig.load()
    if dialect is not None:
        config = config.model_copy(update={"dialect": dialect})
    book = Dictionary.load(dictionary)
    source = package.read_text(encoding="utf-8")
    result = analyse_source(source, book, config)

    for edge in result.edges:
        guard = f"  [{edge.guard}]" if edge.guard else ""
        typer.echo(
            f"b{edge.band} {edge.source!s:<40} -> {edge.target!s:<34} "
            f"{edge.flow.value}/{edge.transform.value}{guard}"
        )
    for refusal in result.refusals:
        typer.echo(f"REFUSED L{refusal.line} {refusal.kind}: {refusal.reason}")
    for boundary in result.boundaries:
        typer.echo(f"DECLARED  {boundary}")

    coverage = result.parse_coverage
    typer.echo(
        f"\n{len(result.edges)} edges | "
        f"parse coverage {'n/a' if coverage is None else f'{coverage * 100:.1f}%'} "
        f"({result.statements_analysed}/{result.statements_seen} statements)"
    )

    if output is not None:
        document = to_document(
            result,
            source_path=package,
            source_text=source,
            dialect=config.dialect,
            config_fingerprint=config.fingerprint(),
            dictionary_fingerprint=book.fingerprint(),
        )
        output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        typer.echo(f"wrote {output}")


@app.command()
def score(
    truth: Annotated[Path, typer.Argument(help="Ground-truth YAML from ground_truth/.")],
    predictions: Annotated[
        Path | None,
        typer.Option(
            "--predictions",
            help=(
                "Analyser output as JSON (a list of predicted edges). Omit to score an "
                "empty prediction set, which establishes the zero baseline."
            ),
        ),
    ] = None,
    corpus: Annotated[
        Path, typer.Option("--corpus", help="Corpus root, for the source-hash check.")
    ] = Path("corpus"),
) -> None:
    """Score analyser output against ground truth, per band and per flow.

    There is no blended headline figure. The gate is band-1 value-flow precision and the
    report says so explicitly.
    """
    from lineage.harness.labels import GroundTruth
    from lineage.harness.scoring import PredictedEdge, render
    from lineage.harness.scoring import score as score_edges

    ground_truth = GroundTruth.load(truth)
    # Refuse to score against a key that no longer matches the code it describes.
    ground_truth.verify_against(corpus)

    predicted: list[PredictedEdge] = []
    declared: list[Boundary] = []
    if predictions is not None:
        payload = json.loads(predictions.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "format" in payload:
            # A lineage-result document (`analyse --output`): boundaries arrive structured,
            # so they can satisfy a structured expectation.
            from lineage.export import from_document

            exported = from_document(payload)
            typer.echo(render(score_edges(ground_truth, exported.edges, exported.boundaries)))
            return
        rows = payload["edges"] if isinstance(payload, dict) else payload
        predicted = [PredictedEdge.model_validate(row) for row in rows]
        if isinstance(payload, dict):
            # Prose from a JSON prediction file carries no kind or subject, so it
            # cannot satisfy a structured expectation. Reported as undeclared
            # rather than assumed satisfied - see ADR-0001 amendment 1d.
            declared = [
                Boundary(
                    kind=BoundaryKind.SOURCE_UNAVAILABLE,
                    subject=str(item)[:60] or "UNSPECIFIED",
                    detail=str(item),
                )
                for item in payload.get("boundaries", [])
            ]

    typer.echo(render(score_edges(ground_truth, predicted, declared)))


@app.command()
def measure(
    corpus: Annotated[Path, typer.Option("--corpus", help="Corpus root.")] = Path("corpus"),
    ground_truth: Annotated[
        Path, typer.Option("--ground-truth", help="Directory of label sets.")
    ] = Path("ground_truth"),
    dictionary: Annotated[
        Path, typer.Option("--dictionary", help="Captured data dictionary.")
    ] = Path("corpus/dictionary.json"),
    witness: Annotated[
        Path | None,
        typer.Option(
            "--witness",
            help="Execution witness (scripts/capture_witness.py). Without one, every edge "
            "reports no execution evidence rather than reporting that it ran.",
        ),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", help="Write the measurement as JSON.")
    ] = None,
) -> None:
    """Score every labelled package and record the number with its provenance.

    Phase 0's output is a number, not software — and a number without the inputs that
    produced it is not defensible six months later.
    """
    from lineage.evidence.witness import ExecutionWitness
    from lineage.harness.measure import as_json, render, run_measurement
    from lineage.resolution.dictionary import Dictionary

    measurement = run_measurement(
        corpus,
        ground_truth,
        Dictionary.load(dictionary),
        AnalysisConfig.load(),
        ExecutionWitness.load(witness) if witness else None,
    )
    typer.echo(render(measurement))

    if output is not None:
        output.write_text(as_json(measurement), encoding="utf-8")
        typer.echo(f"wrote {output}")


if __name__ == "__main__":
    app()
