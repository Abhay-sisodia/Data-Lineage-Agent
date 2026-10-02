-- =====================================================================================
-- B2 · T1 · BREADTH — is each subquery, CTE and set-operation form supported at all?
--
-- Objective card: stress/pg/b2_subqueries_ctes_setops/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (MATERIALIZED hints 12+).
--
-- One construct per statement, simplest correct spelling. Conventions: B1's, plus D-12
-- (a SELECT-list scalar subquery's WHERE columns are VALUES of that column) and D-13 (its
-- ordered pick is INFLUENCE). A subquery in a WHERE still decides which rows land.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · an uncorrelated scalar subquery in the SELECT list
--   The same total on every row. `status` decides which lines feed it (D-12): a value of
--   `revenue`, not a filter - every store row is written.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, revenue)
SELECT
    'store',
    s.name,
    (SELECT SUM(f.net_amount)
       FROM core.fct_order_line AS f
      WHERE f.status = 'COMPLETE')
FROM core.dim_store AS s;


-- -------------------------------------------------------------------------------------
-- S2 · correlated, aggregating scalar subqueries, one with a join inside
--   Both correlation operands are values of the column (D-12). The join inside the first
--   subquery is structural (D-5). COUNT(*) has no value of its own: only the correlation
--   feeds lifetime_orders.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, full_name, lifetime_value, lifetime_orders)
SELECT
    c.id,
    c.full_name,
    (SELECT SUM(p.amount)
       FROM staging.payments AS p
       JOIN staging.orders AS o
         ON o.id = p.order_id
      WHERE o.customer_id = c.id
        AND p.status = 'SETTLED'),
    (SELECT COUNT(*)
       FROM staging.orders AS o2
      WHERE o2.customer_id = c.id)
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S3 · lookup scalar subqueries: one row picked by key, not aggregated
--   The looked-up column arrives unchanged (identity). The keys that select it are
--   conditional values of the same column (D-12). The outer WHERE is a real filter.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name, region)
SELECT
    o.id,
    (SELECT c.full_name FROM staging.customers AS c WHERE c.id = o.customer_id),
    (SELECT s.region FROM core.dim_store AS s WHERE s.id = o.store_id)
FROM staging.orders AS o
WHERE o.channel = 'WEB';


-- -------------------------------------------------------------------------------------
-- S4 · an ordered pick: the latest payment per shipment's order (D-13)
--   paid_at decides WHICH amount arrives: influence on that column, not a filter.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, lifetime_value)
SELECT
    sh.order_id,
    (SELECT p.amount
       FROM staging.payments AS p
      WHERE p.order_id = sh.order_id
      ORDER BY p.paid_at DESC
      LIMIT 1)
FROM staging.shipments AS sh;


-- -------------------------------------------------------------------------------------
-- S5 · EXISTS and NOT EXISTS
--   Both subqueries decide which customers land: every predicate column is a filter,
--   both correlation operands kept. The NOT EXISTS reaches orders through two joins,
--   which are structural.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, region)
SELECT
    c.id,
    c.segment
FROM staging.customers AS c
WHERE EXISTS (
        SELECT 1
        FROM staging.orders AS o
        WHERE o.customer_id = c.id
          AND o.status = 'COMPLETE'
      )
  AND NOT EXISTS (
        SELECT 1
        FROM staging.returns AS r
        JOIN staging.order_lines AS l
          ON l.id = r.order_line_id
        JOIN staging.orders AS o2
          ON o2.id = l.order_id
        WHERE o2.customer_id = c.id
      );


-- -------------------------------------------------------------------------------------
-- S6 · IN and NOT IN
--   The outer operand, the subquery's projected column and the subquery's own WHERE all
--   decide which orders land.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, region, revenue)
SELECT
    o.channel,
    o.currency,
    o.amount
FROM staging.orders AS o
WHERE o.customer_id IN (
        SELECT c.id FROM staging.customers AS c WHERE c.segment = 'ENT'
      )
  AND o.store_id NOT IN (
        SELECT s.id FROM core.dim_store AS s WHERE s.status = 'CLOSED'
      );


-- -------------------------------------------------------------------------------------
-- S7 · ALL, ANY and a scalar subquery compared in WHERE
--   All three are in WHERE, so all their columns are filters. list_price reaches its
--   comparison through AVG, so its filter acts post-aggregation (D-6, per column).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, net_amount)
SELECT
    f.order_date,
    f.store_id,
    f.net_amount
FROM core.fct_order_line AS f
WHERE f.net_amount > ALL (
        SELECT r.amount FROM staging.returns AS r WHERE r.status = 'APPROVED'
      )
  AND f.product_key = ANY (
        SELECT p.product_key FROM core.dim_product AS p WHERE p.brand = 'ACME'
      )
  AND f.unit_price >= (SELECT AVG(p2.list_price) FROM core.dim_product AS p2);


-- -------------------------------------------------------------------------------------
-- S8 · a derived table that aggregates, joined back to its own detail
--   gross_amount is the numerator (derived); net_amount reaches the share only through
--   the derived table's SUM (aggregated), whose GROUP BY influences the share.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, revenue, revenue_share)
SELECT
    f.product_key,
    f.net_amount,
    f.gross_amount / t.total
FROM core.fct_order_line AS f
JOIN (
    SELECT f2.store_id, SUM(f2.net_amount) AS total
    FROM core.fct_order_line AS f2
    GROUP BY f2.store_id
) AS t
  ON t.store_id = f.store_id;


