"""A refusal reason must be true of the dialect it is raised against (finding PG-17).

The refusal register is a flat list of constructs, and until this test existed every entry
was live against every dialect. That is fine for the entries refused because they are
undecidable in principle - a MODEL clause computes by inter-row reference whoever wrote it -
and wrong for the entries that describe ONE language's syntax.

`CONDITIONAL_COMPILATION` is the one a stress file reached. It looks for Oracle's PL/SQL
preprocessor, whose directives are spelled with `$`; PostgreSQL uses `$` as a QUOTING
character, so both halves of the pattern collide with ordinary PostgreSQL text:

    `$$[A-Za-z_]\\w*`  intends `$$PLSQL_UNIT`, also matches `$$it's a value$$`
    `\\$IF\\b`          intends `$IF`,         also matches the dollar-quote tag `$IF$`

**Why these tests are shaped the way they are.** The first is metamorphic and carries the
finding's measurement: two spellings PostgreSQL treats as the same value must produce the
same lineage, and before the fix one produced three edges and the other produced none. No
ground-truth key is involved, so there is nothing to argue about. The rest pin the halves
that make the first one safe - that Oracle still refuses what it should, because a fix that
buys PostgreSQL correctness by breaking Oracle detection has moved a signed measurement
rather than repaired a defect.

The last test is a STRUCTURAL guard rather than a case. Re-narrowing one regex would have
closed this instance and left the seam open; the guard asserts the property directly, so
the next Oracle-only construct added without a `dialects` scope fails here rather than in
someone's PostgreSQL run.
"""

from __future__ import annotations

import pytest

from lineage.analysis.procedure import analyse_source
from lineage.analysis.refusal import (
    CONSTRUCTS,
    classify_statement,
    conditional_compilation_spans,
)
from lineage.config import AnalysisConfig
from lineage.resolution.infer import infer_dictionary

# Identical apart from how one string literal is quoted. PostgreSQL treats `'x'` and `$$x$$`
# as the same value, so the lineage must not depend on the choice.
SINGLE_QUOTED = """
INSERT INTO sales.audit (cust_id, note, amount)
SELECT o.cust_id, 'manual review' , o.amount
FROM   sales.orders o
WHERE  o.status = 'OPEN'
"""

DOLLAR_QUOTED = """
INSERT INTO sales.audit (cust_id, note, amount)
SELECT o.cust_id, $$manual review$$, o.amount
FROM   sales.orders o
WHERE  o.status = 'OPEN'
"""


def _lineage(sql: str) -> set[tuple[str, ...]]:
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    book, _ = infer_dictionary(sql, dialect="postgres", default_schema="sales")
    result = analyse_source(sql, book, config, None)
    return {
        (str(e.source), str(e.target), e.flow.value, e.transform.value) for e in result.edges
    }


def test_dollar_quoting_does_not_change_the_lineage() -> None:
    """The finding's measurement, kept as a test.

    Before the fix: 3 edges at 100% coverage against 0 edges, 1 refusal, 0% coverage. The
    losses included `orders.status -> audit` (filter), which has no connection to the
    literal at all - the refusal is per STATEMENT, so one quoting choice took the whole
    thing.
    """
    assert _lineage(SINGLE_QUOTED) == _lineage(DOLLAR_QUOTED)


