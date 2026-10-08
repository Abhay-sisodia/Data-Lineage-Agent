"""C0 gate: the knowledge-graph store is reachable, and reads are read-only by construction.

Neo4j Community has no role-based users, so the agent's queries cannot run as a read-only
account. `read_session` opens sessions in READ access mode instead; these tests pin that
Neo4j itself then refuses a write, rather than trusting the query validator alone.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.requires_neo4j


def test_neo4j_accepts_a_connection(neo4j_driver: Any) -> None:
    from lineage.graph.neo4j_conn import read_session

    with read_session(neo4j_driver) as session:
        assert session.run("RETURN 1 AS one").single()["one"] == 1


def test_a_read_session_refuses_a_write(neo4j_driver: Any) -> None:
    from neo4j.exceptions import ClientError

    from lineage.graph.neo4j_conn import read_session

    with read_session(neo4j_driver) as session, pytest.raises(ClientError):
        session.execute_read(lambda tx: tx.run("CREATE (:C0Probe {at: 1})").consume())

    with read_session(neo4j_driver) as session:
        left = session.run("MATCH (n:C0Probe) RETURN count(n) AS n").single()["n"]
    assert left == 0, "the write went through a read session"
