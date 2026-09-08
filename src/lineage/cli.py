"""Command line entry point for the analyser and the scoring harness.

Phase 0 has exactly two deliverables, so this CLI stays small on purpose. Commands are
added as the tasks land; anything that looks like product surface does not belong here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from lineage import __version__
from lineage.config import AnalysisConfig

app = typer.Typer(
    help="Phase 0 lineage spike - analyser and scoring harness.",
    no_args_is_help=True,
    add_completion=False,
)

config_app = typer.Typer(help="Inspect the analysis configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")


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
        Path | None, typer.Option("--output", help="Write predicted edges as JSON.")
    ] = None,
) -> None:
    """Extract lineage from one package — set-based and dataflow."""
    from lineage.analysis.procedure import analyse_source
    from lineage.resolution.dictionary import Dictionary

    result = analyse_source(
        package.read_text(encoding="utf-8"),
        Dictionary.load(dictionary),
        AnalysisConfig.load(),
    )

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
        payload = {
            "edges": [edge.model_dump(mode="json") for edge in result.edges],
            "boundaries": result.boundaries,
        }
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
    declared: list[str] = []
    if predictions is not None:
        payload = json.loads(predictions.read_text(encoding="utf-8"))
        rows = payload["edges"] if isinstance(payload, dict) else payload
        predicted = [PredictedEdge.model_validate(row) for row in rows]
        if isinstance(payload, dict):
            declared = payload.get("boundaries", [])

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
