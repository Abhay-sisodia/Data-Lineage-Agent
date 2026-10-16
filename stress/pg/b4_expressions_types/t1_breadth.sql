-- =====================================================================================
-- B4 · T1 · BREADTH — is each expression family supported at all?
--
-- Objective card: stress/pg/b4_expressions_types/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (every construct here is 9.4+;
--                 `cardinality` 9.4, `regexp_match` 10).
--
-- One construct family per statement. Conventions: every B1-B3 decision, plus D-19 (a
-- conditional form is conditional only when it reads two or more distinct columns), D-20
-- (a whole-row reference expands, field-exact), D-21 (dead reads keep their edges) and
-- D-22 (a parameter column is a derived value). A cast is derived: something is between
-- the column and the target.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · CASE: searched over two columns, simple over one, nested
--   D-19: the searched CASE reads status and segment - conditional for both. The simple
--   CASE reads status alone - a mapping, derived. The outer nested CASE reads
--   country_code and (through the inner CASE) segment - conditional for both.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, segment, status, region)
SELECT
    c.id,
    CASE WHEN c.status = 'ACTIVE' THEN c.segment ELSE 'INACTIVE' END,
    CASE c.status
        WHEN 'A' THEN 'ACTIVE'
        WHEN 'C' THEN 'CLOSED'
        ELSE 'OTHER'
    END,
    CASE
        WHEN c.country_code IN ('DE', 'FR') THEN
            CASE WHEN c.segment = 'B2B' THEN 'EU-B2B' ELSE 'EU' END
        ELSE 'ROW'
    END
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S2 · COALESCE, NULLIF, GREATEST, LEAST
--   Two-column forms are conditional; a column against constants is derived (D-19).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily
    (sale_date, store_id, gross_amount, net_amount, avg_order_value, units)
SELECT
    f.order_date,
    COALESCE(f.store_id, 0),
    GREATEST(f.gross_amount, f.net_amount),
    LEAST(f.net_amount, 10000),
    f.net_amount / NULLIF(f.quantity, 0),
    COALESCE(f.quantity, f.order_line_id, 0)
FROM core.fct_order_line AS f;


-- -------------------------------------------------------------------------------------
-- S3 · casts: `::`, CAST, a type modifier, a typed literal, a cast inside WHERE
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (period_month, channel, orders, revenue)
SELECT
    o.order_ts::date,
    CAST(o.channel AS varchar(20)),
    o.id::bigint,
    o.amount::numeric(16, 2)
FROM staging.orders AS o
WHERE o.created_at >= DATE '2024-01-01'
  AND o.store_id::text <> '0';


-- -------------------------------------------------------------------------------------
-- S4 · string functions and the || operator
-- -------------------------------------------------------------------------------------
INSERT INTO core.dim_customer
    (customer_key, email, full_name, country_code, segment, status)
SELECT
    c.id,
    lower(trim(c.email)),
    initcap(c.full_name) || ' (' || upper(c.country_code) || ')',
    left(c.country_code, 2),
    concat_ws('-', c.segment, split_part(c.email, '@', 2)),
    replace(regexp_replace(c.status, '\s+', '', 'g'), '_', ' ')
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S5 · date and time: date_trunc, AT TIME ZONE, interval arithmetic, EXTRACT, age
-- -------------------------------------------------------------------------------------
INSERT INTO core.fct_shipment
    (shipment_id, order_id, shipped_date, delivered_date, promised_date, transit_days)
SELECT
    s.id,
    s.order_id,
    date_trunc('day', s.shipped_at)::date,
    (s.delivered_at AT TIME ZONE 'UTC')::date,
    (s.promised_at + INTERVAL '1 day')::date,
    EXTRACT(DAY FROM s.delivered_at - s.shipped_at)::integer
FROM staging.shipments AS s
WHERE age(s.delivered_at, s.shipped_at) < INTERVAL '30 days';


-- -------------------------------------------------------------------------------------
-- S6 · math: round, arithmetic, power, sqrt, abs, ceil, the % operator
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, revenue, margin, growth_pct, units)
SELECT
    p.id,
    round(p.list_price * 1.2, 2),
    p.list_price - p.cost,
    power(p.cost, 2) / sqrt(abs(p.list_price) + 1),
    ceil(p.list_price)::bigint % 7
FROM staging.products AS p;


-- -------------------------------------------------------------------------------------
-- S7 · IS DISTINCT FROM, projected and in WHERE
--   The ON is structural (D-5). The projected comparison reads two columns, but it is
--   not a conditional FORM - it computes a boolean - so it is derived.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, segment, region)
SELECT
    'changed',
    d.segment,
    (d.region IS DISTINCT FROM s.region)::text
FROM core.dim_customer AS d
JOIN core.dim_store AS s ON s.country_code = d.country_code
WHERE d.status IS DISTINCT FROM 'CLOSED'
  AND d.valid_to IS NOT DISTINCT FROM d.valid_from;


