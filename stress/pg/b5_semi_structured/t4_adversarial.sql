-- =====================================================================================
-- B5 · T4 · ADVERSARIAL — does document lineage get fooled?
--
-- Objective card: stress/pg/b5_semi_structured/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Traps: a JSON key named like a column; a key whose text names a column; `?` as an
-- operator (not a parameter placeholder); a document compared with itself; a literal-
-- only document; an SRF over a literal; a key from another table's column; a text column
-- cast to jsonb; jsonpath variables built from a column; IS NOT JSON on text; an hstore
-- key from a column. Every node is schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · the key 'channel' beside the column channel: different sources
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, segment)
SELECT o.payload ->> 'channel', o.channel
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S2 · the key 'amount' is text inside the document, not the column amount
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (revenue)
SELECT (o.payload ->> 'amount')::numeric
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S3 · ? and ?& are operators here, not parameter placeholders
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel)
SELECT p.name
FROM staging.products AS p
WHERE p.dims ? 'w'
  AND p.dims ?& ARRAY['h', 'd'];


-- -------------------------------------------------------------------------------------
-- S4 · a document compared with itself: still a filter from it
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region)
SELECT c.country_code
FROM staging.customers AS c
WHERE c.attributes @> c.attributes;


-- -------------------------------------------------------------------------------------
-- S5 · a literal-only document: no source for the value
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (status)
SELECT '{"a": 1}'::jsonb ->> 'a'
FROM staging.customers AS c
WHERE c.status = 'X';


-- -------------------------------------------------------------------------------------
-- S6 · an SRF over a literal document: its columns have no source (D-28)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (segment, customer_id)
SELECT e.value, c.id
FROM staging.customers AS c, jsonb_each_text('{"x": "1"}'::jsonb) AS e;


-- -------------------------------------------------------------------------------------
-- S7 · a key taken from another table's column (D-22)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (full_name)
SELECT c.attributes ->> d.region
FROM staging.customers AS c
JOIN core.dim_customer AS d ON d.customer_id = c.id;


-- -------------------------------------------------------------------------------------
-- S8 · a text column cast to jsonb, then extracted
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region)
SELECT (o.discount_code::jsonb) ->> 'k'
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S9 · jsonpath variables built from a column: the variable is read (D-22)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (revenue)
SELECT jsonb_path_query_first(
           o.payload,
           '$.items[*] ? (@.sku == $s).price',
           jsonb_build_object('s', o.discount_code)
       )::numeric
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S10 · IS NOT JSON on a text column in WHERE: a filter
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (region)
SELECT o.channel
FROM staging.orders AS o
WHERE o.discount_code IS NOT JSON;


-- -------------------------------------------------------------------------------------
-- S11 · hstore: a key removed, then a key taken from a column
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (region)
SELECT (d.attrs - 'tmp') -> d.device_id::text
FROM staging.device_attrs AS d;
