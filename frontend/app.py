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
from lineage.resolution.infer import infer_dictionary, is_inferred  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kg_pages  # noqa: E402

CORPUS = ROOT / "corpus"
DICTIONARY = CORPUS / "dictionary.json"
STRESS = ROOT / "stress"

# One dictionary per dialect, and the pair must agree or `resolve_dialect` raises. That
# check is not pedantry: analysing Oracle source against a PostgreSQL catalogue resolves
# nothing and reports a large, plausible, entirely artificial set of dangling references -
# a configuration error wearing the costume of a coverage gap.
#
# The Oracle one is a CAPTURE from a live XE. The PostgreSQL one is SYNTHETIC and says so
# in `stress/dictionary_postgres.md`, which is a difference in authority worth knowing
# while reading anything on this screen.
DICTIONARIES = {
    "oracle": DICTIONARY,
    "postgres": STRESS / "pg" / "dictionary.json",
}

# PostgreSQL has several synthetic schemas; the SQL you paste decides which one fits.
AUTO = "auto-detect from the SQL"
PG_DICTIONARIES = {
    "batch tests (core, mart, staging)": STRESS / "pg" / "dictionary.json",
    "final suite (crm, evt, gold, lgx, mart, s2p)": STRESS / "pg" / "final" / "dictionary.json",
    "legacy (public)": STRESS / "dictionary_postgres.json",
}

EXAMPLES = {
    "oracle": """CREATE OR REPLACE PROCEDURE demo_load IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login
      FROM stg_customer
     WHERE last_login IS NOT NULL;
END demo_load;
/""",
    "postgres": """INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
WITH agg AS (
    SELECT o.cust_id                          AS cust_id,
           date_trunc('month', o.order_date)  AS period_month,
           SUM(o.gross_amount)                AS total,
           COUNT(DISTINCT o.order_id)         AS buyers
      FROM stg_orders o
     WHERE o.currency = 'GBP'
     GROUP BY o.cust_id, date_trunc('month', o.order_date)
)
SELECT a.cust_id,
       a.period_month,
       a.total,
       RANK() OVER w
  FROM agg a
 WHERE a.buyers >= 3
WINDOW w AS (PARTITION BY a.cust_id ORDER BY a.total DESC);""",
}


@st.cache_resource
def _load_dictionary(path: str) -> Dictionary:
    return Dictionary.load(Path(path))


def _dictionary(dialect: str) -> Dictionary:
    return _load_dictionary(str(DICTIONARIES[dialect]))


@st.cache_resource
def _config(dialect: str) -> AnalysisConfig:
    return AnalysisConfig.load().model_copy(update={"dialect": dialect})


def _corpus_files(dialect: str) -> dict[str, Path]:
    """Known-good input, one click away, for the dialect in force.

    Pasting is the point of this tool, but the first question anyone asks is "what does it
    do on something I already have an answer for" - and hunting for file paths is friction
    between a question and its answer.

    PostgreSQL has no corpus, so the five keyed stress packages stand in. They are the only
    PostgreSQL source in the project with a hand-written answer key, which makes them the
    only PostgreSQL input where "is this right?" has an answer that is not an opinion.
    """
    if dialect == "postgres":
        return {
            str(path.relative_to(STRESS / "pg")).replace("\\", "/"): path
            for path in sorted((STRESS / "pg").glob("b*/*.sql"))
        }
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


st.set_page_config(page_title="Lineage (temporary)", layout="wide")

# Three views. The two graph views read the lineage knowledge graph in Neo4j (plan C9);
# "Parse SQL" is the original page and needs nothing but the analyser.
VIEW = st.sidebar.radio("View", ["Parse SQL", "Ask the lineage", "Lineage graph"], index=0)
if VIEW != "Parse SQL":
    if VIEW == "Ask the lineage":
        kg_pages.ask_page()
    else:
        kg_pages.graph_page()
    st.stop()

st.title("Parser output")
st.caption(
    "Temporary inspection tool - paste PL/SQL, see the edges, boundaries and refusals the "
    "analyser produces. No score is shown: pasted SQL has no ground truth, so any number "
    "here would be invented."
)

