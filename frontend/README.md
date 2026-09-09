# Temporary frontend

**This is scaffolding. It is meant to be deleted, and deleting it is one command.**

Phase 0's scope says there is no UI — the output of the phase is a number, not software —
and nothing here is part of that. It exists for one reason: a scoring table says *how many*
edges were right and never *what they say*. Approving what the analyser produces needs eyes
on what the analyser produces.

## Run it

```
.\.venv\Scripts\python.exe -m pip install -r frontend/requirements-frontend.txt
.\.venv\Scripts\python.exe -m streamlit run frontend/app.py
```

Opens on <http://localhost:8501>. Paste PL/SQL, press **Analyse**, and you get:

| tab | what it shows |
|---|---|
| **Edges** | every lineage fact — band, flow, source, target, transform, guard, origin, mechanism, tier |
| **Boundaries** | where knowledge stopped, by kind and subject, and what it bounds |
| **Refusals** | statements the classifier declined, with the code and the reason |
| **Raw JSON** | the same thing unrendered, for diffing or pasting into an issue |

The sidebar loads any corpus package into the editor, so a known answer is one click away.

## Two things it deliberately does not do

**No score.** Pasted SQL has no ground truth, so precision and recall would be invented, and
an invented number on a screen is worse than no screen at all. Scores come from
`lineage.cli measure` against `ground_truth/`.

**No schema of yours.** Names resolve against the captured dictionary
(`corpus/dictionary.json`), which holds the corpus schema only. Paste SQL referencing your
own tables and they will come back as **dangling references** — that is the analyser
declining to guess at an object it was never given, and it is the correct behaviour rather
than a bug in this page.

## Removing it

```
rm -rf frontend/
```

Nothing under `src/`, `tests/`, `corpus/` or `ground_truth/` imports anything here, and
`streamlit` never entered the pinned requirements. The only residue is the package itself
in the venv:

```
.\.venv\Scripts\python.exe -m pip uninstall -y streamlit
```

Or drop the branch entirely: `git branch -D temporary_frontend`.
