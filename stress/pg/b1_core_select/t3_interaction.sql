-- =====================================================================================
-- B1 · T3 · INTERACTION — do the rules compose?
--
-- Objective card: stress/pg/b1_core_select/OBJECTIVES.md, section T3.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (all constructs 9.6+).
--
-- Constructs that each work alone, combined in ONE expression so that two rules meet and
-- have to agree: a window over an aggregate, an aggregate FILTER inside a window, a
-- ranking over a grouping set, an ordered limit over a sum.
--
-- Discipline kept throughout: no column reaches one target by two different paths with
-- two different transforms. That case has no settled answer yet (D-7 ranks ONE path), and
-- a test that depends on an unsettled answer measures the convention, not the analyser.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · a window over an aggregate: RANK() OVER (ORDER BY SUM(x) DESC)
--   The rank has no value source. It is influenced by net_amount through the ORDER BY
--   (D-4), and by the GROUP BY that decided what each SUM is (D-2, composed).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, revenue, margin)
SELECT
    f.product_key,
    SUM(f.net_amount),
    RANK() OVER (ORDER BY SUM(f.net_amount) DESC)
FROM core.fct_order_line AS f
GROUP BY f.product_key;


-- -------------------------------------------------------------------------------------
-- S2 · ratio to a windowed total: x / SUM(y) OVER (PARTITION BY r)
--   Two different columns on purpose: gross_amount is the numerator (derived), and
--   amount_usd reaches the share only through the windowed SUM (aggregated).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (period_month, channel, region, revenue, share_of_region)
SELECT
    f.order_date,
    f.status,
    d.region,
    f.net_amount,
    f.gross_amount / SUM(f.amount_usd) OVER (PARTITION BY d.region)
FROM core.fct_order_line AS f
JOIN core.dim_customer AS d
  ON d.customer_key = f.customer_key;


-- -------------------------------------------------------------------------------------
-- S3 · an aggregate FILTER inside a window: SUM(x) FILTER (WHERE c) OVER (...)
--   D-10 inside D-4. `status` is a value source of the running sum, never a filter on
--   the relation - every row is still written.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, period_month, running_revenue)
SELECT
    f.order_line_id,
    f.order_date,
    SUM(f.net_amount) FILTER (WHERE f.status = 'COMPLETE')
        OVER (PARTITION BY f.product_key ORDER BY f.order_date)
FROM core.fct_order_line AS f;


-- -------------------------------------------------------------------------------------
-- S4 · GROUPING SETS with a window over the grouped result
--   DENSE_RANK partitions by region and orders by the per-set SUM. Both grouping columns
--   decide what each SUM is, so both influence the rank.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region, segment, revenue, customers)
SELECT
    d.region,
    d.segment,
    SUM(f.net_amount),
    DENSE_RANK() OVER (PARTITION BY d.region ORDER BY SUM(f.net_amount) DESC)
FROM core.fct_order_line AS f
JOIN core.dim_customer AS d
  ON d.customer_key = f.customer_key
GROUP BY GROUPING SETS ((d.region, d.segment), (d.region));


-- -------------------------------------------------------------------------------------
-- S5 · CASE inside an aggregate inside a window
--   SUM(CASE ...) OVER (...): the path from net_amount crosses CASE and SUM, and the
--   ladder ranks the hardest - aggregated. `channel` decides which branch: a value source.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, last_order_date, lifetime_value)
SELECT
    f.store_id,
    f.order_date,
    SUM(CASE WHEN f.channel = 'WEB' THEN f.net_amount ELSE 0 END)
        OVER (PARTITION BY f.store_id ORDER BY f.order_date)
FROM core.fct_order_line AS f;


-- -------------------------------------------------------------------------------------
-- S6 · FULL JOIN, then GROUP BY a COALESCE of the two join keys
--   The grouping expression reads BOTH sides' carrier_id, so both influence every
--   aggregated column - including COUNT(*), which has no value source of its own.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, shipments, cost_per_kg)
SELECT
    COALESCE(a.carrier_id, b.carrier_id),
    COUNT(*),
    SUM(a.cost) / NULLIF(SUM(b.weight_kg), 0)
FROM core.fct_shipment AS a
FULL JOIN staging.shipments AS b
       ON b.id = a.shipment_id
GROUP BY COALESCE(a.carrier_id, b.carrier_id);


-- -------------------------------------------------------------------------------------
-- S7 · ordered-set aggregates, one with a FILTER, under GROUP BY
--   percentile_cont with FILTER (D-10 on an ordered-set aggregate), and percentile_disc.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, p50_transit_days, p90_transit_days)
SELECT
    s.carrier_id,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY s.transit_days)
        FILTER (WHERE s.status = 'DELIVERED'),
    percentile_disc(0.9) WITHIN GROUP (ORDER BY s.weight_kg)
FROM core.fct_shipment AS s
GROUP BY s.carrier_id;


