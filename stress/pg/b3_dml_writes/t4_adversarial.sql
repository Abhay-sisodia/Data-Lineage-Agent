-- =====================================================================================
-- B3 · T4 · ADVERSARIAL — do DML's own rules get fooled?
--
-- Objective card: stress/pg/b3_dml_writes/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17.
--
-- Each statement leans on a rule specific to writes, built so that a resolver which does
-- not know the rule produces a confident, wrong edge:
--   * EXCLUDED.c is the value PROPOSED for c, not a column called c in the source;
--   * every SET expression reads the OLD row, not one already updated by another SET;
--   * an explicit column list binds by ITS order, an omitted one by the table's;
--   * a data-modifying CTE runs even when nothing reads it.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · a target alias spelled like another table
--   `customers` is core.dim_store here; staging.customers is `c`.
-- -------------------------------------------------------------------------------------
UPDATE core.dim_store AS customers
SET region = customers.country_code
FROM staging.customers AS c
WHERE c.country_code = customers.country_code
  AND c.status = 'ACTIVE';


-- -------------------------------------------------------------------------------------
-- S2 · EXCLUDED.segment is what the INSERT proposed for segment - customers.country_code
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, segment)
SELECT x.id, x.country_code
FROM staging.customers AS x
ON CONFLICT (customer_id) DO UPDATE
SET segment = EXCLUDED.segment;


-- -------------------------------------------------------------------------------------
-- S3 · the target joined to itself under another alias
-- -------------------------------------------------------------------------------------
UPDATE core.dim_customer AS cur
SET first_order_date = prev.first_order_date
FROM core.dim_customer AS prev
WHERE prev.customer_id = cur.customer_id
  AND prev.is_current = false
  AND cur.is_current;


-- -------------------------------------------------------------------------------------
-- S4 · two SETs that read each other's OLD values
--   cost gets the OLD list_price, not the new `cost * 2`.
-- -------------------------------------------------------------------------------------
UPDATE core.dim_product
SET list_price = cost * 2,
    cost       = list_price;


-- -------------------------------------------------------------------------------------
-- S5 · an explicit column list in a different order from the table
--   Table order is (region, customer_id, full_name, ...); this list is not.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (full_name, customer_id, region)
SELECT c.full_name, c.id, c.country_code
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S6 · an omitted column list with fewer values than the table has columns
--   Binds to the FIRST columns in table order: period_month, channel.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix
SELECT o.order_ts::date, o.channel
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S7 · RETURNING an expression and a column of a DELETE                (D-16)
-- -------------------------------------------------------------------------------------
DELETE FROM core.fct_shipment AS fs
WHERE fs.status = 'VOID'
RETURNING fs.cost * 2 AS doubled_cost, fs.shipment_id;


-- -------------------------------------------------------------------------------------
-- S8 · MERGE whose USING alias is the TARGET's table name
--   `dim_warehouse` in this statement is core.dim_store. The target is `w`.
-- -------------------------------------------------------------------------------------
MERGE INTO core.dim_warehouse AS w
USING core.dim_store AS dim_warehouse
ON w.id = dim_warehouse.id
WHEN MATCHED THEN
    UPDATE SET name = dim_warehouse.name;


-- -------------------------------------------------------------------------------------
-- S9 · a correlated subquery in an UPDATE's WHERE - a filter, NOT D-18
--   D-18 is about a subquery in SET. This one decides which products are updated.
-- -------------------------------------------------------------------------------------
UPDATE core.dim_product AS p
SET status = 'HOT'
WHERE (SELECT SUM(f.quantity)
         FROM core.fct_order_line AS f
        WHERE f.product_key = p.product_key) > 1000;


-- -------------------------------------------------------------------------------------
-- S10a · a writable CTE read by a bare SELECT                           (D-16, D-17)
-- -------------------------------------------------------------------------------------
WITH gone AS (
    DELETE FROM staging.web_events
    WHERE event_ts < DATE '2023-01-01'
    RETURNING id
)
SELECT COUNT(*) AS removed
FROM gone;


-- -------------------------------------------------------------------------------------
-- S10b · a writable CTE that NOTHING reads
--   PostgreSQL executes a data-modifying CTE whether or not it is referenced. The DELETE
--   happens; ignoring the CTE would lose a write silently.
-- -------------------------------------------------------------------------------------
WITH purge AS (
    DELETE FROM staging.inventory_snapshots
    WHERE on_hand = 0
)
INSERT INTO mart.kpi_rollup (level)
VALUES ('purged');


-- -------------------------------------------------------------------------------------
-- S11 · ON CONFLICT ON CONSTRAINT, with a DO UPDATE WHERE on EXCLUDED only
--   The constraint's columns (sale_date, store_id) are the conflict target: structural.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, units)
SELECT i.snapshot_date, i.warehouse_id, i.on_hand
FROM staging.inventory_snapshots AS i
ON CONFLICT ON CONSTRAINT sales_daily_uk DO UPDATE
SET units = EXCLUDED.units
WHERE EXCLUDED.units > 0;
