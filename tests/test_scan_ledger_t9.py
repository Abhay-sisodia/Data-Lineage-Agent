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
