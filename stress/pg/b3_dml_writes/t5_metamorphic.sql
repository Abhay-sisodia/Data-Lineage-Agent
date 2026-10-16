-- =====================================================================================
-- B3 · T5 · METAMORPHIC AND REALISTIC — one write, one answer
--
-- Objective card: stress/pg/b3_dml_writes/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17.
--
-- Pairs tagged `-- @pair Pn A|B`, checked by stress/pg/triage.py.
--   P1-P5  MUST BE EQUAL.
--   P6     SEAM: DELETE ... USING vs DELETE ... WHERE EXISTS - the semi-join match-key
--          seam left open in B2. Expected to DIFFER on the match key only.
--
-- Then one production-shaped nightly job: archive old refunds, rebuild customer activity,
-- and upsert the summary - writable CTE, LEFT JOIN, aggregation, ON CONFLICT with an
-- accumulator and a protective WHERE.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- P1 · an upsert as ON CONFLICT DO UPDATE  vs  as MERGE                      [equal]
-- -------------------------------------------------------------------------------------
-- @pair P1 A
INSERT INTO mart.sales_daily (sale_date, store_id, gross_amount)
SELECT f.order_date, f.store_id, SUM(f.gross_amount)
FROM core.fct_order_line AS f
GROUP BY f.order_date, f.store_id
ON CONFLICT (sale_date, store_id) DO UPDATE
SET gross_amount = EXCLUDED.gross_amount;

-- @pair P1 B
MERGE INTO mart.sales_daily AS t
USING (
    SELECT f.order_date, f.store_id, SUM(f.gross_amount) AS g
    FROM core.fct_order_line AS f
    GROUP BY f.order_date, f.store_id
) AS s
ON t.sale_date = s.order_date AND t.store_id = s.store_id
WHEN MATCHED THEN
    UPDATE SET gross_amount = s.g
WHEN NOT MATCHED THEN
    INSERT (sale_date, store_id, gross_amount) VALUES (s.order_date, s.store_id, s.g);


-- -------------------------------------------------------------------------------------
-- P2 · a CTE-fed INSERT  vs  the same source inlined                         [equal]
-- -------------------------------------------------------------------------------------
-- @pair P2 A
WITH x AS (
    SELECT p.order_id, p.amount FROM staging.payments AS p WHERE p.method = 'CARD'
)
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT x.order_id, x.amount FROM x;

-- @pair P2 B
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT x.order_id, x.amount
FROM (
    SELECT p.order_id, p.amount FROM staging.payments AS p WHERE p.method = 'CARD'
) AS x;


-- -------------------------------------------------------------------------------------
-- P3 · an omitted column list  vs  the explicit one it means                [equal]
--   kpi_rollup's order: level, region, country_code, segment, orders, revenue, customers
-- -------------------------------------------------------------------------------------
-- @pair P3 A
INSERT INTO mart.kpi_rollup
SELECT 'store', s.region, s.country_code, NULL, NULL, NULL, NULL
FROM core.dim_store AS s
WHERE s.status = 'OPEN';

-- @pair P3 B
INSERT INTO mart.kpi_rollup (level, region, country_code)
SELECT 'store', s.region, s.country_code
FROM core.dim_store AS s
WHERE s.status = 'OPEN';


-- -------------------------------------------------------------------------------------
-- P4 · SET a = x, b = y  vs  SET (a, b) = (x, y)                              [equal]
-- -------------------------------------------------------------------------------------
-- @pair P4 A
UPDATE core.dim_warehouse
SET name = upper(name), capacity = capacity + 1
WHERE status = 'OPEN';

-- @pair P4 B
UPDATE core.dim_warehouse
SET (name, capacity) = (upper(name), capacity + 1)
WHERE status = 'OPEN';


-- -------------------------------------------------------------------------------------
-- P5 · DELETE ... WHERE IN  vs  DELETE ... WHERE EXISTS                      [equal]
-- -------------------------------------------------------------------------------------
-- @pair P5 A
DELETE FROM staging.returns AS r
WHERE r.order_line_id IN (
        SELECT l.id FROM staging.order_lines AS l WHERE l.status = 'VOID'
      );

-- @pair P5 B
DELETE FROM staging.returns AS r
WHERE EXISTS (
        SELECT 1 FROM staging.order_lines AS l
        WHERE l.id = r.order_line_id AND l.status = 'VOID'
      );


-- -------------------------------------------------------------------------------------
-- P6 · DELETE ... USING  vs  DELETE ... WHERE EXISTS                          [SEAM]
--   Same rows deleted. USING's match is a join (structural, D-11); EXISTS's correlation
--   keeps both operands as filters. Expected to differ on the match key only.
-- -------------------------------------------------------------------------------------
-- @pair P6 A
DELETE FROM staging.payments AS p
USING staging.orders AS o
WHERE o.id = p.order_id
  AND o.status = 'FRAUD';

-- @pair P6 B
DELETE FROM staging.payments AS p
WHERE EXISTS (
        SELECT 1 FROM staging.orders AS o
        WHERE o.id = p.order_id AND o.status = 'FRAUD'
      );


-- =====================================================================================
-- R1 · REALISTIC: the nightly customer job
--
--   archived  delete refunds older than 90 days, returning what was removed
--   refunds   archived refunds summed per customer
--   activity  first / last order, count and gross per customer
--   upsert    customer_summary: gross less refunds, a recency status, an accumulating
--             order count, and PROTECTED rows left alone
--
--   The archived rows reach the target only through a LEFT JOIN, so their predicate
--   cannot remove a summary row: a value of lifetime_value there (D-14), and a filter
--   on payments, which it deletes from (convention (c)).
-- =====================================================================================
WITH archived AS (
    DELETE FROM staging.payments AS p
    WHERE p.status = 'REFUNDED'
      AND p.paid_at < now() - interval '90 days'
    RETURNING p.order_id, p.amount
),
refunds AS (
    SELECT o.customer_id, SUM(a.amount) AS refunded
    FROM archived AS a
    JOIN staging.orders AS o
      ON o.id = a.order_id
    GROUP BY o.customer_id
),
activity AS (
    SELECT
        o.customer_id,
        MIN(o.order_ts)::date AS first_order,
        MAX(o.order_ts)::date AS last_order,
        COUNT(*)              AS n,
        SUM(o.amount)         AS gross
    FROM staging.orders AS o
    WHERE o.status <> 'CANCELLED'
    GROUP BY o.customer_id
)
INSERT INTO mart.customer_summary AS cs
    (customer_id, first_order_date, last_order_date, lifetime_orders, lifetime_value, status)
SELECT
    act.customer_id,
    act.first_order,
    act.last_order,
    act.n,
    act.gross - COALESCE(r.refunded, 0),
    CASE WHEN act.last_order >= CURRENT_DATE - 90 THEN 'ACTIVE' ELSE 'LAPSED' END
FROM activity AS act
LEFT JOIN refunds AS r
       ON r.customer_id = act.customer_id
ON CONFLICT (customer_id) DO UPDATE
SET last_order_date = EXCLUDED.last_order_date,
    lifetime_orders = cs.lifetime_orders + EXCLUDED.lifetime_orders,
    lifetime_value  = EXCLUDED.lifetime_value,
    status          = EXCLUDED.status
WHERE cs.status <> 'PROTECTED';
