-- =====================================================================================
-- B2 · T5 · METAMORPHIC AND REALISTIC — one computation, one answer?
--
-- Objective card: stress/pg/b2_subqueries_ctes_setops/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17.
--
-- Eight pairs, each tagged `-- @pair Pn A|B`, checked by stress/pg/triage.py.
--
--   P1-P4  MUST BE EQUAL under the current conventions.
--   P5-P8  SEAM PAIRS: the same semi-join, anti-join or per-row pick, spelled two ways
--          that today's conventions label DIFFERENTLY. Their keys label each spelling by
--          today's rules and they are expected to DIFFER. They exist to put each seam on
--          the record with a measurement, for a decision - not to be "fixed" quietly.
--
-- Then one production-shaped model: a carrier scorecard built from CTEs, EXISTS, NOT IN
-- over a UNION, a CASE inside an aggregate, and a lookup.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- P1 · EXISTS  vs  IN                                                        [equal]
-- -------------------------------------------------------------------------------------
-- @pair P1 A
INSERT INTO mart.top_customers (customer_id, region)
SELECT c.id, c.segment
FROM staging.customers AS c
WHERE EXISTS (
        SELECT 1 FROM staging.orders AS o
        WHERE o.customer_id = c.id AND o.status = 'COMPLETE'
      );

-- @pair P1 B
INSERT INTO mart.top_customers (customer_id, region)
SELECT c.id, c.segment
FROM staging.customers AS c
WHERE c.id IN (
        SELECT o.customer_id FROM staging.orders AS o WHERE o.status = 'COMPLETE'
      );


-- -------------------------------------------------------------------------------------
-- P2 · IN (subquery)  vs  = ANY (subquery)                                   [equal]
-- -------------------------------------------------------------------------------------
-- @pair P2 A
INSERT INTO mart.channel_mix (orders, channel)
SELECT o.id, o.channel
FROM staging.orders AS o
WHERE o.id IN (SELECT p.order_id FROM staging.payments AS p WHERE p.method = 'CARD');

-- @pair P2 B
INSERT INTO mart.channel_mix (orders, channel)
SELECT o.id, o.channel
FROM staging.orders AS o
WHERE o.id = ANY (SELECT p.order_id FROM staging.payments AS p WHERE p.method = 'CARD');


-- -------------------------------------------------------------------------------------
-- P3 · a CTE  vs  the same query inlined as a derived table                  [equal]
-- -------------------------------------------------------------------------------------
-- @pair P3 A
WITH u AS (
    SELECT s.region AS r FROM core.dim_store AS s
    UNION ALL
    SELECT w.region FROM core.dim_warehouse AS w WHERE w.status = 'OPEN'
)
INSERT INTO mart.kpi_rollup (region)
SELECT u.r FROM u;

-- @pair P3 B
INSERT INTO mart.kpi_rollup (region)
SELECT u.r
FROM (
    SELECT s.region AS r FROM core.dim_store AS s
    UNION ALL
    SELECT w.region FROM core.dim_warehouse AS w WHERE w.status = 'OPEN'
) AS u;


-- -------------------------------------------------------------------------------------
-- P4 · UNION  vs  UNION of SELECT DISTINCTs                                  [equal]
-- -------------------------------------------------------------------------------------
-- @pair P4 A
INSERT INTO mart.sales_daily (region)
SELECT o.channel FROM staging.orders AS o
UNION
SELECT p.method FROM staging.payments AS p;

-- @pair P4 B
INSERT INTO mart.sales_daily (region)
SELECT DISTINCT o.channel FROM staging.orders AS o
UNION
SELECT DISTINCT p.method FROM staging.payments AS p;


-- -------------------------------------------------------------------------------------
-- P5 · semi-join: IN (subquery)  vs  JOIN + DISTINCT                          [SEAM]
--   Same rows. The IN's match columns are filters; the JOIN's are structural (D-5).
-- -------------------------------------------------------------------------------------
-- @pair P5 A
INSERT INTO mart.product_performance (product_id, category_name)
SELECT p.product_key, p.category_name
FROM core.dim_product AS p
WHERE p.product_key IN (
        SELECT f.product_key FROM core.fct_order_line AS f WHERE f.quantity > 10
      );

-- @pair P5 B
INSERT INTO mart.product_performance (product_id, category_name)
SELECT DISTINCT p.product_key, p.category_name
FROM core.dim_product AS p
JOIN core.fct_order_line AS f
  ON f.product_key = p.product_key
WHERE f.quantity > 10;


-- -------------------------------------------------------------------------------------
-- P6 · anti-join: EXCEPT  vs  NOT EXISTS                                     [SEAM]
--   Same rows. EXCEPT's first arm only supplies values (convention (a)); NOT EXISTS
--   keeps both correlation operands as filters.
-- -------------------------------------------------------------------------------------
-- @pair P6 A
INSERT INTO mart.customer_summary (customer_id)
SELECT c.id FROM staging.customers AS c
EXCEPT
SELECT o.customer_id FROM staging.orders AS o WHERE o.status = 'FRAUD';

