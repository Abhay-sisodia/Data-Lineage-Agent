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
            "ROW", "REGEXP_MATCH", "REGEXP_SPLIT_TO_ARRAY",
            "TSTZRANGE", "TSRANGE", "DATERANGE", "NUMRANGE", "INT4RANGE", "INT8RANGE",
            "TSTZMULTIRANGE", "TSMULTIRANGE", "DATEMULTIRANGE", "NUMMULTIRANGE",
            "INT4MULTIRANGE", "INT8MULTIRANGE", "MULTIRANGE", "RANGE_MERGE",
            "ISEMPTY", "LOWER_INC", "UPPER_INC", "LOWER_INF", "UPPER_INF",
            "ARRAY_POSITIONS", "ARRAY_REPLACE", "TRIM_SCALE", "SCALE", "MIN_SCALE",
            "GCD", "LCM",
            # B6: set-returning built-ins read element-wise, like unnest (D-28 for series).
            "STRING_TO_TABLE", "REGEXP_SPLIT_TO_TABLE", "GENERATE_SUBSCRIPTS", "UNNEST",
            # B5 (finding B5-02): SQL/JSON, jsonpath, the JSON and hstore SRFs (also the
            # select-list form `srf.restate` produces), xpath and array_to_json. A
            # document is read whole (D-29), so each is an ordinary derived built-in.
            "JSON_VALUE", "JSON_QUERY", "JSON_EXISTS",
            "JSONB_PATH_QUERY", "JSONB_PATH_QUERY_FIRST", "JSONB_PATH_QUERY_ARRAY",
            "JSONB_PATH_EXISTS", "JSONB_PATH_MATCH",
            "JSONB_ARRAY_ELEMENTS", "JSONB_ARRAY_ELEMENTS_TEXT",
            "JSON_ARRAY_ELEMENTS", "JSON_ARRAY_ELEMENTS_TEXT",
            "JSONB_EACH", "JSONB_EACH_TEXT", "JSON_EACH", "JSON_EACH_TEXT",
            "JSONB_OBJECT_KEYS", "JSON_OBJECT_KEYS",
            "JSONB_TO_RECORDSET", "JSONB_TO_RECORD", "JSON_TO_RECORDSET", "JSON_TO_RECORD",
            "EACH", "SKEYS", "SVALS", "AKEYS", "AVALS", "HSTORE_TO_JSONB", "HSTORE_TO_JSON",
            "XPATH", "XPATH_EXISTS", "ARRAY_TO_JSON",
            # finding B11-02: sequence functions. Their argument names a sequence in a
            # literal, so they read no column; read as user calls they were declared
            # "could not be summarised", which was false.
            "NEXTVAL", "CURRVAL", "SETVAL", "LASTVAL",
        }
    ),
}

AGGREGATE = {
    "postgres": frozenset(
        {"JSONB_AGG", "JSON_AGG", "JSONB_OBJECT_AGG", "JSON_OBJECT_AGG", "EVERY", "XMLAGG"}
    ),
    # Finding PO-1: Spark / Databricks aggregates that arrive as Anonymous. A Spark model is
    # analysed through the PostgreSQL path (there is no Spark dialect, PO-0), and
    # `SIZE(COLLECT_SET(x) OVER (...))` read `x` as derived. FIRST, ANY_VALUE, MAX_BY,
    # COUNT_IF and APPROX_COUNT_DISTINCT need nothing: SQLGlot types them as aggregates.
    "spark": frozenset({"COLLECT_SET", "COLLECT_LIST", "PERCENTILE_APPROX", "APPROX_PERCENTILE"}),
}

ALL_AGGREGATES = frozenset().union(*AGGREGATE.values())
"""Every dialect's built-in aggregates. `transforms.is_aggregate` has no dialect to hand,
and an aggregate's name means the same thing wherever it parses."""


def is_builtin(name: str, dialect: str) -> bool:
    """Is this unqualified, upper-cased function name a built-in of `dialect`?

    Aggregates are checked across every dialect, as `transforms.is_aggregate` checks them:
    a Spark model runs through the PostgreSQL path (PO-0), and a `COLLECT_SET` read as an
    unknown user routine lost its argument entirely (PO-1).
    """
    key = (dialect or "").lower()
    return name in SCALAR.get(key, frozenset()) or name in ALL_AGGREGATES
