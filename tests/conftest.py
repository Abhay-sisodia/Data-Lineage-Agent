"""Shared fixtures.

Oracle-dependent tests skip rather than fail when no database is running, so the suite
stays useful on a machine that has not started the container. They are also marked
``requires_oracle`` so they can be deselected explicitly:

    pytest -m "not requires_oracle"
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from lineage.oracle import OracleSettings, connect, is_available

SKIP_REASON = (
    "Oracle not available. Start it with 'docker compose up -d' and then run "
    "'.\\.venv\\Scripts\\python.exe scripts\\oracle_bootstrap.py'"
)


@pytest.fixture(scope="session")
def oracle_settings() -> OracleSettings:
    return OracleSettings.from_env()


@pytest.fixture(scope="session")
def oracle_connection(oracle_settings: OracleSettings) -> Iterator[Any]:
    """A session-scoped connection to the local Oracle, or a skip."""
    if not is_available(oracle_settings):
        pytest.skip(SKIP_REASON)
    connection = connect(oracle_settings)
    try:
        yield connection
    finally:
        connection.close()