with st.sidebar:
    st.subheader("Dialect")
    dialect = st.radio(
        "Source dialect",
        list(DICTIONARIES),
        index=0,
        help=(
            "Selects BOTH the parser and the dictionary. The two are checked against each "
            "other - analysing one dialect's source against the other's catalogue would "
            "resolve nothing and report it as a coverage gap."
        ),
    )
    st.caption(
        "Oracle: ANTLR frontend, full bands 0-2.\n\n"
        "PostgreSQL: SQL script frontend plus the PL/pgSQL reader (batch B8), bands 0-2."
    )

    if dialect == "postgres":
        available = {k: v for k, v in PG_DICTIONARIES.items() if v.exists()}
        pg_choice = st.selectbox(
            "PostgreSQL dictionary",
            [AUTO, *available],
            index=0,
            help="Auto picks the dictionary that knows the most tables your SQL names.",
        )
        if pg_choice != AUTO:
            DICTIONARIES["postgres"] = available[pg_choice]

    st.subheader("Schema")
    schema_mode = st.radio(
        "Where do table and column names come from?",
        ["bundled dictionary", "infer from the SQL"],
        index=0,
        help=(
            "The bundled dictionary was CAPTURED (Oracle) or hand-written (PostgreSQL). "
            "Inference reads your own SQL instead - useful when the tables are not in "
            "either, and provisional by construction."
        ),
    )
    inferred_schema = st.text_input(
        "Default schema for inference",
        value="public",
        help="Where an unqualified table name binds. Ignored unless inferring.",
        disabled=schema_mode == "bundled dictionary",
    )

    st.subheader("Target")
    result_target = st.text_input(
        "Target table for a query with no INSERT (optional)",
        value="",
        placeholder="e.g. gold_common.dim_organization_hierarchy",
        help=(
            "A standalone SELECT has no target table, so its own projections are the "
            "target. Left blank they land on `<result>`; named, they land on that "
            "relation - which is what lets one model's output be the next model's input."
        ),
    )

    st.subheader("Load a package")
    files = _corpus_files(dialect)
    choice = st.selectbox("File", ["(paste your own)", *files], index=0)
    st.caption(
        f"Names resolve against `{DICTIONARIES[dialect].relative_to(ROOT)}`. Tables it "
        "does not hold are reported as dangling references rather than guessed at - so "
        "pasted SQL against your own schema will show boundaries, and that is the "
        "analyser working."
        + (
            "\n\n**The PostgreSQL dictionary is synthetic**, not a capture - see "
            "`stress/dictionary_postgres.md`."
            if dialect == "postgres"
            else ""
        )
    )

if choice != "(paste your own)":
    default_sql = files[choice].read_text(encoding="utf-8")
else:
    default_sql = EXAMPLES[dialect]

sql = st.text_area(
    "PL/SQL" if dialect == "oracle" else "SQL",
    value=default_sql,
    height=320,
    key=f"{dialect}:{choice}",
)
if dialect == "postgres" and pg_choice == AUTO:
    picked = kg_pages.best_dictionary(sql, available)
    DICTIONARIES["postgres"] = available[picked]
    st.caption(f"Dictionary auto-detected: **{picked}**")

submitted = st.button("Analyse", type="primary")

# The bridge to the graph views: the SQL in the editor, loaded into the lineage graph with
# the same dictionary choice the analysis uses, so "Ask the lineage" can answer about it.
kg_pages.load_panel(
    sql,
    infer_dictionary(sql, dialect=dialect, default_schema=inferred_schema or "public")[0]
    if schema_mode == "infer from the SQL"
    else _dictionary(dialect),
    _config(dialect),
)

if submitted:
    warnings: list[str] = []
    if schema_mode == "infer from the SQL":
        book, warnings = infer_dictionary(
            sql, dialect=dialect, default_schema=inferred_schema or "public"
        )
    else:
        book = _dictionary(dialect)

    if is_inferred(book):
        st.warning(
            "**This schema was INFERRED from the SQL, not captured from a database.** It "
            "holds only the columns the text mentions, so it under-reports and cannot "
            "expand `SELECT *`. Every number below inherits that provenance.",
            icon="⚠",
        )
        for line in warnings:
            st.caption(f"- {line}")

    try:
        result = analyse_source(
            sql, book, _config(dialect), None, result_target=result_target.strip() or None
        )
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
