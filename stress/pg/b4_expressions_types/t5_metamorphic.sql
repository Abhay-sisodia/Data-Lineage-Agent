-- =====================================================================================
-- B4 · T5 · METAMORPHIC — do equivalent spellings give identical lineage?
--
-- Objective card: stress/pg/b4_expressions_types/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (all constructs 9.4+).
--
-- Pairs tagged `-- @pair Pn A|B`, checked by stress/pg/triage.py.
--   P1-P10  MUST BE EQUAL. P1-P4 hold only under D-19: a guard spelled as a function
--           and as a CASE must classify the same.
--
-- Then one production-shaped model: priced order lines, converted to USD, aggregated by
-- month, banded by margin, shared and compared across months.
-- =====================================================================================


-- P1 · COALESCE(x, 0) vs its CASE: one column, derived (D-19)
-- @pair P1 A
INSERT INTO mart.sales_daily (sale_date, net_amount)
SELECT f.order_date, COALESCE(f.net_amount, 0)
FROM core.fct_order_line AS f;

-- @pair P1 B
INSERT INTO mart.sales_daily (sale_date, net_amount)
SELECT f.order_date, CASE WHEN f.net_amount IS NULL THEN 0 ELSE f.net_amount END
FROM core.fct_order_line AS f;


-- P2 · GREATEST(x, 0) vs its CASE: one column, derived (D-19)
-- @pair P2 A
INSERT INTO mart.sales_daily (sale_date, gross_amount)
SELECT f.order_date, GREATEST(f.gross_amount, 0)
FROM core.fct_order_line AS f;

-- @pair P2 B
INSERT INTO mart.sales_daily (sale_date, gross_amount)
SELECT f.order_date, CASE WHEN f.gross_amount > 0 THEN f.gross_amount ELSE 0 END
FROM core.fct_order_line AS f;


-- P3 · NULLIF(x, 0) vs its CASE: one column, derived (D-19)
-- @pair P3 A
INSERT INTO mart.sales_daily (sale_date, units)
SELECT f.order_date, NULLIF(f.quantity, 0)
FROM core.fct_order_line AS f;

-- @pair P3 B
INSERT INTO mart.sales_daily (sale_date, units)
SELECT f.order_date, CASE WHEN f.quantity = 0 THEN NULL ELSE f.quantity END
FROM core.fct_order_line AS f;


-- P4 · COALESCE(a, b) vs its CASE: two columns, conditional for both
-- @pair P4 A
INSERT INTO mart.sales_daily (sale_date, avg_order_value)
SELECT f.order_date, COALESCE(f.net_amount, f.gross_amount)
FROM core.fct_order_line AS f;

-- @pair P4 B
INSERT INTO mart.sales_daily (sale_date, avg_order_value)
SELECT
    f.order_date,
    CASE WHEN f.net_amount IS NOT NULL THEN f.net_amount ELSE f.gross_amount END
FROM core.fct_order_line AS f;


-- P5 · `::` vs CAST, with a type modifier
-- @pair P5 A
INSERT INTO mart.channel_mix (channel, revenue)
SELECT o.channel, o.amount::numeric(16, 2)
FROM staging.orders AS o;

-- @pair P5 B
INSERT INTO mart.channel_mix (channel, revenue)
SELECT o.channel, CAST(o.amount AS numeric(16, 2))
FROM staging.orders AS o;


-- P6 · || vs concat()
-- @pair P6 A
INSERT INTO core.dim_customer (customer_key, full_name)
SELECT c.id, c.full_name || ' <' || c.email || '>'
FROM staging.customers AS c;

-- @pair P6 B
INSERT INTO core.dim_customer (customer_key, full_name)
SELECT c.id, concat(c.full_name, ' <', c.email, '>')
FROM staging.customers AS c;


-- P7 · EXTRACT vs date_part
-- @pair P7 A
INSERT INTO mart.channel_mix (channel, orders)
SELECT o.channel, EXTRACT(month FROM o.order_ts)::bigint
FROM staging.orders AS o;

-- @pair P7 B
INSERT INTO mart.channel_mix (channel, orders)
SELECT o.channel, date_part('month', o.order_ts)::bigint
FROM staging.orders AS o;


-- P8 · IN (list) vs = ANY (ARRAY[...]) in WHERE
-- @pair P8 A
INSERT INTO mart.channel_mix (channel, revenue)
SELECT o.channel, o.amount
FROM staging.orders AS o
WHERE o.currency IN ('EUR', 'USD');

