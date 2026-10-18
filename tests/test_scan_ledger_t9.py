"""semantic-ir T9: the scan and the ledger agree on two forms holdout4 split them on."""

from __future__ import annotations

import logging
from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.coverage.scan import scan_folder
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "grammar" / "dictionary.json"

PUBLISH = """CREATE FUNCTION tgt.fn_publish(p_k integer) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE v_rows integer;
BEGIN
  UPDATE tgt.t AS t SET b = true WHERE t.k = p_k AND NOT t.b;
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  RETURN v_rows;
END $$;
"""

PREPARED = """PREPARE p_load(integer) AS
INSERT INTO tgt.t (id, v) SELECT a.id, a.v FROM src.a AS a WHERE a.k = $1;
EXECUTE p_load(1);
DEALLOCATE p_load;
EXECUTE p_load(2);
"""


def _book() -> Dictionary:
    logging.getLogger("sqlglot").setLevel(logging.CRITICAL)
    return Dictionary.load(BOOK)


def test_a_routine_whose_update_sets_constants_under_a_where_is_column_level() -> None:
    """holdout4 09:317: its only edges filter the table it writes - lineage, as at top level;
    the ledger called it `not_analysed` and the scan `column-level`."""
    result = analyse_source(PUBLISH, _book(), AnalysisConfig(dialect="postgres"))
    assert [e.status for e in result.ledger] == ["column_level"]


def test_an_execute_of_a_prepared_write_is_predicted_column_level(tmp_path) -> None:
    """holdout4 02:397: EXECUTE of a prepared INSERT is traced (D-35); after DEALLOCATE the
    name means nothing, so the second EXECUTE is not predicted as a write."""
    path = tmp_path / "load.sql"
    path.write_text(PREPARED, encoding="utf-8", newline="\n")
    predicted = {scan.line: scan.prediction for scan in scan_folder(path)}
    assert predicted[3] == "column-level"
    assert predicted[5] != "column-level"


def test_an_execute_of_a_prepared_insert_of_literals_is_no_lineage(tmp_path) -> None:
    """holdout5 E: `PREPARE p AS INSERT ... VALUES ($1, $2)` writes only its parameters -
    literals; the ledger says no lineage, and so must the scan."""
    path = tmp_path / "targets.sql"
    path.write_text(
        "PREPARE p_set(integer, integer) AS INSERT INTO tgt.t (id, v) VALUES ($1, $2);\n"
        "EXECUTE p_set(1, 2);\n",
        encoding="utf-8",
        newline="\n",
    )
    predicted = {scan.line: scan.prediction for scan in scan_folder(path)}
    assert predicted[2] == "no lineage"


def test_a_rowtype_field_assignment_does_not_make_a_body_unreadable() -> None:
    """holdout5 09:120: libpg_query cannot resolve `v.f := e` for `v t%ROWTYPE` offline, so
    the whole body was unreadable and its writes unseen - the scan said no lineage."""
    from lineage.parsing.pg_referee import verdicts

    sql = (
        "CREATE PROCEDURE tgt.p() LANGUAGE plpgsql AS $$\n"
        "DECLARE v tgt.t%ROWTYPE;\n"
        "BEGIN\n"
        "  v.id := 1;\n"
        "  INSERT INTO tgt.t (id) VALUES (v.id);\n"
        "END $$;\n"
    )
    [verdict] = verdicts(sql)
    assert "plpgsql:unreadable_offline" not in verdict.constructs
    assert verdict.writes == ["tgt.t"]
