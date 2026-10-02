-- =====================================================================================
-- B6 · T1 · BREADTH — is each set-returning construct supported at all?
--
-- Objective card: stress/pg/b6_srf_table_functions/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17 (string_to_table 14+,
--                 ROWS FROM 9.4+, WITH ORDINALITY 9.4+, TABLESAMPLE 9.5+).
--
-- One construct per statement. Conventions: every earlier decision, plus D-26 (the ordinal
-- is influenced by its array), D-27 (an SRF in FROM is structural, like a join) and D-28
-- (a series is derived from its bound columns). Every node is schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · unnest in FROM, comma-joined: the element is derived from its array
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
SELECT t.tag, o.amount
FROM staging.orders AS o, unnest(o.tags) AS t(tag);


-- -------------------------------------------------------------------------------------
-- S2 · CROSS JOIN LATERAL unnest, filtered on the element: a filter on the array
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel)
SELECT t.tag
FROM staging.products AS p
CROSS JOIN LATERAL unnest(p.tags) AS t(tag)
WHERE t.tag LIKE 'eco%';


-- -------------------------------------------------------------------------------------
-- S3 · two arrays in one unnest, bound by position
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, segment)
SELECT t.a, t.b
FROM staging.orders AS o,
     unnest(o.tags, string_to_array(o.discount_code, '-')) AS t(a, b);


-- -------------------------------------------------------------------------------------
-- S4 · unnest of an ARRAY[] constructor: one element column, from both columns
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region)
SELECT v
FROM staging.customers AS c, unnest(ARRAY[c.country_code, c.segment]) AS v;


-- -------------------------------------------------------------------------------------
-- S5 · WITH ORDINALITY: the ordinal is influenced by the array, not supplied by it (D-26)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (full_name, position)
SELECT t.tag, t.n
FROM staging.products AS p, unnest(p.tags) WITH ORDINALITY AS t(tag, n);


-- -------------------------------------------------------------------------------------
-- S6 · ROWS FROM two functions, with ordinality: columns by position; the ordinal is
--      influenced by both arrays
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, full_name, position)
SELECT r.a, r.b, r.n
FROM staging.orders AS o,
     ROWS FROM (unnest(o.tags), unnest(ARRAY[o.currency::text])) WITH ORDINALITY AS r(a, b, n);


-- -------------------------------------------------------------------------------------
-- S7 · generate_series over literals: no source (D-28); the warehouse id is the only edge
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id)
SELECT d::date, w.id
FROM generate_series(DATE '2024-01-01', DATE '2024-12-31', INTERVAL '1 day') AS d
CROSS JOIN core.dim_warehouse AS w;


-- -------------------------------------------------------------------------------------
-- S8 · generate_series with column bounds: derived from the bound column (D-28)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, region)
SELECT g::date, s.region
FROM core.dim_store AS s,
     generate_series(s.opened_on::timestamp, s.opened_on + 30, INTERVAL '1 day') AS g;


-- -------------------------------------------------------------------------------------
-- S9 · generate_series in the select list, bounded by a column
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (orders, customers)
SELECT generate_series(1, w.capacity), w.id
FROM core.dim_warehouse AS w;


-- -------------------------------------------------------------------------------------
-- S10 · a user-defined set-returning function with a column-definition list
--   Its body is not in this analysis: declared, nothing guessed from its argument.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, revenue)
SELECT x.k, x.v
FROM staging.orders AS o, analytics.explode_payload(o.payload) AS x(k text, v numeric);


-- -------------------------------------------------------------------------------------
-- S11 · TABLESAMPLE: chance decides which rows are read, no column does
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (region, orders)
SELECT o.channel, o.id
FROM staging.orders AS o TABLESAMPLE BERNOULLI (5)
WHERE o.status = 'PAID';


-- -------------------------------------------------------------------------------------
-- S12 · regexp_split_to_table in the select list (deferred from B4)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (country_code)
SELECT regexp_split_to_table(c.email, '[@.]')
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S13 · string_to_table in FROM, the element passed through a function
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT c.id, length(s.part)
FROM staging.customers AS c, string_to_table(c.full_name, ' ') AS s(part);


-- -------------------------------------------------------------------------------------
-- S14 · an unaliased unnest: its column is called `unnest`
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (category_name)
SELECT unnest
FROM staging.orders AS o, unnest(o.tags);


-- -------------------------------------------------------------------------------------
-- S15 · unnest in the select list beside an ordinary column, over a nested function
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, segment)
SELECT c.id, unnest(string_to_array(c.email, '@'))
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S16 · an unnest whose output is never used: structural only (D-27)
--   It drops orders with no tags and repeats the rest - and names no column.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (gross_amount)
SELECT o.amount
FROM staging.orders AS o, unnest(o.tags) AS t(tag);
