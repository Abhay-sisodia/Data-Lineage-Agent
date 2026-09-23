"""A dictionary inferred from SQL under-reports, and must never invent.

`infer_dictionary` exists for the ordinary case where the real schema is in no captured
catalogue - a Databricks model against `silver_s2p`, say - and without it every table in
such a file is a dangling reference and no lineage comes out at all.

**The governing rule is asymmetric on purpose.** Missing a column costs a dangling
reference, which is visible and survivable. Inventing one puts a name in the dictionary
that the real table does not have, and everything downstream then resolves against a schema
that does not exist - a wrong answer wearing the same clothes as a right one. So the
attribution rules are the two where the answer is DETERMINED, and everything else is
dropped and counted.
"""

from __future__ import annotations

import pytest

from lineage.resolution.infer import INFERRED_MARKER, infer_dictionary, is_inferred


def test_a_qualified_reference_is_attributed() -> None:
    book, _ = infer_dictionary(
        "SELECT o.cust_id, o.amount FROM sales.orders o", default_schema="sales"
    )
    assert book.columns["sales.orders"] == ["cust_id", "amount"]


def test_a_single_table_select_attributes_its_unqualified_columns() -> None:
    """DETERMINED, not guessed: there is nowhere else the column could come from.

    This is the single-table CTE, which is most of what a warehouse model is made of.
    Without it those relations land in the dictionary with NO columns, nothing selected
    from them resolves, and `qualify` then fails the whole statement - measured on a real
    Databricks model, where one such CTE took the result from 20 edges to zero.
    """
    book, warnings = infer_dictionary(
        """
        WITH lei AS (
            SELECT legal_entity_identifier, transacting_entity_indicator
              FROM silver.legal_entity_profiles
             WHERE current_indicator = true
        )
        SELECT legal_entity_identifier FROM lei
        """,
        default_schema="silver",
    )
    assert set(book.columns["silver.legal_entity_profiles"]) == {
        "legal_entity_identifier",
        "transacting_entity_indicator",
        "current_indicator",
    }
    assert not any("no column is attributed" in w for w in warnings)


def test_a_join_does_not_attribute_unqualified_columns() -> None:
    """The negative half, and the one that makes the test above safe.

    `SELECT cust_id FROM a JOIN b` has two candidates. Picking either would invent a fact,
    so the column is dropped and COUNTED - the count is what tells a reader the dictionary
    is thinner than their SQL.
    """
    book, warnings = infer_dictionary(
        "SELECT cust_id, a.x, b.y FROM sales.a a JOIN sales.b b ON a.k = b.k",
        default_schema="sales",
    )
    assert "cust_id" not in book.columns["sales.a"]
    assert "cust_id" not in book.columns["sales.b"]
    assert any("name no table" in w for w in warnings)


def test_a_cte_is_not_put_in_the_dictionary() -> None:
    """A CTE is not a stored object.

    Putting one in would make lineage stop AT the CTE instead of resolving through it to
    the base tables, which is the entire job.
    """
    book, _ = infer_dictionary(
        """
        WITH scoped AS (SELECT o.cust_id FROM sales.orders o)
        SELECT s.cust_id FROM scoped s
        """,
        default_schema="sales",
    )
    assert set(book.objects) == {"sales.orders"}


def test_the_provenance_is_impossible_to_mistake_for_a_capture() -> None:
    """`captured_at` carries a value no real capture can produce.

    Provenance has to survive being saved to disk and read back by someone who was not
    here, because every number computed against this dictionary inherits it.
    """
    book, _ = infer_dictionary("SELECT o.x FROM sales.orders o", default_schema="sales")
    assert book.captured_at == INFERRED_MARKER
    assert is_inferred(book)
    assert not is_inferred(
        book.model_copy(update={"captured_at": "2026-09-24T00:00:00Z"})
    )


def test_select_star_is_warned_about_rather_than_expanded() -> None:
    """The quiet failure this inference is most likely to cause.

    An inferred dictionary holds only the columns the text mentions, so expanding `*`
    against it would bind a handful of columns positionally and confidently - and be wrong
    wherever the real table has more.
    """
    _book, warnings = infer_dictionary(
        "SELECT * FROM sales.orders", default_schema="sales"
    )
    assert any("SELECT *" in w for w in warnings)


def test_unparseable_sql_yields_an_empty_dictionary_and_says_so() -> None:
    book, warnings = infer_dictionary("this is not sql at all ((((", default_schema="s")
    assert book.objects == {}
    assert warnings


@pytest.mark.parametrize("dialect", ["postgres", "oracle"])
def test_inference_folds_identifiers_for_its_dialect(dialect: str) -> None:
    """Names must be folded the way the analyser will fold them when it looks them up.

    An UPPER key in a PostgreSQL dictionary matches nothing and fails SILENTLY as a
    dangling reference - the shape `tests/test_identifier_folding.py` exists for.
    """
    book, _ = infer_dictionary(
        "SELECT O.Cust_Id FROM Sales.Orders O", dialect=dialect, default_schema="Sales"
    )
    expected = "SALES.ORDERS" if dialect == "oracle" else "sales.orders"
    assert expected in book.objects, list(book.objects)
