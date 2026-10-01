-- =====================================================================================
-- B1 · T5 · METAMORPHIC AND REALISTIC — one computation, one answer
--
-- Objective card: stress/pg/b1_core_select/OBJECTIVES.md, section T5.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (all constructs 9.6+).
--
-- Ten PAIRS. The two statements of a pair compute the same rows, so they must produce
-- exactly the same lineage - no hand-written truth needed, the pair is the test. Each
-- statement is tagged `-- @pair P<n> A|B` on the line above it; stress/pg/metamorphic.py
-- groups the analyser's edges by statement and compares A with B.
--
-- Both statements of a pair write the SAME target, so the answer key lists each fact
-- once (under spelling A); the key scores the lineage, the pair check scores the
-- agreement. Three pairs are equal only BECAUSE of a decision taken for this batch -
-- P8 (D-10), P9 (D-11), P10 (D-9) - and are expected to disagree until those are built.
--
-- After the pairs, one production-shaped model: a customer summary built the way a
-- warehouse team would write it.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- P1 · JOIN ... USING (k)  vs  JOIN ... ON a.k = b.k
--   For an inner join the merged column is the left input's, so `order_id` in A is
--   sh.order_id - exactly what B spells out.
-- -------------------------------------------------------------------------------------
-- @pair P1 A
INSERT INTO mart.top_customers (customer_id, position, revenue)
SELECT order_id, sh.carrier_id, pay.amount
FROM staging.shipments AS sh
JOIN staging.payments AS pay USING (order_id);

-- @pair P1 B
INSERT INTO mart.top_customers (customer_id, position, revenue)
SELECT sh.order_id, sh.carrier_id, pay.amount
FROM staging.shipments AS sh
JOIN staging.payments AS pay ON pay.order_id = sh.order_id;


-- -------------------------------------------------------------------------------------
-- P2 · a named WINDOW  vs  the same window inline
-- -------------------------------------------------------------------------------------
-- @pair P2 A
INSERT INTO mart.product_performance (product_id, running_revenue)
SELECT f.product_key, SUM(f.net_amount) OVER w
FROM core.fct_order_line AS f
WINDOW w AS (PARTITION BY f.store_id ORDER BY f.order_date);

-- @pair P2 B
INSERT INTO mart.product_performance (product_id, running_revenue)
SELECT f.product_key, SUM(f.net_amount) OVER (PARTITION BY f.store_id ORDER BY f.order_date)
FROM core.fct_order_line AS f;


-- -------------------------------------------------------------------------------------
-- P3 · GROUP BY 1, 2  vs  GROUP BY the named columns
-- -------------------------------------------------------------------------------------
-- @pair P3 A
INSERT INTO mart.kpi_rollup (region, country_code, orders, revenue)
SELECT s.region, s.country_code, COUNT(*), SUM(f.amount_usd)
FROM core.fct_order_line AS f
JOIN core.dim_store AS s ON s.id = f.store_id
GROUP BY 1, 2;

-- @pair P3 B
INSERT INTO mart.kpi_rollup (region, country_code, orders, revenue)
SELECT s.region, s.country_code, COUNT(*), SUM(f.amount_usd)
FROM core.fct_order_line AS f
JOIN core.dim_store AS s ON s.id = f.store_id
GROUP BY s.region, s.country_code;


-- -------------------------------------------------------------------------------------
-- P4 · SELECT DISTINCT a, b  vs  GROUP BY a, b with no aggregate
-- -------------------------------------------------------------------------------------
-- @pair P4 A
INSERT INTO mart.channel_mix (channel, region)
SELECT DISTINCT o.channel, o.currency
FROM staging.orders AS o;

-- @pair P4 B
INSERT INTO mart.channel_mix (channel, region)
SELECT o.channel, o.currency
FROM staging.orders AS o
GROUP BY o.channel, o.currency;


