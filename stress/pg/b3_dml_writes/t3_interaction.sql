-- =====================================================================================
-- B3 · T3 · INTERACTION — do DML rules compose with B1 and B2's?
--
-- Objective card: stress/pg/b3_dml_writes/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17.
--
-- Writes whose sources carry windows, ranks, FILTER aggregates, HAVING, scalar
-- subqueries in a CASE, and set operations - so a DML rule and a query rule meet in one
-- statement and both have to hold.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · UPDATE ... FROM a grouped derived table carrying a rank over a SUM
-- -------------------------------------------------------------------------------------
UPDATE mart.sales_daily AS sd
SET rank_in_region  = r.rnk,
    avg_order_value = r.avg_net
FROM (
    SELECT
        f.order_date,
        f.store_id,
        AVG(f.net_amount) AS avg_net,
        RANK() OVER (PARTITION BY s.region, f.order_date ORDER BY SUM(f.net_amount) DESC) AS rnk
    FROM core.fct_order_line AS f
    JOIN core.dim_store AS s
      ON s.id = f.store_id
    GROUP BY f.order_date, f.store_id, s.region
    HAVING COUNT(*) > 3
) AS r
WHERE r.order_date = sd.sale_date
  AND r.store_id = sd.store_id;


-- -------------------------------------------------------------------------------------
-- S2 · DELETE duplicates, keeping the latest per key: a filtered ROW_NUMBER (D-9)
-- -------------------------------------------------------------------------------------
DELETE FROM staging.orders AS o
WHERE o.id IN (
        SELECT x.id
        FROM (
            SELECT o2.id,
                   ROW_NUMBER() OVER (
                       PARTITION BY o2.customer_id, o2.order_ts
                       ORDER BY o2.updated_at DESC
                   ) AS rn
            FROM staging.orders AS o2
        ) AS x
        WHERE x.rn > 1
      );


-- -------------------------------------------------------------------------------------
-- S3 · an upsert of FILTER aggregates and a windowed share, with a self-referencing
--      growth calculation in DO UPDATE
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, period_month, revenue, revenue_share, growth_pct)
SELECT
    f.product_key,
    date_trunc('month', f.order_date)::date,
    SUM(f.net_amount) FILTER (WHERE f.status = 'COMPLETE'),
    SUM(f.net_amount) / SUM(SUM(f.net_amount)) OVER (PARTITION BY date_trunc('month', f.order_date)),
    NULL
FROM core.fct_order_line AS f
GROUP BY f.product_key, date_trunc('month', f.order_date)
ON CONFLICT (product_id, period_month) DO UPDATE
SET revenue       = EXCLUDED.revenue,
    revenue_share = EXCLUDED.revenue_share,
    growth_pct    = EXCLUDED.revenue / NULLIF(product_performance.revenue, 0) - 1;


-- -------------------------------------------------------------------------------------
-- S4 · MERGE from the top 10 customers per region (a filtered rank over a SUM)
-- -------------------------------------------------------------------------------------
MERGE INTO mart.top_customers AS t
USING (
    SELECT x.cid, x.region, x.rev
    FROM (
        SELECT
            d.customer_id AS cid,
            d.region,
            SUM(f.net_amount) AS rev,
            ROW_NUMBER() OVER (PARTITION BY d.region ORDER BY SUM(f.net_amount) DESC) AS rn
        FROM core.fct_order_line AS f
        JOIN core.dim_customer AS d
          ON d.customer_key = f.customer_key
        GROUP BY d.customer_id, d.region
    ) AS x
    WHERE x.rn <= 10
) AS s
ON t.customer_id = s.cid
WHEN MATCHED THEN
    UPDATE SET revenue = s.rev, region = s.region
WHEN NOT MATCHED THEN
    INSERT (customer_id, region, revenue) VALUES (s.cid, s.region, s.rev);


-- -------------------------------------------------------------------------------------
-- S5 · an UPDATE ... RETURNING whose returned rows are grouped by the next write
-- -------------------------------------------------------------------------------------
WITH paid AS (
    UPDATE core.fct_payment AS fp
    SET status = 'SETTLED'
    WHERE fp.status = 'PENDING'
      AND fp.paid_date <= CURRENT_DATE
    RETURNING fp.method, fp.amount_usd
)
INSERT INTO mart.channel_mix (channel, revenue, orders)
SELECT p.method, SUM(p.amount_usd), COUNT(*)
FROM paid AS p
GROUP BY p.method;


-- -------------------------------------------------------------------------------------
-- S6 · UPDATE whose SET is a CASE on a scalar subquery (D-18), with an EXISTS in WHERE
-- -------------------------------------------------------------------------------------
UPDATE core.dim_customer AS d
SET segment = CASE
        WHEN (SELECT SUM(o.amount) FROM staging.orders AS o
               WHERE o.customer_id = d.customer_id) > 10000
        THEN 'VIP'
        ELSE d.segment
    END
WHERE EXISTS (
        SELECT 1
        FROM staging.payments AS p
        JOIN staging.orders AS o2
          ON o2.id = p.order_id
        WHERE o2.customer_id = d.customer_id
          AND p.status = 'SETTLED'
      );


-- -------------------------------------------------------------------------------------
-- S7 · INSERT of a UNION ALL, ON CONFLICT DO NOTHING, RETURNING the written rows
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, net_amount)
SELECT f.order_date, f.store_id, f.net_amount
FROM core.fct_order_line AS f
WHERE f.channel = 'WEB'
UNION ALL
SELECT p.paid_date, NULL, p.amount
FROM core.fct_payment AS p
WHERE p.method = 'CASH'
ON CONFLICT DO NOTHING
RETURNING sale_date, net_amount;


-- -------------------------------------------------------------------------------------
-- S8 · WITH ... DELETE ... USING a HAVING-filtered CTE
--   The USING join is structural (D-11); which customers are dormant is decided by the
--   CTE's HAVING - a post-aggregation filter on the deleted table.
-- -------------------------------------------------------------------------------------
WITH dormant AS (
    SELECT o.customer_id
    FROM staging.orders AS o
    GROUP BY o.customer_id
    HAVING MAX(o.order_ts) < DATE '2023-01-01'
)
DELETE FROM mart.customer_summary AS cs
USING dormant AS dm
WHERE dm.customer_id = cs.customer_id
  AND cs.status <> 'PROTECTED';
