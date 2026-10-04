-- =====================================================================================
-- B10 · T5 · METAMORPHIC — do equivalent lexical spellings give identical lineage?
--
-- Objective card: stress/pg/b10_lexical/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17. Run with psql.
--
-- Pairs tagged `-- @pair Pn A|B`, checked by stress/pg/triage.py. P1-P8 MUST BE EQUAL.
-- A prepared statement is compared at its EXECUTE (D-35).
--
-- Then one realistic psql deployment script: meta-commands, a transaction, a prepared
-- monthly load executed twice, and comments throughout.
-- =====================================================================================


-- P1 · a standard literal vs a dollar-quoted literal
-- @pair P1 A
INSERT INTO mart.channel_mix (channel)
SELECT o.channel FROM staging.orders AS o WHERE o.status = 'PAID';

-- @pair P1 B
INSERT INTO mart.channel_mix (channel)
SELECT o.channel FROM staging.orders AS o WHERE o.status = $$PAID$$;


-- P2 · a doubled quote vs an E'' escape
-- @pair P2 A
INSERT INTO mart.kpi_rollup (segment)
SELECT 'it''s ' || c.segment FROM staging.customers AS c;

-- @pair P2 B
INSERT INTO mart.kpi_rollup (segment)
SELECT E'it\'s ' || c.segment FROM staging.customers AS c;


-- P3 · a direct INSERT vs PREPARE + EXECUTE
-- @pair P3 A
INSERT INTO mart.sales_daily (store_id, net_amount)
SELECT o.store_id, SUM(o.amount)
FROM staging.orders AS o
WHERE o.currency = 'USD'
GROUP BY o.store_id;

PREPARE p3(text) AS
INSERT INTO mart.sales_daily (store_id, net_amount)
SELECT o.store_id, SUM(o.amount)
FROM staging.orders AS o
WHERE o.currency = $1
GROUP BY o.store_id;

-- @pair P3 B
EXECUTE p3('USD');


-- P4 · || vs OPERATOR(pg_catalog.||)
-- @pair P4 A
INSERT INTO mart.top_customers (full_name)
SELECT c.full_name || c.email FROM staging.customers AS c;

-- @pair P4 B
INSERT INTO mart.top_customers (full_name)
SELECT c.full_name OPERATOR(pg_catalog.||) c.email FROM staging.customers AS c;


-- P5 · heavily commented vs clean
-- @pair P5 A
INSERT INTO mart.customer_summary /* target */ (customer_id, lifetime_value)
SELECT c.id,                          -- the customer; not c.email
       /* SUM(o.discount_code) /* nested; */ */ SUM(o.amount)
FROM staging.customers AS c           -- FROM staging.payments AS p
JOIN staging.orders AS o              /* JOIN staging.returns AS r */
  ON o.customer_id = c.id
WHERE o.status = 'PAID'               -- AND o.channel = 'web'
GROUP BY c.id /* , c.segment */;

-- @pair P5 B
INSERT INTO mart.customer_summary (customer_id, lifetime_value)
SELECT c.id, SUM(o.amount)
FROM staging.customers AS c
JOIN staging.orders AS o ON o.customer_id = c.id
WHERE o.status = 'PAID'
GROUP BY c.id;


\set ch 'web'
-- P6 · a psql variable vs a literal
-- @pair P6 A
INSERT INTO mart.kpi_rollup (level)
SELECT o.currency FROM staging.orders AS o WHERE o.channel = :'ch';

-- @pair P6 B
INSERT INTO mart.kpi_rollup (level)
SELECT o.currency FROM staging.orders AS o WHERE o.channel = 'web';


-- P7 · CREATE TABLE AS SELECT vs CREATE TABLE AS EXECUTE
-- @pair P7 A
CREATE TABLE mart.p7 AS
SELECT p.id, p.method FROM staging.payments AS p WHERE p.status = 'OK';

DROP TABLE mart.p7;

PREPARE p7 AS
SELECT p.id, p.method FROM staging.payments AS p WHERE p.status = 'OK';

-- @pair P7 B
CREATE TABLE mart.p7 AS EXECUTE p7;


-- P8 · a U&'' literal vs the plain literal it spells
-- @pair P8 A
INSERT INTO mart.kpi_rollup (country_code)
SELECT c.country_code FROM staging.customers AS c WHERE c.segment = U&'B2\0042';

-- @pair P8 B
INSERT INTO mart.kpi_rollup (country_code)
SELECT c.country_code FROM staging.customers AS c WHERE c.segment = 'B2B';


-- =====================================================================================
-- The realistic script: a psql deployment of the monthly product load
-- =====================================================================================
\set ON_ERROR_STOP on
\echo 'loading mart.product_performance; two months'
\timing on

BEGIN;

PREPARE load_pp(date) AS
INSERT INTO mart.product_performance
    (product_id, category_name, period_month, units, revenue, margin)
WITH lines AS (                        -- one row per order line in the month
    SELECT ol.product_id,
           ol.quantity,
           ol.unit_price,
           p.cost,
           cat.name AS category_name   /* the category's display name */
    FROM staging.order_lines AS ol
    JOIN staging.products AS p ON p.id = ol.product_id
    JOIN core.dim_category AS cat ON cat.id = p.category_id
    WHERE ol.status <> $$CANCELLED$$
      AND ol.created_at >= $1
      AND ol.created_at < $1 + interval '1 month'
)
SELECT l.product_id,
       l.category_name,
       $1,
       SUM(l.quantity),
       SUM(l.quantity * l.unit_price),
       SUM(l.quantity * (l.unit_price - l.cost))
FROM lines AS l
GROUP BY l.product_id, l.category_name;

-- R1 · the first month
EXECUTE load_pp('2026-09-01');

-- R2 · the second month: the same edges again
EXECUTE load_pp('2026-10-01');

DEALLOCATE load_pp;

COMMIT;

\echo 'done'
