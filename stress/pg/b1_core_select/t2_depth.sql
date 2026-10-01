-- =====================================================================================
-- B1 · T2 · DEPTH — does support survive nesting?
--
-- Objective card: stress/pg/b1_core_select/OBJECTIVES.md, section T2.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (all constructs 9.6+).
--
-- The T1 constructs, placed 3-6 levels down inside derived tables and CTEs and RENAMED
-- at every level. A rule that holds at the top level and fails one level down is the
-- commonest shape of defect this project has found; this file asks the question for
-- every B1 construct at once.
--
-- Both spellings of a CTE-fed write appear on purpose: `WITH ... INSERT` (the usual
-- PostgreSQL form) and `INSERT ... WITH ... SELECT` (the WITH belongs to the query).
-- They are the same statement to PostgreSQL.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · an alias chain: two columns renamed at each of five levels
--   Three CTEs and two derived tables. Nothing is computed, so both edges stay identity
--   end to end.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name)
WITH l1 AS (
    SELECT c.id AS cid, c.full_name AS nm
    FROM staging.customers AS c
),
l2 AS (
    SELECT l1.cid AS customer_ref, l1.nm AS display
    FROM l1
),
l3 AS (
    SELECT l2.customer_ref AS k, l2.display AS label
    FROM l2
)
SELECT x.who, x.what
FROM (
    SELECT y.kk AS who, y.ll AS what
    FROM (
        SELECT l3.k AS kk, l3.label AS ll
        FROM l3
    ) AS y
) AS x;


-- -------------------------------------------------------------------------------------
-- S2 · a window four levels down, read by name at the top
--   The window's PARTITION BY and ORDER BY must still arrive as influence on the column
--   that finally receives the rolling sum, and the base CTE's WHERE as a filter.
-- -------------------------------------------------------------------------------------
WITH base AS (
    SELECT f.product_key, f.order_date, f.net_amount
    FROM core.fct_order_line AS f
    WHERE f.status = 'COMPLETE'
),
ranked AS (
    SELECT
        b.product_key AS pk,
        b.order_date  AS od,
        SUM(b.net_amount) OVER (
            PARTITION BY b.product_key
            ORDER BY b.order_date
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) AS rolling
    FROM base AS b
)
INSERT INTO mart.product_performance (product_id, period_month, running_revenue)
SELECT z.p, z.d, z.r
FROM (
    SELECT q.pk AS p, q.od AS d, q.rolling AS r
    FROM (
        SELECT rk.pk, rk.od, rk.rolling
        FROM ranked AS rk
    ) AS q
) AS z;


-- -------------------------------------------------------------------------------------
-- S3 · re-aggregation: SUM per order, then AVG of those sums per store
--   Two GROUP BYs on one path. avg_order_value is influenced by BOTH: the inner grouping
--   decides what each order_total is, the outer decides which totals are averaged.
--   `orders` counts the inner grouping key, so only the OUTER grouping influences it.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (store_id, avg_order_value, orders)
WITH per_order AS (
    SELECT
        f.store_id      AS sid,
        f.order_id      AS oid,
        SUM(f.net_amount) AS order_total
    FROM core.fct_order_line AS f
    GROUP BY f.store_id, f.order_id
),
per_store AS (
    SELECT
        po.sid,
        AVG(po.order_total) AS avg_total,
        COUNT(po.oid)       AS n
    FROM per_order AS po
    GROUP BY po.sid
)
SELECT ps.sid, ps.avg_total, ps.n
FROM (
    SELECT s.sid, s.avg_total, s.n
    FROM per_store AS s
) AS ps;


