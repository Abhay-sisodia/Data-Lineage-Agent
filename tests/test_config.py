"""Configuration invariants.

The important test here is `test_every_setting_is_declared`. It is the mechanical
enforcement of a principle that is otherwise just a good intention: a limit that can
change an answer must appear alongside that answer. Add a setting and forget to surface
it, and this test fails rather than the omission shipping silently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from lineage.config import AnalysisConfig


def _count_leaves(value: Any) -> int:
    """Number of scalar leaves in a nested dict."""
    if isinstance(value, dict):
        return sum(_count_leaves(v) for v in value.values())
    return 1


def test_defaults_apply_when_no_file_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no config file and no explicit path, the analyser still runs — on defaults.

    The defaults are themselves a declared specification, not an absence of one.
    """
    monkeypatch.chdir(tmp_path)
    config = AnalysisConfig.load()
    assert config == AnalysisConfig()


def test_explicit_missing_path_is_an_error(tmp_path: Path) -> None:
    """Asking for a specific config that is not there must fail loudly.

    Silently falling back to defaults would mean a run scored under settings nobody
    chose, which is exactly the drift the pinning discipline exists to prevent.
    """
    with pytest.raises(FileNotFoundError):
        AnalysisConfig.load(tmp_path / "does-not-exist.yaml")


def test_shipped_config_file_is_valid() -> None:
    """The checked-in config must parse. It is the default every run uses."""
    config = AnalysisConfig.load(Path("config/analysis.yaml"))
    assert config.dialect == "oracle"
    assert config.budgets.interprocedural_depth_cap >= 0


def test_every_setting_is_declared() -> None:
    """Every configurable leaf must be surfaced in declared_limits().

    A setting that can change an answer but never appears in the coverage statement is a
    hidden defect by definition.
    """
    config = AnalysisConfig()
    leaves = _count_leaves(config.model_dump(mode="json"))
    declared = config.declared_limits()
    assert len(declared) == leaves, (
        f"{leaves} configurable settings but {len(declared)} declared. "
        "A new setting was added without surfacing it in declared_limits()."
    )


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    """A typo'd limit that silently does nothing is worse than an error."""
    path = tmp_path / "analysis.yaml"
    path.write_text(
        yaml.safe_dump({"dialect": "oracle", "budgets": {"interprocedural_dpeth_cap": 6}}),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        AnalysisConfig.load(path)


def test_fingerprint_is_stable_and_sensitive() -> None:
    """Same config, same hash. Different config, different hash.

    This is what lets a score be traced back to the settings that produced it.
    """
    a = AnalysisConfig()
    b = AnalysisConfig()
    assert a.fingerprint() == b.fingerprint()

    c = AnalysisConfig.model_validate({"budgets": {"interprocedural_depth_cap": 7}})
    assert c.fingerprint() != a.fingerprint()


def test_config_is_immutable() -> None:
    """Config is pinned to a run. Mutating it mid-run would make the pin a lie."""
    config = AnalysisConfig()
    with pytest.raises(ValidationError):
        config.budgets.interprocedural_depth_cap = 99  # type: ignore[misc]
