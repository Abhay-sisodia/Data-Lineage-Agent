-- =====================================================================================
-- B4 · T3 · INTERACTION — do expression rules compose with B1/B2 rules?
--
-- Objective card: stress/pg/b4_expressions_types/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (all constructs 9.4+).
--
-- Expressions in the places B1 and B2 gave meaning to: GROUP BY and PARTITION BY
-- (influence, D-2/D-4), ON (structural, D-5), WHERE and HAVING (filters, phase by D-6),
-- FILTER (values, D-10), DISTINCT ON (D-9), scalar subqueries (D-12), set-operation
-- arms (each arm its own path).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · expression GROUP BY key, CASE inside SUM, window over SUM partitioned by an
--      expression
--   The grouping key expression's column influences every aggregated output and not
--   the key itself (D-2).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance
    (product_id, period_month, revenue, revenue_share, running_revenue)
SELECT
    f.product_key,
    date_trunc('month', f.order_date)::date,
    SUM(CASE WHEN f.status = 'COMPLETE' THEN f.net_amount ELSE 0 END),
    SUM(f.net_amount)
        / NULLIF(SUM(SUM(f.net_amount)) OVER (PARTITION BY date_trunc('month', f.order_date)), 0),
    SUM(SUM(COALESCE(f.net_amount, 0))) OVER (
        PARTITION BY f.product_key
        ORDER BY date_trunc('month', f.order_date)
    )
FROM core.fct_order_line AS f
GROUP BY f.product_key, date_trunc('month', f.order_date);


-- -------------------------------------------------------------------------------------
-- S2 · expressions in ON (structural), array and range predicates in WHERE, expression
--      aggregates in HAVING
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, region, orders, revenue)
SELECT
    lower(o.channel),
    s.region,
    COUNT(o.id),
    SUM(o.amount * COALESCE(fx.rate_to_usd, 1))
FROM staging.orders AS o
JOIN core.dim_store AS s
    ON s.id = o.store_id
   AND upper(s.country_code) = left(o.currency, 2)
LEFT JOIN staging.fx_rates AS fx
    ON fx.currency = o.currency
   AND fx.rate_date = o.order_ts::date
WHERE o.tags && ARRAY['retail']
  AND tstzrange(o.created_at, o.updated_at) @> o.order_ts
GROUP BY lower(o.channel), s.region
HAVING SUM(o.amount) > 0
   AND MAX(cardinality(o.tags)) >= 1;


-- -------------------------------------------------------------------------------------
-- S3 · a scalar subquery inside a CASE test, and a CASE inside a scalar subquery
--   D-12: each subquery's WHERE columns (correlation included) are values of the column
--   it feeds - aggregated, because each subquery aggregates. Nothing filters the table.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, status, avg_basket)
SELECT
    c.id,
    CASE
        WHEN (SELECT COUNT(*) FROM staging.orders AS o WHERE o.customer_id = c.id) = 0
            THEN 'PROSPECT'
        ELSE c.status
    END,
    (
        SELECT AVG(CASE WHEN o2.currency = 'USD' THEN o2.amount ELSE o2.amount * 1.1 END)
        FROM staging.orders AS o2
        WHERE o2.customer_id = c.id
          AND o2.status <> 'CANCELLED'
    )
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S4 · LAG over an aggregate, with growth computed by a one-column CASE, over a named
--      window
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance
    (product_id, period_month, prev_month_revenue, growth_pct)
SELECT
    x.product_id,
    x.m,
    LAG(x.rev) OVER w,
    CASE
        WHEN LAG(x.rev) OVER w IS NULL OR LAG(x.rev) OVER w = 0 THEN NULL
        ELSE round((x.rev - LAG(x.rev) OVER w) / LAG(x.rev) OVER w * 100, 4)
    END
FROM (
    SELECT
        p.product_id,
        date_trunc('month', f.order_date)::date AS m,
        SUM(f.net_amount) AS rev
    FROM core.fct_order_line AS f
    JOIN core.dim_product AS p ON p.product_key = f.product_key
    GROUP BY p.product_id, date_trunc('month', f.order_date)
) AS x
WINDOW w AS (PARTITION BY x.product_id ORDER BY x.m);


-- -------------------------------------------------------------------------------------
-- S5 · DISTINCT ON an expression, ordered by an expression key and a tie-breaker (D-9)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, customer_id, full_name, revenue)
SELECT DISTINCT ON (upper(d.region))
    upper(d.region),
    d.customer_id,
    initcap(d.full_name),
    f.net_amount
FROM core.dim_customer AS d
JOIN core.fct_order_line AS f ON f.customer_key = d.customer_key
ORDER BY upper(d.region), f.net_amount DESC NULLS LAST;


-- -------------------------------------------------------------------------------------
-- S6 · a UNION ALL whose arms compute the same column differently
--   Each arm is its own path: identity, conditional (two columns), derived (a clamp).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, revenue)
SELECT 'orders', upper(o.currency), o.amount
FROM staging.orders AS o
WHERE o.status = 'PAID'
UNION ALL
SELECT
    'payments',
    p.currency,
    CASE WHEN p.status = 'REFUND' THEN -p.amount ELSE p.amount END
FROM staging.payments AS p
UNION ALL
SELECT 'returns', NULL, GREATEST(r.amount, 0)
FROM staging.returns AS r;


-- -------------------------------------------------------------------------------------
-- S7 · FILTER with compound predicates, string_agg ordered by another column
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name, shipments, cost_per_kg)
SELECT
    c.id,
    string_agg(upper(w.region), ',' ORDER BY w.name),
    COUNT(*) FILTER (WHERE f.status IN ('DELIVERED', 'RETURNED') AND f.weight_kg > 0),
    SUM(f.cost) FILTER (WHERE f.transit_days IS NOT NULL) / NULLIF(SUM(f.weight_kg), 0)
FROM core.dim_carrier AS c
JOIN core.fct_shipment AS f ON f.carrier_id = c.id
JOIN core.dim_warehouse AS w ON w.id = f.warehouse_id
GROUP BY c.id, c.name;


-- -------------------------------------------------------------------------------------
-- S8 · RANK over a SUM, partitioned by a column and an expression; an aggregate whose
--      precision is another aggregate (D-22)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily
    (sale_date, store_id, region, rank_in_region, avg_order_value)
SELECT
    f.order_date,
    f.store_id,
    s.region,
    RANK() OVER (
        PARTITION BY s.region, date_part('dow', f.order_date)
        ORDER BY SUM(f.net_amount) DESC
    ),
    round(AVG(f.net_amount), MAX(length(s.country_code)))
FROM core.fct_order_line AS f
JOIN core.dim_store AS s ON s.id = f.store_id
GROUP BY f.order_date, f.store_id, s.region;


-- -------------------------------------------------------------------------------------
-- S9 · a band CASE over an aggregate, filtered by its own band one level up
--   The CASE reads one column (SUM's net_amount): derived at the node, aggregated on the
--   path. The grouping key influences the band, as it does every aggregated output. The
--   outer WHERE cuts on the band, which is a SUM: post-aggregation (B2-06).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, revenue)
SELECT b.band, b.region, b.rev
FROM (
    SELECT
        d.region,
        SUM(f.net_amount) AS rev,
        CASE
            WHEN SUM(f.net_amount) > 100000 THEN 'TOP'
            WHEN SUM(f.net_amount) > 50000 THEN 'MID'
            ELSE 'REST'
        END AS band
    FROM core.fct_order_line AS f
    JOIN core.dim_customer AS d ON d.customer_key = f.customer_key
    GROUP BY d.region
) AS b
WHERE b.band <> 'REST';
