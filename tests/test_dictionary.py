"""Name resolution against the data dictionary (T1.4).

The headline test is `test_synonym_redirect_resolves_to_the_real_object`. It is the
defence against silent failure s1: the parser succeeds, binds the literal name, and
reports the wrong object at Tier B with all three witnesses agreeing — because the logs
and the profile describe the same wrong name.

Most tests run offline against the committed `corpus/dictionary.json`, so the defence is
verified on every build rather than only when a database happens to be running.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lineage.resolution.dictionary import (
    Dictionary,
    ObjectInfo,
    StaleDictionaryError,
    UnknownObjectError,
)

DICTIONARY_PATH = Path("corpus/dictionary.json")


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(DICTIONARY_PATH)


def test_synonym_redirect_resolves_to_the_real_object(dictionary: Dictionary) -> None:
    """SILENT FAILURE s1. The code says `customer_target`. It is not that object.

    Getting this wrong produces a confident, corroborated, wrong answer inside a filing.
    """
    resolved = dictionary.resolve("customer_target")

    assert resolved.name == "DW_DIM_CUSTOMER_V2"
    assert resolved.object_type == "TABLE"
    # The literal name must not survive resolution.
    assert resolved.name != "CUSTOMER_TARGET"
    assert resolved.name != "DIM_CUSTOMER"


def test_resolution_chain_is_recorded_as_evidence(dictionary: Dictionary) -> None:
    """ "Confirmed via synonym X -> Y" is the sentence that survives a challenge.

    A resolution with no recorded chain is an assertion, not evidence.
    """
    resolved = dictionary.resolve("customer_target")
    assert any("synonym" in step.lower() for step in resolved.via)
    assert any("DW_DIM_CUSTOMER_V2" in step for step in resolved.via)


def test_unqualified_name_binds_to_a_named_schema(dictionary: Dictionary) -> None:
    """SILENT FAILURE s2. Which schema resolved the name is itself evidence."""
    resolved = dictionary.resolve("stg_customer")
    assert resolved.qualified == "LINEAGE.STG_CUSTOMER"
    assert any("bound to schema" in step for step in resolved.via)


def test_case_and_quoting_do_not_change_resolution(dictionary: Dictionary) -> None:
    assert dictionary.resolve("STG_CUSTOMER").qualified == (
        dictionary.resolve('"stg_customer"').qualified
    )


def test_select_star_expands_to_real_ordered_columns(dictionary: Dictionary) -> None:
    """Without expansion, lineage stays at table level - the useless level."""
    columns = dictionary.columns_of("tmp_recent")
    assert columns == ["CUST_ID", "LAST_LOGIN"]


def test_expansion_follows_synonyms(dictionary: Dictionary) -> None:
    """A SELECT * through a synonym must expand the columns of the REAL table."""
    assert dictionary.columns_of("customer_target") == dictionary.columns_of("dw_dim_customer_v2")


def test_unknown_object_raises_rather_than_expanding_to_nothing(
    dictionary: Dictionary,
) -> None:
    """THE STALE-DDL DEFENCE.

    Returning [] for an unknown table would silently drop every column of something we
    could not see, and produce a confident wrong answer with no symptom. Refusing is the
    only safe behaviour: the caller declares a boundary and counts it.
    """
    with pytest.raises(UnknownObjectError, match="no object named"):
        dictionary.columns_of("table_that_does_not_exist")


def test_views_are_identified(dictionary: Dictionary) -> None:
    """A view hides the base table that actually receives the write (silent failure s6)."""
    assert dictionary.is_view("v_customer_editable")
    assert not dictionary.is_view("dim_customer")


def test_view_text_is_captured_for_later_base_table_resolution(
    dictionary: Dictionary,
) -> None:
    text = dictionary.view_text.get("LINEAGE.V_CUST_L3")
    assert text and "V_CUST_L2" in text.upper()


def test_stale_dictionary_is_detected(dictionary: Dictionary) -> None:
    """A schema snapshot must be pinned alongside the code snapshot.

    Expanding SELECT * against a dictionary that has moved on produces the WRONG column
    list with no symptom at all.
    """
    dictionary.require_fingerprint(dictionary.fingerprint())  # matches - no raise

    with pytest.raises(StaleDictionaryError, match="does not match"):
        dictionary.require_fingerprint("0" * 64)


def test_fingerprint_ignores_capture_time(dictionary: Dictionary) -> None:
    """Two captures of an unchanged schema must fingerprint identically.

    Otherwise the pinning check fires on every run and gets ignored as noise.
    """
    later = dictionary.model_copy(update={"captured_at": "2099-01-01T00:00:00+00:00"})
    assert later.fingerprint() == dictionary.fingerprint()


def test_synonym_cycle_terminates() -> None:
    """Synonym loops exist in old estates. They must not hang the analyser."""
    looped = Dictionary(
        captured_at="2026-09-06T00:00:00+00:00",
        default_schema="X",
        objects={"X.REAL": ObjectInfo(owner="X", name="REAL", object_type="TABLE")},
        synonyms={"X.A": "X.B", "X.B": "X.A"},
    )
    resolved = looped.resolve("a")
    assert not resolved.resolved
    assert any("cycle" in step for step in resolved.via)


@pytest.mark.requires_oracle
def test_committed_dictionary_matches_the_live_database(
    oracle_connection: Any, dictionary: Dictionary
) -> None:
    """The committed snapshot must not drift from the database it describes.

    If this fails, re-run scripts/capture_dictionary.py - deliberately, having noticed.
    """
    from datetime import UTC, datetime

    from lineage.resolution.dictionary import capture

    live = capture(oracle_connection, schema="LINEAGE", captured_at=datetime.now(UTC).isoformat())
    assert live.fingerprint() == dictionary.fingerprint(), (
        "corpus/dictionary.json is stale - re-capture it"
    )
