"""The blind third holdout's gaps (R1-R6), each with the smallest case that shows it.

Holdout3 (a telecom-billing ETL, 10 files, 4,218 lines) scored 89.4% recall blind; these are
the causes, fixed - so a regression names which one moved.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lineage.analysis.procedure import RunCarry, analyse_source
from lineage.config import AnalysisConfig
from lineage.coverage.scan import checklist_outcomes, scan_source
from lineage.resolution.ddl import ddl_payload
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "grammar" / "dictionary.json"
CONFIG = AnalysisConfig(dialect="postgres")


@pytest.fixture(scope="module")
def book() -> Dictionary:
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    return Dictionary.load(BOOK)


def _pairs(result) -> set[tuple[str, str]]:
    return {(str(e.source), str(e.target)) for e in [*result.edges, *result.join_keys]}


def _run(files: list[str], book: Dictionary):
    """Each file in order, as a folder run analyses them; the results, in order."""
    carry = RunCarry(book)
    return [analyse_source(sql, book, CONFIG, carry=carry) for sql in files]


# --- DDL dictionaries ----------------------------------------------------------------------


def test_a_partition_like_or_inherits_table_has_its_parents_columns() -> None:
    payload = ddl_payload(
        """CREATE TABLE s.p (id int, v numeric) PARTITION BY RANGE (id);
CREATE TABLE s.p1 PARTITION OF s.p FOR VALUES FROM (1) TO (10);
CREATE TABLE s.l (LIKE s.p INCLUDING ALL, extra text);
CREATE TABLE s.i (own text) INHERITS (s.p);""",
        "s",
    )
    assert payload["columns"]["s.p1"] == ["id", "v"]
    assert payload["columns"]["s.l"] == ["id", "v", "extra"]
    assert payload["columns"]["s.i"] == ["id", "v", "own"]


# --- R1: what one file builds, the next one sees ---------------------------------------------


def test_a_column_added_by_alter_table_is_a_column(book) -> None:
    (result,) = _run(
        [
            "ALTER TABLE tgt.t ADD COLUMN extra text;\n"
            "INSERT INTO tgt.t (extra) SELECT a.n FROM src.a AS a;"
        ],
        book,
    )
    assert ("column:src.a.n", "column:tgt.t.extra") in _pairs(result)
    assert not [b for b in result.boundaries if "extra" in b.detail]


def test_a_later_file_sees_what_an_earlier_one_created_and_altered(book) -> None:
    _, second = _run(
        [
            "ALTER TABLE tgt.t RENAME COLUMN n TO label;\n"
            "CREATE TABLE tgt.made AS SELECT a.id, a.v AS amount FROM src.a AS a;",
            "INSERT INTO tgt.t (id, label) SELECT m.id, m.amount::text FROM tgt.made AS m;",
        ],
        book,
    )
    assert ("column:tgt.made.amount", "column:tgt.t.label") in _pairs(second)
    assert not [b for b in second.boundaries if "not in dictionary" in b.detail]


def test_a_later_file_calls_a_function_an_earlier_one_defined(book) -> None:
    _, second = _run(
        [
            "CREATE FUNCTION tgt.per(p_k int) RETURNS TABLE (n text) LANGUAGE sql AS $$\n"
            "  SELECT b.n FROM src.b AS b WHERE b.k = p_k $$;",
            "INSERT INTO tgt.t (id, n) SELECT a.id, x.n FROM src.a AS a "
            "LEFT JOIN LATERAL tgt.per(a.k) AS x ON true;",
        ],
        book,
    )
    assert ("column:src.b.n", "column:tgt.t.n") in _pairs(second)


def test_temp_tables_do_not_outlive_their_file(book) -> None:
    carry = RunCarry(book)
    analyse_source(
        "CREATE TEMP TABLE scratch AS SELECT a.id FROM src.a AS a;", book, CONFIG, carry=carry
    )
    assert not [k for k in carry.dictionary.objects if k.startswith("pg_temp.")]


# --- R4 / D-66: values through PL/pgSQL variables -------------------------------------------


def test_select_into_several_targets_is_read(book) -> None:
    result = analyse_source(
        """CREATE FUNCTION tgt.g() RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_n text; v_d date;
BEGIN
  SELECT b.n, coalesce(b.d, now()::date) INTO STRICT v_n, v_d FROM src.b AS b WHERE b.k = 1;
  INSERT INTO tgt.t (n, d) VALUES (v_n, v_d);