-- -------------------------------------------------------------------------------------
-- S8 · DISTINCT ON and a window in the same SELECT
--   The window is evaluated over ALL joined rows before DISTINCT ON keeps one per
--   region. ROW_NUMBER is projected, never filtered, so it is plain influence; the
--   DISTINCT ON key and its tie-breaker are filters (D-9).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, customer_id, revenue, position)
SELECT DISTINCT ON (c.region)
    c.region,
    c.customer_key,
    f.net_amount,
    ROW_NUMBER() OVER (PARTITION BY c.region ORDER BY f.net_amount DESC)
FROM core.fct_order_line AS f
JOIN core.dim_customer AS c
  ON c.customer_key = f.customer_key
ORDER BY c.region, f.net_amount DESC;


-- -------------------------------------------------------------------------------------
-- S9 · ORDER BY an aggregate, with LIMIT: a top-N over sums
--   D-9 with D-6: the ordering is over SUM(net_amount), so the filter it makes acts
--   after aggregation.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name, revenue)
SELECT
    c.customer_id,
    c.full_name,
    SUM(f.net_amount)
FROM core.fct_order_line AS f
JOIN core.dim_customer AS c
  ON c.customer_key = f.customer_key
GROUP BY c.customer_id, c.full_name
ORDER BY SUM(f.net_amount) DESC
LIMIT 10;


-- -------------------------------------------------------------------------------------
-- S10 · a window over an aggregate over an expression: month-on-month growth
--   LAG(SUM(x)) OVER (ORDER BY month), twice, one inside arithmetic. The month is both
--   the grouping key and the window's ORDER BY.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (period_month, revenue, prev_month_revenue, growth_pct)
SELECT
    date_trunc('month', f.order_date)::date,
    SUM(f.gross_amount),
    LAG(SUM(f.gross_amount)) OVER (ORDER BY date_trunc('month', f.order_date)),
    SUM(f.gross_amount)
        / NULLIF(LAG(SUM(f.gross_amount)) OVER (ORDER BY date_trunc('month', f.order_date)), 0)
        - 1
FROM core.fct_order_line AS f
GROUP BY date_trunc('month', f.order_date);


-- -------------------------------------------------------------------------------------
-- S11 · mixed grain in one projection
--   A grouping key, a COUNT DISTINCT, a SUM, a window AVG over that SUM, and a
--   ROW_NUMBER - five columns, each owed exactly its own edges and no others.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily
    (sale_date, region, orders, net_amount, avg_order_value, rank_in_region)
SELECT
    f.order_date,
    s.region,
    COUNT(DISTINCT f.order_id),
    SUM(f.net_amount),
    AVG(SUM(f.net_amount)) OVER (PARTITION BY s.region),
    ROW_NUMBER() OVER (PARTITION BY s.region ORDER BY f.order_date)
FROM core.fct_order_line AS f
JOIN core.dim_store AS s
  ON s.id = f.store_id
GROUP BY f.order_date, s.region;


-- -------------------------------------------------------------------------------------
-- S12 · a compound HAVING over two different aggregates
--   COUNT(DISTINCT store_id) > 1: customers who bought from more than one store.
--   store_id is in no projection at all - the HAVING is its only appearance.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, lifetime_orders, avg_basket)
SELECT
    o.customer_id,
    COUNT(DISTINCT o.id),
    AVG(o.amount)
FROM staging.orders AS o
GROUP BY o.customer_id
HAVING COUNT(DISTINCT o.store_id) > 1
   AND SUM(o.amount) > 0;


-- -------------------------------------------------------------------------------------
-- S13 · all of it at once, at modest size
--   A CTE that aggregates with a FILTER and a HAVING, a second level that windows over
--   it, and a top level that keeps the top 3 per region by that window (D-9 via a
--   filtered rank), and computes a share.
-- -------------------------------------------------------------------------------------
WITH monthly AS (
    SELECT
        date_trunc('month', f.order_date)::date AS m,
        f.channel                               AS ch,
        s.region                                AS rgn,
        SUM(f.net_amount)                       AS rev,
        COUNT(*) FILTER (WHERE f.discount_amount > 0) AS discounted
    FROM core.fct_order_line AS f
    JOIN core.dim_store AS s
      ON s.id = f.store_id
    WHERE f.status <> 'CANCELLED'
    GROUP BY date_trunc('month', f.order_date), f.channel, s.region
    HAVING SUM(f.quantity) > 0
),
ranked AS (
    SELECT
        mo.m, mo.ch, mo.rgn, mo.rev, mo.discounted,
        RANK() OVER (PARTITION BY mo.rgn, mo.m ORDER BY mo.rev DESC) AS rnk,
        mo.rev / SUM(mo.rev) OVER (PARTITION BY mo.rgn, mo.m)       AS share
    FROM monthly AS mo
)
INSERT INTO mart.channel_mix (period_month, channel, region, orders, revenue, share_of_region)
SELECT r.m, r.ch, r.rgn, r.discounted, r.rev, r.share
FROM ranked AS r
WHERE r.rnk <= 3;
