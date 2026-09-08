"""Temp tables and the fusion hazard (T2.5).

Silent failure s3. Forty procedures write `tmp_recent`; key the relation on its name,
compose a path through it, and forty unrelated lineages fuse. An invented edge is worse
than a missing one, because someone acts on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.analysis.scratch import find_fusion_hazards
from lineage.config import AnalysisConfig
from lineage.ir.model import IREdge
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def _analyse(package: str, dictionary: Dictionary):
    source = (CORPUS / "adversarial" / package).read_text(encoding="utf-8")
    return analyse_source(source, dictionary, AnalysisConfig())


def _relation(edge: IREdge, *, target: bool) -> str:
    node = edge.target if target else edge.source
    return node.name.rsplit(".", 1)[0] if "." in node.name else node.name


# --- the property that must hold ------------------------------------------------------


def test_no_edge_crosses_two_procedures(dictionary: Dictionary) -> None:
    """THE s3 ASSERTION: zero cross-procedure edges.

    Every edge is derived from a single statement and carries the unit that produced it.
    The fusion this case exists to prevent would appear as an edge whose source belongs
    to one procedure and whose target belongs to another.
    """
    result = _analyse("silent/s3_shared_temp_table.sql", dictionary)
    assert result.edges

    writer_a_sources = {"STG_CUSTOMER"}
    writer_b_targets = {"FCT_REVENUE"}

    for edge in result.edges:
        fused = (
            _relation(edge, target=False) in writer_a_sources
            and _relation(edge, target=True) in writer_b_targets
        )
        assert not fused, (
            f"invented a cross-procedure edge: {edge.source} -> {edge.target}. "
            "Writer A's customers cannot reach Writer B's revenue fact."
        )


def test_orders_never_reach_the_customer_dimension(dictionary: Dictionary) -> None:
    """The other direction of the same fusion.

    Writer B loads orders into tmp_recent; writer A reads tmp_recent into dim_customer.
    Joining them would claim an order amount drives a customer's active flag.
    """
    result = _analyse("silent/s3_shared_temp_table.sql", dictionary)
    for edge in result.edges:
        assert not (
            _relation(edge, target=False) == "STG_ORDERS"
            and _relation(edge, target=True) == "DIM_CUSTOMER"
        ), f"invented {edge.source} -> {edge.target}"


# --- the hazard is declared -----------------------------------------------------------


def test_shared_scratch_relation_is_declared(dictionary: Dictionary) -> None:
    """Nothing composes paths yet, so nothing is fused - but the constraint is recorded
    before the code that would violate it is written."""
    result = _analyse("silent/s3_shared_temp_table.sql", dictionary)
    hazards = [str(b) for b in result.boundaries if "fusion hazard" in str(b)]

    assert len(hazards) == 1
    assert "TMP_RECENT" in hazards[0]
    assert "S3_TEMP_WRITER_A" in hazards[0]
    assert "S3_TEMP_WRITER_B" in hazards[0]


def test_single_writer_is_not_a_hazard(dictionary: Dictionary) -> None:
    """b1_06 writes and reads gtt_stage from one procedure. Nothing can fuse."""
    result = _analyse("band1/b1_06_temp_tables.sql", dictionary)
    assert [b for b in result.boundaries if "fusion hazard" in str(b)] == []


# --- temporary-ness is a fact, not a naming convention --------------------------------


def test_temporary_comes_from_the_dictionary_not_the_name(dictionary: Dictionary) -> None:
    """`tmp_recent` is a PERMANENT table in this corpus, despite the prefix.

    Treating a name convention as a semantic fact is how a scratch table gets silently
    mis-modelled - and the two cases need opposite treatment. A temporary table's data is
    session-private, so composing across units is invented. A permanent one really is
    shared, and whether data flows between writers is not statically decidable.
    """
    assert dictionary.is_temporary("gtt_stage") is True
    assert dictionary.is_temporary("tmp_recent") is False


def test_hazard_records_which_kind_it_is(dictionary: Dictionary) -> None:
    result = _analyse("silent/s3_shared_temp_table.sql", dictionary)
    hazards = find_fusion_hazards(result.edges, dictionary)
    assert len(hazards) == 1
    assert hazards[0].is_temporary is False
    assert "permanent scratch table" in hazards[0].describe()
    assert "not statically decidable" in hazards[0].describe()


# --- the chain that must still work ---------------------------------------------------


def test_temp_table_chain_is_intact(dictionary: Dictionary) -> None:
    """stg_orders -> gtt_stage -> fct_revenue.

    At table level this looks like two unrelated pipelines. Refusing to model the
    intermediate hop would lose the connection entirely, which is the opposite failure
    from fusing.
    """
    result = _analyse("band1/b1_06_temp_tables.sql", dictionary)
    pairs = {(str(e.source), str(e.target)) for e in result.edges}

    assert ("column:STG_ORDERS.GROSS_AMOUNT", "column:GTT_STAGE.AMOUNT") in pairs
    assert ("column:GTT_STAGE.AMOUNT", "column:FCT_REVENUE.NET_AMOUNT") in pairs
