"""Finding F0-08: a statement starts at its first code token, not at the comments above it.

The script splitter placed a statement on the first line of the comment block in front of
it, so a refusal could point many lines above the statement it is about. The line and the
text now both start at the first code token, keeping `last_line` and every offset counted
from the statement's line consistent; a trailing block of comments is no statement at all.
"""

from __future__ import annotations

from pathlib import Path

from lineage.analysis.procedure import analyse_source
from lineage.config import AnalysisConfig
from lineage.parsing.sqlscript import split_statements
from lineage.resolution.dictionary import Dictionary

BOOK = Path(__file__).resolve().parent.parent / "stress" / "pg" / "dictionary.json"


def test_a_statement_starts_at_its_first_code_token() -> None:
    source = (
        "-- header\n"  # 1
        "-- more\n"  # 2
        "SELECT 1;\n"  # 3
        "\n"  # 4
        "/* a block\n"  # 5
        "   comment */ INSERT INTO t VALUES (1);\n"  # 6
        "SELECT 'not -- a comment'; SELECT 3\n"  # 7
        ";\n"  # 8
        "-- trailing only\n"  # 9
    )
    assert split_statements(source) == [
        (3, "SELECT 1"),
        (6, "INSERT INTO t VALUES (1)"),
        (7, "SELECT 'not -- a comment'"),
        (7, "SELECT 3"),
    ]


def test_a_refusal_points_at_the_statement_not_its_comment_block() -> None:
    source = (
        "-- S8 · clear the audit table\n"
        "-- (a second line of explanation)\n"
        "\n"
        "TRUNCATE mart.load_audit;\n"
    )
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(source, Dictionary.load(BOOK), config, None)
    assert [(r.line, r.end_line) for r in result.refusals] == [(4, 4)]