def test_the_dollar_quoted_form_is_not_refused() -> None:
    """Stated separately from the pair above because equality alone is satisfiable by BOTH
    sides producing nothing, which would be a regression wearing a passing test as a
    disguise."""
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    book, _ = infer_dictionary(DOLLAR_QUOTED, dialect="postgres", default_schema="sales")
    result = analyse_source(DOLLAR_QUOTED, book, config, None)

    assert result.refusals == []
    assert result.parse_coverage == 1.0
    assert len(result.edges) == 3


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("SELECT $$it's a 'quoted' $ value$$ AS v FROM t", id="untagged"),
        pytest.param("SELECT $tag$ nested $$ dollars $tag$ AS v FROM t", id="tagged"),
        pytest.param("SELECT $q$O'Reilly$q$ AS v FROM t", id="short-tag"),
        pytest.param("SELECT $$$$ AS v FROM t", id="empty"),
        pytest.param("SELECT $IF$plain text$IF$ AS v FROM t", id="dollar-tag-named-IF"),
    ],
)
def test_postgres_dollar_quoting_is_never_conditional_compilation(sql: str) -> None:
    """Only `untagged` and `dollar-tag-named-IF` ever failed; the others are here to record
    that the collision was with a SUBSET of dollar-quoting rather than the feature. A defect
    that fires on `$$word$$` and not on `$tag$word$tag$` is exactly the kind that survives
    casual testing, so the passing cases are worth pinning too.

    `dollar-tag-named-IF` is a SELECT rather than the more natural `DO $IF$ ... $IF$`,
    which the first draft used and which fails for an unrelated and entirely correct
    reason: `DO` is a PL/pgSQL anonymous block, refused by `DO_BLOCK` per ADR-0002 section
    5. Asserting `is None` there would have tested that a legitimate refusal had been
    broken. What matters is the tag, so the tag is put somewhere nothing else objects to.
    """
    assert classify_statement(sql, "postgres") is None


def test_oracle_still_refuses_its_own_directives() -> None:
    """The half that makes the fix a repair rather than a trade.

    Oracle conditional compilation is genuinely context-dependent: `PLSQL_CCFLAGS` decides
    which arm is compiled and the flag is not in the file. Refusing it is correct, and the
    phase-0 measurement depends on it continuing to happen.
    """
    inquiry = classify_statement("BEGIN x := $$PLSQL_UNIT; END;", "oracle")
    directive = classify_statement(
        "$IF DBMS_DB_VERSION.VER_LE_12 $THEN SELECT 1 FROM dual; $END", "oracle"
    )

    assert inquiry is not None and inquiry.name == "CONDITIONAL_COMPILATION"
    assert directive is not None and directive.name == "CONDITIONAL_COMPILATION"


def test_conditional_compilation_spans_are_oracle_only() -> None:
    """The SECOND detection site, which the first draft of this fix missed.

    `$IF` poisons neighbouring statements rather than itself, so the span is computed
    separately from the construct match - by this module's pass 2 and again by
    `band0.analyse_source`. Scoping only the `Construct` would have left both spans live,
    and `$IF$` is a legal PostgreSQL dollar-quote tag. The gate therefore lives inside the
    function, where a third caller cannot forget it.
    """
    oracle_source = "$IF DBMS_DB_VERSION.VER_LE_12 $THEN\nINSERT INTO t (c) VALUES (1);\n$END"
    assert conditional_compilation_spans(oracle_source, "oracle") == [(1, 3)]

    postgres_source = "DO $IF$\nBEGIN PERFORM 1; END\n$IF$;"
    assert conditional_compilation_spans(postgres_source, "postgres") == []


def test_no_unscoped_construct_matches_postgres_dollar_quoting() -> None:
    """Structural guard: the seam, not the instance.

    An entry with `dialects=None` claims to be true of every dialect. Any such entry that
    fires on a plain dollar-quoted string is making that claim falsely, because dollar
    quoting is how PostgreSQL writes a string containing quotes and carries no meaning
    beyond the value. `DO_BLOCK` and `DOLLAR_QUOTED_ROUTINE_BODY` deliberately match `$$`
    and are absent from this check by being scoped, or by needing a `CREATE`/`DO` keyword
    that a bare literal does not supply.
    """
    literal = "SELECT $$it's a 'quoted' $ value$$ AS v FROM t"
    offenders = [
        construct.name
        for construct in CONSTRUCTS
        if construct.dialects is None and construct.pattern.search(literal)
    ]
    assert offenders == [], (
        f"{offenders} claim to apply to every dialect but match an ordinary PostgreSQL "
        f"dollar-quoted string literal; scope them with `dialects=` or narrow the pattern"
    )