-- -------------------------------------------------------------------------------------
-- S4 · HAVING three levels down, and a WHERE at the top over an aggregated column
--   Both are post-aggregation (D-6): the outer WHERE is spelled WHERE but filters on
--   COUNT(o.id), so it acts after aggregation.
-- -------------------------------------------------------------------------------------
WITH cust AS (
    SELECT
        o.customer_id  AS cust,
        COUNT(o.id)    AS n_orders,
        SUM(o.amount)  AS spend
    FROM staging.orders AS o
    WHERE o.status <> 'CANCELLED'
    GROUP BY o.customer_id
    HAVING SUM(o.amount) > 100
),
cust2 AS (
    SELECT c.cust, c.n_orders, c.spend
    FROM cust AS c
)
INSERT INTO mart.customer_summary (customer_id, lifetime_orders, lifetime_value)
SELECT t.cust, t.n_orders, t.spend
FROM (
    SELECT c2.cust, c2.n_orders, c2.spend
    FROM cust2 AS c2
) AS t
WHERE t.n_orders >= 3;


-- -------------------------------------------------------------------------------------
-- S5 · ROLLUP in an inner derived table, with an outer filter on GROUPING()
--   `g.gset = 1` keeps only the per-region subtotal rows. What it selects on is the
--   grouping STRUCTURE, not any column's data - flagged in the key, as in T1 S10a.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, segment, revenue)
SELECT 'region', g.rgn, g.seg, g.rev
FROM (
    SELECT
        d.region  AS rgn,
        d.segment AS seg,
        SUM(f.net_amount) AS rev,
        GROUPING(d.region, d.segment) AS gset
    FROM core.fct_order_line AS f
    JOIN core.dim_customer AS d
      ON d.customer_key = f.customer_key
    WHERE d.is_current
    GROUP BY ROLLUP (d.region, d.segment)
) AS g
WHERE g.gset = 1;


-- -------------------------------------------------------------------------------------
-- S6 · a join inside a derived table, joined again outside
--   Four relations, three ON clauses, none of which gives an edge (D-5). Every value
--   the outer query reads from `j` has to be traced back through the inner join.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (period_month, channel, region, revenue)
SELECT
    j.month_start,
    j.channel,
    st.region,
    j.line_value * fx.rate_to_usd
FROM (
    SELECT
        date_trunc('month', o.order_ts)::date AS month_start,
        o.channel,
        o.store_id,
        o.currency,
        o.order_ts::date                      AS odate,
        l.quantity * l.unit_price             AS line_value
    FROM staging.orders AS o
    JOIN staging.order_lines AS l
      ON l.order_id = o.id
) AS j
JOIN core.dim_store AS st
  ON st.id = j.store_id
JOIN staging.fx_rates AS fx
  ON fx.currency  = j.currency
 AND fx.rate_date = j.odate;


-- -------------------------------------------------------------------------------------
-- S7 · an aggregate FILTER three levels down (D-10 at depth)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, on_time_pct)
SELECT o.cid, o.pct
FROM (
    SELECT i.cid, i.pct
    FROM (
        SELECT
            s.carrier_id AS cid,
            AVG(s.transit_days)
                FILTER (WHERE s.delivered_date <= s.promised_date) AS pct
        FROM core.fct_shipment AS s
        GROUP BY s.carrier_id
    ) AS i
) AS o;


-- -------------------------------------------------------------------------------------
-- S8 · DISTINCT ON two levels down, feeding an aggregate (D-9 at depth)
--   The latest order per customer is chosen in the derived table; the outer query then
--   counts and sums only those survivors.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, orders, revenue)
SELECT
    latest.cur,
    COUNT(latest.cust),
    SUM(latest.amt)
FROM (
    SELECT DISTINCT ON (o.customer_id)
        o.customer_id AS cust,
        o.currency    AS cur,
        o.amount      AS amt
    FROM staging.orders AS o
    ORDER BY o.customer_id, o.updated_at DESC
) AS latest
GROUP BY latest.cur;


-- -------------------------------------------------------------------------------------
-- S9 · a predicate five levels down whose columns are never projected
--   method and currency appear ONLY in the innermost WHERE. They decide what lands in
--   the target all the same.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, revenue)
SELECT a.k, a.v
FROM (
    SELECT b.k, b.v
    FROM (
        SELECT c.k, c.v
        FROM (
            SELECT d.k, d.v
            FROM (
                SELECT p.order_id AS k, p.amount AS v
                FROM staging.payments AS p
                WHERE p.method = 'CARD'
                  AND p.currency = 'USD'
            ) AS d
        ) AS c
    ) AS b
) AS a;


