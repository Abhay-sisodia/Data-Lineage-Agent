# Lineage Spike — Phase 0

Column-level lineage through procedural Oracle PL/SQL, plus a scoring harness that says
how good it is.

**The output of Phase 0 is a number, not software.** Two things get built: an analyser and
a scoring harness. No database, no API, no UI, no packaging, no agents. See
[trackers/Phase0_tracker.md](trackers/Phase0_tracker.md) for the task list and the gates,
and [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) for why any of this exists.

The question the phase answers: *can column-level lineage through real procedural PL/SQL
reach ≥ 95% precision against hand-labelled ground truth?* Everything downstream is
contingent on that number.

---

## Setup

Requires Python 3.11, Java 17+ (for the ANTLR tool jar), and Docker (for Oracle XE).

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip pip-tools
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
```

`--no-deps` on the editable install is deliberate: dependencies come from the hashed
requirements files and nowhere else, so an install can never silently resolve a different
version than the one that was pinned.

### Dependencies

`requirements.in` / `requirements-dev.in` hold loose constraints. The authoritative sets
are the compiled `requirements.txt` and `requirements-dev.txt` — fully pinned, with
hashes. **Install only from the compiled files.**

To change a dependency, edit the `.in` file and recompile:

```powershell
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --strip-extras --output-file=requirements.txt requirements.in
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --strip-extras --output-file=requirements-dev.txt requirements-dev.in
```

Both compiled files are committed. This is not ceremony: the product ships as installed
software into a customer VPC — eventually as an air-gapped bundle with signed images and
no network access at install time — so a build that cannot be reproduced exactly is a
build that cannot ship.

---

## Layout

```
src/lineage/
  config.py       analysis budgets and limits - config is data, and every limit
                  that can change an answer is reported alongside that answer
  parsing/        ANTLR (program shape) + SQLGlot (statement semantics)
  resolution/     data-dictionary name resolution: synonyms, views, schema context
  analysis/       CFG, def-use, guards, interprocedural summaries
  ir/             the v0 intermediate representation
  harness/        scoring: precision, recall, coverage - per band, never blended
corpus/           test packages, by band
ground_truth/     hand-labelled edges - frozen and hashed
tests/            unit tests + the adversarial silent-failure suite
docs/decisions/   ADRs
trackers/         phase trackers
```

## Running

```powershell
.\.venv\Scripts\python.exe -m pytest              # all tests
.\.venv\Scripts\python.exe -m pytest -m "not requires_oracle"   # skip Oracle-dependent
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
```

## Two rules that hold for the whole phase

- **Score per band, always.** A blended number hides that band 0 is perfect and band 1 is
  broken, which is the only thing worth learning here.
- **Never emit a guessed edge.** A construct the analyser cannot resolve is detected and
  declared. Abstention is a deliverable, not a failure mode.