-- -------------------------------------------------------------------------------------
-- S8 · regular expressions: substring FROM, regexp_match, ~, !~*, SIMILAR TO
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, region)
SELECT
    substring(o.discount_code FROM '^[A-Z]+'),
    (regexp_match(o.channel, '([a-z]+)'))[1]
FROM staging.orders AS o
WHERE o.discount_code ~ '^PROMO'
  AND o.channel !~* 'test'
  AND o.currency SIMILAR TO '(EUR|USD)';


-- -------------------------------------------------------------------------------------
-- S9 · row constructors: ROW(...) projected, row equality and row ordering in WHERE
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name)
SELECT
    c.id,
    ROW(c.full_name, c.email)::text
FROM staging.customers AS c
WHERE (c.country_code, c.segment) = ('DE', 'B2B')
   OR (c.created_at, c.id) > (TIMESTAMPTZ '2024-01-01 00:00:00+00', 0);


-- -------------------------------------------------------------------------------------
-- S10 · composite field access and a whole-row reference (D-20)
--   (c).id IS c.id. row_to_json(c) reads every column of customers - each a derived
--   value of `region` here.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name, region)
SELECT
    (c).id,
    (c).full_name,
    length(row_to_json(c)::text)::text
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S11 · arrays: subscript, slice, ARRAY[] constructor, = ANY, @>, &&
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, region, orders)
SELECT
    o.tags[1],
    array_to_string(o.tags[2:3], ','),
    cardinality(ARRAY[o.customer_id, o.store_id])
FROM staging.orders AS o
WHERE 'vip' = ANY (o.tags)
  AND o.tags @> ARRAY['web']
  AND NOT (o.tags && ARRAY['test', 'fraud']);


-- -------------------------------------------------------------------------------------
-- S12 · array_agg under array_length, grouped
--   COUNT(DISTINCT ...) of the elements, spelled through an array: aggregated.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, customers)
SELECT
    'store',
    s.region,
    array_length(array_agg(DISTINCT o.customer_id), 1)
FROM staging.orders AS o
JOIN core.dim_store AS s ON s.id = o.store_id
GROUP BY s.region;


-- -------------------------------------------------------------------------------------
-- S13 · ranges: tstzrange and daterange, lower/upper, @> and && in a CASE and in WHERE
--   The CASE reads three columns (shipped_at, promised_at, delivered_at): conditional.
-- -------------------------------------------------------------------------------------
INSERT INTO core.fct_shipment (shipment_id, transit_days, status)
SELECT
    s.id,
    upper(tstzrange(s.shipped_at, s.delivered_at))::date
        - lower(tstzrange(s.shipped_at, s.delivered_at))::date,
    CASE
        WHEN tstzrange(s.shipped_at, s.promised_at) @> s.delivered_at THEN 'ON_TIME'
        ELSE 'LATE'
    END
FROM staging.shipments AS s
WHERE daterange(s.shipped_at::date, s.delivered_at::date)
      && daterange(DATE '2024-01-01', DATE '2024-12-31');


-- -------------------------------------------------------------------------------------
-- S14 · literals and volatile functions: no value has a source
--   Only the WHERE names a column.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, orders)
SELECT
    'snapshot',
    to_char(now(), 'YYYY-MM'),
    (random() * 100)::bigint
FROM core.dim_date AS d
WHERE d.is_weekend;


-- -------------------------------------------------------------------------------------
-- S15 · parameter columns (D-22): a width, a unit chosen by CASE, a precision
--   The CASE inside date_trunc reads one column (mode): derived. Every parameter column
--   is a derived value of the output it parameterises.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name, period_month, cost_per_kg)
SELECT
    c.id,
    lpad(c.name, length(c.mode) + 10, '*'),
    date_trunc(CASE WHEN c.mode = 'AIR' THEN 'week' ELSE 'month' END, s.shipped_date)::date,
    round(s.cost / NULLIF(s.weight_kg, 0), s.transit_days)
FROM core.dim_carrier AS c
JOIN core.fct_shipment AS s ON s.carrier_id = c.id;


-- -------------------------------------------------------------------------------------
-- S16 · boolean expressions projected, and a two-column COALESCE of dates
-- -------------------------------------------------------------------------------------
INSERT INTO core.dim_customer (customer_key, is_current, first_order_date)
SELECT
    d.customer_key,
    (d.valid_to IS NULL AND d.status <> 'CLOSED'),
    COALESCE(d.first_order_date, d.valid_from)
FROM core.dim_customer AS d;


-- -------------------------------------------------------------------------------------
-- S17 · format() and to_char() with format strings, substr, BETWEEN
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, country_code)
SELECT
    format('%s/%s', w.region, w.name),
    to_char(w.capacity, 'FM999,999'),
    upper(substr(w.region, 1, 2))
FROM core.dim_warehouse AS w
WHERE w.capacity BETWEEN 100 AND 10000;
