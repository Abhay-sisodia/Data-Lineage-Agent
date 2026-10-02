-- =====================================================================================
-- B6 · T5 · METAMORPHIC — do equivalent set-returning spellings give identical lineage?
--
-- Objective card: stress/pg/b6_srf_table_functions/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Pairs tagged `-- @pair Pn A|B`, checked by stress/pg/triage.py. P1-P6 MUST BE EQUAL.
--
-- Then one realistic report: product tags (the first two by position) by calendar month,
-- with a literal month series joined to tagged order lines.
-- Every node is schema-qualified (D-25).
-- =====================================================================================


-- P1 · unnest in FROM vs unnest in the select list
-- @pair P1 A
INSERT INTO mart.channel_mix (channel)
SELECT t.tag
FROM staging.orders AS o, unnest(o.tags) AS t(tag);

-- @pair P1 B
INSERT INTO mart.channel_mix (channel)
SELECT unnest(o.tags)
FROM staging.orders AS o;


-- P2 · comma vs CROSS JOIN LATERAL
-- @pair P2 A
INSERT INTO mart.kpi_rollup (level)
SELECT t.tag
FROM staging.products AS p, unnest(p.tags) AS t(tag);

-- @pair P2 B
INSERT INTO mart.kpi_rollup (level)
SELECT t.tag
FROM staging.products AS p
CROSS JOIN LATERAL unnest(p.tags) AS t(tag);


-- P3 · ROWS FROM two unnests vs one unnest of two arrays
-- @pair P3 A
INSERT INTO mart.top_customers (region, full_name)
SELECT r.a, r.b
FROM staging.orders AS o,
     ROWS FROM (unnest(o.tags), unnest(string_to_array(o.discount_code, '-'))) AS r(a, b);

-- @pair P3 B
INSERT INTO mart.top_customers (region, full_name)
SELECT r.a, r.b
FROM staging.orders AS o,
     unnest(o.tags, string_to_array(o.discount_code, '-')) AS r(a, b);


-- P4 · string_to_table vs unnest(string_to_array)
-- @pair P4 A
INSERT INTO mart.kpi_rollup (segment)
SELECT s
FROM staging.customers AS c, string_to_table(c.full_name, ' ') AS s;

-- @pair P4 B
INSERT INTO mart.kpi_rollup (segment)
SELECT s
FROM staging.customers AS c, unnest(string_to_array(c.full_name, ' ')) AS s;


-- P5 · regexp_split_to_table vs unnest(regexp_split_to_array)
-- @pair P5 A
INSERT INTO mart.kpi_rollup (country_code)
SELECT regexp_split_to_table(c.email, '[@.]')
FROM staging.customers AS c;

-- @pair P5 B
INSERT INTO mart.kpi_rollup (country_code)
SELECT unnest(regexp_split_to_array(c.email, '[@.]'))
FROM staging.customers AS c;


-- P6 · a series in FROM vs in the select list
-- @pair P6 A
INSERT INTO mart.kpi_rollup (orders)
SELECT g
FROM core.dim_warehouse AS w, generate_series(1, w.capacity) AS g;

-- @pair P6 B
INSERT INTO mart.kpi_rollup (orders)
SELECT generate_series(1, w.capacity)
FROM core.dim_warehouse AS w;


-- -------------------------------------------------------------------------------------
-- R1 · tag revenue by month
--   months:        a literal series - no source (D-28)
--   tagged_lines:  each COMPLETE order line exploded into its product's first two tags;
--                  t.n <= 2 is a filter from the array (D-26, D-9)
--   final:         joined to the calendar on the month (structural), grouped by tag and
--                  month; the sums are aggregated and the tag influences them
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (category_name, period_month, units, revenue)
WITH months AS (
    SELECT m::date AS month
    FROM generate_series(DATE '2024-01-01', DATE '2024-12-01', INTERVAL '1 month') AS m
),
tagged_lines AS (
    SELECT
        t.tag,
        date_trunc('month', f.order_date)::date AS month,
        f.quantity,
        f.net_amount
    FROM core.fct_order_line AS f
    JOIN core.dim_product AS dp ON dp.product_key = f.product_key
    JOIN staging.products AS p ON p.id = dp.product_id
    CROSS JOIN LATERAL unnest(p.tags) WITH ORDINALITY AS t(tag, n)
    WHERE t.n <= 2
      AND f.status = 'COMPLETE'
)
SELECT tl.tag, mo.month, SUM(tl.quantity), SUM(tl.net_amount)
FROM months AS mo
JOIN tagged_lines AS tl ON tl.month = mo.month
GROUP BY tl.tag, mo.month;
