"""Dynamic SQL log recovery (T2.9).

The premise is sound: what the parser cannot construct, the database already executed and
recorded. The measurement is about the hard half — tying a logged statement back to the
procedure that emitted it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lineage.analysis.logrecovery import (
    Emitter,
    LoggedStatement,
    Signal,
    attribute,
    literal_fragments,
    recover,
)


def _statement(sql: str, module: str | None = None, action: str | None = None) -> LoggedStatement:
    return LoggedStatement(
        sql_id=sql[:12],
        sql_text=sql,
        module=module,
        action=action,
        executions=1,
        first_load_time=datetime(2026, 9, 6, tzinfo=UTC),
        parsing_schema="LINEAGE",
    )


CONCATENATED = Emitter(
    unit="B2_DYNAMIC_CONCATENATED",
    fragments=["UPDATE DIM_CUSTOMER SET ", " WHERE REGION = '"],
    action="b2_dynamic_concatenated",
)
CONSTANT = Emitter(
    unit="B2_DYNAMIC_CONSTANT",
    fragments=["UPDATE DIM_CUSTOMER SET IS_ACTIVE = 1 WHERE REGION = 'EU'"],
    action="b2_dynamic_constant",
)


# --- the favourable case -------------------------------------------------------------


def test_module_action_attributes_confidently() -> None:
    """Session attributes are set by the code, so they are unambiguous when present."""
    logged = _statement("UPDATE dim_customer SET is_active = 1", action="b2_dynamic_concatenated")
    result = attribute(logged, [CONCATENATED, CONSTANT])

    assert result.unit == "B2_DYNAMIC_CONCATENATED"
    assert result.signal is Signal.MODULE_ACTION
    assert result.high_confidence


# --- the unfavourable case, which is the one that matters -----------------------------


def test_shape_never_attributes_on_its_own() -> None:
    """MEASURED FINDING, 2026-09-06.

    Against real logged statements with the session attributes stripped, shape matching
    named the WRONG procedure for 2 of 5 and the right one for none. It now produces
    candidates and abstains.

    The failure is not a weak heuristic. `b2_dynamic_constant` embeds a complete
    statement as a literal, and `b2_dynamic_concatenated` assembles that same text at
    runtime - so the fragment is unique in the SOURCE and identical in the OUTPUT. Two
    procedures that can emit the same SQL are indistinguishable by their SQL.
    """
    logged = _statement("UPDATE dim_customer SET is_active = 1 WHERE region = 'EU'")
    result = attribute(logged, [CONCATENATED, CONSTANT])

    assert result.unit is None, "shape attributed an emitter on its own"
    assert not result.attributed
    assert not result.high_confidence


def test_shape_still_produces_candidates() -> None:
    """Abstaining is not the same as saying nothing.

    A ranked shortlist an SME can settle in five minutes is valuable; a confident wrong
    emitter is not. Candidates are ranked by fragments matched, never by plausibility.
    """
    logged = _statement("UPDATE dim_customer SET is_active = 1 WHERE region = 'EU'")
    result = attribute(logged, [CONCATENATED, CONSTANT])

    assert result.candidates
    assert "B2_DYNAMIC_CONSTANT" in result.candidates


def test_nothing_matching_is_reported_as_unattributed() -> None:
    """A statement nothing identifies is counted, never assigned to the likeliest guess."""
    logged = _statement("MERGE INTO something_else USING dual ON (1=1)")
    result = attribute(logged, [CONCATENATED, CONSTANT])

    assert result.unit is None
    assert result.signal is Signal.NONE
    assert not result.candidates


def test_only_module_action_counts_as_high_confidence() -> None:
    """Everything weaker is a lead to investigate, not an attribution to rely on."""
    by_module = attribute(
        _statement("UPDATE dim_customer SET x = 1", action="b2_dynamic_concatenated"),
        [CONCATENATED],
    )
    by_shape = attribute(
        _statement("UPDATE dim_customer SET is_active = 1 WHERE region = 'EU'"), [CONSTANT]
    )

    assert by_module.high_confidence
    assert not by_shape.high_confidence


# --- mechanics ------------------------------------------------------------------------


def test_literal_fragments_ignore_short_noise() -> None:
    """A fragment of three characters matches everything and identifies nothing."""
    fragments = literal_fragments("v := 'EU' || 'UPDATE dim_customer SET is_active = 1';")
    assert "UPDATE DIM_CUSTOMER SET IS_ACTIVE = 1" in fragments
    assert "EU" not in fragments


def test_truncation_is_detected() -> None:
    """Oracle caps V$SQL text; a truncated statement may attribute but not parse."""
    assert _statement("x" * 1000).truncated
    assert not _statement("SELECT 1 FROM dual").truncated


def test_recovery_rate_counts_only_high_confidence() -> None:
    """The rate must measure what is reliable, not what was merely guessed at."""
    statements = [
        _statement("UPDATE dim_customer SET a = 1", action="b2_dynamic_concatenated"),
        _statement("UPDATE dim_customer SET is_active = 1 WHERE region = 'EU'"),
    ]
    report = recover(statements, [CONCATENATED, CONSTANT])

    assert report.total == 2
    assert report.high_confidence == 1
    assert report.rate == 0.5
