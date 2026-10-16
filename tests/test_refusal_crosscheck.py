"""The contradiction check must not report clean when it could not run.

Stress finding S2-10. `violations()` asks "is there an edge whose (unit, line) falls inside
this refusal's span?" - and for a trigger unit the two sides are numbered in different
spaces, so the answer is structurally "no" whatever the truth is:

  * a trigger body is analysed out of the DICTIONARY, wrapped in a synthetic
    `CREATE OR REPLACE PROCEDURE`, so its edges carry lines 3, 4, 7;
  * a refusal on the same statement comes from the FILE pass and carries line 39.

`edges from refused` is rendered as a kill-criterion row and the phase-0 verdict leans on
it. A guard that passes without looking is worse than one that fails.

There is no such refusal in the corpus today - the one that raised the finding, `s6`'s
`INSERT ... VALUES`, was removed by D-1 - so this file reconstructs the condition. That is
the point: the defect is latent, and a latent defect with no test is one refactor away from
being live again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")

# A file that BOTH writes tmp_recent - so trg_recent_audit's edges are inherited from the
# dictionary at body-relative lines - AND redefines that trigger with a body band 0 refuses,
# which the file pass attributes to the trigger unit at a file line. Exactly the S2-10 shape.
TRIGGER_REFUSAL = """CREATE OR REPLACE PROCEDURE writes_tmp_recent IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT s.cust_id, s.last_login FROM stg_customer s;
END writes_tmp_recent;
/

CREATE OR REPLACE TRIGGER trg_recent_audit
AFTER INSERT ON tmp_recent
FOR EACH ROW
BEGIN
    INSERT INTO dim_customer_hier (cust_id, parent_cust_id)
    SELECT h.cust_id, h.parent_cust_id
      FROM dim_customer_hier h
     START WITH h.parent_cust_id IS NULL
   CONNECT BY PRIOR h.cust_id = h.parent_cust_id;
END;
/"""


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def test_a_trigger_unit_refusal_is_reported_as_not_checkable(dictionary: Dictionary) -> None:
    """The whole finding, reconstructed and asserted from both sides.

    `refusal_violations` says clean and is not wrong to - it genuinely found no edge whose
    line falls in the refusal's span. What it cannot say is that the comparison was
    meaningless, and that is what the second list is for.
    """
    result = analyse_source(TRIGGER_REFUSAL, dictionary, AnalysisConfig())

    refused_units = {r.unit for r in result.refusals if r.unit}
    assert "TRG_RECENT_AUDIT" in refused_units, "the trigger body was not refused"

    trigger_edges = [e for e in result.edges if e.origin.unit.upper() == "TRG_RECENT_AUDIT"]
    assert trigger_edges, "no trigger edges were inherited, so there is nothing to mis-check"

    # The two line spaces, demonstrated rather than asserted in prose.
    refusal_lines = {r.line for r in result.refusals if r.unit == "TRG_RECENT_AUDIT"}
    edge_lines = {e.origin.line for e in trigger_edges}
    assert refusal_lines.isdisjoint(edge_lines)

    assert result.refusal_violations() == [], "the old check should still find nothing"
    assert len(result.refusals_not_cross_checkable()) == 1, (
        "the refusal's contradiction check could not run and nothing said so"
    )


def test_an_ordinary_refusal_is_still_checkable(dictionary: Dictionary) -> None:
    """The guard on the guard: a normal unit must not be reported as un-checkable.

    If this started returning refusals, the new count would be noise and the row it feeds
    would stop meaning anything - which is the failure mode S2-10 is about, arriving from
    the other direction.
    """
    source = """CREATE OR REPLACE PROCEDURE plain_refusal IS
BEGIN
    INSERT INTO dim_customer_hier (cust_id, parent_cust_id)
    SELECT h.cust_id, h.parent_cust_id
      FROM dim_customer_hier h
     START WITH h.parent_cust_id IS NULL
   CONNECT BY PRIOR h.cust_id = h.parent_cust_id;
END plain_refusal;
/"""
    result = analyse_source(source, dictionary, AnalysisConfig())

    assert result.refusals, "expected the CONNECT BY to be refused"
    assert result.refusals_not_cross_checkable() == []


def test_the_corpus_has_no_uncheckable_refusal_today(dictionary: Dictionary) -> None:
    """Pins the live state: the finding's own instance is gone, and should stay gone.

    `s6_updatable_view` refused an `INSERT ... VALUES` inside a trigger and produced an edge
    from the same statement. D-1 removed that refusal. If a future change puts a refusal
    back inside a trigger body, this fails and points at S2-10 rather than letting the
    kill-criterion row quietly report PASS.
    """
    for package in sorted(CORPUS.rglob("*.sql")):
        result = analyse_source(package.read_text(encoding="utf-8"), dictionary, AnalysisConfig())
        assert result.refusals_not_cross_checkable() == [], (
            f"{package.name} has a refusal whose contradiction check cannot run"
        )