-- @pair P6 B
INSERT INTO mart.customer_summary (customer_id)
SELECT DISTINCT c.id
FROM staging.customers AS c
WHERE NOT EXISTS (
        SELECT 1 FROM staging.orders AS o
        WHERE o.customer_id = c.id AND o.status = 'FRAUD'
      );


-- -------------------------------------------------------------------------------------
-- P7 · a scalar subquery  vs  LEFT JOIN LATERAL of the same aggregate       [SEAM]
--   One row per carrier either way. D-12 makes the scalar's correlation a value; the
--   LATERAL is a row source, so its WHERE is a filter.
-- -------------------------------------------------------------------------------------
-- @pair P7 A
INSERT INTO mart.carrier_scorecard (carrier_id, avg_transit_days)
SELECT
    c.id,
    (SELECT AVG(f.transit_days) FROM core.fct_shipment AS f WHERE f.carrier_id = c.id)
FROM core.dim_carrier AS c;

-- @pair P7 B
INSERT INTO mart.carrier_scorecard (carrier_id, avg_transit_days)
SELECT c.id, x.a
FROM core.dim_carrier AS c
LEFT JOIN LATERAL (
    SELECT AVG(f.transit_days) AS a
    FROM core.fct_shipment AS f
    WHERE f.carrier_id = c.id
) AS x ON TRUE;


-- -------------------------------------------------------------------------------------
-- P8 · latest value per row: ordered pick  vs  LEFT JOIN to DISTINCT ON      [SEAM]
--   D-13 makes the scalar's ordering influence; D-9 makes the derived table's DISTINCT
--   ON a filter - though under a LEFT JOIN it removes no order.
-- -------------------------------------------------------------------------------------
-- @pair P8 A
INSERT INTO mart.sales_daily (orders, net_amount)
SELECT
    o.id,
    (SELECT p.amount FROM staging.payments AS p
      WHERE p.order_id = o.id
      ORDER BY p.paid_at DESC
      LIMIT 1)
FROM staging.orders AS o;

-- @pair P8 B
INSERT INTO mart.sales_daily (orders, net_amount)
SELECT o.id, lp.amount
FROM staging.orders AS o
LEFT JOIN (
    SELECT DISTINCT ON (p.order_id) p.order_id, p.amount
    FROM staging.payments AS p
    ORDER BY p.order_id, p.paid_at DESC
) AS lp
  ON lp.order_id = o.id;


-- =====================================================================================
-- R1 · REALISTIC: the monthly carrier scorecard
--
--   shipped  delivered or in-transit shipments whose order has a settled payment
--   lost     orders known lost: lost shipments UNION non-receipt returns
--   clean    shipped, minus anything lost
--   monthly  per carrier and month: count, on-time share, cost per kg
--   final    carrier name by lookup; months with fewer than ten shipments dropped
-- =====================================================================================
WITH shipped AS (
    SELECT s.shipment_id, s.order_id, s.carrier_id, s.shipped_date,
           s.delivered_date, s.promised_date, s.weight_kg, s.cost
    FROM core.fct_shipment AS s
    WHERE s.status IN ('DELIVERED', 'IN_TRANSIT')
      AND EXISTS (
            SELECT 1 FROM core.fct_payment AS p
            WHERE p.order_id = s.order_id AND p.status = 'SETTLED'
          )
),
lost AS (
    SELECT sh.order_id FROM staging.shipments AS sh WHERE sh.status = 'LOST'
    UNION
    SELECT r.order_line_id FROM staging.returns AS r WHERE r.reason_code = 'NOT_RECEIVED'
),
clean AS (
    SELECT sp.carrier_id, sp.shipped_date, sp.delivered_date, sp.promised_date,
           sp.weight_kg, sp.cost
    FROM shipped AS sp
    WHERE sp.order_id NOT IN (SELECT l.order_id FROM lost AS l)
),
monthly AS (
    SELECT
        cl.carrier_id,
        date_trunc('month', cl.shipped_date)::date AS m,
        COUNT(*) AS n,
        AVG(CASE WHEN cl.delivered_date <= cl.promised_date THEN 1.0 ELSE 0.0 END) AS on_time,
        SUM(cl.cost) / NULLIF(SUM(cl.weight_kg), 0) AS cpk
    FROM clean AS cl
    GROUP BY cl.carrier_id, date_trunc('month', cl.shipped_date)
)
INSERT INTO mart.carrier_scorecard
    (carrier_id, carrier_name, period_month, shipments, on_time_pct, cost_per_kg)
SELECT
    mo.carrier_id,
    (SELECT dc.name FROM core.dim_carrier AS dc WHERE dc.id = mo.carrier_id),
    mo.m,
    mo.n,
    mo.on_time,
    mo.cpk
FROM monthly AS mo
WHERE mo.n >= 10;
