-- =====================================================================================
-- B3 · T1 · BREADTH — is each DML form supported at all?
--
-- Objective card: stress/pg/b3_dml_writes/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (`NOT MATCHED BY SOURCE` 17+,
--                 MERGE 15+, ON CONFLICT 9.5+).
--
-- One DML form per statement. Conventions: every B1/B2 decision, plus D-15 (an ON
-- CONFLICT target is structural), D-16 (RETURNING lands in <result>), D-17 (a writable
-- CTE's predicate filters every table its rows reach) and D-18 (D-12 applies in a SET and
-- in VALUES). TRUNCATE and COPY are refused by name; their refusals must stay local.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · INSERT ... SELECT with the column list OMITTED
--   Bound by POSITION to the table's own column order: region, customer_id, full_name,
--   revenue, position.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers
SELECT c.country_code, c.id, c.full_name, NULL, NULL
FROM staging.customers AS c
WHERE c.status = 'ACTIVE';


-- -------------------------------------------------------------------------------------
-- S2a · INSERT ... VALUES of literals and DEFAULT: nothing has a source
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region)
VALUES ('total', 'ALL'), ('none', DEFAULT);


-- -------------------------------------------------------------------------------------
-- S2b · INSERT ... DEFAULT VALUES: a row with no source at all
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix DEFAULT VALUES;


-- -------------------------------------------------------------------------------------
-- S3 · INSERT ... VALUES whose values are scalar subqueries (D-18)
--   Each subquery's WHERE decides which rows feed that one value.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, revenue, customers)
VALUES (
    'global',
    (SELECT SUM(f.net_amount) FROM core.fct_order_line AS f WHERE f.status = 'COMPLETE'),
    (SELECT COUNT(DISTINCT c.id) FROM staging.customers AS c)
);


-- -------------------------------------------------------------------------------------
-- S4 · ON CONFLICT (k) DO NOTHING (D-15)
--   The conflict target matches incoming rows against the table: structural, like a
--   MERGE's ON - no edge, even though matched rows are dropped.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, full_name, segment)
SELECT c.id, c.full_name, c.segment
FROM staging.customers AS c
ON CONFLICT (customer_id) DO NOTHING;


-- -------------------------------------------------------------------------------------
-- S5 · ON CONFLICT ... DO UPDATE with EXCLUDED, a self-reference and a WHERE
--   EXCLUDED.c is the value the INSERT proposed for c. `sd.net_amount + EXCLUDED...` is
--   an accumulator: a real self-edge. The DO UPDATE WHERE is a filter.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily AS sd (sale_date, store_id, net_amount, orders)
SELECT f.order_date, f.store_id, SUM(f.net_amount), COUNT(DISTINCT f.order_id)
FROM core.fct_order_line AS f
GROUP BY f.order_date, f.store_id
ON CONFLICT (sale_date, store_id) DO UPDATE
SET net_amount = sd.net_amount + EXCLUDED.net_amount,
    orders     = EXCLUDED.orders
WHERE sd.net_amount IS DISTINCT FROM EXCLUDED.net_amount;


-- -------------------------------------------------------------------------------------
-- S6 · UPDATE with a self-referencing SET and a WHERE
-- -------------------------------------------------------------------------------------
UPDATE core.dim_product
SET list_price = list_price * 1.05,
    status     = 'REPRICED'
WHERE brand = 'ACME'
  AND status <> 'RETIRED';


-- -------------------------------------------------------------------------------------
-- S7 · UPDATE ... FROM
--   PostgreSQL joins the target to the FROM list: `c.id = d.customer_id` is the join,
--   structural (D-11). The other two conditions are filters on the updated table.
-- -------------------------------------------------------------------------------------
UPDATE core.dim_customer AS d
SET country_code = c.country_code,
    email        = lower(c.email)
FROM staging.customers AS c
WHERE c.id = d.customer_id
  AND d.valid_to IS NULL
  AND c.segment <> 'TEST';


-- -------------------------------------------------------------------------------------
-- S8 · UPDATE SET (a, b) = (correlated row subquery)            (D-18)
--   The lookup decides BOTH columns' values: the looked-up region arrives unchanged,
--   and the literal 'XX' arrives only when a warehouse matches (NULL otherwise), so the
--   lookup keys are conditional values of both.
-- -------------------------------------------------------------------------------------
UPDATE core.dim_store AS s
SET (region, country_code) = (
        SELECT w.region, 'XX' FROM core.dim_warehouse AS w WHERE w.id = s.id
    )
