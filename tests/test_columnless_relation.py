"""PG-16: one column-less relation must not refuse every statement in the run.

SQLGlot's `qualify` raises `Table <name> must have at least one column` when the schema it
is handed carries an empty entry - and it raises for the STATEMENT, not for the reference.
So a single column-less relation anywhere in the dictionary refused every statement,
**including statements that never mention it**.

Found by a user pasting a production Databricks model into the inspection UI. Neither
shipped dictionary has an empty entry, which is why no test, probe or stress package had
ever reached it. A CAPTURE easily can: a table whose columns the capturing user lacks
privileges to read comes back empty, and a permission gap on one unrelated table would take
a whole run to zero edges with a reason naming a table the reader never asked about.

The fix omits empty entries from the SQLGlot schema, so such a reference resolves as
DANGLING - visible, counted, and local to the statement that touches it, which is what the
taxonomy says an unknown object should be.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary, ObjectInfo


@pytest.fixture
def book() -> Dictionary:
    columns = {"s.orders": ["cust_id", "amount"], "s.lonely": []}
    return Dictionary(
        captured_at="2026-09-24T00:00:00Z",
        default_schema="s",
        dialect="postgres",
        objects={
            key: ObjectInfo(owner="s", name=key.split(".")[1], object_type="TABLE")
            for key in columns
        },
        columns=columns,
        temporary=dict.fromkeys(columns, False),
    )


@pytest.fixture
def config() -> AnalysisConfig:
    return AnalysisConfig.load().model_copy(update={"dialect": "postgres"})


def test_a_columnless_relation_does_not_refuse_unrelated_statements(
    book: Dictionary, config: AnalysisConfig
) -> None:
    """The blast radius, which is the whole finding.

    This query does not mention `s.lonely` at all. Before the fix it was refused
    NAME_NOT_RESOLVED, with a reason naming a table the reader never asked about.
    """
    result = analyse_source("SELECT o.cust_id, o.amount FROM s.orders o;", book, config)

    assert result.refusals == [], f"refused by an unrelated empty entry: {result.refusals}"
    assert len(result.edges) == 2


def test_reading_the_columnless_relation_is_declared_locally(
    book: Dictionary, config: AnalysisConfig
) -> None:
    """The other half: the gap is still reported, just proportionately.

    Omitting the entry must not make an unknown column silently fine. It becomes a
    dangling reference on the statement that touches it - visible, counted, and local.
    """
    result = analyse_source("SELECT l.anything FROM s.lonely l;", book, config)

    assert result.refusals == []
    assert not result.edges
    assert any(
        b.kind.value == "unresolved_identifier" for b in result.boundaries
    ), f"the gap vanished entirely: {[(b.kind.value, b.subject) for b in result.boundaries]}"


def test_a_populated_dictionary_is_unaffected(
    book: Dictionary, config: AnalysisConfig
) -> None:
    """The control. Dropping empty entries must not drop entries that have columns.

    Without this, a fix that returned an empty schema for everything would satisfy both
    assertions above and destroy all name resolution.
    """
    from lineage.analysis.band0 import _schema_for

    schema = _schema_for(book)
    assert "orders" in schema and schema["orders"], "a populated relation was dropped"
    assert "lonely" not in schema