END $$;""",
        book,
        CONFIG,
    )
    assert result.refusals == []
    assert {
        ("variable:tgt.g.v_n", "column:tgt.t.n"),
        ("variable:tgt.g.v_d", "column:tgt.t.d"),
    } <= _pairs(result)


NEXT_DAY = """CREATE FUNCTION tgt.nbd(p_date date, p_k int) RETURNS date LANGUAGE plpgsql AS $$
DECLARE v_day date := p_date;
BEGIN
  WHILE EXISTS (SELECT 1 FROM src.b AS h WHERE h.d = v_day AND h.k = p_k) LOOP
    v_day := v_day + 1;
  END LOOP;
  RETURN v_day;
END $$;
"""


def test_a_function_returning_a_local_copy_of_its_argument_carries_it(book) -> None:
    result = analyse_source(
        NEXT_DAY + "INSERT INTO tgt.t (d) SELECT tgt.nbd(a.d, a.k) FROM src.a AS a;", book, CONFIG
    )
    assert ("column:src.a.d", "column:tgt.t.d") in _pairs(result)


def test_a_record_field_passed_to_such_a_function_is_its_value(book) -> None:
    result = analyse_source(
        NEXT_DAY
        + """CREATE PROCEDURE tgt.p() LANGUAGE plpgsql AS $$
DECLARE cal record;
BEGIN
  FOR cal IN SELECT b.d, b.k FROM src.b AS b WHERE b.n = 'x' LOOP
    INSERT INTO tgt.t (d) SELECT tgt.nbd(cal.d, cal.k) FROM src.a AS a;
  END LOOP;
END $$;""",
        book,
        CONFIG,
    )
    pairs = _pairs(result)
    assert ("column:src.b.d", "column:tgt.t.d") in pairs
    assert ("column:src.b.k", "column:tgt.t.d") not in pairs  # only steers the loop


# --- R3: statements SQLGlot could not read ---------------------------------------------------


def test_transaction_control_is_no_lineage(book) -> None:
    result = analyse_source(
        "BEGIN;\nSAVEPOINT s;\nRELEASE SAVEPOINT s;\nROLLBACK TO SAVEPOINT s;\nCOMMIT;",
        book,
        CONFIG,
    )
    assert result.refusals == []
    assert {e.status for e in result.ledger} == {"no_lineage"}


def test_on_conflict_after_a_trailing_cross_join_lateral(book) -> None:
    result = analyse_source(
        "INSERT INTO tgt.t (id, v) SELECT a.id, a.v FROM src.a AS a UNION ALL "
        "SELECT b.id, b.v + g.i FROM src.b AS b CROSS JOIN LATERAL generate_series(1, b.k) "
        "AS g(i) ON CONFLICT DO NOTHING;",
        book,
        CONFIG,
    )
    assert result.refusals == []
    assert {("column:src.a.v", "column:tgt.t.v"), ("column:src.b.v", "column:tgt.t.v")} <= _pairs(
        result
    )


# --- R6: the scan predicts what the ledger then says ----------------------------------------


@pytest.mark.parametrize(
    ("sql", "prediction", "status"),
    [
        ("CREATE INDEX ix ON tgt.t (v);", "no lineage", "no_lineage"),
        ("DELETE FROM tgt.t AS t USING src.a AS a WHERE a.id = t.id;", "no lineage", "no_lineage"),
        ("DELETE FROM tgt.t AS t USING src.a AS a WHERE a.id = t.id AND a.n = 'x';",
         "column-level", "column_level"),
        ("ALTER TABLE tgt.t ALTER COLUMN n TYPE varchar(9) USING upper(n);",
         "column-level", "column_level"),
        ("ALTER TABLE tgt.t ADD COLUMN extra text;", "no lineage", "no_lineage"),
        ("CREATE FUNCTION tgt.f(p int) RETURNS int LANGUAGE plpgsql AS $$\n"
         "DECLARE v int;\nBEGIN\n  SELECT a.k INTO v FROM src.a AS a WHERE a.id = p;\n"
         "  RETURN v;\nEND $$;", "no lineage", "no_lineage"),
        ("DO $$ DECLARE n text; BEGIN n := (SELECT a.n FROM src.a AS a LIMIT 1);\n"
         "  EXECUTE format('CREATE TABLE tgt.%I (i int)', n); END $$;", "declared", "declared"),
    ],
    ids=["index", "key-only-delete", "filtering-delete", "alter-using", "add-column",
         "read-only-function", "dynamic-ddl"],
)  # fmt: skip
def test_the_scan_predicts_the_ledger(sql: str, prediction: str, status: str, book) -> None:
    (scanned,) = scan_source(sql, "x.sql", checklist_outcomes())
    (entry,) = analyse_source(sql, book, CONFIG).ledger
    assert (scanned.prediction, entry.status) == (prediction, status)