-- -------------------------------------------------------------------------------------
-- P5 · ROLLUP (a, b)  vs  GROUPING SETS ((a, b), (a), ())
-- -------------------------------------------------------------------------------------
-- @pair P5 A
INSERT INTO mart.kpi_rollup (region, segment, revenue)
SELECT d.region, d.segment, SUM(f.net_amount)
FROM core.fct_order_line AS f
JOIN core.dim_customer AS d ON d.customer_key = f.customer_key
GROUP BY ROLLUP (d.region, d.segment);

-- @pair P5 B
INSERT INTO mart.kpi_rollup (region, segment, revenue)
SELECT d.region, d.segment, SUM(f.net_amount)
FROM core.fct_order_line AS f
JOIN core.dim_customer AS d ON d.customer_key = f.customer_key
GROUP BY GROUPING SETS ((d.region, d.segment), (d.region), ());


-- -------------------------------------------------------------------------------------
-- P6 · HAVING SUM(x) > 0  vs  WHERE s > 0 over a subquery computing s = SUM(x)
--   Same rows removed, at the same point in the computation: post-aggregation in both
--   (D-6 decides by what a filter does, not by its keyword).
-- -------------------------------------------------------------------------------------
-- @pair P6 A
INSERT INTO mart.sales_daily (store_id, net_amount)
SELECT f.store_id, SUM(f.net_amount)
FROM core.fct_order_line AS f
GROUP BY f.store_id
HAVING SUM(f.net_amount) > 0;

-- @pair P6 B
INSERT INTO mart.sales_daily (store_id, net_amount)
SELECT t.store_id, t.s
FROM (
    SELECT f.store_id, SUM(f.net_amount) AS s
    FROM core.fct_order_line AS f
    GROUP BY f.store_id
) AS t
WHERE t.s > 0;


-- -------------------------------------------------------------------------------------
-- P7 · COUNT(DISTINCT x)  vs  COUNT(x) over a SELECT DISTINCT
-- -------------------------------------------------------------------------------------
-- @pair P7 A
INSERT INTO mart.customer_summary (customer_id, lifetime_orders)
SELECT o.customer_id, COUNT(DISTINCT o.store_id)
FROM staging.orders AS o
GROUP BY o.customer_id;

-- @pair P7 B
INSERT INTO mart.customer_summary (customer_id, lifetime_orders)
SELECT d.customer_id, COUNT(d.store_id)
FROM (
    SELECT DISTINCT o.customer_id, o.store_id
    FROM staging.orders AS o
) AS d
GROUP BY d.customer_id;


-- -------------------------------------------------------------------------------------
-- P8 · AVG(x) FILTER (WHERE c)  vs  AVG(CASE WHEN c THEN x END)          [D-10]
-- -------------------------------------------------------------------------------------
-- @pair P8 A
INSERT INTO mart.carrier_scorecard (carrier_id, avg_transit_days)
SELECT s.carrier_id, AVG(s.transit_days) FILTER (WHERE s.status = 'DELIVERED')
FROM core.fct_shipment AS s
GROUP BY s.carrier_id;

-- @pair P8 B
INSERT INTO mart.carrier_scorecard (carrier_id, avg_transit_days)
SELECT s.carrier_id, AVG(CASE WHEN s.status = 'DELIVERED' THEN s.transit_days END)
FROM core.fct_shipment AS s
GROUP BY s.carrier_id;


-- -------------------------------------------------------------------------------------
-- P9 · JOIN ... ON  vs  a comma join with the condition in WHERE           [D-11]
-- -------------------------------------------------------------------------------------
-- @pair P9 A
INSERT INTO mart.sales_daily (sale_date, region, gross_amount)
SELECT f.order_date, s.region, f.gross_amount
FROM core.fct_order_line AS f
JOIN core.dim_store AS s ON s.id = f.store_id;

-- @pair P9 B
INSERT INTO mart.sales_daily (sale_date, region, gross_amount)
SELECT f.order_date, s.region, f.gross_amount
FROM core.fct_order_line AS f, core.dim_store AS s
WHERE s.id = f.store_id;


