"""Finding F0-07: a parenthesised set-operation arm was refused.

`(SELECT ...) UNION ALL (SELECT ...)` - a common house style - and `... UNION ALL (VALUES
...)` were refused at the top level and under INSERT as "set-operation arm is Subquery, not
a SELECT"; inside a derived table a `(VALUES ...)` arm was declared "fewer columns than
position". A parenthesised arm is now the arm inside it, and literal VALUES rows supply no
lineage, with nothing declared - the unparenthesised spellings' answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"

FX = "SELECT f.currency, f.rate_to_usd FROM staging.fx_rates AS f"
PAY = "SELECT p.currency, p.amount FROM staging.payments AS p"
LEDGER = "SELECT l.currency, l.amount FROM staging.ledger AS l"
LITERAL = "VALUES ('EUR', 1.1)"


def _facts(sql: str) -> set[tuple[str, str, str, str]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(sql, Dictionary.load(BOOK), config, None)
    assert result.refusals == [] and result.boundaries == [], (result.refusals, result.boundaries)
    return {(e.flow.value, str(e.source), str(e.target), e.transform.value) for e in result.edges}


@pytest.mark.parametrize(
    ("parenthesised", "plain"),
    [
        (f"({FX}) UNION ALL ({PAY})", f"{FX} UNION ALL {PAY}"),
        (f"{FX} UNION ALL ({LITERAL})", f"{FX} UNION ALL {LITERAL}"),
        (f"({FX}) EXCEPT ({PAY})", f"{FX} EXCEPT {PAY}"),
        (
            f"INSERT INTO staging.fx_rates (currency, rate_to_usd) ({FX}) UNION ALL ({LITERAL})",
            f"INSERT INTO staging.fx_rates (currency, rate_to_usd) {FX} UNION ALL {LITERAL}",
        ),
        (
            f"SELECT u.currency FROM ({FX} UNION ALL ({LITERAL})) AS u",
            f"SELECT u.currency FROM ({FX} UNION ALL {LITERAL}) AS u",
        ),
    ],
    ids=["two SELECTs", "VALUES", "EXCEPT", "INSERT", "derived table"],
)
def test_a_parenthesised_arm_is_the_arm_inside(parenthesised: str, plain: str) -> None:
    expected = _facts(plain)
    assert expected
    assert _facts(parenthesised) == expected


def test_a_parenthesised_right_hand_set_operation_keeps_the_operator_that_joins_it() -> None:
    """`A EXCEPT (B UNION C)`: B and C both only remove rows of A."""
    facts = _facts(f"{FX} EXCEPT ({PAY} UNION {LEDGER})")
    values = {source for flow, source, _, _ in facts if flow == "value"}
    filters = {source for flow, source, _, _ in facts if flow == "filter"}
    assert values == {"column:staging.fx_rates.currency", "column:staging.fx_rates.rate_to_usd"}
    assert {"column:staging.payments.amount", "column:staging.ledger.amount"} <= filters


def test_an_arm_s_own_limit_still_chooses_its_rows() -> None:
    """D-9 inside the parentheses: `(... ORDER BY 2 LIMIT 3)` cuts that arm by rate."""
    filters = {
        source
        for flow, source, _, _ in _facts(f"({FX} ORDER BY 2 LIMIT 3) UNION ALL ({PAY})")
        if flow == "filter"
    }
    assert filters == {"column:staging.fx_rates.rate_to_usd"}
