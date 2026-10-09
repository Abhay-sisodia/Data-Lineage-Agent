# ruff: noqa: E501 - report lines and probe statements quote SQL verbatim.
"""Step 0 of the final suite: which PostgreSQL constructs still fail before lineage starts.

    .\\.venv\\Scripts\\python.exe stress/pg/final/probe_limitations.py

Each probe is one small statement over the shared warehouse (stress/pg/dictionary.json)
that holds one construct. A probe that is refused or declared `parse_failure` is a
candidate for LIMITATIONS.md; a probe that analyses is in bounds for the tests. Edges are
printed too, so a construct that parses but loses its columns shows up here as a finding,
not an exclusion.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "src"))

from lineage.analysis.procedure import analyse_source  # noqa: E402
from lineage.config import AnalysisConfig  # noqa: E402
from lineage.resolution.dictionary import Dictionary  # noqa: E402

ORDERS = "staging.orders AS o"
PROBES: dict[str, str] = {
    # --- grouping -------------------------------------------------------------------------
    "GROUP BY ALL a (PG quantifier)": f"SELECT o.status, count(*) AS n FROM {ORDERS} GROUP BY ALL o.status;",
    "GROUP BY DISTINCT": f"SELECT o.status, count(*) AS n FROM {ORDERS} GROUP BY DISTINCT o.status, ROLLUP (o.status);",
    "GROUP BY mixed CUBE + GROUPING SETS": (
        f"SELECT o.status, o.channel, count(*) AS n FROM {ORDERS} "
        "GROUP BY o.store_id, CUBE (o.status), GROUPING SETS ((o.channel), ());"
    ),
    "positional GROUP BY 1, 2": f"SELECT o.status, o.channel, sum(o.amount) AS s FROM {ORDERS} GROUP BY 1, 2;",
    "count(ALL x)": f"SELECT count(ALL o.id) AS n FROM {ORDERS};",
    "percentile_cont(ARRAY) WITHIN GROUP": f"SELECT percentile_cont(ARRAY[0.1,0.9]) WITHIN GROUP (ORDER BY o.amount) AS q FROM {ORDERS};",
    "hypothetical rank(x) WITHIN GROUP": f"SELECT rank(100) WITHIN GROUP (ORDER BY o.amount) AS r FROM {ORDERS};",
    "mode() WITHIN GROUP": f"SELECT mode() WITHIN GROUP (ORDER BY o.channel) AS m FROM {ORDERS};",
    # --- windows --------------------------------------------------------------------------
    "frame GROUPS EXCLUDE": f"SELECT sum(o.amount) OVER (ORDER BY o.order_ts GROUPS BETWEEN 1 PRECEDING AND 1 FOLLOWING EXCLUDE CURRENT ROW) AS s FROM {ORDERS};",
    "frame RANGE interval": f"SELECT sum(o.amount) OVER (ORDER BY o.order_ts RANGE BETWEEN INTERVAL '7 days' PRECEDING AND CURRENT ROW) AS s FROM {ORDERS};",
    "named window inheritance": f"SELECT sum(o.amount) OVER w2 AS s FROM {ORDERS} WINDOW w1 AS (PARTITION BY o.status), w2 AS (w1 ORDER BY o.order_ts);",
    "agg FILTER OVER": f"SELECT count(*) FILTER (WHERE o.amount > 0) OVER (PARTITION BY o.status) AS s FROM {ORDERS};",
    # --- paging / distinct ------------------------------------------------------------------
    "FETCH WITH TIES": f"SELECT o.id FROM {ORDERS} ORDER BY o.amount FETCH FIRST 5 ROWS WITH TIES;",
    "OFFSET ROWS FETCH NEXT": f"SELECT o.id FROM {ORDERS} ORDER BY o.amount OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY;",
    "LIMIT ALL": f"SELECT o.id FROM {ORDERS} LIMIT ALL;",
    "SELECT ALL": f"SELECT ALL o.id FROM {ORDERS};",
    "DISTINCT ON": f"SELECT DISTINCT ON (o.customer_id) o.customer_id, o.amount FROM {ORDERS} ORDER BY o.customer_id, o.order_ts DESC NULLS LAST;",
    "ORDER BY COLLATE": f'SELECT o.id FROM {ORDERS} ORDER BY o.status COLLATE "C" DESC;',
    # --- from clause ----------------------------------------------------------------------
    "ONLY t": "SELECT o.id FROM ONLY staging.orders o;",
    "t * inheritance": "SELECT o.id FROM staging.orders * AS o;",
    "TABLESAMPLE BERNOULLI REPEATABLE": f"SELECT o.id FROM {ORDERS} TABLESAMPLE BERNOULLI (2.5) REPEATABLE (42);",
    "TABLESAMPLE SYSTEM + JOIN USING": "SELECT o.id, c.full_name FROM staging.orders AS o TABLESAMPLE SYSTEM (1) JOIN staging.customers AS c ON c.id = o.customer_id;",
    "NATURAL RIGHT JOIN": "SELECT x.id FROM staging.orders AS x NATURAL RIGHT JOIN staging.payments AS p;",
    "parenthesised join tree": (
        "SELECT o.id, c.full_name, s.name AS store FROM (staging.orders o LEFT JOIN "
        "(staging.customers c JOIN core.dim_store s ON s.id = c.id) ON c.id = o.customer_id);"
    ),
    "ROWS FROM coldef WITH ORDINALITY": (
        "SELECT r.a, r.n, r.ord FROM ROWS FROM (json_to_record('{\"a\":1}') AS (a int), "
        "generate_series(1, 3)) WITH ORDINALITY AS r (a, n, ord);"
    ),
    "unnest multi-array": "SELECT u.a, u.b FROM unnest(ARRAY[1,2], ARRAY['x','y']) AS u (a, b);",
    "LATERAL VALUES": f"SELECT o.id, v.k, v.x FROM {ORDERS} CROSS JOIN LATERAL (VALUES ('a', o.amount), ('b', o.store_id)) AS v (k, x);",
    "implicit lateral SRF (comma)": f"SELECT o.id, g.d FROM {ORDERS}, generate_series(o.order_ts, o.created_at, '1 day'::interval) WITH ORDINALITY AS g (d, n);",
    "jsonb_to_recordset coldef": f"SELECT o.id, r.sku, r.qty FROM {ORDERS} CROSS JOIN LATERAL jsonb_to_recordset(o.payload -> 'items') AS r (sku text, qty int);",
    "jsonb_each WITH ORDINALITY": f"SELECT o.id, kv.key, kv.value, kv.n FROM {ORDERS} CROSS JOIN LATERAL jsonb_each(o.payload) WITH ORDINALITY AS kv (key, value, n);",
    "regexp_matches SRF": f"SELECT o.id, (m.m)[1] AS c FROM {ORDERS} LEFT JOIN LATERAL regexp_matches(o.status, '([a-z]+)', 'g') AS m (m) ON true;",
    "JSON_TABLE": (
        f"SELECT o.id, jt.sku, jt.qty, jt.n FROM {ORDERS}, JSON_TABLE(o.payload, '$.items[*]' COLUMNS "
        "(n FOR ORDINALITY, sku text PATH '$.sku', qty int PATH '$.qty')) AS jt;"
    ),
    "JSON_TABLE PASSING + NESTED": (
        f"SELECT o.id, jt.sku, jt.lot FROM {ORDERS}, JSON_TABLE(o.payload, '$.items[*] ? (@.q > $m)' "
        "PASSING 1 AS m COLUMNS (sku text PATH '$.sku', NESTED PATH '$.lots[*]' COLUMNS "
        "(lot text PATH '$.id'))) AS jt;"
    ),
    "XMLTABLE": (
        "SELECT f.id, x.sku FROM staging.feed_xml AS f, XMLTABLE('/r/l' PASSING f.doc COLUMNS "
        "sku text PATH 'sku', n FOR ORDINALITY) AS x;"
    ),
    # --- expressions ------------------------------------------------------------------------
    "ISNULL / NOTNULL": f"SELECT o.amount ISNULL AS a, o.amount NOTNULL AS b FROM {ORDERS};",
    "IS UNKNOWN": f"SELECT (o.amount > 0) IS NOT UNKNOWN AS a FROM {ORDERS};",
    "BETWEEN SYMMETRIC": f"SELECT o.amount NOT BETWEEN SYMMETRIC 10 AND 1 AS a FROM {ORDERS};",
    "LIKE ANY ARRAY": f"SELECT o.status LIKE ANY (ARRAY['a%','b%']) AS a FROM {ORDERS};",
    "LIKE ESCAPE / SIMILAR ESCAPE": f"SELECT o.status LIKE 'a!_%' ESCAPE '!' AS a, o.status NOT SIMILAR TO '%(x|y)%' ESCAPE '\\' AS b FROM {ORDERS};",
    "row comparison": f"SELECT (o.customer_id, o.store_id) < (1, 2) AS a, ROW(o.id, o.amount) IS NULL AS b FROM {ORDERS};",
    "row IS DISTINCT FROM": f"SELECT (o.customer_id, o.store_id) IS DISTINCT FROM (1, 2) AS a FROM {ORDERS};",
    "array slices": f"SELECT o.tags[1] AS a, o.tags[2:3] AS b, o.tags[:2] AS c, o.tags[2:] AS d FROM {ORDERS};",
    "array ops": f"SELECT o.tags @> ARRAY['x'] AS a, o.tags && ARRAY['y'] AS b, cardinality(o.tags) AS c, array_position(o.tags, 'x') AS d FROM {ORDERS};",
    "ARRAY(subquery)": f"SELECT ARRAY(SELECT p.amount FROM staging.payments p WHERE p.order_id = o.id ORDER BY p.amount) AS a FROM {ORDERS};",
    "composite field (x).f": f"SELECT (sa).amount AS a FROM {ORDERS} JOIN LATERAL (SELECT o.amount, o.id) AS sa ON true;",
    "whole-row to_jsonb(t)": f"SELECT to_jsonb(o) AS a, row_to_json(o.*) AS b FROM {ORDERS};",
    "SRF field in select": "SELECT (jsonb_each_text('{\"k\":\"v\"}'::jsonb)).key AS k;",
    "VARIADIC": f"SELECT concat(VARIADIC ARRAY['a', o.status]) AS a FROM {ORDERS};",
    "named args =>": "SELECT make_interval(days => 3, hours => 4) AS a;",
    "TRIM/OVERLAY/POSITION/SUBSTRING FROM FOR": f"SELECT TRIM(BOTH 'x' FROM o.status) AS a, OVERLAY(o.status PLACING 'z' FROM 2 FOR 1) AS b, POSITION('-' IN o.status) AS c, SUBSTRING(o.status FROM 1 FOR 3) AS d FROM {ORDERS};",
    "SUBSTRING regex FROM": f"SELECT SUBSTRING(o.status FROM '[0-9]+$') AS a FROM {ORDERS};",
    "NORMALIZE": f"SELECT NORMALIZE(o.status, NFKC) AS a FROM {ORDERS};",
    "EXTRACT fields": f"SELECT EXTRACT(ISODOW FROM o.order_ts) AS a, EXTRACT(EPOCH FROM o.order_ts) AS b, EXTRACT('dow' FROM o.order_ts) AS c FROM {ORDERS};",
    "AT TIME ZONE chain": f"SELECT (o.order_ts AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata' AS a FROM {ORDERS};",
    "interval forms": "SELECT INTERVAL '1-2' YEAR TO MONTH AS a, INTERVAL '3 4:05:06' DAY TO SECOND(3) AS b, INTERVAL (2) '1.5 seconds' AS c, INTERVAL '1' YEAR AS d;",
    "typed time literals": "SELECT TIMESTAMP(3) WITH TIME ZONE '2024-01-01 00:00' AS a, TIME WITH TIME ZONE '04:05-08' AS b, DATE 'infinity' AS c;",
    "CURRENT_* / LOCAL*": "SELECT CURRENT_TIME(2) AS a, LOCALTIMESTAMP(0) AS b, CURRENT_USER AS c, SESSION_USER AS d, CURRENT_CATALOG AS e, SYSTEM_USER AS f;",
    "OVERLAPS": f"SELECT (o.order_ts, o.created_at) OVERLAPS (o.created_at, INTERVAL '1 day') AS a FROM {ORDERS};",
    "jsonb operators": f"SELECT o.payload -> 'a' ->> 'b' AS a, o.payload #>> '{{a,b}}' AS b, o.payload ? 'k' AS c, o.payload ?| ARRAY['x'] AS d, o.payload @> '{{}}' AS e, o.payload - 'k' AS f, o.payload #- '{{a}}' AS g FROM {ORDERS};",
    "jsonb subscript": f"SELECT o.payload['a']['b'] AS a FROM {ORDERS};",
    "jsonpath ops @? @@": f"SELECT o.payload @? '$.a[*]' AS a, o.payload @@ '$.b > 1' AS b FROM {ORDERS};",
    "SQL/JSON JSON_VALUE RETURNING ON EMPTY": f"SELECT JSON_VALUE(o.payload, '$.a' RETURNING int DEFAULT 5 ON EMPTY NULL ON ERROR) AS a FROM {ORDERS};",
    "SQL/JSON JSON_QUERY WRAPPER": f"SELECT JSON_QUERY(o.payload, '$.a' RETURNING jsonb WITH CONDITIONAL ARRAY WRAPPER) AS a FROM {ORDERS};",
    "SQL/JSON JSON_EXISTS PASSING": f"SELECT JSON_EXISTS(o.payload, '$.a ? (@ > $q)' PASSING 1 AS q) AS a FROM {ORDERS};",
    "JSON_OBJECT ABSENT ON NULL RETURNING": f"SELECT JSON_OBJECT('a' VALUE o.amount, 'b' : o.status ABSENT ON NULL RETURNING jsonb) AS a FROM {ORDERS};",
    "JSON_ARRAYAGG ORDER BY": f"SELECT JSON_ARRAYAGG(o.status ORDER BY o.order_ts RETURNING jsonb) AS a FROM {ORDERS};",
    "JSON_OBJECTAGG": f"SELECT JSON_OBJECTAGG(o.status VALUE o.amount) AS a FROM {ORDERS};",
    "IS JSON OBJECT": f"SELECT o.payload IS JSON OBJECT WITH UNIQUE KEYS AS a FROM {ORDERS};",
    "XMLELEMENT/ATTRIBUTES/FOREST/AGG": f"SELECT XMLELEMENT(NAME r, XMLATTRIBUTES(o.id AS \"id\"), XMLFOREST(o.amount AS a, o.status)) AS a FROM {ORDERS};",
    "XMLSERIALIZE / XMLPARSE": f"SELECT XMLSERIALIZE(CONTENT XMLPARSE(CONTENT o.status) AS text) AS a FROM {ORDERS};",
    "xpath with namespaces": "SELECT xpath('/n:r/@id', f.doc, ARRAY[ARRAY['n','urn:x']]) AS a FROM staging.feed_xml AS f;",
    "COLLATE in expr": f'SELECT lower(o.status COLLATE "en-US-x-icu") AS a, o.status COLLATE pg_catalog."default" AS b FROM {ORDERS};',
    "COLLATION FOR": f"SELECT COLLATION FOR (o.status) AS a FROM {ORDERS};",
    "OPERATOR(pg_catalog.+)": f"SELECT o.amount OPERATOR(pg_catalog.+) 1 AS a FROM {ORDERS};",
    "prefix |/ @ ~": f"SELECT |/ o.amount AS a, @ o.amount AS b, ~ o.store_id AS c FROM {ORDERS};",
    "typed literal int4 '5'": "SELECT int4 '5' AS a, jsonb '{}' AS b, pg_catalog.int4 '7' AS c;",
    "numeric literals 0x/0o/_": "SELECT 0x1F AS a, 0o17 AS b, 1_000 AS c, 5. AS d, .5 AS e;",
    "bit / hex string literals": "SELECT B'1010' | B'0101' AS a, X'1F' AS b;",
    "U& literal UESCAPE": "SELECT U&'d!0061t' UESCAPE '!' AS a;",
    "U& identifier": f'SELECT o.id AS U&"d\\0061t" FROM {ORDERS};',
    "E-string + dollar quote": "SELECT E'a\\tb' AS a, $tag$ x $$ y $tag$ AS b, $$it's$$ AS c;",
    "nested block comment": "SELECT 1 AS a /* a /* b */ c */;",
    "regclass cast": "SELECT 'staging.orders'::regclass AS a;",
    "money / inet / tsvector casts": f"SELECT o.amount::money AS a, '1.2.3.4'::inet AS b, o.status::tsvector AS c FROM {ORDERS};",
    "CAST AS text ARRAY": "SELECT CAST('{a}' AS text ARRAY) AS a, CAST('{1}' AS integer[2][2]) AS b;",
    "type with time zone cast": f"SELECT CAST(o.order_ts AS timestamp(0) without time zone) AS a, CAST('1 hour' AS interval hour to minute) AS b FROM {ORDERS};",
    "full-text": f"SELECT to_tsvector('english', o.status) @@ to_tsquery('a & b') AS a, to_tsquery('a') <-> to_tsquery('b') AS b FROM {ORDERS};",
    "range ops": f"SELECT int4range(1, o.store_id) @> 3 AS a, int4range(1,2) -|- int4range(2,3) AS b, int4range(1,5) * int4range(2,6) AS c FROM {ORDERS};",
    "geometric typed literals": "SELECT box '((0,0),(1,1))' && box '((0,0),(2,2))' AS a, point(0,0) <-> point(3,4) AS b;",
    "inet ops": "SELECT inet '1.2.3.4' << inet '1.2.3.0/24' AS a, inet '1.2.3.0/24' >>= inet '1.2.3.0/24' AS b;",
    "geometric prefix ops": "SELECT ?- lseg '[(0,0),(1,0)]' AS a, @-@ path '[(0,0),(3,4)]' AS b, # polygon '((0,0),(1,1),(1,0))' AS c;",
    "IS NORMALIZED": f"SELECT o.status IS NFC NORMALIZED AS a FROM {ORDERS};",
    "AT LOCAL": f"SELECT o.order_ts AT LOCAL AS a FROM {ORDERS};",
    "AT TIME ZONE INTERVAL": f"SELECT o.order_ts AT TIME ZONE INTERVAL '+05:30' AS a FROM {ORDERS};",
    "SUBSTRING SIMILAR": f"SELECT SUBSTRING(o.status SIMILAR '%#\"[a-z]#\"%' ESCAPE '#') AS a FROM {ORDERS};",
    "pg_input_error_info composite": "SELECT (pg_catalog.pg_input_error_info('x', 'int4')).message AS a;",
    # --- subqueries / CTE ---------------------------------------------------------------------
    "SEARCH DEPTH / CYCLE": (
        "WITH RECURSIVE t (id, parent) AS (SELECT c.id, c.id FROM staging.customers c "
        "UNION ALL SELECT c.id, t.id FROM staging.customers c JOIN t ON c.id = t.parent) "
        "SEARCH DEPTH FIRST BY id SET ord CYCLE id SET is_cycle USING pth "
        "SELECT t.id, t.ord, t.is_cycle FROM t;"
    ),
    "MATERIALIZED / NOT MATERIALIZED": f"WITH a AS MATERIALIZED (SELECT o.id FROM {ORDERS}), b AS NOT MATERIALIZED (SELECT a.id FROM a) SELECT b.id FROM b;",
    "nested WITH RECURSIVE in scalar": "SELECT (WITH RECURSIVE c (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM c WHERE n < 3) SELECT max(n) FROM c) AS a;",
    "TABLE command in setop": "SELECT p.id, p.order_id, p.method, p.amount, p.currency, p.status, p.paid_at, p.created_at FROM staging.payments p UNION ALL TABLE staging.payments;",
    "VALUES ORDER BY LIMIT branch": "SELECT c.id, c.full_name FROM staging.customers c UNION ALL (VALUES (1, 'x') ORDER BY 1 LIMIT 1);",
    "EXISTS (TABLE x)": f"SELECT EXISTS (TABLE staging.customers) AS a FROM {ORDERS};",
    "SELECT with no columns in EXISTS": f"SELECT NOT EXISTS (SELECT FROM staging.payments p WHERE p.order_id = o.id) AS a FROM {ORDERS};",
    "quantified subquery SOME/ALL": f"SELECT o.amount > ALL (SELECT p.amount FROM staging.payments p) AS a, o.amount < SOME (SELECT p.amount FROM staging.payments p) AS b FROM {ORDERS};",
    "row IN subquery": f"SELECT (o.customer_id, o.store_id) IN (SELECT c.id, c.id FROM staging.customers c) AS a FROM {ORDERS};",
    "subscript on subquery": "SELECT (SELECT array_agg(g) FROM generate_series(1, 3) g)[1:2] AS a;",
    "ROW() empty": "SELECT ROW() AS a, ROW(1) AS b, ARRAY[]::int[] AS c;",
    # --- writes ---------------------------------------------------------------------------------
    "INSERT ON CONFLICT DO UPDATE EXCLUDED": (
        "INSERT INTO mart.customer_summary (customer_id, lifetime_value) SELECT o.customer_id, sum(o.amount) "
        "FROM staging.orders o GROUP BY o.customer_id ON CONFLICT (customer_id) DO UPDATE "
        "SET lifetime_value = mart.customer_summary.lifetime_value + EXCLUDED.lifetime_value WHERE EXCLUDED.lifetime_value > 0;"
    ),
    "INSERT ON CONFLICT ON CONSTRAINT": (
        "INSERT INTO mart.customer_summary (customer_id, lifetime_value) SELECT o.customer_id, o.amount FROM staging.orders o "
        "ON CONFLICT ON CONSTRAINT customer_summary_pkey DO NOTHING;"
    ),
    "INSERT OVERRIDING SYSTEM VALUE": "INSERT INTO mart.customer_summary (customer_id) OVERRIDING SYSTEM VALUE SELECT o.customer_id FROM staging.orders o;",
    "INSERT RETURNING into CTE": (
        "WITH ins AS (INSERT INTO mart.customer_summary (customer_id) SELECT o.customer_id FROM staging.orders o RETURNING customer_id) "
        "SELECT count(*) AS n FROM ins;"
    ),
    "UPDATE FROM": "UPDATE mart.customer_summary AS s SET lifetime_value = o.amount FROM staging.orders o WHERE o.customer_id = s.customer_id;",
    "UPDATE SET (a,b) = (SELECT ...)": (
        "UPDATE mart.customer_summary AS s SET (lifetime_value, lifetime_orders) = "
        "(SELECT sum(o.amount), count(*) FROM staging.orders o WHERE o.customer_id = s.customer_id);"
    ),
    "DELETE USING RETURNING": "DELETE FROM mart.customer_summary AS s USING staging.orders o WHERE o.customer_id = s.customer_id RETURNING s.customer_id;",
    "MERGE basic": (
        "MERGE INTO mart.customer_summary AS t USING staging.orders AS o ON t.customer_id = o.customer_id "
        "WHEN MATCHED THEN UPDATE SET lifetime_value = o.amount WHEN NOT MATCHED THEN INSERT (customer_id, lifetime_value) VALUES (o.customer_id, o.amount);"
    ),
    "MERGE NOT MATCHED BY SOURCE": (
        "MERGE INTO mart.customer_summary AS t USING staging.orders AS o ON t.customer_id = o.customer_id "
        "WHEN MATCHED AND o.amount > 0 THEN UPDATE SET lifetime_value = o.amount "
        "WHEN NOT MATCHED BY SOURCE THEN DELETE WHEN NOT MATCHED THEN DO NOTHING;"
    ),
    "MERGE RETURNING (PG17)": (
        "MERGE INTO mart.customer_summary AS t USING staging.orders AS o ON t.customer_id = o.customer_id "
        "WHEN MATCHED THEN UPDATE SET lifetime_value = o.amount RETURNING merge_action(), t.customer_id;"
    ),
    "CTAS WITH NO DATA": "CREATE TABLE mart.x_ctas AS SELECT o.id, o.amount FROM staging.orders o WITH NO DATA;",
    "CREATE TEMP TABLE AS": "CREATE TEMP TABLE tmp_x AS SELECT o.id, o.amount FROM staging.orders o;",
    "SELECT INTO": "SELECT o.id, o.amount INTO TEMP tmp_y FROM staging.orders o;",
    "CREATE VIEW col list": "CREATE OR REPLACE VIEW mart.v_x (oid, amt) AS SELECT o.id, o.amount FROM staging.orders o;",
    "CREATE MATERIALIZED VIEW": "CREATE MATERIALIZED VIEW mart.mv_x AS SELECT o.id, o.amount FROM staging.orders o WITH DATA;",
    "CREATE RECURSIVE VIEW": "CREATE RECURSIVE VIEW mart.v_r (n) AS SELECT 1 UNION ALL SELECT n + 1 FROM mart.v_r WHERE n < 3;",
    "TRUNCATE": "TRUNCATE mart.customer_summary;",
    # --- lexical / psql --------------------------------------------------------------------------
    "psql :'v' variable": f"SELECT o.id FROM {ORDERS} WHERE o.status = :'v';",
    "psql :v variable": f"SELECT o.id FROM {ORDERS} WHERE o.store_id = :v;",
    "UPDATE SET unknown target column": (
        "UPDATE mart.customer_summary AS s SET (lifetime_value, no_such_col) = "
        "(SELECT sum(o.amount), count(*) FROM staging.orders o WHERE o.customer_id = s.customer_id);"
    ),
    "$1 parameter": f"SELECT o.id FROM {ORDERS} WHERE o.store_id = $1;",
}


def main() -> int:
    book = Dictionary.load(ROOT / "stress" / "pg" / "dictionary.json")
    config = AnalysisConfig.load().model_copy(update={"dialect": "postgres"})
    bad = 0
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    for name, sql in PROBES.items():
        if only and not any(o.lower() in name.lower() for o in only):
            continue
        try:
            result = analyse_source(sql, book, config, None)
        except Exception as error:
            print(f"CRASH   {name}: {type(error).__name__}: {error}")
            bad += 1
            continue
        refusals = [f"{r.code.value}" for r in result.refusals]
        bounds = sorted({f"{b.kind.value}:{b.subject}" for b in result.boundaries})
        flag = "FAIL  " if refusals or any(b.startswith("parse_failure") for b in bounds) else "ok    "
        if flag.startswith("FAIL"):
            bad += 1
        edges = sorted(
            f"{e.flow.value[0]}:{str(e.source).split(':', 1)[1]}>{str(e.target).split(':', 1)[1]}"
            for e in result.edges
        )
        print(f"{flag}{name}  seen={result.statements_seen} edges={len(edges)} refusals={refusals} bounds={bounds}")
        if "-v" in sys.argv:
            for edge in edges:
                print(f"          {edge}")
    print(f"\n{bad} of {len(PROBES)} probes fail before lineage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