-- -------------------------------------------------------------------------------------
-- S9 · CROSS JOIN LATERAL with ORDER BY ... LIMIT: the top 3 orders per store
--   A LATERAL is a ROW SOURCE, so its correlation decides which rows land (a filter, as
--   PG-03 pinned), and its ORDER BY under a LIMIT is D-9. The ROW_NUMBER is projected,
--   not filtered: plain influence.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (category_name, product_id, revenue, units)
SELECT
    s.region,
    t.oid,
    t.amt,
    t.rn
FROM core.dim_store AS s
CROSS JOIN LATERAL (
    SELECT
        o.id     AS oid,
        o.amount AS amt,
        ROW_NUMBER() OVER (ORDER BY o.amount DESC) AS rn
    FROM staging.orders AS o
    WHERE o.store_id = s.id
    ORDER BY o.amount DESC
    LIMIT 3
) AS t;


-- -------------------------------------------------------------------------------------
-- S10 · a six-CTE chain with MATERIALIZED / NOT MATERIALIZED hints
--   The hints change the plan, never the lineage. The last CTE is named `f`, the same as
--   the table alias inside the first: different scopes, no capture.
-- -------------------------------------------------------------------------------------
WITH a AS MATERIALIZED (
    SELECT f.order_id, f.store_id, f.net_amount
    FROM core.fct_order_line AS f
    WHERE f.status = 'COMPLETE'
),
b AS NOT MATERIALIZED (
    SELECT a.store_id, a.order_id, a.net_amount * 1.1 AS gross_up
    FROM a
),
c AS (
    SELECT b.store_id, SUM(b.gross_up) AS rev
    FROM b
    GROUP BY b.store_id
),
d AS (
    SELECT c.store_id, c.rev, ds.region
    FROM c
    JOIN core.dim_store AS ds
      ON ds.id = c.store_id
),
e AS (
    SELECT d.region, SUM(d.rev) AS region_rev
    FROM d
    GROUP BY d.region
),
f AS (
    SELECT e.region, e.region_rev
    FROM e
    WHERE e.region_rev > 0
)
INSERT INTO mart.channel_mix (channel, region, revenue)
SELECT 'all', f.region, f.region_rev
FROM f;


-- -------------------------------------------------------------------------------------
-- S11 · WITH RECURSIVE: walk the category tree
--   Both arms feed the same output columns by position. `path` is built from names in
--   both arms (derived); `department` is carried unchanged from the root. `depth` starts
--   at a literal and only ever adds to itself: no base source. The anchor's WHERE picks
--   the roots (a filter); the recursive arm's join is structural; `t.depth < 6` tests a
--   column with no base source, so it filters on nothing nameable.
-- -------------------------------------------------------------------------------------
WITH RECURSIVE tree AS (
    SELECT c.id, c.name, c.department, 1 AS depth, c.name::text AS path
    FROM core.dim_category AS c
    WHERE c.parent_id IS NULL
    UNION ALL
    SELECT ch.id, ch.name, t.department, t.depth + 1, t.path || ' > ' || ch.name
    FROM core.dim_category AS ch
    JOIN tree AS t
      ON ch.parent_id = t.id
    WHERE t.depth < 6
)
INSERT INTO mart.kpi_rollup (level, region, segment, orders)
SELECT tr.path, tr.department, tr.name, tr.depth
FROM tree AS tr;


-- -------------------------------------------------------------------------------------
-- S12a · UNION ALL at the top of an INSERT: both arms supply values, by position
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (region, net_amount)
SELECT o.channel, o.amount
FROM staging.orders AS o
WHERE o.status = 'COMPLETE'
UNION ALL
SELECT p.method, p.amount
FROM staging.payments AS p;


-- -------------------------------------------------------------------------------------
-- S12b · INTERSECT, then EXCEPT ALL (convention (a))
--   INTERSECT binds tighter: (A INTERSECT B) EXCEPT ALL C. Only A supplies the value; B
--   and C decide which values survive, so every column of theirs is a filter.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id)
SELECT o.customer_id
FROM staging.orders AS o
INTERSECT
SELECT d.customer_id
FROM core.dim_customer AS d
WHERE d.segment = 'SMB'
EXCEPT ALL
SELECT r2.order_line_id
FROM staging.returns AS r2
WHERE r2.status = 'REFUNDED';


-- -------------------------------------------------------------------------------------
-- S12c · a parenthesised UNION, then EXCEPT, inside a CTE
--   UNION (distinct) removes only duplicates of the values written: no filter edge for
--   that. The EXCEPT arm is a filter in full.
-- -------------------------------------------------------------------------------------
WITH both_sides AS (
    (SELECT s.region AS r, s.country_code AS cc
       FROM core.dim_store AS s
     UNION
     SELECT w.region, NULL
       FROM core.dim_warehouse AS w)
    EXCEPT
    SELECT d.region, d.country_code
    FROM core.dim_customer AS d
    WHERE d.is_current = false
)
INSERT INTO mart.kpi_rollup (region, country_code)
SELECT b.r, b.cc
FROM both_sides AS b;


-- -------------------------------------------------------------------------------------
-- S12d · ORDER BY and LIMIT over a whole UNION ALL (D-9 over a set operation)
--   `ORDER BY 2` is the second output column, which BOTH arms supply, so the amount in
--   each arm decides which twenty rows land.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT p.order_id, p.amount
FROM staging.payments AS p
UNION ALL
SELECT r.order_line_id, r.amount
FROM staging.returns AS r
ORDER BY 2 DESC
LIMIT 20;


-- -------------------------------------------------------------------------------------
-- S13 · a bare SELECT with a correlated scalar subquery (D-8 target, D-12 lineage)
-- -------------------------------------------------------------------------------------
SELECT
    w.name AS warehouse_name,
    (SELECT SUM(i.on_hand)
       FROM staging.inventory_snapshots AS i
      WHERE i.warehouse_id = w.id) AS stock_units
FROM core.dim_warehouse AS w;
