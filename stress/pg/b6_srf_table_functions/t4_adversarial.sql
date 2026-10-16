-- =====================================================================================
-- B6 · T4 · ADVERSARIAL — do set-returning functions get fooled?
--
-- Objective card: stress/pg/b6_srf_table_functions/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Traps: an element alias named like a source column; an SRF alias named like a real
-- table; a literal array; an SRF over a scalar subquery; a series of literals only; an
-- unnest that multiplies an aggregate without being used (D-27); TABLESAMPLE ...
-- REPEATABLE; a CTE named `unnest` beside the function; an element named like its own
-- array; an SRF inside an IN subquery; a series between two timestamp columns.
-- Every node is schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · the element is called `channel`; t.channel is the tag, o.channel the column
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, segment)
SELECT t.channel, o.channel
FROM staging.orders AS o, unnest(o.tags) AS t(channel);


-- -------------------------------------------------------------------------------------
-- S2 · the SRF's alias is `customers`, beside staging.customers aliased c
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, full_name)
SELECT customers.tag, c.full_name
FROM staging.customers AS c
JOIN staging.orders AS o ON o.customer_id = c.id,
     unnest(o.tags) AS customers(tag);


-- -------------------------------------------------------------------------------------
-- S3 · unnest of a literal array: the element has no source
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, region)
SELECT x, s.region
FROM core.dim_store AS s, unnest(ARRAY['a', 'b']) AS x;


-- -------------------------------------------------------------------------------------
-- S4 · an SRF over a scalar subquery: the subquery's columns are values (D-12)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (category_name)
SELECT unnest((
    SELECT array_agg(p.brand)
    FROM staging.products AS p
    WHERE p.status = 'ACTIVE'
));


-- -------------------------------------------------------------------------------------
-- S5 · a series of literals only, in the select list: no edge, no boundary (D-28)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (units)
SELECT generate_series(1, 10);


-- -------------------------------------------------------------------------------------
-- S6 · an unnest that multiplies a COUNT without being used: structural (D-27)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, orders)
SELECT o.channel, COUNT(o.id)
FROM staging.orders AS o, unnest(o.tags) AS t
GROUP BY o.channel;


-- -------------------------------------------------------------------------------------
-- S7 · TABLESAMPLE with a REPEATABLE seed: still no column
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (revenue)
SELECT p.amount
FROM staging.payments AS p TABLESAMPLE SYSTEM (20) REPEATABLE (42)
WHERE p.method = 'CARD';


-- -------------------------------------------------------------------------------------
-- S8 · a CTE named `unnest` beside the function unnest(...)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (segment, status)
WITH unnest AS (
    SELECT c.id, c.segment
    FROM staging.customers AS c
)
SELECT unnest.segment, t.v
FROM unnest, unnest(ARRAY[unnest.id::text]) AS t(v);


-- -------------------------------------------------------------------------------------
-- S9 · the element is named exactly like its array: tags.tags is an element, o.tags the
--      array
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (region, full_name)
SELECT tags.tags, array_to_string(o.tags, ',')
FROM staging.orders AS o, unnest(o.tags) AS tags(tags);


-- -------------------------------------------------------------------------------------
-- S10 · an SRF inside an IN subquery in WHERE: a filter from the array
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (region)
SELECT o.channel
FROM staging.orders AS o
WHERE 'vip' IN (SELECT unnest(o.tags));


-- -------------------------------------------------------------------------------------
-- S11 · a daily series between two timestamp columns of the same row
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id)
SELECT g::date, sh.warehouse_id
FROM staging.shipments AS sh,
     generate_series(sh.shipped_at, sh.delivered_at, INTERVAL '1 day') AS g;
