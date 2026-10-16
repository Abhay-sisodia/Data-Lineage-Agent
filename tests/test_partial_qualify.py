"""One bad column reference no longer refuses the whole statement (end-to-end gap 6).

`fr.net_income`, where the CTE `fr` never projects `net_income`, made SQLGlot's `qualify`
raise, and the statement was refused: every column of a 49-column report lost its lineage
over one name. Now the reference is declared `unresolved_identifier` once, and only the
columns that depend on it lose that one source.
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.ir.model import BoundaryKind
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
BOOK = Dictionary.load(ROOT / "stress" / "pg" / "dictionary.json")
POSTGRES = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})

SQL = """WITH fr AS (
    SELECT o.amount AS revenue, o.status AS status
    FROM staging.orders AS o
)
SELECT fr.revenue * 2          AS doubled,
       fr.net_income / fr.revenue AS margin,
       fr.status                AS status
FROM fr;
"""


def _facts(sql: str) -> tuple[set[tuple[str, str]], list[tuple[str, str]], int, int]:
    result = analyse_source(sql, BOOK, POSTGRES, None)
    facts = {(str(e.source).split(":")[-1], str(e.target).split(".")[-1]) for e in result.edges}
    marks = [(b.kind.value, b.subject) for b in result.boundaries]
    return facts, marks, result.statements_analysed, len(result.refusals)


def test_one_bad_reference_keeps_the_rest_of_the_lineage() -> None:
    facts, marks, analysed, refusals = _facts(SQL)
    assert (analysed, refusals) == (1, 0)
    assert ("staging.orders.amount", "doubled") in facts
    assert ("staging.orders.status", "status") in facts
    # The bad column's own row still gets the source it does have.
    assert ("staging.orders.amount", "margin") in facts
    assert marks.count((BoundaryKind.UNRESOLVED_IDENTIFIER.value, "NET_INCOME")) == 1


def test_a_clean_statement_is_unchanged() -> None:
    clean = SQL.replace("fr.net_income / fr.revenue", "fr.revenue / 2")
    facts, marks, analysed, refusals = _facts(clean)
    assert (analysed, refusals) == (1, 0)
    assert not [m for m in marks if m[0] == BoundaryKind.UNRESOLVED_IDENTIFIER.value]
    assert ("staging.orders.amount", "margin") in facts
