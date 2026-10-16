-- =====================================================================================
-- B1 · T1 · BREADTH — is each core-SELECT construct supported at all?
--
-- Objective card: stress/pg/b1_core_select/OBJECTIVES.md, section T1.
-- Schema:         stress/pg/schema.sql (every relation schema-qualified).
-- PostgreSQL:     17. `FETCH FIRST ... WITH TIES` needs 13+; everything else 9.6+.
--
-- One construct group per statement, the simplest correct spelling of each. A miss here
-- means "not supported" rather than "broken by combination" - combination is T3's job,
-- nesting is T2's.
--
-- Each statement is preceded by a header naming what it carries, so a miss in the
-- results maps straight back to a checklist row.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · projections and expressions
--   identity, string functions and concatenation, a one-column COALESCE (a null guard,
--   so derived and NOT conditional), date_trunc + cast, arithmetic, NULLIF, GREATEST,
--   CAST(... AS ...), and a searched CASE. One literal column, which has no source.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance
    (product_id, category_name, period_month, units, revenue, margin,
     revenue_share, running_revenue, prev_month_revenue, growth_pct)
SELECT
    p.id,
    upper(trim(COALESCE(p.brand, 'n/a'))) || ' / ' || p.sku,
    date_trunc('month', p.created_at)::date,
    0,
    p.list_price * 1.2,
    p.list_price - p.cost,
    NULLIF(p.cost, 0) / p.list_price,
    GREATEST(p.list_price, p.cost),
    CAST(p.cost AS numeric(16,2)),
    CASE WHEN p.status = 'ACTIVE' THEN p.list_price ELSE NULL END
FROM staging.products AS p;


-- -------------------------------------------------------------------------------------
-- S2 · WHERE
--   =, IN (list), BETWEEN, LIKE, ILIKE, IS NOT NULL, IS DISTINCT FROM, AND / OR / NOT,
--   and a comparison between two columns of the SAME relation (not a join - D-11 is
--   about two different relations, so both operands here are filters).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, customer_id, full_name, revenue, position)
SELECT
    c.country_code,
    c.id,
    c.full_name,
    NULL,
    NULL
FROM staging.customers AS c
WHERE c.status = 'ACTIVE'
  AND c.segment IN ('SMB', 'ENT')
  AND c.signup_ts BETWEEN DATE '2024-01-01' AND DATE '2024-12-31'
  AND (c.email LIKE '%@%' OR c.full_name ILIKE 'a%')
  AND c.attributes IS NOT NULL
  AND c.country_code IS DISTINCT FROM 'XX'
  AND NOT (c.created_at < c.signup_ts);


-- -------------------------------------------------------------------------------------
-- S3 · INNER JOIN with a multi-condition ON
--   The ON carries a join equality AND a literal predicate on o.status. Both are
--   structural under D-5: an ON clause gives no edge, whatever it compares.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (period_month, channel, region, orders, revenue, share_of_region)
SELECT
    o.order_ts::date,
    o.channel,
    s.region,
    l.order_id,
    l.quantity * l.unit_price,
    l.discount_pct / 100
FROM staging.order_lines AS l
INNER JOIN staging.orders AS o
        ON o.id = l.order_id
       AND o.status <> 'CANCELLED'
INNER JOIN core.dim_store AS s
        ON s.id = o.store_id;


-- -------------------------------------------------------------------------------------
-- S4a · LEFT JOIN, right-side predicate in the ON
--   `sh.status = 'DELIVERED'` in the ON decides which shipments MATCH, not which
--   carriers survive - every carrier is kept. Structural, no edge.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name, shipments)
SELECT
    c.id,
    c.name,
    sh.id
FROM core.dim_carrier AS c
LEFT JOIN staging.shipments AS sh
       ON sh.carrier_id = c.id
      AND sh.status = 'DELIVERED';


