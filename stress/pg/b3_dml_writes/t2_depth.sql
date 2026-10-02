-- =====================================================================================
-- B3 · T2 · DEPTH — do DML writes survive deep sources?
--
-- Objective card: stress/pg/b3_dml_writes/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (MERGE ... RETURNING 17+).
--
-- Writes fed by CTE chains, set operations and five-level derived tables, written with
-- the WITH in front of UPDATE, DELETE and MERGE as well as INSERT; and data-modifying CTEs
-- chained so one write's returned rows decide the next write.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · WITH ... UPDATE ... FROM a four-CTE chain with a window and a filtered decile
--   `c.decile >= 5` filters a window ordered by an aggregate: D-9, post-aggregation.
--   `d.customer_key = cs.customer_id` joins the target to FROM: structural (D-11).
-- -------------------------------------------------------------------------------------
WITH a AS (
    SELECT f.customer_key, f.net_amount, f.status
    FROM core.fct_order_line AS f
    WHERE f.order_date >= DATE '2025-01-01'
),
b AS (
    SELECT a.customer_key, SUM(a.net_amount) AS rev
    FROM a
    WHERE a.status <> 'CANCELLED'
    GROUP BY a.customer_key
),
c AS (
    SELECT b.customer_key, b.rev, NTILE(10) OVER (ORDER BY b.rev) AS decile
    FROM b
),
d AS (
    SELECT c.customer_key, c.rev, c.decile
    FROM c
    WHERE c.decile >= 5
)
UPDATE mart.customer_summary AS cs
SET lifetime_value = d.rev,
    value_decile   = d.decile
FROM d
WHERE d.customer_key = cs.customer_id;


-- -------------------------------------------------------------------------------------
-- S2 · an upsert from a UNION ALL over two CTEs, aggregated
-- -------------------------------------------------------------------------------------
WITH web AS (
    SELECT o.customer_id AS cid, o.amount FROM staging.orders AS o WHERE o.channel = 'WEB'
),
store AS (
    SELECT o.customer_id AS cid, o.amount FROM staging.orders AS o WHERE o.channel = 'STORE'
),
both_sides AS (
    SELECT web.cid, web.amount FROM web
    UNION ALL
    SELECT store.cid, store.amount FROM store
)
INSERT INTO mart.customer_summary (customer_id, lifetime_value, lifetime_orders)
SELECT b.cid, SUM(b.amount), COUNT(*)
FROM both_sides AS b
GROUP BY b.cid
ON CONFLICT (customer_id) DO UPDATE
SET lifetime_value  = EXCLUDED.lifetime_value,
    lifetime_orders = EXCLUDED.lifetime_orders;


-- -------------------------------------------------------------------------------------
-- S3 · MERGE whose USING is five levels deep, aggregating and filtering on the way up
-- -------------------------------------------------------------------------------------
MERGE INTO mart.product_performance AS t
USING (
    SELECT z.pid, z.m, z.rev, z.units
    FROM (
        SELECT y.pid, y.m, y.rev, y.units
        FROM (
            SELECT x.pid, x.m, SUM(x.amt) AS rev, SUM(x.qty) AS units
            FROM (
                SELECT f.product_key                              AS pid,
                       date_trunc('month', f.order_date)::date    AS m,
                       f.net_amount                               AS amt,
                       f.quantity                                 AS qty
                FROM core.fct_order_line AS f
                WHERE f.status = 'COMPLETE'
            ) AS x
            GROUP BY x.pid, x.m
        ) AS y
        WHERE y.units > 0
    ) AS z
) AS s
ON t.product_id = s.pid AND t.period_month = s.m
WHEN MATCHED THEN
    UPDATE SET revenue = s.rev, units = s.units
WHEN NOT MATCHED THEN
    INSERT (product_id, period_month, revenue, units)
    VALUES (s.pid, s.m, s.rev, s.units);


-- -------------------------------------------------------------------------------------
-- S4 · three writes chained through RETURNING                      (D-16, D-17)
--   upd marks stale shipments lost; del removes their pending payments; the INSERT
--   archives what was removed. upd's predicate decides which order_ids reach del's IN,
--   so it decides which payments are deleted and archived: it filters all three tables.
-- -------------------------------------------------------------------------------------
WITH upd AS (
    UPDATE core.fct_shipment AS fs
    SET status = 'LOST'
    WHERE fs.delivered_date IS NULL
      AND fs.shipped_date < DATE '2024-01-01'
    RETURNING fs.shipment_id, fs.order_id, fs.cost
),
del AS (
    DELETE FROM core.fct_payment AS fp
    WHERE fp.order_id IN (SELECT u.order_id FROM upd AS u)
      AND fp.status = 'PENDING'
    RETURNING fp.order_id, fp.amount
)
INSERT INTO mart.channel_mix (orders, revenue)
SELECT d.order_id, d.amount
FROM del AS d;


-- -------------------------------------------------------------------------------------
-- S5 · INSERT ... RETURNING inside a CTE, counted by the next INSERT    (D-16, D-17)
-- -------------------------------------------------------------------------------------
WITH ins AS (
    INSERT INTO mart.top_customers (customer_id, full_name, revenue)
    SELECT c.id, c.full_name, 0
    FROM staging.customers AS c
    WHERE c.segment = 'VIP'
    RETURNING customer_id, full_name
)
INSERT INTO mart.kpi_rollup (level, customers)
SELECT 'vip-added', COUNT(i.customer_id)
FROM ins AS i;


-- -------------------------------------------------------------------------------------
-- S6 · WITH ... DELETE driven by a HAVING CTE and a correlated scalar comparison
--   All in the DELETE's WHERE, so all filters on the deleted table. list_price appears
--   twice: compared directly (pre) and through AVG (post).
-- -------------------------------------------------------------------------------------
WITH stale AS (
    SELECT i.product_id
    FROM staging.inventory_snapshots AS i
    GROUP BY i.product_id
    HAVING MAX(i.snapshot_date) < DATE '2024-06-01'
)
DELETE FROM core.dim_product AS p
WHERE p.product_id IN (SELECT s.product_id FROM stale AS s)
  AND p.list_price < (
        SELECT AVG(p2.list_price)
        FROM core.dim_product AS p2
        WHERE p2.category_id = p.category_id
      );


-- -------------------------------------------------------------------------------------
-- S7 · UPDATE ... FROM an aggregating derived table
-- -------------------------------------------------------------------------------------
UPDATE core.dim_store AS s
SET opened_on = x.first_day
FROM (
    SELECT f.store_id, MIN(f.order_date) AS first_day
    FROM core.fct_order_line AS f
    GROUP BY f.store_id
) AS x
WHERE x.store_id = s.id;


-- -------------------------------------------------------------------------------------
-- S8 · WITH ... MERGE ... RETURNING                                  (D-16)
--   merge_action() reports which arm ran: no column's value. The returned t columns are
--   the written table's; USING's filter decides which rows are merged AND returned.
-- -------------------------------------------------------------------------------------
WITH src AS (
    SELECT w.id, w.name
    FROM core.dim_warehouse AS w
    WHERE w.status = 'OPEN'
)
MERGE INTO core.dim_store AS t
USING src
ON t.id = src.id
WHEN MATCHED THEN
    UPDATE SET name = src.name
RETURNING merge_action() AS action, t.id AS store_id, t.name AS store_name;
