"""Generate the ANTLR PL/SQL parser.

Run from the repository root:

    .\\.venv\\Scripts\\python.exe scripts\\generate_parser.py

Generated sources land in ``src/lineage/parsing/generated/`` and are NOT committed —
they are large, mechanical, and fully reproducible from the grammar. The grammar files
themselves ARE committed, so the build does not depend on GitHub being reachable.

Two things here are deliberate rather than incidental:

* The tool jar is verified against the SHA-1 that Maven Central publishes alongside it,
  and its SHA-256 is recorded in ``tools/antlr.lock.json`` so later runs detect a
  changed jar. We pin and verify Python dependencies; the code generator that produces
  a third of the analyser deserves the same treatment.
* ANTLR's Python target emits flat imports for ``superClass`` bases
  (``from PlSqlLexerBase import ...``), which only work if the output directory is on
  sys.path. Rather than mutating sys.path at import time, generation rewrites those to
  relative imports so the output is an ordinary package.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ANTLR_VERSION = "4.13.2"
MAVEN_BASE = (
    "https://repo1.maven.org/maven2/org/antlr/antlr4/"
    f"{ANTLR_VERSION}/antlr4-{ANTLR_VERSION}-complete.jar"
)

ROOT = Path(__file__).resolve().parent.parent
GRAMMAR_DIR = ROOT / "grammars" / "plsql"
TOOLS_DIR = ROOT / "tools"
JAR_PATH = TOOLS_DIR / f"antlr-{ANTLR_VERSION}-complete.jar"
LOCK_PATH = TOOLS_DIR / "antlr.lock.json"
OUT_DIR = ROOT / "src" / "lineage" / "parsing" / "generated"

GRAMMARS = ["PlSqlLexer.g4", "PlSqlParser.g4"]
BASE_CLASSES = ["PlSqlLexerBase.py", "PlSqlParserBase.py"]

# `from PlSqlFoo import ...` -> `from .PlSqlFoo import ...`, preserving indentation.
FLAT_IMPORT = re.compile(r"^(\s*)from (PlSql[A-Za-z0-9_]*) import ", re.MULTILINE)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_java() -> None:
    if shutil.which("java") is None:
        raise SystemExit("java not found on PATH. The ANTLR tool needs a JRE (17+).")


def ensure_jar() -> None:
    """Download the ANTLR tool jar if absent, and verify it every run."""
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)

    if not JAR_PATH.exists():
        print(f"downloading {MAVEN_BASE}")
        with urllib.request.urlopen(MAVEN_BASE) as response:
            JAR_PATH.write_bytes(response.read())

        print("verifying against Maven Central's published SHA-1")
        with urllib.request.urlopen(MAVEN_BASE + ".sha1") as response:
            expected_sha1 = response.read().decode("ascii").split()[0].strip().lower()
        actual_sha1 = _sha1(JAR_PATH)
        if actual_sha1 != expected_sha1:
            JAR_PATH.unlink()
            raise SystemExit(
                f"jar SHA-1 mismatch: expected {expected_sha1}, got {actual_sha1}. "
                "Download discarded."
            )

    actual_sha256 = _sha256(JAR_PATH)
    if LOCK_PATH.exists():
        recorded = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
        if recorded.get("sha256") != actual_sha256:
            raise SystemExit(
                f"ANTLR jar does not match {LOCK_PATH.name}.\n"
                f"  recorded: {recorded.get('sha256')}\n"
                f"  actual:   {actual_sha256}\n"
                "Delete the jar to re-download, or update the lock deliberately."
            )
    else:
        LOCK_PATH.write_text(
            json.dumps(
                {"version": ANTLR_VERSION, "source": MAVEN_BASE, "sha256": actual_sha256},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"recorded jar sha256 in {LOCK_PATH.name}")


def generate() -> None:
    """Run the ANTLR tool over the grammars."""
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    command = [
        "java",
        "-jar",
        str(JAR_PATH),
        "-Dlanguage=Python3",
        "-visitor",
        "-o",
        str(OUT_DIR),
        *[str(GRAMMAR_DIR / g) for g in GRAMMARS],
    ]
    print("generating parser (this takes a couple of minutes for a grammar this size)")
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"ANTLR generation failed with exit code {result.returncode}")
    if result.stderr.strip():
        print("ANTLR warnings:\n" + result.stderr.strip())

    # ANTLR mirrors the input path under -o; flatten anything it nested.
    for stray in list(OUT_DIR.rglob("*.py")) + list(OUT_DIR.rglob("*.interp")):
        if stray.parent != OUT_DIR:
            stray.replace(OUT_DIR / stray.name)
    for leftover in sorted(OUT_DIR.rglob("*"), reverse=True):
        if leftover.is_dir() and not any(leftover.iterdir()):
            leftover.rmdir()


def vendor_base_classes() -> None:
    """Copy the grammar's Python base classes next to the generated sources."""
    for name in BASE_CLASSES:
        shutil.copy2(GRAMMAR_DIR / name, OUT_DIR / name)