-- -------------------------------------------------------------------------------------
-- S4b · LEFT JOIN, the same right-side predicate moved to WHERE
--   Now it removes rows the join kept (including every carrier with no shipment), so
--   it IS a filter. Same predicate, different clause, different answer - and both are
--   right.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, segment, orders, revenue)
SELECT
    'carrier',
    c.mode,
    c.status,
    sh.id,
    sh.cost
FROM core.dim_carrier AS c
LEFT JOIN staging.shipments AS sh
       ON sh.carrier_id = c.id
WHERE sh.status = 'DELIVERED';


-- -------------------------------------------------------------------------------------
-- S5 · RIGHT JOIN
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, region, orders, gross_amount, net_amount)
SELECT
    o.created_at::date,
    o.store_id,
    p.method,
    o.id,
    p.amount,
    o.amount
FROM staging.payments AS p
RIGHT JOIN staging.orders AS o
        ON o.id = p.order_id;


-- -------------------------------------------------------------------------------------
-- S6 · FULL OUTER JOIN with COALESCE over the two join keys
--   COALESCE over two COLUMN arguments is a genuine choice of source: conditional, from
--   both sides. The cast inside the second argument does not lower that.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (period_month, channel, revenue)
SELECT
    COALESCE(f.rate_date, p.paid_at::date),
    COALESCE(f.currency, p.currency),
    p.amount * f.rate_to_usd
FROM staging.fx_rates AS f
FULL OUTER JOIN staging.payments AS p
             ON p.currency = f.currency
            AND p.paid_at::date = f.rate_date;


-- -------------------------------------------------------------------------------------
-- S7 · CROSS JOIN to a one-row derived table
--   The derived table's WHERE decides which single row is crossed in, so
--   dim_date.calendar_date is a filter on the written relation.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, customer_id, full_name, position)
SELECT
    s.region,
    s.id,
    s.name,
    d.fiscal_year::integer
FROM core.dim_store AS s
CROSS JOIN (
    SELECT dd.fiscal_year
    FROM core.dim_date AS dd
    WHERE dd.calendar_date = DATE '2025-01-01'
) AS d;


-- -------------------------------------------------------------------------------------
-- S8a · JOIN ... USING
--   `order_id` is MERGED. For an INNER JOIN PostgreSQL's merged column is the left
--   input's, so the value comes from shipments.order_id. `carrier_id` is unqualified
--   and only shipments has it.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, shipments, cost_per_kg)
SELECT
    carrier_id,
    order_id,
    sh.cost / NULLIF(sh.weight_kg, 0)
FROM staging.shipments AS sh
JOIN staging.payments AS pay USING (order_id);


-- -------------------------------------------------------------------------------------
-- S8b · NATURAL JOIN
--   The two derived tables share exactly ONE column name, `region`, so the natural join
--   is on region alone. Unqualified `region` is the merged column: the left input's.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, segment, customers)
SELECT
    'warehouse',
    region,
    opened_on::text,
    w.capacity
FROM (
    SELECT wh.id AS warehouse_id, wh.region, wh.capacity
    FROM core.dim_warehouse AS wh
) AS w
NATURAL JOIN (
    SELECT st.region, st.opened_on, st.country_code
    FROM core.dim_store AS st
) AS s;


-- -------------------------------------------------------------------------------------
-- S9 · GROUP BY, the plain aggregates, and HAVING
--   COUNT(DISTINCT x), SUM, AVG; COUNT(*) has no source. HAVING carries MIN and MAX
--   over columns not otherwise projected, so the post-aggregation filter is the only
--   place those columns appear.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily
    (sale_date, store_id, orders, units, gross_amount, net_amount, avg_order_value)
SELECT
    f.order_date,
    f.store_id,
    COUNT(DISTINCT f.order_id),
    SUM(f.quantity),
    SUM(f.gross_amount),
    SUM(f.net_amount),
    AVG(f.net_amount)