-- -------------------------------------------------------------------------------------
-- S10 · a column carried up two levels and then dropped
--   `reserved` is projected as unused_cost at depth 3 and 2, and never referenced by the
--   level that writes. It contributes nothing.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, units)
SELECT x.d, x.u
FROM (
    SELECT y.d, y.u, y.unused_cost
    FROM (
        SELECT
            i.snapshot_date AS d,
            i.on_hand       AS u,
            i.reserved      AS unused_cost
        FROM staging.inventory_snapshots AS i
    ) AS y
) AS x;


-- -------------------------------------------------------------------------------------
-- S11 · a named window defined two levels down and used twice there
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, prev_month_revenue, growth_pct)
SELECT w.pk, w.prev_rev, w.next_qty
FROM (
    SELECT
        f.order_line_id         AS pk,
        LAG(f.net_amount) OVER win AS prev_rev,
        LEAD(f.quantity)  OVER win AS next_qty
    FROM core.fct_order_line AS f
    WINDOW win AS (PARTITION BY f.store_id ORDER BY f.order_date)
) AS w;


-- -------------------------------------------------------------------------------------
-- S12 · three sibling CTEs aggregating at three grains, then joined
--   Each output column must be influenced ONLY by the GROUP BY of the CTE it came from.
--   All three CTEs alias fct_order_line as `f`: one alias, three scopes.
-- -------------------------------------------------------------------------------------
WITH by_customer AS (
    SELECT f.customer_key AS ck, SUM(f.net_amount) AS cust_rev
    FROM core.fct_order_line AS f
    GROUP BY f.customer_key
),
by_day AS (
    SELECT f.order_date AS od, COUNT(DISTINCT f.order_id) AS day_orders
    FROM core.fct_order_line AS f
    GROUP BY f.order_date
),
by_customer_day AS (
    SELECT f.customer_key AS ck, f.order_date AS od, MAX(f.unit_price) AS max_price
    FROM core.fct_order_line AS f
    GROUP BY f.customer_key, f.order_date
)
INSERT INTO mart.customer_summary
    (customer_id, last_order_date, lifetime_value, lifetime_orders, avg_basket)
SELECT
    bcd.ck,
    bcd.od,
    bc.cust_rev,
    bd.day_orders,
    bcd.max_price
FROM by_customer_day AS bcd
JOIN by_customer AS bc
  ON bc.ck = bcd.ck
JOIN by_day AS bd
  ON bd.od = bcd.od;


-- -------------------------------------------------------------------------------------
-- S13 · a window over an aggregate, the aggregate three levels below the window
--   The rank orders by a SUM computed in a CTE two levels down. Influence composes: the
--   rank is influenced by the summed column (through the ORDER BY) AND by the GROUP BY
--   that decided what each sum is.
-- -------------------------------------------------------------------------------------
WITH store_rev AS (
    SELECT f.store_id AS sid, f.order_date AS dt, SUM(f.gross_amount) AS gross
    FROM core.fct_order_line AS f
    GROUP BY f.store_id, f.order_date
),
store_rev_named AS (
    SELECT sr.sid, sr.dt, sr.gross, ds.region AS rgn
    FROM store_rev AS sr
    JOIN core.dim_store AS ds
      ON ds.id = sr.sid
)
INSERT INTO mart.sales_daily (sale_date, region, gross_amount, rank_in_region)
SELECT
    v.dt,
    v.rgn,
    v.gross,
    RANK() OVER (PARTITION BY v.rgn, v.dt ORDER BY v.gross DESC)
FROM (
    SELECT n.dt, n.sid, n.rgn, n.gross
    FROM store_rev_named AS n
) AS v;