WHERE s.status = 'OPEN';


-- -------------------------------------------------------------------------------------
-- S9 · UPDATE SET col = (correlated aggregate subquery)          (D-18)
--   No WHERE on the UPDATE: every payment row is written, unmatched ones with NULL.
-- -------------------------------------------------------------------------------------
UPDATE core.fct_payment AS fp
SET amount_usd = (
        SELECT SUM(p.amount)
        FROM staging.payments AS p
        WHERE p.order_id = fp.order_id
          AND p.currency = 'USD'
    );


-- -------------------------------------------------------------------------------------
-- S10 · DELETE with an OR and an IN subquery
-- -------------------------------------------------------------------------------------
DELETE FROM core.fct_shipment AS fs
WHERE fs.status = 'CANCELLED'
   OR fs.order_id IN (SELECT o.id FROM staging.orders AS o WHERE o.status = 'FRAUD');


-- -------------------------------------------------------------------------------------
-- S11 · DELETE ... USING
--   The target is joined to the USING list: `p.id = fp.payment_id` is the join (D-11).
-- -------------------------------------------------------------------------------------
DELETE FROM core.fct_payment AS fp
USING staging.payments AS p
WHERE p.id = fp.payment_id
  AND p.status = 'REVERSED';


-- -------------------------------------------------------------------------------------
-- S12 · DELETE with no WHERE: empties the table, names no column
-- -------------------------------------------------------------------------------------
DELETE FROM mart.top_customers;


-- -------------------------------------------------------------------------------------
-- S13 · a standalone UPDATE ... RETURNING                          (D-16)
--   The returned columns are the written table's columns; the WHERE decides which rows
--   are updated AND which are returned, so it filters both.
-- -------------------------------------------------------------------------------------
UPDATE core.dim_warehouse
SET capacity = capacity + 100
WHERE region = 'EU'
RETURNING id AS warehouse_id, capacity AS new_capacity;


-- -------------------------------------------------------------------------------------
-- S14 · a data-modifying CTE: DELETE ... RETURNING feeding an INSERT (D-17)
--   The archive pattern. The DELETE's predicate decides which returns are deleted AND
--   which are copied: a filter on both tables.
-- -------------------------------------------------------------------------------------
WITH moved AS (
    DELETE FROM staging.returns AS r
    WHERE r.status = 'CLOSED'
      AND r.created_at < DATE '2024-01-01'
    RETURNING r.id, r.amount, r.created_at
)
INSERT INTO mart.sales_daily (orders, gross_amount, sale_date)
SELECT m.id, m.amount, m.created_at::date
FROM moved AS m;


-- -------------------------------------------------------------------------------------
-- S15 · MERGE with four arms, including PostgreSQL 17's NOT MATCHED BY SOURCE
--   ON is structural (D-5); USING's WHERE and every arm condition are filters.
-- -------------------------------------------------------------------------------------
MERGE INTO core.dim_customer AS t
USING (
    SELECT c.id, c.full_name, c.segment, c.status
    FROM staging.customers AS c
    WHERE c.signup_ts >= DATE '2024-01-01'
) AS s
ON t.customer_id = s.id
WHEN MATCHED AND s.status = 'CLOSED' THEN
    DELETE
WHEN MATCHED THEN
    UPDATE SET full_name = s.full_name, segment = s.segment
WHEN NOT MATCHED THEN
    INSERT (customer_key, customer_id, full_name, segment, is_current)
    VALUES (s.id, s.id, s.full_name, s.segment, true)
WHEN NOT MATCHED BY SOURCE AND t.is_current THEN
    UPDATE SET is_current = false;


-- -------------------------------------------------------------------------------------
-- S16a · TRUNCATE - refused by name; must not affect the statements around it
-- -------------------------------------------------------------------------------------
TRUNCATE TABLE mart.channel_mix;


-- -------------------------------------------------------------------------------------
-- S16b · COPY FROM a file - refused by name (source outside the database)
-- -------------------------------------------------------------------------------------
COPY mart.kpi_rollup (level, region) FROM '/data/kpi.csv' WITH (FORMAT csv);


-- -------------------------------------------------------------------------------------
-- S17 · an ordinary INSERT after both refusals: it must be analysed normally
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
SELECT p.method, p.amount
FROM staging.payments AS p
WHERE p.status = 'SETTLED';