FROM core.fct_order_line AS f
GROUP BY f.order_date, f.store_id
HAVING COUNT(*) > 5
   AND MAX(f.unit_price) < 10000
   AND MIN(f.discount_amount) >= 0;


-- -------------------------------------------------------------------------------------
-- S10a · GROUP BY ROLLUP, with GROUPING() in the projection
--   GROUPING() reports which grouping set a row belongs to; it carries no column's
--   value. No convention exists for it yet - see the key, where it is flagged rather
--   than labelled.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, country_code, orders, revenue)
SELECT
    CASE GROUPING(s.region, s.country_code)
        WHEN 0 THEN 'country'
        WHEN 1 THEN 'region'
        ELSE 'total'
    END,
    s.region,
    s.country_code,
    COUNT(f.order_id),
    SUM(f.amount_usd)
FROM core.fct_order_line AS f
JOIN core.dim_store AS s
  ON s.id = f.store_id
GROUP BY ROLLUP (s.region, s.country_code);


-- -------------------------------------------------------------------------------------
-- S10b · GROUP BY CUBE
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, region, orders, revenue)
SELECT
    p.method,
    p.currency,
    COUNT(p.id),
    SUM(p.amount)
FROM staging.payments AS p
GROUP BY CUBE (p.method, p.currency);


-- -------------------------------------------------------------------------------------
-- S10c · GROUP BY GROUPING SETS, including the empty grand-total set
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, country_code, customers)
SELECT
    c.segment,
    c.country_code,
    COUNT(c.id)
FROM staging.customers AS c
GROUP BY GROUPING SETS ((c.segment), (c.country_code), ());


-- -------------------------------------------------------------------------------------
-- S11 · the less common aggregates
--   string_agg with its own ORDER BY (the ordering column is influence, D-4); array_agg
--   inside cardinality(); FILTER (WHERE ...) on COUNT(*) and on AVG (D-10: the FILTER
--   columns are VALUE sources of that one column, never a filter on the relation);
--   percentile_cont ... WITHIN GROUP (the ordered column IS the value), with and without
--   a FILTER.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard
    (carrier_id, carrier_name, period_month, shipments, on_time_pct,
     avg_transit_days, p50_transit_days, p90_transit_days, cost_per_kg)
SELECT
    f.carrier_id,
    string_agg(f.status, ',' ORDER BY f.shipped_date),
    min(f.shipped_date),
    cardinality(array_agg(DISTINCT f.order_id)),
    (COUNT(*) FILTER (WHERE f.delivered_date <= f.promised_date))::numeric
        / NULLIF(COUNT(*), 0),
    AVG(f.transit_days) FILTER (WHERE f.warehouse_id IS NOT NULL),
    percentile_cont(0.5) WITHIN GROUP (ORDER BY f.transit_days),
    percentile_cont(0.9) WITHIN GROUP (ORDER BY f.transit_days)
        FILTER (WHERE f.weight_kg > 0),
    SUM(f.cost) / NULLIF(SUM(f.weight_kg), 0)
FROM core.fct_shipment AS f
GROUP BY f.carrier_id;


-- -------------------------------------------------------------------------------------
-- S12 · window functions
--   A named WINDOW used three times; inline windows with ROWS frames; ROW_NUMBER, RANK
--   and NTILE (no argument - nothing to supply a value); FIRST_VALUE and LAG/LEAD
--   (value-selecting, so derived); SUM OVER (aggregated). PARTITION BY and ORDER BY are
--   influence on the window's own output column (D-4). Nothing is filtered on any window,
--   so D-9 does not apply here.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance
    (product_id, category_name, period_month, units, revenue, margin,
     revenue_share, running_revenue, prev_month_revenue, growth_pct)
