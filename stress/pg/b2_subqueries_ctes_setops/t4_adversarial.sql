-- =====================================================================================
-- B2 · T4 · ADVERSARIAL — do subquery scoping rules get fooled?
--
-- Objective card: stress/pg/b2_subqueries_ctes_setops/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17.
--
-- PostgreSQL resolves a name in a subquery INNERMOST SCOPE FIRST, and only then outward.
-- Every statement here is built so that resolving outward first - or by name instead of
-- by position - gives a confident, wrong edge. The forbidden list carries the test.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · an inner alias that captures the outer one
--   Inside the EXISTS, `o` is staging.PAYMENTS: the inner alias hides the outer. So
--   `o.order_id = o.id` compares two payments columns and correlates to nothing.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
SELECT o.channel, o.amount
FROM staging.orders AS o
WHERE EXISTS (
        SELECT 1
        FROM staging.payments AS o
        WHERE o.order_id = o.id
      );


-- -------------------------------------------------------------------------------------
-- S2 · unqualified names inside subqueries
--   `id` exists in payments, so it binds INSIDE (payments.id). `store_id` exists only in
--   orders, so it binds OUTSIDE (orders.store_id) - a correlation by unqualified name.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (region, net_amount)
SELECT o.discount_code, o.amount
FROM staging.orders AS o
WHERE EXISTS (SELECT 1 FROM staging.payments AS p WHERE p.order_id = id)
  AND EXISTS (
        SELECT 1
        FROM staging.shipments AS s
        WHERE s.carrier_id = store_id
          AND s.status = 'DELIVERED'
      );


-- -------------------------------------------------------------------------------------
-- S3 · a CTE named like a real table
--   `orders` here is the CTE over staging.customers. Nothing reads staging.orders.
-- -------------------------------------------------------------------------------------
WITH orders AS (
    SELECT c.id AS customer_id, c.segment AS channel
    FROM staging.customers AS c
    WHERE c.status = 'ACTIVE'
)
INSERT INTO mart.top_customers (customer_id, region)
SELECT o.customer_id, o.channel
FROM orders AS o;


-- -------------------------------------------------------------------------------------
-- S4 · set-operation arms whose aliases are SWAPPED
--   Arms bind by POSITION. The second arm calls its first column `segment`, but it lands
--   in the first output column, `region`.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region, segment)
SELECT x.region, x.segment
FROM (
    SELECT s.region AS region, s.name AS segment FROM core.dim_store AS s
    UNION ALL
    SELECT w.name AS segment, w.region AS region FROM core.dim_warehouse AS w
) AS x;


-- -------------------------------------------------------------------------------------
-- S5 · NOT IN whose subquery projection is aliased like a column
--   `AS id` names orders.customer_id. orders.id is never read.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, segment)
SELECT c.id, c.segment
FROM staging.customers AS c
WHERE c.id NOT IN (
        SELECT o.customer_id AS id FROM staging.orders AS o WHERE o.status = 'FRAUD'
      );


-- -------------------------------------------------------------------------------------
-- S6 · a scalar subquery aliased like an input column, then ordered by that alias
--   `ORDER BY amount` is the OUTPUT column - the scalar subquery - not any input amount.
--   With the LIMIT it decides which ten land (D-9), and its sources are the subquery's
--   value and its D-12 correlation; the MAX makes them post-aggregation.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (orders, gross_amount)
SELECT
    c.id,
    (SELECT MAX(p.amount)
       FROM staging.payments AS p
       JOIN staging.orders AS o
         ON o.id = p.order_id
      WHERE o.customer_id = c.id) AS amount
FROM staging.customers AS c
ORDER BY amount DESC
LIMIT 10;


-- -------------------------------------------------------------------------------------
-- S7 · a derived-table alias that renames one column to another's name
--   `s.id` is payments.ORDER_ID. payments.id is never projected.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (orders, revenue, channel)
SELECT s.id, s.amount, o.currency
FROM (
    SELECT p.order_id AS id, p.amount
    FROM staging.payments AS p
) AS s
JOIN staging.orders AS o
  ON o.id = s.id;


-- -------------------------------------------------------------------------------------
-- S8 · a scalar subquery whose PROJECTION reads the outer row
--   Days from order to the latest payment. order_ts is a value of the column from
--   outside the subquery, beside the aggregated paid_at.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, avg_transit_days)
SELECT
    o.store_id,
    (SELECT MAX(p.paid_at)::date - o.order_ts::date
       FROM staging.payments AS p
      WHERE p.order_id = o.id)
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S9 · an ORDER BY ... LIMIT inside an EXISTS
--   Whether a row exists does not depend on which row is first, so the ordering decides
--   nothing. Only the correlation filters.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, category_name)
SELECT p.product_key, p.category_name
FROM core.dim_product AS p
WHERE EXISTS (
        SELECT 1
        FROM core.fct_order_line AS f
        WHERE f.product_key = p.product_key
        ORDER BY f.order_date DESC
        LIMIT 1
      );


-- -------------------------------------------------------------------------------------
-- S10 · one CTE joined to itself: this day against the day before
-- -------------------------------------------------------------------------------------
WITH m AS (
    SELECT f.store_id, f.order_date, SUM(f.net_amount) AS rev
    FROM core.fct_order_line AS f
    GROUP BY f.store_id, f.order_date
)
INSERT INTO mart.sales_daily (store_id, sale_date, net_amount, gross_amount)
SELECT cur.store_id, cur.order_date, cur.rev, prev.rev
FROM m AS cur
LEFT JOIN m AS prev
       ON prev.store_id = cur.store_id
      AND prev.order_date = cur.order_date - 1;


-- -------------------------------------------------------------------------------------
-- S11 · an anti-join spelled LEFT JOIN ... IS NULL
--   The ON is the join (structural). `o.id IS NULL` tests the null-supplying side: it is
--   what removes every customer who has an order - a real filter.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, country_code)
SELECT c.full_name, c.country_code
FROM staging.customers AS c
LEFT JOIN staging.orders AS o
       ON o.customer_id = c.id
WHERE o.id IS NULL;
