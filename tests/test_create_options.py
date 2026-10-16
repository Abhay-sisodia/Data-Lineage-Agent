"""Finding F0-01: a CREATE that SQLGlot keeps as a raw `Command` vanished.

`CREATE MATERIALIZED VIEW ... WITH DATA`, `CREATE RECURSIVE VIEW`, `CREATE VIEW ... WITH CHECK
OPTION` and `CREATE TEMP TABLE ... ON COMMIT DROP AS` each parsed as an opaque Command,
whose kind band 0 skipped BEFORE counting it: statements seen 0, no refusal, no boundary,
and the lineage of everything the statement created gone (final suite, Step 0 probe).

`pg_grammar` now restates those options; each test pins that the restated statement gives
the lineage of its plain spelling. Anything still opaque is counted and refused, never
skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.parsing.pg_grammar import restate
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def _result(sql: str):
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    return analyse_source(sql, Dictionary.load(BOOK), config, None)


def _facts(sql: str) -> set[tuple[str, str, str, str]]:
    result = _result(sql)
    assert result.refusals == [], result.refusals
    assert result.statements_seen >= 1
    return {(e.flow.value, str(e.source), str(e.target), e.transform.value) for e in result.edges}


QUERY = "SELECT o.id, o.amount FROM staging.orders AS o WHERE o.status = 'paid'"

SPELLINGS = {
    "matview WITH DATA": (
        f"CREATE MATERIALIZED VIEW mart.mv_x AS {QUERY} WITH DATA",
        f"CREATE MATERIALIZED VIEW mart.mv_x AS {QUERY}",
    ),
    "matview TABLESPACE": (
        f"CREATE MATERIALIZED VIEW mart.mv_x TABLESPACE pg_default AS {QUERY}",
        f"CREATE MATERIALIZED VIEW mart.mv_x AS {QUERY}",
    ),
    "view WITH CHECK OPTION": (
        f"CREATE VIEW mart.v_x AS {QUERY} WITH CASCADED CHECK OPTION",
        f"CREATE VIEW mart.v_x AS {QUERY}",
    ),
    "view WITH (options)": (
        f"CREATE VIEW mart.v_x WITH (security_barrier = true) AS {QUERY}",
        f"CREATE VIEW mart.v_x AS {QUERY}",
    ),
    "temp table ON COMMIT DROP": (
        f"CREATE TEMP TABLE tmp_x ON COMMIT DROP AS {QUERY}",
        f"CREATE TEMP TABLE tmp_x AS {QUERY}",
    ),
    "table TABLESPACE, WITHOUT OIDS": (
        f"CREATE TABLE mart.t_x WITHOUT OIDS TABLESPACE fast AS {QUERY}",
        f"CREATE TABLE mart.t_x AS {QUERY}",
    ),
    "CREATE TABLE AS TABLE": (
        "CREATE TABLE mart.t_x AS TABLE staging.payments",
        "CREATE TABLE mart.t_x AS SELECT * FROM staging.payments",
    ),
}


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_each_option_gives_the_plain_spellings_lineage(spelling: str) -> None:
    written, plain = SPELLINGS[spelling]
    expected = _facts(plain)
    assert expected, "the plain spelling must carry lineage for the comparison to mean anything"
    assert _facts(written) == expected


def test_a_recursive_view_is_its_documented_with_recursive_equivalent() -> None:
    recursive = (
        "CREATE RECURSIVE VIEW mart.v_tree (id, root) AS "
        "SELECT c.id, c.id FROM core.dim_category AS c WHERE c.parent_id IS NULL "
        "UNION ALL SELECT ch.id, t.root FROM core.dim_category AS ch "
        "JOIN v_tree AS t ON ch.parent_id = t.id"
    )
    equivalent = (
        "CREATE VIEW mart.v_tree AS WITH RECURSIVE v_tree (id, root) AS ("
        "SELECT c.id, c.id FROM core.dim_category AS c WHERE c.parent_id IS NULL "
        "UNION ALL SELECT ch.id, t.root FROM core.dim_category AS ch "
        "JOIN v_tree AS t ON ch.parent_id = t.id) SELECT id, root FROM v_tree"
    )
    assert _facts(recursive) == _facts(equivalent)
    assert ("filter", "column:core.dim_category.parent_id", "relation:mart.v_tree", "identity") in (
        _facts(recursive)
    )


def test_with_no_data_writes_nothing_until_refresh() -> None:
    """B7-06 / D-32 for a materialized view: no rows at CREATE, the definition at REFRESH."""
    created = _result(f"CREATE MATERIALIZED VIEW mart.mv_x AS {QUERY} WITH NO DATA")
    assert created.statements_seen == 1 and created.edges == [] and created.refusals == []

    refreshed = _result(
        f"CREATE MATERIALIZED VIEW mart.mv_x AS {QUERY} WITH NO DATA;\n"
        "REFRESH MATERIALIZED VIEW mart.mv_x;"
    )
    assert {e.origin.line for e in refreshed.edges} == {2}
    assert ("column:staging.orders.amount", "column:mart.mv_x.amount") in {
        (str(e.source), str(e.target)) for e in refreshed.edges
    }


def test_a_create_still_unreadable_is_counted_and_refused_never_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The safety net: an option nothing restates must not make the statement vanish.

    With the restatement switched off, `WITH DATA` is a Command again - exactly the shape
    any option `pg_grammar` does not know yet will take.
    """
    from lineage.parsing import pg_grammar

    monkeypatch.setattr(pg_grammar, "restate", lambda source: source)
    result = _result(
        f"-- a comment in front\nCREATE MATERIALIZED VIEW mart.mv_x AS {QUERY} WITH DATA"
    )
    assert result.statements_seen == 1
    assert [r.code.value for r in result.refusals] == ["PARSE_FAILED"]
    assert result.edges == []


def test_a_command_with_no_query_is_still_not_counted() -> None:
    """The net is for CREATE ... AS <query> only: SET, LOCK, VACUUM still carry nothing."""
    result = _result("LOCK TABLE staging.orders IN EXCLUSIVE MODE;\nVACUUM staging.orders;")
    assert result.statements_seen == 0 and result.refusals == [] and result.edges == []


def test_the_restatement_keeps_lines_and_leaves_other_statements_alone() -> None:
    source = (
        "CREATE MATERIALIZED VIEW mart.mv_x AS\n"
        "SELECT 'WITH DATA' AS label FROM staging.orders\n"
        "WITH DATA;\n"
        "SELECT 'CREATE RECURSIVE VIEW v (a) AS SELECT 1' AS s;\n"
    )
    restated = restate(source)
    assert restated.count("\n") == source.count("\n")
    assert "'WITH DATA'" in restated
    assert "'CREATE RECURSIVE VIEW v (a) AS SELECT 1'" in restated
    assert restated.splitlines()[3] == source.splitlines()[3]
