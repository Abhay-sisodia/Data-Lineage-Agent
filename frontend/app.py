"""TEMPORARY inspection UI. Paste PL/SQL, see what the analyser makes of it.

**This is scaffolding and is meant to be deleted.** Phase 0's scope is explicit that there
is no UI - the output of the phase is a number, not software - and nothing here is part of
that. It exists for one reason: reading a scoring table tells you *how many* edges were
right, and never *what they say*. Approving the analyser's actual output needs eyes on the
actual output.

Deliberately does not:

* touch anything under ``src/lineage`` - it imports the analyser and renders it, so
  deleting ``frontend/`` removes every trace;
* appear in the pinned, hashed requirements - see ``requirements-frontend.txt``;
* compute or display a score. There is no ground truth for pasted SQL, so any number here
  would be invented, and an invented number on a screen is worse than no screen.

Run it with::

    .\\.venv\\Scripts\\python.exe -m streamlit run frontend/app.py
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import streamlit as st

# The app runs from the repo root; the analyser lives under src/.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lineage.analysis.procedure import analyse_source  # noqa: E402
from lineage.config import AnalysisConfig  # noqa: E402
from lineage.resolution.dictionary import Dictionary  # noqa: E402

CORPUS = ROOT / "corpus"
DICTIONARY = CORPUS / "dictionary.json"

EXAMPLE = """CREATE OR REPLACE PROCEDURE demo_load IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login
      FROM stg_customer
     WHERE last_login IS NOT NULL;
END demo_load;
/"""


@st.cache_resource
def _dictionary() -> Dictionary:
    return Dictionary.load(DICTIONARY)


@st.cache_resource
def _config() -> AnalysisConfig:
    return AnalysisConfig.load()


def _corpus_files() -> dict[str, Path]:
    """Every corpus package, so known-good input is one click away.

    Pasting is the point of this tool, but the first question anyone asks is "what does it
    do on something I already have an answer for" - and hunting for file paths is friction
    between a question and its answer.
    """
    return {
        str(path.relative_to(CORPUS)).replace("\\", "/"): path
        for path in sorted(CORPUS.rglob("*.sql"))
    }


def _edge_rows(edges) -> list[dict[str, object]]:
    return [
        {
            "band": edge.band,
            "flow": edge.flow.value,
            "source": str(edge.source),
            "target": str(edge.target),
            "transform": edge.transform.value,
            "guard": edge.guard or "",
            "origin": str(edge.origin),
            "mechanism": edge.mechanism.value,
            "tier": edge.tier.value,
            "unexercised": {True: "yes", False: "no", None: "not measured"}[edge.unexercised],
        }
        for edge in edges
    ]


st.set_page_config(page_title="Parser output (temporary)", layout="wide")
st.title("Parser output")
st.caption(
    "Temporary inspection tool - paste PL/SQL, see the edges, boundaries and refusals the "
    "analyser produces. No score is shown: pasted SQL has no ground truth, so any number "
    "here would be invented."
)

with st.sidebar:
    st.subheader("Load a corpus package")
    files = _corpus_files()
    choice = st.selectbox("File", ["(paste your own)", *files], index=0)
    st.caption(
        f"Names resolve against the captured dictionary "
        f"(`{DICTIONARY.relative_to(ROOT)}`). Tables it does not hold are reported as "
        "dangling references rather than guessed at - so pasted SQL against your own "
        "schema will show boundaries, and that is the analyser working."
    )

if choice != "(paste your own)":
    default_sql = files[choice].read_text(encoding="utf-8")
else:
    default_sql = EXAMPLE

sql = st.text_area("PL/SQL", value=default_sql, height=320, key=choice)
submitted = st.button("Analyse", type="primary")

if submitted:
    try:
        result = analyse_source(sql, _dictionary(), _config(), None)
    except Exception:
        st.error("The analyser raised. Full traceback below - this is a defect, not a refusal.")
        st.code(traceback.format_exc(), language="text")
        st.stop()

    seen = result.statements_seen
    analysed = result.statements_analysed
    coverage = result.parse_coverage

    top = st.columns(4)
    top[0].metric("Edges", len(result.edges))
    top[1].metric("Statements seen", seen)
    top[2].metric("Analysed", analysed)
    top[3].metric("Parse coverage", "n/a" if coverage is None else f"{coverage * 100:.1f}%")

    edges_tab, boundaries_tab, refusals_tab, json_tab = st.tabs(
        [
            f"Edges ({len(result.edges)})",
            f"Boundaries ({len(result.boundaries)})",
            f"Refusals ({len(result.refusals)})",
            "Raw JSON",
        ]
    )

    with edges_tab:
        if result.edges:
            st.dataframe(_edge_rows(result.edges), use_container_width=True, hide_index=True)
            st.caption(
                "`band` is the hardest construct on the path, `transform` is what happened "
                "to the value, and a `filter` edge means the source decided WHICH rows - "
                "not what value came back."
            )
        else:
            st.info(
                "No edges. That is a real answer, not a failure - check the boundaries and "
                "refusals tabs for what the analyser declined to guess at."
            )

    with boundaries_tab:
        if result.boundaries:
            st.dataframe(
                [
                    {
                        "kind": b.kind.value,
                        "subject": b.subject,
                        "bounds": str(b.attaches_to) if b.attaches_to else "",
                        "detail": b.detail,
                    }
                    for b in result.boundaries
                ],
                use_container_width=True,
                hide_index=True,
            )
            st.caption("Where knowledge stops, declared and counted rather than guessed.")
        else:
            st.info("No boundaries declared.")

    with refusals_tab:
        if result.refusals:
            st.dataframe(
                [
                    {
                        "unit": r.unit,
                        "line": r.line,
                        "code": r.code.value,
                        "reason": r.reason,
                    }
                    for r in result.refusals
                ],
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                "A refused statement produces NO edges, and each one costs exactly one "
                "statement of parse coverage. Refusing is never free."
            )
        else:
            st.info("Nothing refused.")

    with json_tab:
        st.code(
            json.dumps(
                {
                    "edges": [json.loads(e.model_dump_json()) for e in result.edges],
                    "boundaries": [json.loads(b.model_dump_json()) for b in result.boundaries],
                    "refusals": [
                        {"unit": r.unit, "line": r.line, "code": r.code.value, "reason": r.reason}
                        for r in result.refusals
                    ],
                    "statements_seen": seen,
                    "statements_analysed": analysed,
                },
                indent=2,
            ),
            language="json",
        )