-- -------------------------------------------------------------------------------------
-- P10 · latest row per key: ROW_NUMBER() = 1  vs  DISTINCT ON              [D-9]
-- -------------------------------------------------------------------------------------
-- @pair P10 A
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT x.customer_id, x.amount
FROM (
    SELECT o.customer_id, o.amount,
           ROW_NUMBER() OVER (PARTITION BY o.customer_id ORDER BY o.order_ts DESC) AS rn
    FROM staging.orders AS o
) AS x
WHERE x.rn = 1;

-- @pair P10 B
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT DISTINCT ON (o.customer_id) o.customer_id, o.amount
FROM staging.orders AS o
ORDER BY o.customer_id, o.order_ts DESC;


-- =====================================================================================
-- R1 · REALISTIC: the customer summary model
--
--   order_facts       one row per (customer, order): order date, value, units
--   customer_rollup   one row per customer: first/last order, count, LTV, basket
--   current_customer  the current SCD row per customer (latest valid_from)
--   scored            rank within region and decile by LTV
--   final             names cleaned, a recency status, regionless customers dropped
--
--   Every B1 construct a real model leans on: two aggregation grains, HAVING, DISTINCT
--   ON, two windows, a CASE over an aggregate, COALESCE, and joins that are structural.
-- =====================================================================================
WITH order_facts AS (
    SELECT
        f.customer_key,
        f.order_id,
        MIN(f.order_date)   AS order_date,
        SUM(f.amount_usd)   AS order_value_usd,
        SUM(f.quantity)     AS order_units
    FROM core.fct_order_line AS f
    WHERE f.status NOT IN ('CANCELLED', 'FRAUD')
    GROUP BY f.customer_key, f.order_id
),
customer_rollup AS (
    SELECT
        o.customer_key,
        MIN(o.order_date)        AS first_order,
        MAX(o.order_date)        AS last_order,
        COUNT(*)                 AS n_orders,
        SUM(o.order_value_usd)   AS ltv,
        AVG(o.order_value_usd)   AS basket
    FROM order_facts AS o
    GROUP BY o.customer_key
    HAVING SUM(o.order_value_usd) > 0
),
current_customer AS (
    SELECT DISTINCT ON (d.customer_id)
        d.customer_id,
        d.customer_key,
        d.full_name,
        d.segment,
        d.region
    FROM core.dim_customer AS d
    ORDER BY d.customer_id, d.valid_from DESC
),
scored AS (
    SELECT
        cc.customer_id,
        cc.full_name,
        cc.segment,
        cc.region,
        cr.first_order,
        cr.last_order,
        cr.n_orders,
        cr.ltv,
        cr.basket,
        RANK()    OVER (PARTITION BY cc.region ORDER BY cr.ltv DESC) AS value_rank,
        NTILE(10) OVER (ORDER BY cr.ltv)                             AS value_decile
    FROM current_customer AS cc
    JOIN customer_rollup AS cr
      ON cr.customer_key = cc.customer_key
)
INSERT INTO mart.customer_summary
    (customer_id, full_name, segment, region, first_order_date, last_order_date,
     lifetime_orders, lifetime_value, avg_basket, value_rank, value_decile, status)
SELECT
    s.customer_id,
    initcap(s.full_name),
    COALESCE(s.segment, 'UNKNOWN'),
    s.region,
    s.first_order,
    s.last_order,
    s.n_orders,
    round(s.ltv, 2),
    round(s.basket, 2),
    s.value_rank,
    s.value_decile,
    CASE
        WHEN s.last_order >= CURRENT_DATE - 90  THEN 'ACTIVE'
        WHEN s.last_order >= CURRENT_DATE - 365 THEN 'LAPSING'
        ELSE 'CHURNED'
    END
FROM scored AS s
WHERE s.region IS NOT NULL;
