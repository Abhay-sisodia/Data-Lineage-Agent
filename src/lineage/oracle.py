"""Connection settings for the local Oracle used by the spike.

Deliberately thin. The product never holds database credentials of its own — a customer
runs the collectors inside their own VPC against their own access. This module exists so
the corpus can be compiled and executed locally, and so the log-recovery sub-spike has a
real ``V$SQL`` to read.

Settings come from the environment with development defaults, so nothing secret is
committed and nothing has to be edited to run the tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OracleSettings:
    """Where the local Oracle is and who to connect as."""

    host: str = "localhost"
    port: int = 1521
    service: str = "XEPDB1"
    user: str = "lineage"
    password: str = "lineage_app"

    @property
    def dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service}"

    @classmethod
    def from_env(cls) -> OracleSettings:
        return cls(
            host=os.environ.get("ORACLE_HOST", "localhost"),
            port=int(os.environ.get("ORACLE_PORT", "1521")),
            service=os.environ.get("ORACLE_SERVICE", "XEPDB1"),
            user=os.environ.get("ORACLE_APP_USER", "lineage"),
            password=os.environ.get("ORACLE_APP_PASSWORD", "lineage_app"),
        )

    @classmethod
    def admin_from_env(cls) -> OracleSettings:
        """Privileged settings, used only by the bootstrap script."""
        return cls(
            host=os.environ.get("ORACLE_HOST", "localhost"),
            port=int(os.environ.get("ORACLE_PORT", "1521")),
            service=os.environ.get("ORACLE_SERVICE", "XEPDB1"),
            user="system",
            password=os.environ.get("ORACLE_SYS_PASSWORD", "lineage_sys"),
        )


def connect(settings: OracleSettings | None = None) -> Any:
    """Open a connection in thin mode — no Oracle Instant Client required."""
    import oracledb

    resolved = settings or OracleSettings.from_env()
    return oracledb.connect(user=resolved.user, password=resolved.password, dsn=resolved.dsn)


def is_available(settings: OracleSettings | None = None) -> bool:
    """True if a connection can be opened right now.

    Used to skip Oracle-dependent tests rather than fail them, so the suite stays useful
    on a machine with no container running.
    """
    try:
        with connect(settings):
            return True
    except Exception:
        return False
