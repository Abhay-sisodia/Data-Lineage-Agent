"""An `E'...'` literal honours backslash escapes, and the noise-stripper must too (PG-23).

`_strip_noise` blanks comments and string literals so a construct name inside a string
cannot trip the classifier. It treated `''` as the only quote escape, which is right for a
standard-conforming literal and wrong for PostgreSQL's escape string: in `E'O\\'Reilly'` the
`\\'` is a quote INSIDE the string. Read the old way, the scan closed the literal there, and
every later quote in the file flipped polarity - real code blanked, real literals visible.

That breaks the classifier in BOTH directions, and both are pinned here:

* **invent** - a construct name inside a later literal is left visible and refuses a
  statement that has nothing wrong with it;
* **miss** - a real construct after the literal is blanked and the statement is analysed
  as if it were not there.
"""

from __future__ import annotations

from lineage.analysis.procedure import analyse_source
from lineage.analysis.refusal import _strip_noise, classify_statement
from lineage.config import AnalysisConfig
from lineage.resolution.dictionary import Dictionary, ObjectInfo

INVENTS = (
    "INSERT INTO t (a, b) SELECT E'O\\'Reilly', 'see JSON_TABLE( in the docs' FROM s"
)
MISSES = "SELECT E'O\\'Reilly' FROM t CONNECT BY PRIOR id = pid"


def test_an_escaped_quote_does_not_close_an_escape_string() -> None:
    """The prefix letter is left in place, as it always was: blanking starts at the quote."""
    literal = "'O\\'Reilly'"
    clean = _strip_noise(f"SELECT E{literal} AS n FROM t")
    assert clean == "SELECT E" + " " * len(literal) + " AS n FROM t"


def test_offsets_and_line_structure_survive() -> None:
    text = "SELECT E'a\\'\nb' AS n\nFROM t"
    clean = _strip_noise(text)
    assert len(clean) == len(text)
    assert clean.count("\n") == text.count("\n")
    assert clean.endswith(" AS n\nFROM t")


def test_a_construct_name_in_a_later_literal_is_not_invented() -> None:
    """Before the fix: `XMLTABLE` refused this statement from inside a string."""
    assert classify_statement(INVENTS, "postgres") is None


def test_a_real_construct_after_the_literal_is_not_missed() -> None:
    """Before the fix: the desynchronised scan blanked `CONNECT BY` and returned None."""
    construct = classify_statement(MISSES, "postgres")
    assert construct is not None and construct.name == "CONNECT_BY"


def test_lower_case_prefix_is_the_same_string() -> None:
    construct = classify_statement(MISSES.replace("E'", "e'"), "postgres")
    assert construct is not None and construct.name == "CONNECT_BY"


def test_an_escaped_backslash_before_the_closing_quote_closes_it() -> None:
    """`E'C:\\\\'` is the two characters `C:\\` - the second backslash is escaped, so the
    quote after it closes the literal. Treating `\\` as escaping the QUOTE would run on."""
    construct = classify_statement(
        "SELECT E'C:\\\\' FROM t CONNECT BY PRIOR id = pid", "postgres"
    )
    assert construct is not None and construct.name == "CONNECT_BY"


def test_a_keyword_ending_in_e_is_not_an_escape_prefix() -> None:
    """`LIKE'C:\\'` is a standard literal holding `C:\\` - the E belongs to `LIKE`. Read as
    an escape string, the `\\'` would swallow the rest of the statement."""
    construct = classify_statement(
        "SELECT 1 FROM t WHERE n LIKE'C:\\' CONNECT BY PRIOR id = pid", "oracle"
    )
    assert construct is not None and construct.name == "CONNECT_BY"


def test_an_identifier_ending_in_e_is_not_an_escape_prefix() -> None:
    construct = classify_statement(
        "SELECT 1 FROM t WHERE \"code\"||note_E'x\\' CONNECT BY PRIOR id = pid", "oracle"
    )
    assert construct is not None and construct.name == "CONNECT_BY"


def _book() -> Dictionary:
    return Dictionary(
        captured_at="2026-10-01T00:00:00Z",
        default_schema="public",
        dialect="postgres",
        objects={
            name: ObjectInfo(owner="public", name=name.split(".")[1], object_type="TABLE")
            for name in ("public.s", "public.t")
        },
        columns={"public.s": ["id", "name"], "public.t": ["a", "b"]},
        temporary={"public.s": False, "public.t": False},
    )


def test_end_to_end_the_statement_is_analysed_not_refused() -> None:
    """The property a user sees: the edge exists and nothing was refused."""
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    result = analyse_source(
        "INSERT INTO t (a, b) SELECT s.name, E'O\\'Reilly' FROM s "
        "WHERE s.name <> 'see JSON_TABLE( in the docs'",
        _book(),
        config,
        None,
    )
    assert result.refusals == []
    assert any(
        str(e.source) == "column:s.name" and str(e.target) == "column:t.a" for e in result.edges
    )
