"""Fixes from the production pilot (a 20-file PostgreSQL 17 code base, 2026-10-12).

Each was found by running a real-shaped warehouse through the tool, not by a key:

1. `INSERT ... SELECT ... CROSS JOIN tmp AS r ON CONFLICT ...` failed to parse (SQLGlot read
   the `ON` as the join's condition) - four upserts lost, the order fact's among them.
2. `DROP TRIGGER IF EXISTS t ON tbl` was refused as unreadable; every other DROP is passed
   over as carrying no lineage.
3. A view already in a live-pulled catalogue lost its own CREATE VIEW's lineage: the
   write-through-a-view rewrite treated the definition as a write through it.
4. An unparseable statement's `alias.column` text became table nodes (380 in the pilot).
5. A PL/pgSQL routine the reader gave up on was refused with a generic reason; the
   refusal now says what stopped the reader.
"""

from __future__ import annotations

import json
from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.graph.model import statement_tables
from lineage.parsing.rewrite import strip_unparseable_clauses
from lineage.parsing.sqlscript import statement_kind
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
BOOK_PATH = ROOT / "stress" / "pg" / "dictionary.json"
POSTGRES = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})


def _book(**extra: object) -> Dictionary:
    payload = json.loads(BOOK_PATH.read_text(encoding="utf-8"))
    for key, value in extra.items():
        payload[key] = {**payload.get(key, {}), **value}  # type: ignore[dict-item]
    return Dictionary.model_validate(payload)


def test_cross_join_before_on_conflict_parses_and_keeps_lineage() -> None:
    sql = (
        "INSERT INTO mart.top_customers (customer_id, revenue)\n"
        "SELECT o.customer_id, o.amount FROM staging.orders AS o\n"
        "CROSS JOIN mart.events_log AS r\n"
        "ON CONFLICT (customer_id) DO UPDATE SET revenue = EXCLUDED.revenue;\n"
    )
    rewritten = strip_unparseable_clauses(sql)
    assert "CROSS JOIN mart.events_log AS r WHERE TRUE" in rewritten
    assert rewritten.count("\n") == sql.count("\n"), "line numbers must hold"
    assert strip_unparseable_clauses(rewritten) == rewritten, "idempotent"
    result = analyse_source(sql, _book(), POSTGRES, None)
    assert result.refusals == []
    facts = {(str(e.source).split(":")[-1], str(e.target).split(":")[-1]) for e in result.edges}
    assert ("staging.orders.amount", "mart.top_customers.revenue") in facts


def test_drop_trigger_carries_no_lineage_and_is_not_refused() -> None:
    assert statement_kind("DROP TRIGGER IF EXISTS trg ON mart.t", "postgres") == "drop_statement"
    result = analyse_source("DROP TRIGGER IF EXISTS trg ON mart.top_customers;", _book(), POSTGRES)
    assert result.refusals == [] and result.boundaries == []


def test_a_catalogued_view_keeps_its_own_definition() -> None:
    sql = "CREATE OR REPLACE VIEW mart.v_rev AS\nSELECT o.amount AS amt FROM staging.orders AS o;\n"
    book = _book(
        objects={"mart.v_rev": {"owner": "mart", "name": "v_rev", "object_type": "VIEW"}},
        columns={"mart.v_rev": ["amt"]},
        view_text={"mart.v_rev": "SELECT o.amount AS amt FROM staging.orders AS o"},
    )
    targets = {str(e.target).split(":")[-1] for e in analyse_source(sql, book, POSTGRES).edges}
    assert targets == {"mart.v_rev.amt"}


def test_unreadable_sql_yields_only_real_relations() -> None:
    broken = (
        "INSERT INTO core.f (a) SELECT t.x, excluded.y FROM staging.o t "
        "JOIN ref.fx r ON r.k = t.k ((("
    )
    assert statement_tables(broken) == {"core.f", "staging.o", "ref.fx"}
    assert statement_tables("FROBNICATE mart.x WITH ((") == {"mart.x"}


def test_a_refused_routine_says_what_stopped_the_reader() -> None:
    sql = (
        "CREATE OR REPLACE FUNCTION mart.f() RETURNS void LANGUAGE plpgsql AS $fn$\n"
        "DECLARE v_id bigint;\n"
        "BEGIN\n"
        "  INSERT INTO mart.events_log (kind) VALUES ('x') RETURNING id INTO v_id;\n"
        "END;\n"
        "$fn$;\n"
    )
    reasons = [r.reason for r in analyse_source(sql, _book(), POSTGRES).refusals]
    assert any("stopped at: DML ... RETURNING ... INTO" in reason for reason in reasons), reasons
