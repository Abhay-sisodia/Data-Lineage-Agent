-- =====================================================================================
-- B4 · T4 · ADVERSARIAL — does expression handling get fooled?
--
-- Objective card: stress/pg/b4_expressions_types/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (all constructs 9.4+).
--
-- Traps: dead reads (D-21), one column read twice (one distinct column for D-19), a
-- self-join's two instances (two), constant-only results behind a two-column test,
-- columns named like keywords and functions, column names inside string literals and
-- format strings, cast chains, an alias named like another real table, column-valued
-- subscripts and slice bounds.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · dead reads keep their edges (D-21)
--   x * 0, a never-taken branch, a COALESCE whose first argument is never NULL: each
--   column is still read, and each expression reads one column - derived.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, gross_amount, net_amount, units)
SELECT
    f.order_date,
    f.store_id,
    f.gross_amount * 0,
    CASE WHEN false THEN f.net_amount END,
    COALESCE(1, f.quantity)
FROM core.fct_order_line AS f;


-- -------------------------------------------------------------------------------------
-- S2 · one column, read twice or three times: still one distinct column (D-19)
--   customer_key qualified and unqualified is the same column of the same relation.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, lifetime_value, avg_basket, region)
SELECT
    d.customer_id,
    COALESCE(d.customer_key, d.customer_key),
    GREATEST(d.customer_key, customer_key, 0),
    CASE
        WHEN d.region IS NULL THEN 'n/a'
        WHEN d.region = '' THEN 'n/a'
        ELSE d.region
    END
FROM core.dim_customer AS d;


-- -------------------------------------------------------------------------------------
-- S3 · a self-join: a.list_price and b.list_price are two columns (D-19)
--   Both instances are the same dictionary column, so each fact appears once. The ON,
--   inequality included, is structural (D-5).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, revenue, margin)
SELECT
    a.product_id,
    GREATEST(a.list_price, b.list_price),
    LEAST(a.cost, b.cost)
FROM core.dim_product AS a
JOIN core.dim_product AS b
    ON b.product_id = a.product_id
   AND b.product_key <> a.product_key;


-- -------------------------------------------------------------------------------------
-- S4 · columns named year / month / quarter / week beside EXTRACT(year FROM ...)
--   Inside EXTRACT, `year` and `month` are field keywords, not dim_date's columns.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, orders)
SELECT
    d.year::text,
    d.month::text || '/' || d.quarter,
    EXTRACT(year FROM d.calendar_date)::bigint
FROM core.dim_date AS d
WHERE d.week > 0
  AND EXTRACT(month FROM d.calendar_date) = d.month;


-- -------------------------------------------------------------------------------------
-- S5 · column names inside string literals and a format string
--   'o.channel' is text. format()'s %I arguments are literals; only o.currency is read.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, region)
SELECT
    'o.channel',
    format('%I.%I = %L', 'orders', 'amount', o.currency)
FROM staging.orders AS o
WHERE o.status = 'o.amount > 0';


-- -------------------------------------------------------------------------------------
-- S6 · cast chains
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (orders, revenue, channel)
SELECT
    o.amount::text::numeric::bigint,
    CAST(CAST(o.amount AS text) AS numeric(16, 2)),
    o.currency::text
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S7 · whole-row and field access through an alias named like another real table
--   `orders` here is staging.customers. (orders).id is customers.id (D-20).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name, region)
SELECT
    (orders).id,
    (orders).full_name,
    md5(orders::text)
FROM staging.customers AS orders;


-- -------------------------------------------------------------------------------------
-- S8 · a subscript and a slice bound that are columns (D-22: parameters are values)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, orders)
SELECT
    o.tags[o.store_id],
    array_length(o.tags[1:o.store_id], 1)
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S9 · NULL tests spelled four ways in WHERE, a row IS NOT NULL among them
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT p.order_id, p.amount
FROM staging.payments AS p
WHERE NOT (p.paid_at IS NULL)
  AND p.method IS DISTINCT FROM NULL
  AND ROW(p.currency, p.status) IS NOT NULL;


-- -------------------------------------------------------------------------------------
-- S10 · constant results behind a two-column test: conditional (D-19)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, segment)
SELECT
    c.id,
    CASE WHEN c.country_code = 'DE' AND c.segment = 'B2B' THEN 'DACH-B2B' ELSE 'OTHER' END
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S11 · a two-column NULLIF; a one-column guard nested inside a two-column COALESCE
--   On net_amount's path the NULLIF adds nothing (one column) and the COALESCE (two
--   columns) makes it conditional.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, net_amount, avg_order_value)
SELECT
    f.order_date,
    f.store_id,
    NULLIF(f.net_amount, f.gross_amount),
    COALESCE(NULLIF(f.net_amount, 0), f.unit_price)
FROM core.fct_order_line AS f;


-- -------------------------------------------------------------------------------------
-- S12 · a CASE in ORDER BY under LIMIT (D-9): its column decides which rows land
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT p.order_id, p.amount
FROM staging.payments AS p
ORDER BY CASE WHEN p.method = 'CARD' THEN 0 ELSE 1 END, p.paid_at DESC
LIMIT 100;


-- -------------------------------------------------------------------------------------
-- S13 · columns where constants usually stand: BETWEEN SYMMETRIC bounds, an IN list
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level)
SELECT o.channel
FROM staging.orders AS o
WHERE o.amount BETWEEN SYMMETRIC o.id AND o.store_id
  AND o.currency IN ('EUR', o.discount_code);


-- -------------------------------------------------------------------------------------
-- S14 · a column multiplying an interval literal
-- -------------------------------------------------------------------------------------
INSERT INTO core.fct_shipment (shipment_id, promised_date)
SELECT
    s.id,
    (s.shipped_at + s.weight_kg * INTERVAL '1 hour')::date
FROM staging.shipments AS s;
