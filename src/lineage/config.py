"""Analysis configuration.

Three principles, carried over from the build plan and worth restating here because
they are easy to erode:

1. Config is data, not constants in code. Who changed a limit, when, and why is an
   auditable fact.
2. Every limit that can change an answer appears alongside that answer. A depth cap set
   silently is a hidden defect; the same cap, published, is a specification.
3. Config is pinned to the run. Re-running March must use March's configuration, or
   findings signed in March silently stop reproducing once someone tunes a threshold.

Phase 0 only needs (1) and (2) — there is no ledger yet to pin a run to. But the shape
is built now so that (3) is a storage change later rather than a rewrite.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_PATH = Path("config/analysis.yaml")


class Budgets(BaseModel):
    """Limits on how far the analysis will go before declaring a boundary.

    Every one of these is a declared coverage parameter. Exceeding a budget is never an
    error and never a guess — it emits a boundary node that gets counted and reported.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    interprocedural_depth_cap: int = Field(
        default=6,
        ge=0,
        description="How many levels of proc-calls-proc to follow before a boundary node.",
    )
    loop_fixpoint_iteration_cap: int = Field(
        default=10,
        ge=1,
        description="Iterations allowed to reach a stable edge set before non-convergence.",
    )
    view_expansion_depth_cap: int = Field(
        default=10,
        ge=1,
        description="Nesting depth for views on views, independent of cycle detection.",
    )


class LogRecovery(BaseModel):
    """Settings for recovering dynamic SQL from the query log (T3.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=True,
        description="When false, dynamic SQL is declared unresolvable rather than recovered.",
    )
    retention_months: int = Field(
        default=18,
        ge=0,
        description="Query log window available. Affects what can be attributed at all.",
    )
    statement_text_truncated_at: int = Field(
        default=1000,
        ge=0,
        description=(
            "Characters before the log truncates statement text. Oracle V$SQL has "
            "historically capped at 1000. A truncated statement may be attributable but "
            "not fully parseable, so the edge is marked partial rather than dropped."
        ),
    )


class Scoring(BaseModel):
    """How the harness decides two edges are the same fact (ADR-0001 amendment 1).

    This is the one config section that changes the measurement itself rather than the
    analysis, which is why it is a declared limit like any other: a score is only
    comparable to another score taken under the same key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    origin_in_dedup: bool = Field(
        default=False,
        description=(
            "Carry origin UNIT into prediction dedup, so two same-key claims from two units "
            "count as two claims. MEASURED HARMFUL on this corpus (2026-09-09): it produces "
            "5 false positives from legitimate multi-route facts - a callee summarised at "
            "two call sites, a trigger edge inherited by its table's writer - and catches no "
            "fabrication, because the s2/b2_05 fabrications are refused before emission. "
            "Kept switchable so the finding stays reproducible, not because it should be on."
        ),
    )


class AnalysisConfig(BaseModel):
    """The full configuration for one analysis run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dialect: str = Field(default="oracle", description="Source dialect. Phase 0 is Oracle only.")
    budgets: Budgets = Field(default_factory=Budgets)
    log_recovery: LogRecovery = Field(default_factory=LogRecovery)
    scoring: Scoring = Field(default_factory=Scoring)

    @classmethod
    def load(cls, path: Path | None = None) -> Self:
        """Load configuration from YAML, falling back to defaults if absent."""
        resolved = path or DEFAULT_CONFIG_PATH
        if not resolved.exists():
            if path is not None:
                raise FileNotFoundError(f"No configuration file at {resolved}")
            return cls()
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)

    def declared_limits(self) -> dict[str, Any]:
        """The limits in force, flattened for the coverage statement.

        Anything that can change an answer belongs in this dict. If a setting is added
        to this class and not surfaced here, that is a hidden defect by definition.
        """
        return {
            "dialect": self.dialect,
            "interprocedural_depth_cap": self.budgets.interprocedural_depth_cap,
            "loop_fixpoint_iteration_cap": self.budgets.loop_fixpoint_iteration_cap,
            "view_expansion_depth_cap": self.budgets.view_expansion_depth_cap,
            "log_recovery_enabled": self.log_recovery.enabled,
            "query_log_retention_months": self.log_recovery.retention_months,
            "log_statement_truncated_at": self.log_recovery.statement_text_truncated_at,
            "scoring_origin_in_dedup": self.scoring.origin_in_dedup,
        }

    def fingerprint(self) -> str:
        """Stable hash of the configuration, for pinning a result to the settings that produced it.

        Phase 0 records this next to every harness run so a score can always be traced
        back to the exact configuration that produced it. Later this becomes half of the
        run record — the artifacts being the other half.
        """
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
