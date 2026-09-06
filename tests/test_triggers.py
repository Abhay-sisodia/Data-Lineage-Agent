"""Triggers attach to the table, not the caller (T3.3).

The register's rule in one sentence: *parse separately and attach to the TABLE, not the
caller. Any statement touching that table inherits the trigger's edges.*

The tests that matter here are the inheritance ones. An analyser that reads triggers out
of whichever source file declares them scores perfectly on `b2_05` and misses the same
trigger for every other writer - and nothing in a precision figure distinguishes the two.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.analysis.scratch import find_fusion_hazards
from lineage.analysis.triggers import analyse_trigger
from lineage.config import AnalysisConfig
from lineage.harness.labels import Flow
from lineage.resolution.dictionary import Dictionary

CORPUS = Path("corpus")


@pytest.fixture(scope="module")
def dictionary() -> Dictionary:
    return Dictionary.load(CORPUS / "dictionary.json")


def _analyse(relative: str, dictionary: Dictionary):
    return analyse_source(
        (CORPUS / "adversarial" / relative).read_text(encoding="utf-8"),
        dictionary,
        AnalysisConfig(),
    )


def _pairs(result) -> set[tuple[str, str]]:
    return {(str(e.source), str(e.target)) for e in result.edges}


# --- the dictionary carries them ------------------------------------------------------


def test_triggers_are_captured_with_the_object_they_fire_on(dictionary: Dictionary) -> None:
    """A trigger is a database object, discovered at ingest like a synonym or a view.

    Reading them from source files would make a trigger invisible to every file that does
    not declare it, which is every file that actually fires it.
    """
    assert dictionary.triggers, "no triggers captured - re-run scripts/capture_dictionary.py"
    audit = dictionary.triggers["LINEAGE.TRG_RECENT_AUDIT"]

    assert audit.table == "LINEAGE.TMP_RECENT"
    assert audit.fires_on("INSERT")
    assert not audit.fires_on("DELETE")


def test_a_trigger_is_found_from_the_table_it_fires_on(dictionary: Dictionary) -> None:
    names = {t.name for t in dictionary.triggers_on("TMP_RECENT")}
    assert names == {"TRG_RECENT_AUDIT"}


def test_a_synonym_does_not_hide_a_trigger(dictionary: Dictionary) -> None:
    """Two silent failures would otherwise compound: the synonym hides the table, and the
    table's triggers go with it."""
    direct = dictionary.triggers_on("DIM_CUSTOMER")
    assert {t.name for t in direct} == {"TRG_CUSTOMER_DEFAULT"}


def test_a_merge_fires_both_insert_and_update_triggers(dictionary: Dictionary) -> None:
    """Which arm a row takes is not statically decidable, so any overlap counts.

    Found by measurement: `fires_on` originally tested the caller's event string against
    the trigger's, and a MERGE claiming "INSERT UPDATE" matched nothing at all.
    """
    default = dictionary.triggers["LINEAGE.TRG_CUSTOMER_DEFAULT"]
    assert default.fires_on("INSERT UPDATE")


# --- inheritance: the point of the whole task -----------------------------------------


def test_a_file_that_never_mentions_the_trigger_inherits_its_edges(
    dictionary: Dictionary,
) -> None:
    """`b0_01` is nine lines of INSERT ... SELECT. It writes dim_customer.lifetime_value.

    Nothing in its source says so. This is the edge the register calls a silent failure:
    the regulated column's ONLY writer in this corpus is a trigger, so an analyser that
    misses triggers reports it as having no writer - which reads as a finding, not a gap.
    """
    result = _analyse("band0/b0_01_insert_select.sql", dictionary)
    pairs = _pairs(result)

    assert ("column:DIM_CUSTOMER.LIFETIME_VALUE", "column:DIM_CUSTOMER.LIFETIME_VALUE") in pairs
    assert ("column:TMP_RECENT.CUST_ID", "relation:DIM_CUSTOMER") in pairs


@pytest.mark.parametrize(
    "package",
    [
        "band0/b0_01_insert_select.sql",
        "band0/b0_03_select_star.sql",
        "band1/b1_01_local_variables.sql",
        "band2/b2_01_dynamic_constant.sql",
        "silent/s2_schema_context.sql",
        "silent/s5_positional_union.sql",
    ],
)
def test_every_writer_of_the_table_inherits_it(package: str, dictionary: Dictionary) -> None:
    """Six files, one trigger, none of them mentions it."""
    result = _analyse(package, dictionary)
    assert (
        "column:DIM_CUSTOMER.LIFETIME_VALUE",
        "column:DIM_CUSTOMER.LIFETIME_VALUE",
    ) in _pairs(result)


def test_the_edge_carries_the_trigger_as_its_origin(dictionary: Dictionary) -> None:
    """Origin is what separates 'attached to the table' from 'attached to the caller'.

    The match key is identical either way, so precision and recall cannot tell them
    apart - which is why this has to be asserted directly.
    """
    result = _analyse("band0/b0_01_insert_select.sql", dictionary)
    inherited = [e for e in result.edges if str(e.target) == "column:DIM_CUSTOMER.LIFETIME_VALUE"]

    assert inherited
    assert all(e.origin.unit == "TRG_RECENT_AUDIT" for e in inherited)
    assert all(e.band == 2 for e in inherited)


