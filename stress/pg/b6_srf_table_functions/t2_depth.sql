-- =====================================================================================
-- B6 · T2 · DEPTH — do set-returning functions survive nesting?
--
-- Objective card: stress/pg/b6_srf_table_functions/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- SRFs carried through CTE chains and derived tables. Every node is schema-qualified
-- (D-25); D-26..D-28 apply at every level.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · unnest of an array_agg, two CTEs up: the aggregate dominates the element's path,
--      and the grouping key influences it
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region, level)
WITH grouped AS (
    SELECT c.country_code, array_agg(DISTINCT c.segment) AS segs
    FROM staging.customers AS c
    GROUP BY c.country_code
),
exploded AS (
    SELECT g.country_code, s.seg
    FROM grouped AS g, unnest(g.segs) AS s(seg)
)
SELECT e.country_code, upper(e.seg)
FROM exploded AS e;


-- -------------------------------------------------------------------------------------
-- S2 · a literal calendar series cross-joined to stores, facts LEFT JOINed three CTEs up
--   The calendar has no source (D-28); the series and the cross join are structural.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, net_amount)
WITH cal AS (
    SELECT d::date AS day
    FROM generate_series(DATE '2024-01-01', DATE '2024-03-31', INTERVAL '1 day') AS d
),
daily AS (
    SELECT f.order_date, f.store_id, SUM(f.net_amount) AS net
    FROM core.fct_order_line AS f
    GROUP BY f.order_date, f.store_id
),
filled AS (
    SELECT cal.day, s.id AS store_id, COALESCE(daily.net, 0) AS net
    FROM cal
    CROSS JOIN core.dim_store AS s
    LEFT JOIN daily ON daily.order_date = cal.day AND daily.store_id = s.id
)
SELECT filled.day, filled.store_id, filled.net
FROM filled;


-- -------------------------------------------------------------------------------------
-- S3 · the ordinal carried up a CTE and filtered there: a filter from the array
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (full_name, position)
WITH ranked AS (
    SELECT p.id, t.tag, t.n
    FROM staging.products AS p, unnest(p.tags) WITH ORDINALITY AS t(tag, n)
),
firsts AS (
    SELECT r.tag, r.n
    FROM ranked AS r
    WHERE r.n <= 3
)
SELECT f.tag, f.n
FROM firsts AS f;


-- -------------------------------------------------------------------------------------
-- S4 · a nested SRF argument two derived tables down, then grouped and cut on the count
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, orders)
SELECT w.word, w.cnt
FROM (
    SELECT x.word, COUNT(x.id) AS cnt
    FROM (
        SELECT c.id, lower(s.word) AS word
        FROM staging.customers AS c,
             unnest(string_to_array(c.full_name || ' ' || c.email, ' ')) AS s(word)
    ) AS x
    GROUP BY x.word
) AS w
WHERE w.cnt > 1;


-- -------------------------------------------------------------------------------------
-- S5 · tags exploded in one CTE, payments summed in another, joined and grouped by tag
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
WITH tagged AS (
    SELECT o.id, t.tag
    FROM staging.orders AS o
    CROSS JOIN LATERAL unnest(o.tags) AS t(tag)
),
paid AS (
    SELECT p.order_id, SUM(p.amount) AS paid
    FROM staging.payments AS p
    GROUP BY p.order_id
)
SELECT tg.tag, SUM(pd.paid)
FROM tagged AS tg
JOIN paid AS pd ON pd.order_id = tg.id
GROUP BY tg.tag;


-- -------------------------------------------------------------------------------------
-- S6 · ROWS FROM zipping an unnest with a series bounded by the array's length
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, customer_id)
SELECT z.tag, z.i
FROM (
    SELECT r.tag, r.i
    FROM staging.orders AS o,
         ROWS FROM (unnest(o.tags), generate_series(1, cardinality(o.tags))) AS r(tag, i)
) AS z;


-- -------------------------------------------------------------------------------------
-- S7 · a month series between aggregated bounds, in a grouped derived table
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, period_month)
SELECT x.carrier_id, x.m
FROM (
    SELECT
        s.carrier_id,
        generate_series(
            date_trunc('month', min(s.shipped_date)),
            date_trunc('month', max(s.shipped_date)),
            INTERVAL '1 month'
        )::date AS m
    FROM core.fct_shipment AS s
    GROUP BY s.carrier_id
) AS x;
