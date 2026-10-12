"""D-57: a lookup join's key is reported as `join_key`, apart from the scored edges.

D-5 gives a join key no value, filter or influence edge, and every closed-world key is
written that way. The production pilot showed what that loses: `raw.orders.status` mapped
through `ref."OrderStatusMap"` had no lineage to the order status it decides. The key is
now its own claim - `AnalysisResult.join_keys`, graph relationship `KEYS` - so the answer
keys are untouched and impact, the explorer and the agent can still follow it.
"""

from __future__ import annotations

import json
from pathlib import Path

from lineage.analysis import band0
from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.export import from_document, to_document
from lineage.graph.model import build_batch
from lineage.ir.model import Flow
from lineage.resolution.dictionary import Dictionary

ROOT = Path(__file__).resolve().parent.parent
BOOK = Dictionary.model_validate(
    json.loads((ROOT / "stress" / "pg" / "dictionary.json").read_text(encoding="utf-8"))
)
POSTGRES = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
TARGET = "INSERT INTO mart.top_customers (region, customer_id, full_name, revenue, position)\n"

LOOKUP = (
    TARGET + "SELECT c.country_code, o.customer_id, c.full_name, o.amount * f.rate_to_usd, 1\n"
    "FROM staging.orders AS o\n"
    "LEFT JOIN staging.customers AS c ON c.id = o.customer_id\n"
    "LEFT JOIN staging.fx_rates AS f\n"
    "  ON f.currency = upper(o.currency) AND f.rate_date = o.order_ts::date;\n"
)


def _facts(edges: list) -> set[tuple[str, str]]:
    return {(str(e.source).split(":")[-1], str(e.target).split(":")[-1]) for e in edges}


def test_a_lookup_key_is_a_join_key_of_what_the_lookup_supplies() -> None:
    result = analyse_source(LOOKUP, BOOK, POSTGRES)
    assert all(edge.flow is Flow.JOIN_KEY for edge in result.join_keys)
    assert _facts(result.join_keys) == {
        ("staging.orders.customer_id", "mart.top_customers.region"),
        ("staging.orders.customer_id", "mart.top_customers.full_name"),
        # Both equalities of the FX lookup, through the expressions they sit in.
        ("staging.orders.currency", "mart.top_customers.revenue"),
        ("staging.orders.order_ts", "mart.top_customers.revenue"),
    }


def test_join_keys_never_enter_the_scored_edges() -> None:
    result = analyse_source(LOOKUP, BOOK, POSTGRES)
    assert all(edge.flow is not Flow.JOIN_KEY for edge in result.edges)
    # The edges are exactly what band 0 says without join keys (D-5 unchanged).
    plain = band0.analyse_source(LOOKUP, BOOK, POSTGRES)
    assert plain.join_keys == [] and _facts(result.edges) == _facts(plain.edges)


def test_a_lookup_in_a_cte_gives_the_same_keys_as_one_written_inline() -> None:
    in_cte = (
        TARGET + "WITH looked AS (\n"
        "  SELECT o.customer_id, o.amount, c.full_name, c.country_code\n"
        "  FROM staging.orders AS o LEFT JOIN staging.customers AS c ON c.id = o.customer_id\n"
        ")\n"
        "SELECT l.country_code, l.customer_id, l.full_name, l.amount, 1 FROM looked AS l;\n"
    )
    inline = (
        TARGET + "SELECT c.country_code, o.customer_id, c.full_name, o.amount, 1\n"
        "FROM staging.orders AS o LEFT JOIN staging.customers AS c ON c.id = o.customer_id;\n"
    )
    keys = _facts(analyse_source(in_cte, BOOK, POSTGRES).join_keys)
    assert keys == _facts(analyse_source(inline, BOOK, POSTGRES).join_keys)
    assert keys == {
        ("staging.orders.customer_id", "mart.top_customers.region"),
        ("staging.orders.customer_id", "mart.top_customers.full_name"),
    }


def test_a_preserved_side_has_no_key() -> None:
    """The FROM side keeps its rows whatever the join finds; a RIGHT join preserves the other."""
    right = (
        TARGET + "SELECT c.country_code, c.id, c.full_name, o.amount, 1\n"
        "FROM staging.orders AS o RIGHT JOIN staging.customers AS c ON c.id = o.customer_id;\n"
    )
    assert analyse_source(right, BOOK, POSTGRES).join_keys == []


def test_join_keys_round_trip_and_load_as_keys(tmp_path: Path) -> None:
    result = analyse_source(LOOKUP, BOOK, POSTGRES)
    document = to_document(
        result,
        source_path="lookup.sql",
        source_text=LOOKUP,
        dialect="postgres",
        config_fingerprint="c",
        dictionary_fingerprint="d",
    )
    back = from_document(json.loads(json.dumps(document)))
    assert back.join_keys == result.join_keys and back.edges == result.edges
    assert len(build_batch([back]).relationships["KEYS"]) == len(result.join_keys)
    # A document written before D-57 has no join keys, and still reads.
    del document["join_keys"]
    assert from_document(document).join_keys == []