def test_a_trigger_is_not_inherited_by_the_wrong_event(dictionary: Dictionary) -> None:
    """`trg_customer_default` is BEFORE INSERT ON dim_customer.

    `b1_02` only ever UPDATEs dim_customer, so the trigger does not run and claiming its
    edge would be invented lineage.
    """
    result = _analyse("band1/b1_02_if_case.sql", dictionary)
    assert (
        "column:DIM_CUSTOMER.REGION",
        "column:DIM_CUSTOMER.IS_ACTIVE",
    ) not in _pairs(result)


def test_a_merge_insert_arm_does_inherit_it(dictionary: Dictionary) -> None:
    result = _analyse("band0/b0_02_merge.sql", dictionary)
    assert ("column:DIM_CUSTOMER.REGION", "column:DIM_CUSTOMER.IS_ACTIVE") in _pairs(result)


# --- correlation names ----------------------------------------------------------------


def test_new_resolves_to_the_triggering_table_not_the_updated_one(
    dictionary: Dictionary,
) -> None:
    """THE SILENT FAILURE OF THIS BAND.

    In `WHERE cust_id = :NEW.cust_id` both operands look like `dim_customer.cust_id`. One
    is; the other is a column of `tmp_recent`, which the statement never names. Bind
    :NEW against the statement's own FROM clause and the two collapse into a plausible
    self-edge, and the trigger's real input disappears without trace.
    """
    result = _analyse("band2/b2_05_triggers.sql", dictionary)
    filters = {(str(e.source), str(e.target)) for e in result.edges if e.flow is Flow.FILTER}

    assert ("column:TMP_RECENT.CUST_ID", "relation:DIM_CUSTOMER") in filters
    assert ("column:DIM_CUSTOMER.CUST_ID", "relation:DIM_CUSTOMER") in filters


def test_a_before_trigger_assignment_is_a_column_write(dictionary: Dictionary) -> None:
    """`:NEW.is_active := CASE WHEN :NEW.region = 'EU' ...`

    No DML statement anywhere names either column. It is the only way a value is decided
    without a statement, and the only place this edge exists in the corpus.
    """
    default = dictionary.triggers["LINEAGE.TRG_CUSTOMER_DEFAULT"]
    edges = analyse_trigger(default, dictionary).edges

    assert [
        e
        for e in edges
        if (str(e.source), str(e.target))
        == ("column:DIM_CUSTOMER.REGION", "column:DIM_CUSTOMER.IS_ACTIVE")
    ]


def test_the_branch_guard_survives_into_the_edge(dictionary: Dictionary) -> None:
    """The default applies only when the incoming value is NULL — a real condition."""
    default = dictionary.triggers["LINEAGE.TRG_CUSTOMER_DEFAULT"]
    edges = analyse_trigger(default, dictionary).edges

    assert edges
    assert all(e.guard and "IS NULL" in e.guard.upper() for e in edges)


# --- INSTEAD OF triggers on views ------------------------------------------------------


def test_an_instead_of_trigger_resolves_the_view_to_its_base_table(
    dictionary: Dictionary,
) -> None:
    """s6's second silent failure.

    The trigger's :NEW row belongs to a VIEW. Left unresolved every edge names an object
    that stores nothing, and `dim_customer` — the regulated table actually written —
    appears to have no writer at all.
    """
    result = _analyse("silent/s6_updatable_view.sql", dictionary)
    pairs = _pairs(result)

    assert ("column:DIM_CUSTOMER.CUST_ID", "column:DW_AUDIT_LOG.CUST_ID") in pairs
    assert not [
        e
        for e in result.edges
        if e.origin.unit == "TRG_CUSTOMER_EDITABLE" and "V_CUSTOMER_EDITABLE" in str(e.source)
    ]


def test_the_audit_row_binds_positionally(dictionary: Dictionary) -> None:
    """`INSERT INTO dw_audit_log (cust_id, changed_by, changed_at)
        VALUES (:NEW.cust_id, USER, SYSDATE)`

    Positional, like a UNION arm and a FETCH INTO. USER and SYSDATE supply values from
    nowhere and produce no edge - the literal rule.
    """
    result = _analyse("silent/s6_updatable_view.sql", dictionary)
    audit = [e for e in result.edges if "DW_AUDIT_LOG" in str(e.target)]

    assert len(audit) == 1
    assert str(audit[0].target) == "column:DW_AUDIT_LOG.CUST_ID"


# --- what triggers must NOT break ------------------------------------------------------


def test_a_trigger_is_not_a_fusion_hazard(dictionary: Dictionary) -> None:
    """A trigger is not an independent unit competing for a scratch relation.

    It runs as part of somebody else's write, always. Counting it made `dim_customer`
    look like a shared scratch table the moment trigger analysis landed - a false hazard,
    and a coverage statement full of false hazards is one nobody reads.
    """
    result = _analyse("silent/s3_shared_temp_table.sql", dictionary)
    hazards = find_fusion_hazards(result.edges, dictionary)

    assert [h.relation for h in hazards] == ["TMP_RECENT"]


def test_the_shared_temp_table_defence_still_holds(dictionary: Dictionary) -> None:
    """T2.5's assertion, re-checked because triggers add a third writer to the picture."""
    result = _analyse("silent/s3_shared_temp_table.sql", dictionary)
    pairs = _pairs(result)

    assert ("column:STG_ORDERS.CUST_ID", "relation:DIM_CUSTOMER") not in pairs
    assert ("column:STG_CUSTOMER.CUST_ID", "column:FCT_REVENUE.CUST_ID") not in pairs
