"""Plan step 8: the grammar checklist's gaps, closed - each with the case that found it.

The checklist (`docs/pg-coverage.md`) measures every construct; these tests name the fixes,
so a regression says which one moved rather than only that a snapshot changed.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "grammar" / "dictionary.json"


@pytest.fixture(scope="module")
def book() -> Dictionary:
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    return Dictionary.load(BOOK)


def _run(sql: str, book: Dictionary):
    return analyse_source(sql, book, AnalysisConfig(dialect="postgres"))


def _pairs(result) -> set[tuple[str, str]]:
    return {(str(e.source), str(e.target)) for e in [*result.edges, *result.join_keys]}


def _reaches(result, source: str, target: str) -> bool:
    nxt: dict[str, set[str]] = {}
    for a, b in _pairs(result):
        nxt.setdefault(a, set()).add(b)
    seen, todo = {source}, [source]
    while todo:
        for b in nxt.get(todo.pop(), ()):
            if b == target:
                return True
            if b not in seen:
                seen.add(b)
                todo.append(b)
    return False


def test_a_self_assignment_after_a_subquery_no_longer_hangs(book) -> None:
    started = time.monotonic()
    result = _run(
        """CREATE FUNCTION tgt.f() RETURNS void LANGUAGE plpgsql AS $$
DECLARE total numeric;
BEGIN
  total := (SELECT sum(a.v) FROM src.a AS a);
  total := total * 2;
  INSERT INTO tgt.t (v) VALUES (total);
END $$;""",
        book,
    )
    assert time.monotonic() - started < 10
    assert _reaches(result, "column:src.a.v", "column:tgt.t.v")


@pytest.mark.parametrize(
    "sql",
    ["CLUSTER tgt.t USING t_v_idx", "NOTIFY load_done, 'x'", "REASSIGN OWNED BY etl TO postgres"],
)
def test_harmless_ddl_is_no_lineage_not_a_parse_failure(sql: str, book) -> None:
    result = _run(f"{sql};", book)
    assert result.refusals == []
    assert [e.status for e in result.ledger] == ["no_lineage"]


@pytest.mark.parametrize(
    ("sql", "source", "target"),
    [
        (
            "CREATE FUNCTION tgt.load() RETURNS void LANGUAGE sql AS $$\n"
            "  INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a\n$$;",
            "column:src.a.v", "column:tgt.t.v",
        ),
        (
            "CREATE FUNCTION tgt.dbl(x numeric) RETURNS numeric LANGUAGE sql\n"
            "  AS $$ SELECT $1 * 2 $$;\n"
            "INSERT INTO tgt.t (v) SELECT tgt.dbl(a.v) FROM src.a AS a;",
            "column:src.a.v", "column:tgt.t.v",
        ),
        (
            "CREATE FUNCTION tgt.fn_rows() RETURNS SETOF src.b LANGUAGE sql\n"
            "  AS $$ SELECT * FROM src.b $$;\n"
            "INSERT INTO tgt.t (v) SELECT r.v FROM tgt.fn_rows() AS r;",
            "column:src.b.v", "column:tgt.t.v",
        ),
    ],
    ids=["void", "scalar", "setof"],
)  # fmt: skip
def test_language_sql_bodies_are_read(sql: str, source: str, target: str, book) -> None:
    result = _run(sql, book)
    assert result.refusals == []
    assert (source, target) in _pairs(result)


@pytest.mark.parametrize(
    ("sql", "source", "target"),
    [
        ("EXPLAIN ANALYZE INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a",
         "column:src.a.v", "column:tgt.t.v"),
        ("ALTER TABLE tgt.t ALTER COLUMN n TYPE varchar(20) USING upper(n)",
         "column:tgt.t.n", "column:tgt.t.n"),
        ("INSERT INTO tgt.t (x) SELECT XMLPI(NAME php, a.n) FROM src.a AS a",
         "column:src.a.n", "column:tgt.t.x"),
        ("INSERT INTO tgt.t (id, k, v, n, d) TABLE src.b", "column:src.b.v", "column:tgt.t.v"),
        ("INSERT INTO tgt.t (v) SELECT x.vv FROM src.a AS x (i, kk, vv)",
         "column:src.a.v", "column:tgt.t.v"),
        ("INSERT INTO tgt.t (v) SELECT a.v FROM src.a AS a FOR KEY SHARE OF a",
         "column:src.a.v", "column:tgt.t.v"),
        ("UPDATE tgt.t AS t SET v = a.v FROM src.a AS a WHERE a.id = t.id "
         "RETURNING WITH (OLD AS o) o.v", "column:src.a.v", "column:tgt.t.v"),
        ("CREATE RULE r AS ON INSERT TO src.a DO ALSO INSERT INTO tgt.t (id, v) "
         "VALUES (NEW.id, NEW.v)", "column:src.a.v", "column:tgt.t.v"),
    ],
    ids=["explain-analyze", "alter-using", "xmlpi", "table", "alias-list", "lock-of",
         "returning-old", "rule"],
)  # fmt: skip
def test_restated_statements_give_their_lineage(sql: str, source: str, target: str, book) -> None:
    result = _run(f"{sql};", book)
    assert result.refusals == []
    assert (source, target) in _pairs(result)


def test_set_default_is_no_source_and_no_unresolved_name(book) -> None:
    result = _run("UPDATE tgt.t SET v = DEFAULT;", book)
    assert result.boundaries == [] and result.refusals == []


@pytest.mark.parametrize(
    "body",
    [
        "DECLARE r record;\nBEGIN\n  SELECT 0::numeric AS v INTO r;\n"
        "  r.v := (SELECT max(a.v) FROM src.a AS a);\n  INSERT INTO tgt.t (v) VALUES (r.v);\nEND",
        "DECLARE new_v numeric;\nBEGIN\n  UPDATE src.a AS a SET v = a.v + 1 WHERE a.id = 1 "
        "RETURNING a.v INTO new_v;\n  INSERT INTO tgt.t (v) VALUES (new_v);\nEND",
    ],
    ids=["record-field-assignment", "returning-into-on-one-line"],
)
def test_plpgsql_forms_now_read(body: str, book) -> None:
    result = _run(f"CREATE FUNCTION tgt.g() RETURNS void LANGUAGE plpgsql AS $$\n{body} $$;", book)
    assert result.refusals == []
    assert _reaches(result, "column:src.a.v", "column:tgt.t.v")


def test_a_callers_argument_reaches_what_the_routine_writes(book) -> None:
    result = _run(
        """CREATE FUNCTION tgt.put(p_v numeric) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO tgt.t (v) VALUES (p_v);
