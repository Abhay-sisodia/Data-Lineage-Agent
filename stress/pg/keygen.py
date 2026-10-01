"""Compile a compact `.keyspec` into the YAML answer key the harness scores against.

    .\\.venv\\Scripts\\python.exe stress/pg/keygen.py stress/pg/b1_core_select/t1_breadth.keyspec
    .\\.venv\\Scripts\\python.exe stress/pg/keygen.py --all

**Why this exists.** A stress file of ~300 lines carries 100-200 lineage facts, and the YAML
form spends eight lines on each. Written by hand at that size, the YAML is where key errors
hide: four of the earlier stress findings were mistakes in the key, not in the analyser. A
keyspec states one fact per line, so a statement's whole answer fits on one screen beside
the SQL it describes.

**The keyspec is the authored key; the YAML is generated.** Edit the keyspec and regenerate,
never the reverse. The YAML is still what `GroundTruth.load` reads and what the source hash
pins, so the harness is unchanged - this only changes how the key is written.

Keyspec grammar, one directive per line (`#` starts a comment, copied into the YAML):

    @ <line> <title>                       the origin line for the facts that follow
    value     <src>[, <src>…] -> <tgt>[, <tgt>…] <transform>
    filter    <src>[, <src>…] -> <relation> [post]
    influence <src>[, <src>…] -> <tgt>[, <tgt>…]
    forbid    <flow> <src> -> <tgt> [<transform>] | <reason>
    expect    <boundary kind> <subject> | <reason>
    note      <text>                       attached to the facts on the line above

`src` and `tgt` are `table.column`, written exactly as the analyser names nodes: bare table
name, lower case. A filter's target is a relation name. Lists expand to every
(source, target) pair, which is what a GROUP BY's influence actually is.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TRANSFORMS = {"identity", "derived", "aggregated", "conditional"}


def _names(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def _node(name: str, kind: str = "column") -> dict[str, str]:
    return {"kind": kind, "name": name}


def compile_spec(spec: Path) -> str:
    sql = spec.with_suffix(".sql")
    if not sql.exists():
        raise SystemExit(f"{spec.name}: no SQL file beside it ({sql.name})")

    edges: list[dict[str, object]] = []
    forbidden: list[dict[str, object]] = []
    expected: list[dict[str, str]] = []
    header: list[str] = []
    line: int | None = None
    last: list[dict[str, object]] = []

    for number, raw in enumerate(spec.read_text(encoding="utf-8").splitlines(), start=1):
        text = raw.strip()
        where = f"{spec.name}:{number}"
        if not text:
            continue
        if text.startswith("#"):
            if line is None:
                header.append(text[1:].strip())
            continue

        directive, _, rest = text.partition(" ")
        rest = rest.strip()

        if directive == "@":
            number_text, _, _title = rest.partition(" ")
            line = int(number_text)
            last = []
            continue
        if directive == "note":
            if not last:
                raise SystemExit(f"{where}: note with nothing above it")
            for edge in last:
                edge["note"] = (str(edge.get("note", "")) + " " + rest).strip()
            continue
        if directive == "expect":
            body, _, reason = rest.partition("|")
            kind, _, subject = body.strip().partition(" ")
            expected.append({"kind": kind, "subject": subject.strip(), "reason": reason.strip()})
            continue
        if directive == "forbid":
            body, _, reason = rest.partition("|")
            if not reason.strip():
                raise SystemExit(f"{where}: a forbidden edge must say why it looks right")
            flow, _, arrow = body.strip().partition(" ")
            source, _, target_part = arrow.partition("->")
            target_bits = target_part.split()
            entry: dict[str, object] = {
                "source": _node(source.strip()),
                "target": _node(target_bits[0], "relation" if flow == "filter" else "column"),
                "flow": flow,
                "reason": reason.strip(),
            }
            if len(target_bits) > 1:
                entry["transform"] = target_bits[1]
            forbidden.append(entry)
            continue

        if line is None:
            raise SystemExit(f"{where}: a fact before any '@ <line>'")
        sources_part, _, target_part = rest.partition("->")
        if not target_part:
            raise SystemExit(f"{where}: no '->' in {text!r}")
        sources = _names(sources_part)
        target_words = target_part.strip().rsplit(" ", 1)

        last = []
        if directive == "value":
            if len(target_words) != 2 or target_words[1] not in TRANSFORMS:
                raise SystemExit(f"{where}: value needs a transform, one of {sorted(TRANSFORMS)}")
            targets, transform = _names(target_words[0]), target_words[1]
            pairs = [
                (s, t, "value", transform, "pre-aggregation", "column")
                for s in sources
                for t in targets
            ]
        elif directive == "filter":
            phase = "pre-aggregation"
            relation = target_part.strip()
            if relation.endswith(" post"):
                relation, phase = relation[: -len(" post")].strip(), "post-aggregation"
            pairs = [(s, relation, "filter", "identity", phase, "relation") for s in sources]
        elif directive == "influence":
            targets = _names(target_part)
            pairs = [
                (s, t, "influence", "identity", "pre-aggregation", "column")
                for s in sources
                for t in targets
            ]
        else:
            raise SystemExit(f"{where}: unknown directive {directive!r}")

        for source, target, flow, transform, phase, target_kind in pairs:
            edge: dict[str, object] = {
                "source": _node(source),
                "target": _node(target, target_kind),
                "flow": flow,
                "transform": transform,
            }
            if phase != "pre-aggregation":
                edge["phase"] = phase
            edge.update(
                {"band": 0, "origin": {"unit": "<script>", "line": line}, "evidence": "source_read"}
            )
            edges.append(edge)
            last.append(edge)

    key = {
        "package": f"pg/{spec.parent.name}/{sql.name}",
        "dialect": "postgres",
        "source_sha256": hashlib.sha256(sql.read_bytes()).hexdigest(),
        "labelled_by": (
            "claude (source_read; written before the analyser was run; synthetic schema)"
        ),
        "labelled_at": "2026-10-01",
        "edges": edges,
        "forbidden": forbidden,
        "expected_boundaries": expected,
    }
    banner = [
        f"# GENERATED from {spec.name} by stress/pg/keygen.py - edit the keyspec, not this file.",
        "#",
        *[f"# {line_text}" if line_text else "#" for line_text in header],
        "",
    ]
    return "\n".join(banner) + yaml.safe_dump(key, sort_keys=False, width=100)


def main() -> int:
    specs = (
        sorted(HERE.glob("**/*.keyspec"))
        if "--all" in sys.argv
        else [Path(a) for a in sys.argv[1:]]
    )
    if not specs:
        raise SystemExit("usage: keygen.py <file.keyspec>... | --all")
    for spec in specs:
        # LF on every platform, as .gitattributes pins it.
        spec.with_suffix(".yaml").write_text(
            compile_spec(spec.resolve()), encoding="utf-8", newline="\n"
        )
        print(f"wrote {spec.with_suffix('.yaml').name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
