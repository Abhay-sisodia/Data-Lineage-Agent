"""The Cypher fallback's lexical checks: read-only, known schema, a row limit."""

from __future__ import annotations

import pytest

from lineage.agent.cypher_guard import ROW_LIMIT, check

GOOD = "MATCH (c:Column)-[f:FILTERS]->(t:Table) WHERE c.id ENDS WITH '.status_code' RETURN f.id"


def test_a_read_query_passes_and_gets_a_row_limit() -> None:
    checked = check(GOOD)
    assert checked.ok and checked.query.endswith(f"LIMIT {ROW_LIMIT}")
    assert check(f"{GOOD} LIMIT 5").query.endswith("LIMIT 5")


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) DETACH DELETE n",
        "MATCH (n:Column) SET n.x = 1 RETURN n",
        "CREATE (:Column {id: 'x'}) RETURN 1",
        "MERGE (n:Table {id: 'x'}) RETURN n",
        "CALL db.labels() YIELD label RETURN label",
        "LOAD CSV FROM 'file:///x' AS row RETURN row",
        "MATCH (n:Secret) RETURN n",
        "MATCH (a)-[r:OWNS]->(b) RETURN r",
        "MATCH (n:Column) RETURN n; MATCH (m) DELETE m",
        "MATCH (n:Column)",
    ],
)
def test_writes_unknown_schema_and_stacked_statements_are_rejected(query: str) -> None:
    assert not check(query).ok


def test_keywords_inside_strings_do_not_trip_the_check() -> None:
    assert check("MATCH (c:Column) WHERE c.id = 'mart.delete_log.set' RETURN c.id").ok
