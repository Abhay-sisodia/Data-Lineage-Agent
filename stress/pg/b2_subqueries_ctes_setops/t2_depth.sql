-- =====================================================================================
-- B2 · T2 · DEPTH — do subqueries, CTEs and set operations survive nesting?
--
-- Objective card: stress/pg/b2_subqueries_ctes_setops/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17.
--
-- Subqueries inside subqueries, set operations inside CTEs inside derived tables, a
-- LATERAL inside a CTE, recursion feeding aggregation, and correlations that reach across
-- two levels. The rules are T1's; the question is whether they still hold down here.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · an IN subquery inside a scalar subquery, correlated to the outermost query
--   Everything inside the SELECT-list scalar subquery, the nested IN included, decides
--   which payments feed lifetime_value: all of it is a value of that column (D-12).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, lifetime_value)
SELECT
    c.id,
    (SELECT SUM(p.amount)
       FROM staging.payments AS p
      WHERE p.order_id IN (
              SELECT o.id
              FROM staging.orders AS o
              WHERE o.customer_id = c.id
                AND o.channel = 'WEB'
            ))
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S2 · a scalar comparison inside an EXISTS, correlated one level up
--   All in WHERE: filters. net_amount appears twice - compared directly (pre-aggregation)
--   and through AVG (post) - two phases of one column, two facts.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, full_name)
SELECT
    c.customer_id,
    c.full_name
FROM core.dim_customer AS c
WHERE c.is_current
  AND EXISTS (
        SELECT 1
        FROM core.fct_order_line AS f
        WHERE f.customer_key = c.customer_key
          AND f.net_amount > (
                SELECT AVG(f2.net_amount)
                FROM core.fct_order_line AS f2
                WHERE f2.store_id = f.store_id
              )
      );


-- -------------------------------------------------------------------------------------
-- S3 · eight CTEs with a UNION ALL in the middle and a window near the end
-- -------------------------------------------------------------------------------------
WITH s1 AS (
    SELECT o.id, o.customer_id, o.amount, o.channel
    FROM staging.orders AS o
    WHERE o.status <> 'CANCELLED'
),
s2 AS (
    SELECT s1.customer_id, s1.amount, s1.channel
    FROM s1
),
s3 AS (
    SELECT s2.customer_id, s2.amount, 'order' AS kind
    FROM s2
    WHERE s2.channel = 'WEB'
    UNION ALL
    SELECT r.order_line_id, -r.amount, 'return'
    FROM staging.returns AS r
),
s4 AS (
    SELECT s3.customer_id, SUM(s3.amount) AS net
    FROM s3
    GROUP BY s3.customer_id
),
s5 AS (
    SELECT s4.customer_id, s4.net
    FROM s4
    WHERE s4.net > 0
),
s6 AS (
    SELECT s5.customer_id AS cid, s5.net AS value
    FROM s5
),
s7 AS (
    SELECT s6.cid, s6.value, NTILE(4) OVER (ORDER BY s6.value) AS quartile
    FROM s6
),
s8 AS (
    SELECT s7.cid, s7.value, s7.quartile
    FROM s7
)
INSERT INTO mart.customer_summary (customer_id, lifetime_value, value_decile)
SELECT s8.cid, s8.value, s8.quartile
FROM s8;


-- -------------------------------------------------------------------------------------
-- S4 · a UNION inside a derived table inside a CTE, then a correlated scalar subquery
--   The scalar subquery correlates to `pe.k`, which is fed by BOTH union arms - so both
--   arms' keys are values of `revenue` (D-12).
-- -------------------------------------------------------------------------------------
WITH people AS (
    SELECT u.k, u.nm
    FROM (
        SELECT c.id AS k, c.full_name AS nm
        FROM staging.customers AS c
        UNION
        SELECT d.customer_id, d.full_name
        FROM core.dim_customer AS d
        WHERE d.is_current
    ) AS u
)
INSERT INTO mart.top_customers (customer_id, full_name, revenue)
SELECT
    pe.k,
    pe.nm,
    (SELECT SUM(o.amount) FROM staging.orders AS o WHERE o.customer_id = pe.k)
FROM people AS pe;


