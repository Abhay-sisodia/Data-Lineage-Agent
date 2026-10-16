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
            # finding TT-5: the XML functions, and the plain forms `parsing.pg_grammar`
            # restates the keyword syntax into (XMLPI, XMLEXISTS, XMLSERIALIZE, XMLROOT as
            # xmlconcat, COLLATION FOR as pg_collation_for). A document is read whole (D-29).
            "XMLPI", "XMLEXISTS", "XMLSERIALIZE", "XMLCONCAT", "XMLCOMMENT", "XMLFOREST",
            "PG_COLLATION_FOR",
            # finding F0-03: every PostgreSQL 17 built-in measured to arrive as Anonymous and
            # not listed above - full text, geometry, network, math in degrees, catalogue,
            # session and size functions among them. Read as user calls they were declared
            # source_unavailable (D-53): the edges were right, the declaration false.
            "ABBREV", "ACOSD", "AREA", "ARRAY_FILL", "ARRAY_SAMPLE", "ARRAY_SHUFFLE",
            "ARRAY_TO_TSVECTOR", "ASIND", "ATAN2D", "ATAND", "BIT_COUNT", "BOUND_BOX", "BOX",
            "BROADCAST", "CENTER", "CIRCLE", "CONVERT_FROM", "CONVERT_TO", "COSD", "COTD", "CRC32",
            "CRC32C", "CURRENT_QUERY", "CURRENT_SETTING", "DATE_SUBTRACT", "DIAGONAL", "DIAMETER",
            "ENUM_FIRST", "ENUM_LAST", "ENUM_RANGE", "ERF", "ERFC", "FAMILY", "FORMAT_TYPE",
            "GET_BYTE", "GET_CURRENT_TS_CONFIG", "HAS_COLUMN_PRIVILEGE", "HAS_SCHEMA_PRIVILEGE",
            "HAS_TABLE_PRIVILEGE", "HEIGHT", "HOSTMASK", "INET_CLIENT_ADDR", "INET_CLIENT_PORT",
            "INET_MERGE", "INET_SAME_FAMILY", "INET_SERVER_ADDR", "INET_SERVER_PORT", "ISCLOSED",
            "ISOPEN", "JSON_ARRAY_LENGTH", "JSON_POPULATE_RECORD", "JSON_POPULATE_RECORDSET",
            "JSON_TO_TSVECTOR", "JSONB_ARRAY_LENGTH", "JSONB_OBJECT", "JSONB_PATH_EXISTS_TZ",
            "JSONB_PATH_MATCH_TZ", "JSONB_PATH_QUERY_ARRAY_TZ", "JSONB_PATH_QUERY_FIRST_TZ",
            "JSONB_PATH_QUERY_TZ", "JSONB_POPULATE_RECORD", "JSONB_POPULATE_RECORD_VALID",
            "JSONB_POPULATE_RECORDSET", "JSONB_SET_LAX", "JSONB_TO_TSVECTOR", "LINE", "LSEG",
            "MACADDR8_SET7BIT", "MASKLEN", "NETMASK", "NETWORK", "NPOINTS", "NUMNODE",
            "PARSE_IDENT", "PATH", "PCLOSE", "PG_ADVISORY_LOCK", "PG_ADVISORY_XACT_LOCK",
            "PG_BACKEND_PID", "PG_BLOCKING_PIDS", "PG_CANCEL_BACKEND", "PG_COLUMN_COMPRESSION",
            "PG_COLUMN_SIZE", "PG_CONF_LOAD_TIME", "PG_CURRENT_LOGFILE", "PG_CURRENT_XACT_ID",
            "PG_CURRENT_XACT_ID_IF_ASSIGNED", "PG_GET_VIEWDEF", "PG_HAS_ROLE",
            "PG_INPUT_ERROR_INFO", "PG_INPUT_IS_VALID", "PG_IS_OTHER_TEMP_SCHEMA",
            "PG_JIT_AVAILABLE", "PG_LISTENING_CHANNELS", "PG_MY_TEMP_SCHEMA",
            "PG_NOTIFICATION_QUEUE_USAGE", "PG_NOTIFY", "PG_POSTMASTER_START_TIME",
            "PG_RELATION_SIZE", "PG_SAFE_SNAPSHOT_BLOCKING_PIDS", "PG_SIZE_BYTES", "PG_SIZE_PRETTY",
            "PG_SLEEP", "PG_TABLE_SIZE", "PG_TERMINATE_BACKEND", "PG_TOTAL_RELATION_SIZE",
            "PG_TRIGGER_DEPTH", "PG_TRY_ADVISORY_LOCK", "PG_TYPEOF", "PG_XACT_STATUS",
            "PHRASETO_TSQUERY", "PLAINTO_TSQUERY", "POINT", "POLYGON", "POPEN", "QUERY_TO_XML",
            "QUERYTREE", "RADIUS", "RANDOM_NORMAL", "SET_BIT", "SET_BYTE", "SET_CONFIG",
            "SET_MASKLEN", "SETSEED", "SETWEIGHT", "SHA224", "SIND", "SLOPE", "STATEMENT_TIMESTAMP",
            "STRIP", "TABLE_TO_XML", "TAND", "TEXT", "TIMEOFDAY", "TO_ASCII", "TO_BIN", "TO_OCT",
            "TO_REGCLASS", "TO_REGPROC", "TO_REGTYPE", "TO_TSQUERY", "TO_TSVECTOR",
            "TRANSACTION_TIMESTAMP", "TRIM_ARRAY", "TS_DELETE", "TS_FILTER", "TS_HEADLINE",
            "TS_RANK", "TS_RANK_CD", "TS_REWRITE", "TSQUERY_PHRASE", "TSVECTOR_TO_ARRAY",
            "TXID_CURRENT", "TXID_CURRENT_IF_ASSIGNED", "TXID_SNAPSHOT_XMAX", "UNISTR",
            "UUID_EXTRACT_TIMESTAMP", "UUID_EXTRACT_VERSION", "UUIDV4", "WEBSEARCH_TO_TSQUERY",
            "WIDTH", "XML_IS_WELL_FORMED", "XML_IS_WELL_FORMED_CONTENT",
            "XML_IS_WELL_FORMED_DOCUMENT", "XMLATTRIBUTES", "XMLROOT", "XMLTEXT",
        }
    ),
}

AGGREGATE = {
    "postgres": frozenset(
        {
            "JSONB_AGG", "JSON_AGG", "JSONB_OBJECT_AGG", "JSON_OBJECT_AGG", "EVERY", "XMLAGG",
            # finding F0-03: the strict / unique variants and the range aggregates.
            "JSON_AGG_STRICT", "JSONB_AGG_STRICT", "JSON_OBJECT_AGG_STRICT",
            "JSONB_OBJECT_AGG_STRICT", "JSON_OBJECT_AGG_UNIQUE", "JSONB_OBJECT_AGG_UNIQUE",
            "RANGE_AGG", "RANGE_INTERSECT_AGG",
        }
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
