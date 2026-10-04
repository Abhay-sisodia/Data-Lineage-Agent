-- =====================================================================================
-- B11 · T1 · MODELS — a dbt-style staging-to-mart model set, end to end
--
-- Objective card: stress/pg/b11_integration/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Cleaning views over staging, a CTE-heavy CTAS fact over the views (D-31: views are
-- inlined, tables are nodes), a grouped mart load from the fact table, a top-N by a
-- filtered ranking (D-9), and a materialized view with REFRESH (D-32). Every earlier
-- decision applies; open shapes are avoided (D-43).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · the orders cleaning view
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_orders_clean AS
SELECT o.id            AS order_id,
       o.customer_id,
       o.store_id,
       o.order_ts::date AS order_date,
       lower(o.channel) AS channel,
       o.currency,
       o.amount
FROM staging.orders AS o
WHERE o.status <> 'CANCELLED';


-- -------------------------------------------------------------------------------------
-- S2 · the order-lines cleaning view, with computed gross and discount
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_lines_clean AS
SELECT ol.order_id,
       ol.product_id,
       ol.quantity,
       ol.unit_price,
       ol.quantity * ol.unit_price AS gross,
       ol.quantity * ol.unit_price * COALESCE(ol.discount_pct, 0) / 100 AS discount
FROM staging.order_lines AS ol
WHERE ol.status = 'SHIPPED';


-- -------------------------------------------------------------------------------------
-- S3 · the fact model: CTEs over both views and the FX rates, materialised as a table
-- -------------------------------------------------------------------------------------
CREATE TABLE mart.fct_sales AS
WITH o AS (
    SELECT * FROM mart.v_orders_clean
),
l AS (
    SELECT * FROM mart.v_lines_clean
),
fx AS (
    SELECT r.currency, r.rate_date, r.rate_to_usd
    FROM staging.fx_rates AS r
)
SELECT o.order_id,
       o.order_date,
       o.store_id,
       o.channel,
       l.product_id,
       l.quantity,
       l.gross - l.discount                    AS net,
       (l.gross - l.discount) * fx.rate_to_usd AS net_usd
FROM o
JOIN l  ON l.order_id = o.order_id
JOIN fx ON fx.currency = o.currency AND fx.rate_date = o.order_date;


-- -------------------------------------------------------------------------------------
-- S4 · the daily mart, grouped from the fact table and enriched from a dimension
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, region, orders, units, net_amount, avg_order_value)
WITH d AS (
    SELECT f.order_date,
           f.store_id,
           COUNT(DISTINCT f.order_id) AS orders,
           SUM(f.quantity)            AS units,
           SUM(f.net)                 AS net
    FROM mart.fct_sales AS f
    GROUP BY f.order_date, f.store_id
)
SELECT d.order_date,
       d.store_id,
       s.region,
       d.orders,
       d.units,
       d.net,
       d.net / NULLIF(d.orders, 0)
FROM d
JOIN core.dim_store AS s ON s.id = d.store_id;


-- -------------------------------------------------------------------------------------
-- S5 · top ten customers per region: a ranking over a SUM, filtered, not projected
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, customer_id, revenue)
SELECT x.region, x.customer_id, x.revenue
FROM (
    SELECT dc.region,
           o.customer_id,
           SUM(o.amount) AS revenue,
           ROW_NUMBER() OVER (PARTITION BY dc.region ORDER BY SUM(o.amount) DESC) AS rn
    FROM mart.v_orders_clean AS o
    JOIN core.dim_customer AS dc ON dc.customer_id = o.customer_id
    WHERE dc.is_current
    GROUP BY dc.region, o.customer_id
) AS x
WHERE x.rn <= 10;


-- -------------------------------------------------------------------------------------
-- S6 · a materialized view over the fact table
-- -------------------------------------------------------------------------------------
CREATE MATERIALIZED VIEW mart.mv_channel AS
SELECT f.channel, SUM(f.net_usd) AS revenue
FROM mart.fct_sales AS f
GROUP BY f.channel;


-- -------------------------------------------------------------------------------------
-- S7 · its nightly REFRESH (D-32)
-- -------------------------------------------------------------------------------------
REFRESH MATERIALIZED VIEW mart.mv_channel;


-- -------------------------------------------------------------------------------------
-- S8 · the channel mart, read from the materialized view (a node, D-31)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
SELECT m.channel, m.revenue
FROM mart.mv_channel AS m;