-- -------------------------------------------------------------------------------------
-- S5 · a LATERAL with GROUP BY and ORDER BY ... LIMIT, inside a CTE
--   The top five products per store by quantity. The LIMIT orders by SUM(quantity):
--   a post-aggregation D-9 filter.
-- -------------------------------------------------------------------------------------
WITH store_top AS (
    SELECT s.id AS sid, s.region, t.pid, t.qty
    FROM core.dim_store AS s
    CROSS JOIN LATERAL (
        SELECT f.product_key AS pid, SUM(f.quantity) AS qty
        FROM core.fct_order_line AS f
        WHERE f.store_id = s.id
        GROUP BY f.product_key
        ORDER BY SUM(f.quantity) DESC
        LIMIT 5
    ) AS t
)
INSERT INTO mart.product_performance (product_id, category_name, units)
SELECT st.pid, st.region, st.qty
FROM store_top AS st;


-- -------------------------------------------------------------------------------------
-- S6 · recursion feeding two levels of aggregation
--   Every category is mapped to its root's department through the tree; sales are
--   summed per category, then per department.
-- -------------------------------------------------------------------------------------
WITH RECURSIVE tree AS (
    SELECT c.id AS root_id, c.id AS node_id, c.department
    FROM core.dim_category AS c
    WHERE c.parent_id IS NULL
    UNION ALL
    SELECT t.root_id, ch.id, t.department
    FROM core.dim_category AS ch
    JOIN tree AS t
      ON ch.parent_id = t.node_id
),
leaf_sales AS (
    SELECT p.category_id, SUM(f.net_amount) AS rev
    FROM core.fct_order_line AS f
    JOIN core.dim_product AS p
      ON p.product_key = f.product_key
    GROUP BY p.category_id
),
dept AS (
    SELECT t.department, SUM(ls.rev) AS rev
    FROM tree AS t
    JOIN leaf_sales AS ls
      ON ls.category_id = t.node_id
    GROUP BY t.department
)
INSERT INTO mart.channel_mix (channel, revenue)
SELECT d.department, d.rev
FROM dept AS d;


-- -------------------------------------------------------------------------------------
-- S7 · NOT EXISTS over a derived UNION ALL
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, region, orders)
SELECT
    o.channel,
    o.currency,
    o.id
FROM staging.orders AS o
WHERE NOT EXISTS (
        SELECT 1
        FROM (
            SELECT r.order_line_id AS ref FROM staging.returns AS r
            UNION ALL
            SELECT p.order_id FROM staging.payments AS p WHERE p.status = 'CHARGEBACK'
        ) AS bad
        WHERE bad.ref = o.id
      );


-- -------------------------------------------------------------------------------------
-- S8 · an IN whose subquery groups and filters with HAVING
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (store_id, net_amount)
SELECT
    f.store_id,
    f.net_amount
FROM core.fct_order_line AS f
WHERE f.customer_key IN (
        SELECT g.customer_key
        FROM core.fct_order_line AS g
        GROUP BY g.customer_key
        HAVING COUNT(DISTINCT g.order_id) >= 3
      );


-- -------------------------------------------------------------------------------------
-- S9 · an ordered pick from a CTE, inside a scalar subquery (D-12, D-13 at depth)
--   The CTE's own WHERE is reached only through the scalar subquery, so it decides which
--   rows feed carrier_name and nothing else: a value of that column, like the
--   subquery's own WHERE (D-12's reasoning).
-- -------------------------------------------------------------------------------------
WITH latest AS (
    SELECT sh.order_id, sh.carrier_id, sh.shipped_at
    FROM staging.shipments AS sh
    WHERE sh.status <> 'LOST'
)
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name)
SELECT
    c.id,
    (SELECT l.order_id::text
       FROM latest AS l
      WHERE l.carrier_id = c.id
      ORDER BY l.shipped_at DESC
      LIMIT 1)
FROM core.dim_carrier AS c;


-- -------------------------------------------------------------------------------------
-- S10 · a UNION ALL inside a scalar subquery, both arms correlated
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region, revenue)
SELECT
    s.region,
    (SELECT MAX(z.amt)
       FROM (
            SELECT o.amount AS amt
            FROM staging.orders AS o
            WHERE o.store_id = s.id
            UNION ALL
            SELECT p.amount
            FROM staging.payments AS p
            JOIN staging.orders AS o2
              ON o2.id = p.order_id
            WHERE o2.store_id = s.id
       ) AS z)
FROM core.dim_store AS s;
