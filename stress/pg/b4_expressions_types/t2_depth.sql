-- =====================================================================================
-- B4 · T2 · DEPTH — does expression handling survive nesting?
--
-- Objective card: stress/pg/b4_expressions_types/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql.   PostgreSQL: 17 (all constructs 9.4+).
--
-- Each statement carries expressions through 3-6 levels of CTEs or derived tables. The
-- transform is ranked along the whole path (D-7), and D-19 counts the columns a node
-- reads IN ITS OWN SCOPE: `COALESCE(x.net, 0)` reads one column even when x.net was built
-- from two.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · four CTE levels: a cast, then a guard, then a two-column CASE, then an aggregate
--   The aggregate dominates every path through it. The final CASE reads one column
--   (total): it adds nothing above aggregated.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, segment, lifetime_value, status)
WITH l1 AS (
    SELECT o.customer_id, o.amount::numeric(14, 2) AS amt, o.status, o.channel
    FROM staging.orders AS o
),
l2 AS (
    SELECT l1.customer_id, COALESCE(l1.amt, 0) AS amt, l1.status, l1.channel
    FROM l1
),
l3 AS (
    SELECT
        l2.customer_id,
        CASE WHEN l2.status = 'REFUNDED' THEN -l2.amt ELSE l2.amt END AS signed_amt,
        l2.channel
    FROM l2
),
l4 AS (
    SELECT
        l3.customer_id,
        SUM(l3.signed_amt) AS total,
        string_agg(DISTINCT l3.channel, ',') AS channels
    FROM l3
    GROUP BY l3.customer_id
)
SELECT
    l4.customer_id,
    l4.channels,
    l4.total,
    CASE WHEN l4.total > 1000 THEN 'GOLD' ELSE 'STD' END
FROM l4;


-- -------------------------------------------------------------------------------------
-- S2 · a whole row carried through a derived table, then taken apart (D-20)
--   The inner query projects the row `c` itself and its jsonb text. (x.c).id is
--   customers.id, unchanged; j reads every column.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, full_name, region)
SELECT
    (x.c).id,
    (x.c).full_name,
    x.j
FROM (
    SELECT c, to_jsonb(c)::text AS j
    FROM staging.customers AS c
    WHERE c.status = 'ACTIVE'
) AS x;


-- -------------------------------------------------------------------------------------
-- S3 · D-19 in the node's own scope
--   x.net is built from two columns, but COALESCE(x.net, 0) reads one: derived.
--   GREATEST(x.net, x.gross) reads two: conditional for every base column behind them.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (sale_date, store_id, net_amount, gross_amount)
SELECT
    x.d,
    x.s,
    COALESCE(x.net, 0),
    GREATEST(x.net, x.gross)
FROM (
    SELECT
        f.order_date AS d,
        f.store_id AS s,
        f.gross_amount - f.discount_amount AS net,
        f.gross_amount AS gross
    FROM core.fct_order_line AS f
) AS x;


-- -------------------------------------------------------------------------------------
-- S4 · a band over an aggregate, two derived tables up, filtered at the top
--   GROUP BY on an expression (NULLIF) gives influence from its column. The outer WHERE
--   on the band reaches a SUM: post-aggregation (B2-06).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, segment, revenue)
SELECT t.lvl, t.region, t.seg, t.rev
FROM (
    SELECT
        CASE WHEN u.rev > 10000 THEN 'HIGH' ELSE 'LOW' END AS lvl,
        u.region,
        u.seg,
        u.rev
    FROM (
        SELECT
            d.region,
            NULLIF(d.segment, '') AS seg,
            SUM(f.net_amount) AS rev
        FROM core.fct_order_line AS f
        JOIN core.dim_customer AS d ON d.customer_key = f.customer_key
        WHERE d.is_current
        GROUP BY d.region, NULLIF(d.segment, '')
    ) AS u
) AS t
WHERE t.lvl = 'HIGH';


-- -------------------------------------------------------------------------------------
-- S5 · arrays through two CTEs: concatenation, subscript, cardinality, &&, FILTER
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, region, orders)
WITH tagged AS (
    SELECT o.id, o.channel, o.tags || ARRAY[o.currency::text] AS all_tags
    FROM staging.orders AS o
),
firsts AS (
    SELECT
        t.id,
        t.channel,
        t.all_tags[1] AS first_tag,
        cardinality(t.all_tags) AS n
    FROM tagged AS t
    WHERE t.all_tags && ARRAY['promo']
)
SELECT
    f.channel,
    upper(f.first_tag),
    COUNT(DISTINCT f.id) FILTER (WHERE f.n > 2)
FROM firsts AS f
GROUP BY f.channel, upper(f.first_tag);


-- -------------------------------------------------------------------------------------
-- S6 · ranges built in one CTE, measured in the next, averaged at the top
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, period_month, avg_transit_days, on_time_pct)
WITH spans AS (
    SELECT s.carrier_id, tstzrange(s.shipped_at, s.delivered_at) AS r, s.promised_at
    FROM staging.shipments AS s
    WHERE s.delivered_at IS NOT NULL
),
measured AS (
    SELECT
        sp.carrier_id,
        date_trunc('month', lower(sp.r))::date AS m,
        EXTRACT(EPOCH FROM upper(sp.r) - lower(sp.r)) / 86400 AS days,
        (upper(sp.r) <= sp.promised_at)::int AS on_time
    FROM spans AS sp
)
SELECT ms.carrier_id, ms.m, AVG(ms.days), AVG(ms.on_time)
FROM measured AS ms
GROUP BY ms.carrier_id, ms.m;


-- -------------------------------------------------------------------------------------
-- S7 · six derived-table levels; one expression at each of three of them
--   Identity is broken once (at e, by subtraction, and at c, by upper) and stays derived
--   through every pass-through level above.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, category_name, margin)
SELECT a.pid, a.cat, a.m
FROM (
    SELECT b.pid, b.cat, b.m
    FROM (
        SELECT c.pid, upper(c.cat) AS cat, c.m
        FROM (
            SELECT d.pid, d.cat, round(d.m, 2) AS m
            FROM (
                SELECT e.pid, e.cat, e.lp - e.cost AS m
                FROM (
                    SELECT
                        p.product_id AS pid,
                        p.category_name AS cat,
                        p.list_price AS lp,
                        p.cost
                    FROM core.dim_product AS p
                    WHERE p.status = 'ACTIVE'
                ) AS e
            ) AS d
        ) AS c
    ) AS b
) AS a;


-- -------------------------------------------------------------------------------------
-- S8 · a CASE over two CTEs' columns, and a whole-row test in WHERE (D-20)
--   The CASE reads o.last_ts and c.status: conditional. `c IS NOT NULL` tests every
--   column of the CTE row c, so each is a filter. o is on the null side of the LEFT
--   JOIN, so nothing of it filters (D-14), and the ON is structural (D-5).
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, full_name, status)
WITH c AS (
    SELECT cu.id, cu.full_name, cu.status
    FROM staging.customers AS cu
),
o AS (
    SELECT od.customer_id, MAX(od.order_ts) AS last_ts
    FROM staging.orders AS od
    GROUP BY od.customer_id
)
SELECT
    c.id,
    c.full_name,
    CASE
        WHEN o.last_ts IS NULL THEN 'NEVER'
        WHEN o.last_ts < now() - INTERVAL '1 year' THEN 'LAPSED'
        ELSE c.status
    END
FROM c
LEFT JOIN o ON o.customer_id = c.id
WHERE c IS NOT NULL;