def rewrite_imports() -> None:
    """Make the generated package self-contained.

    ANTLR emits ``from PlSqlLexerBase import PlSqlLexerBase``, which resolves only if the
    output directory is on sys.path. Rewriting to a relative import makes this an
    ordinary package and keeps sys.path manipulation out of the runtime.
    """
    for path in OUT_DIR.glob("*.py"):
        original = path.read_text(encoding="utf-8")
        rewritten = FLAT_IMPORT.sub(r"\1from .\2 import ", original)
        if rewritten != original:
            path.write_text(rewritten, encoding="utf-8")


def pythonise_actions() -> None:
    """Translate the grammar's Java-syntax actions and predicates into Python.

    The upstream grammar is maintained against the Java target: its embedded actions use
    ``this.``, ``&&`` and ``||``, which ANTLR copies verbatim into whatever target it is
    generating. The Python output therefore does not compile as written. grammars-v4
    handles this in its own CI with a transformation step; this is ours.

    The replacement is deliberately narrow. ``||`` is also PL/SQL's concatenation
    operator and appears in the generated token tables as a literal name, so the boolean
    operators are only rewritten on lines that contain ``this.`` — which, in this
    grammar, is exactly the set of lines carrying an embedded action or predicate.
    """
    java_this = re.compile(r"\bthis\.")
    touched = 0

    for path in OUT_DIR.glob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        changed = False
        for index, line in enumerate(lines):
            if "this." not in line:
                continue
            rewritten = line.replace("&&", " and ").replace("||", " or ")
            rewritten = java_this.sub("self.", rewritten)
            if rewritten != line:
                lines[index] = rewritten
                changed = True
        if changed:
            path.write_text("".join(lines), encoding="utf-8")
            touched += 1

    print(f"translated Java-syntax actions in {touched} module(s)")


def verify_compiles() -> None:
    """Byte-compile every generated module.

    Generation that produces syntactically invalid code must fail here, loudly, rather
    than at import time in the middle of an analysis run.
    """
    import py_compile

    for path in sorted(OUT_DIR.glob("*.py")):
        try:
            py_compile.compile(str(path), doraise=True, quiet=1)
        except py_compile.PyCompileError as exc:
            raise SystemExit(f"generated module does not compile: {path.name}\n{exc}") from exc
    print("all generated modules compile")


def write_init() -> None:
    (OUT_DIR / "__init__.py").write_text(
        '"""ANTLR-generated PL/SQL lexer and parser.\n\n'
        "Generated by scripts/generate_parser.py from grammars/plsql/. Do not edit by\n"
        "hand and do not commit - regenerate instead.\n"
        '"""\n',
        encoding="utf-8",
    )


def main() -> None:
    ensure_java()
    ensure_jar()
    generate()
    vendor_base_classes()
    rewrite_imports()
    pythonise_actions()
    write_init()
    verify_compiles()

    produced = sorted(p.name for p in OUT_DIR.glob("*.py"))
    print(f"\ngenerated {len(produced)} modules in {OUT_DIR.relative_to(ROOT)}:")
    for name in produced:
        print(f"  {name}")


if __name__ == "__main__":
    main()
