"""Finding TE-1: a column is traced through any realistic depth of CTEs, and a cap is declared.

`stress/pg/torture_entity/REPORT.md` (local, not committed) has the evidence: a 112-stage
CTE pipeline lost every spine fact with a clean report, because the trace stopped at a
literal depth of 20 and returned nothing. Each test is the smallest script that shows one
half of the fix.

* a 120-CTE chain traces to its base table, values and window influence alike
* past `TRACE_DEPTH_CAP` the stop is declared as `depth_cap`, never silent, never a crash
* a chain whose columns each read two columns of the stage before is traced once per
  column (memoised), not 2^depth times
"""

from __future__ import annotations

import time
from pathlib import Path

from lineage.analysis.band0 import TRACE_DEPTH_CAP
from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _edges(result, flow: str) -> set[tuple[str, str, str]]:
    return {
        (str(e.source), str(e.target), e.transform.value)
        for e in result.edges
        if e.flow.value == flow
    }


def _chain(stages: int) -> str:
    """`c0` reads staging.orders; each `cN` is `p.*` of the one before; a window on top."""
    ctes = ["c0 AS (SELECT o.status, o.amount FROM staging.orders AS o)"]
    ctes += [f"c{n} AS (SELECT p.* FROM c{n - 1} AS p)" for n in range(1, stages + 1)]
    ctes.append(
        f"w AS (SELECT p.amount, SUM(p.amount) OVER (PARTITION BY p.status) AS total "
        f"FROM c{stages} AS p)"
    )
    return "WITH " + ",\n".join(ctes) + "\nSELECT w.amount, w.total FROM w;"


def test_a_120_stage_chain_traces_to_its_base_table() -> None:
    result = _result(_chain(120))
    assert _edges(result, "value") == {
        ("column:staging.orders.amount", "column:<result>.amount", "identity"),
        ("column:staging.orders.amount", "column:<result>.total", "aggregated"),
    }
    assert _edges(result, "influence") == {
        ("column:staging.orders.status", "column:<result>.total", "identity"),
    }
    assert result.boundaries == []
    assert result.refusals == []


def test_past_the_cap_the_stop_is_declared_not_silent() -> None:
    """Declared at the cap, and the statement is still analysed: the stack holds that far."""
    result = _result(_chain(TRACE_DEPTH_CAP + 40))
    assert {b.kind.value for b in result.boundaries} == {"depth_cap"}
    assert result.refusals == []
    assert _edges(result, "value") == set()


def test_a_fanning_chain_is_traced_once_per_column() -> None:
    """Each stage's two columns read both of the stage before: 2^40 paths, 80 columns."""
    ctes = ["d0 AS (SELECT o.amount AS a, o.customer_id AS b FROM staging.orders AS o)"]
    ctes += [
        f"d{n} AS (SELECT p.a + p.b AS a, p.a - p.b AS b FROM d{n - 1} AS p)" for n in range(1, 41)
    ]
    sql = "WITH " + ",\n".join(ctes) + "\nSELECT d40.a FROM d40;"
    started = time.perf_counter()
    result = _result(sql)
    assert time.perf_counter() - started < 30
    assert _edges(result, "value") == {
        ("column:staging.orders.amount", "column:<result>.a", "derived"),
        ("column:staging.orders.customer_id", "column:<result>.a", "derived"),
    }
