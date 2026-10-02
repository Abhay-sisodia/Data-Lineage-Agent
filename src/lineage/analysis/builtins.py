"""Built-in functions SQLGlot does not model, by dialect (findings B1-04 and PG-04).

SQLGlot parses the functions it knows into typed nodes and everything else into
`exp.Anonymous`. `band0._call_site_edges` reads Anonymous as "a user-defined routine whose
source is not in this analysis" - right for `pkg.calc_margin(x)`, wrong for a built-in the
parser simply has no class for. `cardinality(array_agg(x))` was declared
`source_unavailable` and lost `x` (B1-04); `jsonb_build_object('k', x)` lost `x` the same
way (PG-04). The refusal was honest that something was missing and wrong about what.

A name listed here is a built-in: its value derives from its arguments, so the ordinary
value rule applies and the transform ladder classifies it like any typed function. The
lists are deliberately the functions measured to arrive as `Anonymous` in SQLGlot's
PostgreSQL dialect - a typed node never reaches this code, so listing `upper` would be
dead weight - plus their obvious siblings. Unqualified names only: `myschema.cardinality`
is a user routine that happens to share a name.

**Aggregates are listed apart** because they change more than whether an edge exists: an
aggregate makes the transform `aggregated` and brings the GROUP BY's influence with it.
`jsonb_agg(x)` read as a scalar would be the B1-03 mistake again.
"""

from __future__ import annotations

SCALAR = {
    "postgres": frozenset(
        {
            # arrays
            "CARDINALITY", "ARRAY_DIMS", "ARRAY_UPPER", "ARRAY_LOWER", "ARRAY_NDIMS",
            # json / jsonb construction and access
            "JSONB_BUILD_OBJECT", "JSON_BUILD_OBJECT", "JSONB_BUILD_ARRAY", "JSON_BUILD_ARRAY",
            "TO_JSONB", "TO_JSON", "ROW_TO_JSON", "JSONB_SET", "JSONB_INSERT",
            "JSONB_STRIP_NULLS", "JSON_STRIP_NULLS", "JSONB_EXTRACT_PATH_TEXT",
            "JSON_EXTRACT_PATH_TEXT", "JSONB_EXTRACT_PATH", "JSON_EXTRACT_PATH", "JSONB_TYPEOF",
            "JSON_TYPEOF", "JSONB_PRETTY",
            # text
            "REGEXP_MATCHES", "OCTET_LENGTH", "QUOTE_IDENT", "QUOTE_LITERAL", "QUOTE_NULLABLE",
            # date and time
            "AGE", "MAKE_DATE", "MAKE_TIMESTAMP", "MAKE_TIMESTAMPTZ", "MAKE_INTERVAL",
            "TIMEZONE", "ISFINITE", "CLOCK_TIMESTAMP",
            # misc
            "NUM_NONNULLS", "NUM_NULLS", "HSTORE",
            # MERGE ... RETURNING merge_action() (PostgreSQL 17): reports the arm, no column
            "MERGE_ACTION",
            # finding B4-03: a row constructor, regex, range and numeric built-ins.
            # `regexp_split_to_table` is left out on purpose: it returns a SET, and a
            # set-returning function in a select list changes the row count (batch B6).
            "ROW", "REGEXP_MATCH", "REGEXP_SPLIT_TO_ARRAY",
            "TSTZRANGE", "TSRANGE", "DATERANGE", "NUMRANGE", "INT4RANGE", "INT8RANGE",
            "TSTZMULTIRANGE", "TSMULTIRANGE", "DATEMULTIRANGE", "NUMMULTIRANGE",
            "INT4MULTIRANGE", "INT8MULTIRANGE", "MULTIRANGE", "RANGE_MERGE",
            "ISEMPTY", "LOWER_INC", "UPPER_INC", "LOWER_INF", "UPPER_INF",
            "ARRAY_POSITIONS", "ARRAY_REPLACE", "TRIM_SCALE", "SCALE", "MIN_SCALE",
            "GCD", "LCM",
        }
    ),
}

AGGREGATE = {
    "postgres": frozenset(
        {"JSONB_AGG", "JSON_AGG", "JSONB_OBJECT_AGG", "JSON_OBJECT_AGG", "EVERY", "XMLAGG"}
    ),
}

ALL_AGGREGATES = frozenset().union(*AGGREGATE.values())
"""Every dialect's built-in aggregates. `transforms.is_aggregate` has no dialect to hand,
and an aggregate's name means the same thing wherever it parses."""


def is_builtin(name: str, dialect: str) -> bool:
    """Is this unqualified, upper-cased function name a built-in of `dialect`?"""
    key = (dialect or "").lower()
    return name in SCALAR.get(key, frozenset()) or name in AGGREGATE.get(key, frozenset())
