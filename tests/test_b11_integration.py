"""Batch B11's fixes, pinned outside the stress files.

`stress/pg/b11_integration/REPORT.md` has the evidence. B11 found two defects in the seams
between earlier batches; each test is the smallest script that shows one, with a control.

* B11-01  a function row source under CROSS JOIN LATERAL binds its column-definition list,
          as the comma-join form does
* B11-02  sequence functions are built-ins that read no column
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _values(result) -> set[tuple[str, str, str]]:
    return {
        (str(e.source), str(e.target), e.transform.value)
        for e in result.edges
        if e.flow.value == "value"
    }


RECORDSET = (
    "INSERT INTO core.fct_shipment (shipment_id, carrier_id, weight_kg)\n"
    "SELECT s.id, x.carrier, x.kg\nFROM staging.shipments AS s {join} "
    "jsonb_to_recordset(s.tracking) AS x(carrier integer, kg numeric);"
)
EXPECTED = {
    ("column:staging.shipments.id", "column:core.fct_shipment.shipment_id", "identity"),
    ("column:staging.shipments.tracking", "column:core.fct_shipment.carrier_id", "derived"),
    ("column:staging.shipments.tracking", "column:core.fct_shipment.weight_kg", "derived"),
}


def test_a_recordset_under_cross_join_lateral_binds_its_columns() -> None:
    result = _result(RECORDSET.format(join="CROSS JOIN LATERAL"))
    assert _values(result) == EXPECTED
    assert result.boundaries == []


def test_the_comma_join_form_gives_the_same_lineage() -> None:
    """The control: the form that already worked, and must still agree."""
    assert _values(_result(RECORDSET.format(join=","))) == EXPECTED


def test_nextval_reads_no_column_and_declares_nothing() -> None:
    result = _result(
        "INSERT INTO core.dim_customer (customer_key, customer_id)\n"
        "SELECT nextval('core.dim_customer_seq'), c.id FROM staging.customers AS c;"
    )
    assert _values(result) == {
        ("column:staging.customers.id", "column:core.dim_customer.customer_id", "identity")
    }
    assert result.boundaries == []