SELECT
    f.product_key,
    FIRST_VALUE(f.channel) OVER w,
    f.order_date,
    ROW_NUMBER() OVER w,
    SUM(f.net_amount) OVER (
        PARTITION BY f.product_key
        ORDER BY f.order_date
        ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
    ),
    RANK() OVER (PARTITION BY f.store_id ORDER BY f.gross_amount DESC),
    NTILE(4) OVER (ORDER BY f.amount_usd),
    SUM(f.gross_amount) OVER (
        PARTITION BY f.product_key
        ORDER BY f.order_date
        ROWS UNBOUNDED PRECEDING
    ),
    LAG(f.net_amount) OVER w,
    LEAD(f.quantity) OVER w - f.quantity
FROM core.fct_order_line AS f
WINDOW w AS (PARTITION BY f.product_key ORDER BY f.order_date);


-- -------------------------------------------------------------------------------------
-- S13a · DISTINCT
--   The rows DISTINCT removes are fully decided by the values written, so it adds no
--   filter edge (D-9 excludes it by name).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (region, channel)
SELECT DISTINCT
    w.region,
    w.status
FROM core.dim_warehouse AS w;


-- -------------------------------------------------------------------------------------
-- S13b · DISTINCT ON
--   One row per customer, the LATEST by order_ts. Under D-9 both the DISTINCT ON key and
--   the tie-breaking ORDER BY column decide which row survives: both are filters, as is
--   the ordinary WHERE.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, customer_id, full_name, revenue)
SELECT DISTINCT ON (o.customer_id)
    o.currency,
    o.customer_id,
    o.discount_code,
    o.amount
FROM staging.orders AS o
WHERE o.status <> 'CANCELLED'
ORDER BY o.customer_id, o.order_ts DESC;


-- -------------------------------------------------------------------------------------
-- S14a · ORDER BY ... LIMIT ... OFFSET
--   The ordering decides which 100 rows land: amount and paid_at are filters (D-9).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, revenue, position)
SELECT
    p.order_id,
    p.amount,
    NULL
FROM staging.payments AS p
WHERE p.status = 'SETTLED'
ORDER BY p.amount DESC, p.paid_at
LIMIT 100 OFFSET 10;


-- -------------------------------------------------------------------------------------
-- S14b · FETCH FIRST ... ROWS WITH TIES (PostgreSQL 13+)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, units, gross_amount)
SELECT
    r.created_at::date,
    r.quantity,
    r.amount
FROM staging.returns AS r
ORDER BY r.amount DESC
FETCH FIRST 50 ROWS WITH TIES;


-- -------------------------------------------------------------------------------------
-- S14c · ORDER BY with NO row limit
--   Removes no rows, so `reserved` is nothing at all - not a filter, not influence.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, units)
SELECT
    i.snapshot_date,
    i.on_hand
FROM staging.inventory_snapshots AS i
ORDER BY i.reserved;


-- -------------------------------------------------------------------------------------
-- S14d · LIMIT with NO ORDER BY
--   Keeps ten arbitrary rows. No column decides which, so there is no filter edge.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date)
SELECT d.calendar_date
FROM core.dim_date AS d
LIMIT 10;


-- -------------------------------------------------------------------------------------
-- S15 · a bare SELECT (D-8): the query's own output is the target, `<result>`
--   DENSE_RANK has no argument; its window is influence on capacity_rank.
-- -------------------------------------------------------------------------------------
SELECT
    w.name AS warehouse_name,
    DENSE_RANK() OVER (PARTITION BY w.region ORDER BY w.capacity DESC) AS capacity_rank
FROM core.dim_warehouse AS w
WHERE w.status = 'OPEN';


-- -------------------------------------------------------------------------------------
-- S16 · a second bare SELECT, with GROUP BY and HAVING, into the same `<result>`
--   Column names are disjoint from S15's, so the two cannot be confused in the key.
-- -------------------------------------------------------------------------------------
SELECT
    cat.department AS dept,
    COUNT(cat.id) AS n_categories
FROM core.dim_category AS cat
GROUP BY cat.department
HAVING COUNT(cat.id) > 1;
