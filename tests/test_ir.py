"""IR v0 (T2.1).

Pins the properties the rest of the system relies on: edges carry their provenance,
facts are superseded rather than mutated, and identity and matching are deliberately
different things.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from lineage.ir.model import (
    UNSET_TIME,
    EdgeLedger,
    Flow,
    IREdge,
    Mechanism,
    Node,
    NodeKind,
    Origin,
    Tier,
    Transform,
)

MARCH = datetime(2026, 3, 14, tzinfo=UTC)
SEPTEMBER = datetime(2026, 9, 6, tzinfo=UTC)


def _edge(**overrides: Any) -> IREdge:
    payload: dict[str, Any] = {
        "source": Node(kind=NodeKind.COLUMN, name="REF_POLICY.WINDOW_DAYS"),
        "target": Node(kind=NodeKind.VARIABLE, name="V_DAYS"),
        "flow": Flow.VALUE,
        "transform": Transform.IDENTITY,
        "band": 1,
        "mechanism": Mechanism.DEF_USE,
        "tier": Tier.A,
        "origin": Origin(unit="B1_LOCAL_VARIABLES", line=15),
    }
    payload.update(overrides)
    return IREdge.model_validate(payload)


# --- validation ---------------------------------------------------------------------


def test_origin_is_required() -> None:
    """A fact whose origin cannot be stated is not evidence."""
    with pytest.raises(ValidationError):
        IREdge.model_validate(
            {
                "source": Node(kind=NodeKind.COLUMN, name="A.B"),
                "target": Node(kind=NodeKind.COLUMN, name="C.D"),
                "band": 0,
                "mechanism": Mechanism.AST,
            }
        )


def test_mechanism_is_required() -> None:
    """How an edge was derived is never assumed."""
    with pytest.raises(ValidationError):
        IREdge.model_validate(
            {
                "source": Node(kind=NodeKind.COLUMN, name="A.B"),
                "target": Node(kind=NodeKind.COLUMN, name="C.D"),
                "band": 0,
                "origin": Origin(unit="X", line=1),
            }
        )


def test_guard_is_the_only_nullable_descriptive_attribute() -> None:
    """Most edges are genuinely unconditional, so guard is optional. Nothing else is."""
    edge = _edge()
    assert edge.guard is None
    assert edge.origin is not None
    assert edge.mechanism is not None
    assert edge.tier is not None


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _edge(confidence=0.87)


def test_mechanism_and_tier_are_independent() -> None:
    """Mechanism is HOW it was found; tier is HOW WELL it was corroborated.

    An AST-derived edge is still only Tier A if nothing else confirms it - blending the
    two would hide which leg of the evidence triangle is missing.
    """
    ast_but_uncorroborated = _edge(mechanism=Mechanism.AST, tier=Tier.A)
    inferred_but_corroborated = _edge(mechanism=Mechanism.INFERRED, tier=Tier.B)
    assert ast_but_uncorroborated.mechanism != inferred_but_corroborated.mechanism
    assert ast_but_uncorroborated.tier != inferred_but_corroborated.tier


def test_unexercised_is_a_separate_axis_from_tier() -> None:
    """An edge can be provably in the code AND never observed running.

    That combination is a finding, not a low tier - and it is the difference between
    "dead code, safe to remove" and "the year-end path that has not run yet".
    """
    edge = _edge(tier=Tier.A, unexercised=True)
    assert edge.tier is Tier.A
    assert edge.unexercised


# --- identity versus matching (ADR-0001 amendment 1) ---------------------------------


def test_guard_changes_identity_but_not_match_key() -> None:
    """The b1_09 case: the same edge on the happy path and in an exception handler.

    Two facts - one fires when the lookup succeeds, one only when it fails - but the
    precision gate must not move on guard text.
    """
    happy = _edge(guard=None)
    handler = _edge(guard="EXCEPTION NO_DATA_FOUND")

    assert happy.match_key() == handler.match_key()
    assert happy.identity() != handler.identity()


def test_origin_changes_identity_but_not_match_key() -> None:
    """The b1_03 case: the same self-edge as accumulation and as decay."""
    accumulation = _edge(origin=Origin(unit="B1_LOOPS", line=15))
    decay = _edge(origin=Origin(unit="B1_LOOPS", line=32))

    assert accumulation.match_key() == decay.match_key()
    assert accumulation.identity() != decay.identity()


def test_transform_is_in_the_match_key() -> None:
    """SUM(x) and a copy of x are different facts about the same pair of columns."""
    assert _edge(transform=Transform.IDENTITY).match_key() != (
        _edge(transform=Transform.AGGREGATED).match_key()
    )


# --- supersession -------------------------------------------------------------------


def test_edges_are_immutable() -> None:
    edge = _edge()
    with pytest.raises(ValidationError):
        edge.band = 2  # type: ignore[misc]


def test_supersede_closes_the_old_interval_and_keeps_it() -> None:
    """Never update an edge. Close it and append.

    An auditor does not ask what is true now; they ask whether it was true when you said
    it was. Only an append-only record answers that.
    """
    ledger = EdgeLedger()
    original = _edge(valid_from=MARCH, tx_from=MARCH, tier=Tier.A)
    ledger.append(original)

    revised = _edge(valid_from=SEPTEMBER, tx_from=SEPTEMBER, tier=Tier.B)
    ledger.supersede(revised, when=SEPTEMBER)

    assert len(ledger.edges) == 2, "the old version must survive"
    assert ledger.edges[0].valid_to == SEPTEMBER
    assert ledger.edges[0].tier is Tier.A, "history must not be rewritten"
    assert len(ledger.current()) == 1
    assert ledger.current()[0].tier is Tier.B


def test_as_of_reconstructs_the_past() -> None:
    """'What was the lineage on the filing date' is a query, not an excavation."""
    ledger = EdgeLedger()
    ledger.append(_edge(valid_from=MARCH, tx_from=MARCH, tier=Tier.A))
    ledger.supersede(_edge(valid_from=SEPTEMBER, tx_from=SEPTEMBER, tier=Tier.B), when=SEPTEMBER)

    in_march = ledger.as_of(datetime(2026, 5, 1, tzinfo=UTC))
    assert len(in_march) == 1
    assert in_march[0].tier is Tier.A

    now = ledger.as_of(datetime(2026, 10, 1, tzinfo=UTC))
    assert len(now) == 1
    assert now[0].tier is Tier.B


def test_supersede_only_touches_the_same_fact() -> None:
    """A different fact must not be closed as collateral."""
    ledger = EdgeLedger()
    unrelated = _edge(target=Node(kind=NodeKind.VARIABLE, name="V_OTHER"), valid_from=MARCH)
    ledger.append(unrelated)
    ledger.append(_edge(valid_from=MARCH))

    ledger.supersede(_edge(valid_from=SEPTEMBER, tier=Tier.B), when=SEPTEMBER)

    still_open = {str(edge.target) for edge in ledger.current()}
    assert "variable:V_OTHER" in still_open


# --- determinism --------------------------------------------------------------------


def test_default_transaction_time_is_fixed_not_now() -> None:
    """Analyser output must be byte-identical between runs.

    Stamping datetime.now() on every edge would break the reproducibility test for no
    benefit in phase 0, where nothing writes to a ledger. A real run supplies its time
    from the run record.
    """
    assert _edge().tx_from == UNSET_TIME
    assert _edge().tx_from == _edge().tx_from
