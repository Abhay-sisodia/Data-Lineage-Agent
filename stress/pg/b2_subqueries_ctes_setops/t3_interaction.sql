-- =====================================================================================
-- B2 · T3 · INTERACTION — do the subquery rules compose with B1's?
--
-- Objective card: stress/pg/b2_subqueries_ctes_setops/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17.
--
-- A per-row scalar lookup that is then aggregated, a set operation feeding windows,
-- EXISTS over a HAVING-filtered CTE, recursion feeding GROUP BY and a window, a scalar
-- subquery deciding a CASE, a LIMIT over a UNION of aggregates, a correlated HAVING.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · a per-row scalar lookup, multiplied in, then summed one level up
--   The FX lookup's keys are values of the converted amount (D-12); the outer SUM makes
--   every path aggregated, and the outer GROUP BY influences the total.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (store_id, net_amount)
SELECT x.store_id, SUM(x.amt_usd)
FROM (
    SELECT
        o.store_id,
        o.amount * (SELECT fx.rate_to_usd
                      FROM staging.fx_rates AS fx
                     WHERE fx.currency = o.currency
                       AND fx.rate_date = o.order_ts::date) AS amt_usd
    FROM staging.orders AS o
) AS x
GROUP BY x.store_id;


-- -------------------------------------------------------------------------------------
-- S2 · a UNION ALL feeding two windows
--   Both arms supply the partition and the ordering, so both arms' columns influence.
-- -------------------------------------------------------------------------------------
WITH flows AS (
    SELECT o.customer_id AS cid, o.order_ts AS ts, o.amount AS amt
    FROM staging.orders AS o
    UNION ALL
    SELECT r.order_line_id, r.created_at, -r.amount
    FROM staging.returns AS r
)
INSERT INTO mart.customer_summary (customer_id, lifetime_value, value_rank)
SELECT
    fl.cid,
    SUM(fl.amt) OVER (PARTITION BY fl.cid ORDER BY fl.ts),
    ROW_NUMBER() OVER (PARTITION BY fl.cid ORDER BY fl.ts DESC)
FROM flows AS fl;


-- -------------------------------------------------------------------------------------
-- S3 · EXISTS over a CTE that groups and filters with HAVING
-- -------------------------------------------------------------------------------------
WITH busy_stores AS (
    SELECT f.store_id
    FROM core.fct_order_line AS f
    GROUP BY f.store_id
    HAVING SUM(f.quantity) > 1000
)
INSERT INTO mart.kpi_rollup (level, region, country_code)
SELECT 'busy', s.name, s.country_code
FROM core.dim_store AS s
WHERE EXISTS (SELECT 1 FROM busy_stores AS b WHERE b.store_id = s.id);


-- -------------------------------------------------------------------------------------
-- S4 · recursion feeding GROUP BY and a window over the counts
--   COUNT(*) has no value source; the grouping column influences both counts.
-- -------------------------------------------------------------------------------------
WITH RECURSIVE tree AS (
    SELECT c.id, c.department, 0 AS lvl
    FROM core.dim_category AS c
    WHERE c.parent_id IS NULL
    UNION ALL
    SELECT ch.id, t.department, t.lvl + 1
    FROM core.dim_category AS ch
    JOIN tree AS t
      ON ch.parent_id = t.id
)
INSERT INTO mart.channel_mix (channel, orders, share_of_region)
SELECT
    t.department,
    COUNT(*),
    COUNT(*)::numeric / SUM(COUNT(*)) OVER ()
FROM tree AS t
GROUP BY t.department;


-- -------------------------------------------------------------------------------------
-- S5 · an IN over an INTERSECT, on an aggregating query with HAVING
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, units, revenue)
SELECT
    f.product_key,
    SUM(f.quantity),
    SUM(f.net_amount)
FROM core.fct_order_line AS f
WHERE f.product_key IN (
        SELECT p.product_key FROM core.dim_product AS p WHERE p.brand = 'ACME'
        INTERSECT
        SELECT p2.product_key FROM core.dim_product AS p2 WHERE p2.status = 'ACTIVE'
      )
GROUP BY f.product_key
HAVING SUM(f.quantity) > 10;


-- -------------------------------------------------------------------------------------
-- S6 · scalar subqueries deciding a CASE and wrapped in COALESCE
--   The count decides which branch the name takes: its correlation is a value of
--   full_name (D-12), aggregated because the subquery counts.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name, revenue)
SELECT
    c.id,
    CASE
        WHEN (SELECT COUNT(*) FROM staging.orders AS o WHERE o.customer_id = c.id) > 5
        THEN c.full_name
        ELSE 'occasional'
    END,
    COALESCE(
        (SELECT SUM(p.amount)
           FROM staging.payments AS p
           JOIN staging.orders AS o2
             ON o2.id = p.order_id
          WHERE o2.customer_id = c.id),
        0
    )
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S7 · LEFT JOIN LATERAL of an aggregate, with a NOT IN inside
--   A LATERAL is a row source: its WHERE is a filter under today's convention, though
--   this one aggregates without GROUP BY and so always returns one row. That is the
--   tension T5 pairs against a scalar subquery; it is labelled by the convention here.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, orders, revenue)
SELECT
    c.name,
    x.n,
    x.avg_days
FROM core.dim_carrier AS c
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS n, AVG(f.transit_days) AS avg_days
    FROM core.fct_shipment AS f
    WHERE f.carrier_id = c.id
      AND f.shipment_id NOT IN (
            SELECT s.id FROM staging.shipments AS s WHERE s.status = 'LOST'
          )
) AS x ON TRUE;


-- -------------------------------------------------------------------------------------
-- S8 · a UNION ALL of two aggregations, cut by ORDER BY ... LIMIT over the whole
--   D-9 over a set operation, ordered by an aggregate: post-aggregation, both arms.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, revenue)
SELECT 'store', s.region, SUM(f.net_amount)
FROM core.fct_order_line AS f
JOIN core.dim_store AS s
  ON s.id = f.store_id
GROUP BY s.region
UNION ALL
SELECT 'warehouse', w.region, SUM(sh.cost)
FROM core.fct_shipment AS sh
JOIN core.dim_warehouse AS w
  ON w.id = sh.warehouse_id
GROUP BY w.region
ORDER BY 3 DESC
LIMIT 5;


-- -------------------------------------------------------------------------------------
-- S9 · a HAVING compared against an aggregate of aggregates in a subquery
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (store_id, gross_amount)
SELECT f.store_id, SUM(f.gross_amount)
FROM core.fct_order_line AS f
GROUP BY f.store_id
HAVING SUM(f.gross_amount) > (
        SELECT AVG(s.total)
        FROM (
            SELECT SUM(g.gross_amount) AS total
            FROM core.fct_order_line AS g
            GROUP BY g.store_id
        ) AS s
      );


-- -------------------------------------------------------------------------------------
-- S10 · DISTINCT ON over a UNION ALL, with a lookup scalar subquery in the projection
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name, cost_per_kg)
SELECT DISTINCT ON (u.cid)
    u.cid,
    (SELECT dc.name FROM core.dim_carrier AS dc WHERE dc.id = u.cid),
    u.cost
FROM (
    SELECT f.carrier_id AS cid, f.cost, f.shipped_date
    FROM core.fct_shipment AS f
    UNION ALL
    SELECT s.carrier_id, s.cost, s.shipped_at::date
    FROM staging.shipments AS s
) AS u
ORDER BY u.cid, u.shipped_date DESC;