END $$;
SELECT tgt.put(a.v) FROM src.a AS a;""",
        book,
    )
    assert _reaches(result, "column:src.a.v", "column:tgt.t.v")


def test_a_transition_table_reads_the_triggers_table(book) -> None:
    result = _run(
        """CREATE FUNCTION tgt.audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO tgt.t (id, v) SELECT n.id, n.v FROM new_rows AS n;
  RETURN NULL;
END $$;
CREATE TRIGGER a_audit AFTER INSERT ON src.a REFERENCING NEW TABLE AS new_rows
  FOR EACH STATEMENT EXECUTE FUNCTION tgt.audit();""",
        book,
    )
    assert ("column:src.a.v", "column:tgt.t.v") in _pairs(result)


def test_a_lookup_keyed_through_another_lookup_carries_its_keys(book) -> None:
    """Holdout gap G4: the second lookup's key and its dedup ordering are join keys too."""
    result = _run(
        """INSERT INTO tgt.t (id, n)
WITH latest AS (
    SELECT DISTINCT ON (b.id) b.id, b.k, b.n FROM src.b AS b ORDER BY b.id, b.d DESC
)
SELECT a.id, look.n
FROM src.a AS a
LEFT JOIN latest AS l ON l.id = a.id
LEFT JOIN src.b AS look ON look.k = l.k;""",
        book,
    )
    keys = {(str(e.source), str(e.target)) for e in result.join_keys}
    assert ("column:src.b.k", "column:tgt.t.n") in keys  # the first lookup's key
    assert ("column:src.a.id", "column:tgt.t.n") in keys  # G4: the second lookup's key
    assert ("column:src.b.d", "column:tgt.t.n") in keys  # G4: its dedup ordering
