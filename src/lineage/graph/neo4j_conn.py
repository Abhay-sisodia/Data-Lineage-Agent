"""Connection settings for the Neo4j instance that holds the lineage knowledge graph.

Deliberately thin, like `lineage.oracle`: settings come from the environment with local
development defaults, so nothing secret is committed and the tests run unedited.

**Reads are read-only by construction.** Neo4j Community has no role-based users, so a
separate read-only account is not available. Every query a tool or the agent runs goes
through `read_session`, which opens the session in READ access mode - Neo4j then rejects
any write in it (`Neo.ClientError.Statement.AccessMode`). Only the graph build writes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Neo4jSettings:
    """Where the local Neo4j is and who to connect as."""

    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "lineage_graph"
    database: str = "neo4j"

    @classmethod
    def from_env(cls) -> Neo4jSettings:
        return cls(
            uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
            user=os.environ.get("NEO4J_USER", "neo4j"),
            password=os.environ.get("NEO4J_PASSWORD", "lineage_graph"),
            database=os.environ.get("NEO4J_DATABASE", "neo4j"),
        )


def connect(settings: Neo4jSettings | None = None) -> Any:
    """A driver for the local Neo4j. The caller closes it."""
    from neo4j import GraphDatabase

    resolved = settings or Neo4jSettings.from_env()
    return GraphDatabase.driver(resolved.uri, auth=(resolved.user, resolved.password))


def read_session(driver: Any, settings: Neo4jSettings | None = None) -> Any:
    """A session in which Neo4j itself refuses every write."""
    from neo4j import READ_ACCESS

    resolved = settings or Neo4jSettings.from_env()
    return driver.session(database=resolved.database, default_access_mode=READ_ACCESS)


def is_available(settings: Neo4jSettings | None = None) -> bool:
    """True if a connection can be opened right now.

    Used to skip Neo4j-dependent tests rather than fail them, so the suite stays useful on
    a machine with no container running, or without the knowledge-graph dependencies.
    """
    try:
        with connect(settings) as driver:
            driver.verify_connectivity()
            return True
    except Exception:
        return False