-- @pair P8 B
INSERT INTO mart.channel_mix (channel, revenue)
SELECT o.channel, o.amount
FROM staging.orders AS o
WHERE o.currency = ANY (ARRAY['EUR', 'USD']);


-- P9 · IS DISTINCT FROM vs its expanded form in WHERE
-- @pair P9 A
INSERT INTO mart.channel_mix (channel, region)
SELECT o.channel, o.currency
FROM staging.orders AS o
WHERE o.status IS DISTINCT FROM o.discount_code;

-- @pair P9 B
INSERT INTO mart.channel_mix (channel, region)
SELECT o.channel, o.currency
FROM staging.orders AS o
WHERE (o.status <> o.discount_code)
   OR ((o.status IS NULL) <> (o.discount_code IS NULL));


-- P10 · (c).full_name vs c.full_name (D-20)
-- @pair P10 A
INSERT INTO mart.top_customers (customer_id, full_name)
SELECT (c).id, (c).full_name
FROM staging.customers AS c
WHERE (c).status = 'ACTIVE';

-- @pair P10 B
INSERT INTO mart.top_customers (customer_id, full_name)
SELECT c.id, c.full_name
FROM staging.customers AS c
WHERE c.status = 'ACTIVE';


-- -------------------------------------------------------------------------------------
-- R1 · a realistic pricing, conversion and banding model
--   priced:  net unit price after a guarded discount (one-column guard: derived)
--   usd:     revenue and margin in USD; the rate is on a LEFT JOIN's null side (D-14),
--            guarded by COALESCE(rate, 1)
--   monthly: SUM by product, category and month
--   final:   a margin band - a CASE over margin, revenue and the category name (three
--            columns: conditional, aggregated where a SUM is on the path); the share of
--            the month; growth against the previous month; a guard on units in WHERE
--            (post-aggregation, B2-06)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance
    (product_id, category_name, period_month, units, revenue, margin, revenue_share,
     growth_pct)
WITH priced AS (
    SELECT
        ol.order_id,
        ol.product_id,
        o.order_ts,
        o.currency,
        ol.quantity,
        ol.unit_price * (1 - COALESCE(ol.discount_pct, 0) / 100) AS net_unit,
        p.cost,
        p.category_id
    FROM staging.order_lines AS ol
    JOIN staging.orders AS o ON o.id = ol.order_id
    JOIN staging.products AS p ON p.id = ol.product_id
    WHERE ol.status <> 'CANCELLED'
      AND o.order_ts >= DATE '2024-01-01'
),
usd AS (
    SELECT
        pr.product_id,
        pr.category_id,
        date_trunc('month', pr.order_ts)::date AS m,
        pr.quantity,
        pr.net_unit * pr.quantity * COALESCE(fx.rate_to_usd, 1) AS rev_usd,
        (pr.net_unit - pr.cost) * pr.quantity * COALESCE(fx.rate_to_usd, 1) AS margin_usd
    FROM priced AS pr
    LEFT JOIN staging.fx_rates AS fx
        ON fx.currency = pr.currency
       AND fx.rate_date = pr.order_ts::date
),
monthly AS (
    SELECT
        u.product_id,
        u.category_id,
        u.m,
        SUM(u.quantity) AS units,
        SUM(u.rev_usd) AS rev,
        SUM(u.margin_usd) AS margin
    FROM usd AS u
    GROUP BY u.product_id, u.category_id, u.m
)
SELECT
    mo.product_id,
    CASE
        WHEN mo.margin < 0 THEN 'LOSS'
        WHEN mo.margin / NULLIF(mo.rev, 0) < 0.1 THEN 'THIN'
        ELSE upper(cat.name)
    END,
    mo.m,
    mo.units,
    round(mo.rev, 2),
    round(mo.margin, 2),
    mo.rev / NULLIF(SUM(mo.rev) OVER (PARTITION BY mo.m), 0),
    round(
        100 * (mo.rev - LAG(mo.rev) OVER (PARTITION BY mo.product_id ORDER BY mo.m))
            / NULLIF(LAG(mo.rev) OVER (PARTITION BY mo.product_id ORDER BY mo.m), 0),
        4
    )
FROM monthly AS mo
JOIN core.dim_category AS cat ON cat.id = mo.category_id
WHERE GREATEST(mo.units, 0) > 0;
