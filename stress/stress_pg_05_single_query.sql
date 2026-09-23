-- ====================================================================================
-- STRESS PG 5 / ONE QUERY, SIXTEEN CTEs, SEVEN BASE RELATIONS
-- ====================================================================================
--
-- A single statement. Everything below the INSERT line is one SELECT, and the question it
-- asks is the one the other four packages cannot: does lineage survive LENGTH?
--
-- The other packages are each a handful of statements, and every one of them is small
-- enough that a defect shows up immediately. Production warehouse SQL is not like that. It
-- is one query, four hundred lines long, where a value enters at `stg_order_lines.unit_price`
-- and arrives sixteen derivations later in a column called `revenue_share_of_region`, and
-- nobody can hold the whole path in their head. That is where a lineage tool earns its
-- keep, and it is also where a tool that silently drops one CTE looks exactly like a tool
-- that works.
--
-- ------------------------------------------------------------------------------------
-- THE PIPELINE, so the shape is legible before the SQL
-- ------------------------------------------------------------------------------------
--
--   stg_orders ------> orders_scoped ------------\
--   stg_order_lines -> lines_enriched -> order_lines_agg -> order_facts
--   stg_products ----/                                       ^
--   stg_returns -----> returns_scoped -> returns_by_order ---/
--                                                            |
--                                     +----------------------+
--                                     |
--   stg_customer ---> customer_base --+--> eligible_customers (EXCEPT)
--                            |        |
--                            |        +--> first_orders
--                            |        +--> monthly_customer -+-> monthly_region
--                            |                               +-> segments (UNION ALL)
--                            |                               +-> enriched (4 windows)
--                            |                                        |
--   stg_categories -> cat_tree (RECURSIVE) -> top_category_ranked ----+
--   ref_policy ---------------------------------------------(final WHERE)
--
-- Grain changes FIVE times: line -> order -> customer-month -> region-month, plus a
-- separate customer-category grain that rejoins at the end.
--
-- ------------------------------------------------------------------------------------
-- WHAT IS DELIBERATELY IN HERE
-- ------------------------------------------------------------------------------------
--
--  * a value that passes through FOUR aggregations before it lands
--    (unit_price -> line_value -> line_total -> net_amount -> net_revenue -> a window SUM)
--  * a relation joined ONLY to be filtered on (stg_customer in customer_base's EXISTS)
--  * a column carried three CTEs purely to make a join possible (category_id)
--  * an UNREFERENCED CTE projection (order_facts.line_total) - computed, never used
--  * a recursive CTE whose depth column has no base-table source at all
--  * an EXCEPT whose second arm reads the same relation as its first (convention (a))
--  * a UNION ALL whose only differing output is a literal
--  * four window functions, two of them partitioned on a column from a DIFFERENT CTE
--  * an aggregate over an aggregate (monthly_region sums monthly_customer's SUM)
--  * a HAVING three CTEs above the columns it constrains
--  * an IN (subquery) against a seventh relation in the final WHERE
--
-- ------------------------------------------------------------------------------------
-- WHAT IS DELIBERATELY NOT
-- ------------------------------------------------------------------------------------
--
-- LATERAL, unnest and jsonb_build_object. All three are PARTIAL (PG-03, PG-04, PG-05) and
-- already measured in stress_pg_02. Including known gaps in a package built to find NEW
-- ones just guarantees a lower score that says nothing.
--
-- DISTINCT ON, and the ORDER BY ... LIMIT form of row selection. Unconventioned, and
-- stress_pg_02 and stress_pg_04 already carry the flag.
--
-- The INSERT wrapper is the smallest thing that gives the query a target: a bare SELECT
-- produces nothing at all today (PG-09).
--
-- Ground truth was written from this source BEFORE the analyser was run over it.
-- ====================================================================================

INSERT INTO fct_customer_360 (
    cust_id,
    region,
    cohort_month,
    period_month,
    first_order_date,
    orders_placed,
    gross_revenue,
    net_revenue,
    refund_total,
    units_bought,
    distinct_categories,
    top_category_name,
    avg_order_value,
    region_rank,
    cohort_rank,
    running_net_revenue,
    prev_month_net,
    revenue_share_of_region,
    category_depth,
    segment
)
WITH RECURSIVE

-- ------------------------------------------------------------------------------------
-- 1. orders_scoped - the base population of orders.
--
--    Three predicates, and all three have to survive fifteen more CTEs to be reported.
--    None of these columns reaches a target except order_date, so currency and status_code
--    exist ONLY as filters - which is the shape that vanishes quietly.
-- ------------------------------------------------------------------------------------
orders_scoped AS (
    SELECT o.order_id                        AS order_id,
           o.cust_id                         AS cust_id,
           o.order_date                      AS order_date,
           date_trunc('month', o.order_date) AS period_month,
           o.gross_amount                    AS gross_amount,
           COALESCE(o.discount_amt, 0)       AS discount_amt
      FROM stg_orders o
     WHERE o.currency = 'GBP'
       AND o.status_code <> 'CANCELLED'
       AND o.order_date >= DATE '2023-01-01'
),

-- ------------------------------------------------------------------------------------
-- 2. lines_enriched - order lines, widened with the product's category.
--
--    `category_id` is carried from here through order_lines_agg and customer_top_category
--    purely so a join can be made fourteen CTEs later. It is never a value source, and the
--    column that IS one - category_name - lives in a relation not joined until CTE 14.
-- ------------------------------------------------------------------------------------
lines_enriched AS (
    SELECT l.order_id                AS order_id,
           l.product_id              AS product_id,
           p.category_id             AS category_id,
           l.quantity                AS quantity,
           l.quantity * l.unit_price AS line_value
      FROM stg_order_lines l
      JOIN stg_products p
        ON p.product_id = l.product_id
     WHERE l.quantity > 0
),

-- ------------------------------------------------------------------------------------
-- 3. order_lines_agg - GRAIN CHANGE 1: line -> order.
--
--    COUNT(DISTINCT category_id) is the only place stg_products contributes anything at
--    all, and it contributes it as an aggregate rather than as a projection.
-- ------------------------------------------------------------------------------------
order_lines_agg AS (
    SELECT e.order_id                  AS order_id,
           SUM(e.line_value)           AS line_total,
           SUM(e.quantity)             AS units,
           COUNT(DISTINCT e.category_id) AS category_count
      FROM lines_enriched e
     GROUP BY e.order_id
),

-- ------------------------------------------------------------------------------------
-- 4. returns_scoped - refunds, filtered.
-- ------------------------------------------------------------------------------------
returns_scoped AS (
    SELECT r.order_id       AS order_id,
           r.product_id     AS product_id,
           r.refund_amount  AS refund_amount
      FROM stg_returns r
     WHERE r.status_code = 'DONE'
       AND r.returned_at >= DATE '2023-01-01'
),

-- ------------------------------------------------------------------------------------
-- 5. returns_by_order - GRAIN CHANGE 2, on a second branch of the pipeline.
-- ------------------------------------------------------------------------------------
returns_by_order AS (
    SELECT rs.order_id           AS order_id,
           SUM(rs.refund_amount) AS refund_total
      FROM returns_scoped rs
     GROUP BY rs.order_id
),

-- ------------------------------------------------------------------------------------
-- 6. order_facts - the three branches meet.
--
--    `net_amount` mixes an aggregated operand with two non-aggregated ones, which is the
--    PG-11 question restated inside a long pipeline. Here it is then wrapped in a SUM two
--    CTEs later, so the ambiguity is resolved by what happens ABOVE it - and the key says
--    so rather than relying on it.
--
--    `line_total` IS COMPUTED HERE AND NEVER USED AGAIN. It is the trap: an analyser that
--    traces every projection of a CTE rather than the ones actually referenced will emit
--    edges from it, and they will look entirely ordinary.
-- ------------------------------------------------------------------------------------
order_facts AS (
    SELECT os.order_id                          AS order_id,
           os.cust_id                           AS cust_id,
           os.order_date                        AS order_date,
           os.period_month                      AS period_month,
           os.gross_amount                      AS gross_amount,
           os.discount_amt                      AS discount_amt,
           COALESCE(ola.line_total, 0)          AS line_total,
           COALESCE(ola.units, 0)               AS units,
           COALESCE(ola.category_count, 0)      AS category_count,
           COALESCE(rbo.refund_total, 0)        AS refund_total,
           os.gross_amount
             - os.discount_amt
             - COALESCE(rbo.refund_total, 0)    AS net_amount
      FROM orders_scoped os
      LEFT JOIN order_lines_agg ola
        ON ola.order_id = os.order_id
      LEFT JOIN returns_by_order rbo
        ON rbo.order_id = os.order_id
),

-- ------------------------------------------------------------------------------------
-- 7. customer_base - the customer dimension, filtered, with a correlated EXISTS back
--    into the fact pipeline.
--
--    The EXISTS correlates against order_facts - a CTE, not a base relation - so the
--    correlation's source column has to be traced six CTEs back to stg_orders.cust_id.
-- ------------------------------------------------------------------------------------
customer_base AS (
    SELECT c.cust_id                          AS cust_id,
           c.region                           AS region,
           c.signup_date                      AS signup_date,
           date_trunc('month', c.signup_date) AS cohort_month
      FROM stg_customer c
     WHERE c.status_code = 'A'
       AND EXISTS (SELECT 1
                     FROM order_facts f
                    WHERE f.cust_id = c.cust_id)
),

-- ------------------------------------------------------------------------------------
-- 8. first_orders - a second aggregation of order_facts at a different grain from the
--    one CTE 10 will use.
-- ------------------------------------------------------------------------------------
first_orders AS (
    SELECT f.cust_id            AS cust_id,
           MIN(f.order_date)    AS first_order_date
      FROM order_facts f
     GROUP BY f.cust_id
),

-- ------------------------------------------------------------------------------------
-- 9. eligible_customers - an EXCEPT whose two arms read THE SAME RELATION.
--
--    Convention (a): an EXCEPT's second arm decides which rows survive and supplies none
--    of the value. So stg_customer.region here is FILTER lineage, while the same column
--    read through customer_base at CTE 16 is VALUE lineage. One column, two flows, two
--    different facts.
-- ------------------------------------------------------------------------------------
eligible_customers AS (
    SELECT cb.cust_id
      FROM customer_base cb
    EXCEPT
    SELECT c2.cust_id
      FROM stg_customer c2
     WHERE c2.region = 'XX'
),

-- ------------------------------------------------------------------------------------
-- 10. monthly_customer - GRAIN CHANGE 3: order -> customer-month.
--
--     Seven aggregates, and its HAVING constrains a column whose source is three CTEs
--     below it. Everything downstream of here is influenced by these two grouping keys.
-- ------------------------------------------------------------------------------------
monthly_customer AS (
    SELECT f.cust_id                AS cust_id,
           f.period_month           AS period_month,
           COUNT(*)                 AS orders_placed,
           SUM(f.gross_amount)      AS gross_revenue,
           SUM(f.net_amount)        AS net_revenue,
           SUM(f.refund_total)      AS refund_total,
           SUM(f.units)             AS units_bought,
           MAX(f.category_count)    AS distinct_categories,
           AVG(f.net_amount)        AS avg_order_value
      FROM order_facts f
      JOIN eligible_customers ec
        ON ec.cust_id = f.cust_id
     GROUP BY f.cust_id, f.period_month
    HAVING SUM(f.gross_amount) > 0
),

-- ------------------------------------------------------------------------------------
-- 11. monthly_region - GRAIN CHANGE 4, and AN AGGREGATE OVER AN AGGREGATE.
--
--     SUM(mc.net_revenue) where net_revenue is itself SUM(f.net_amount). The value is the
--     same columns either way; what changes is the grain it is reported at.
-- ------------------------------------------------------------------------------------
monthly_region AS (
    SELECT cb.region             AS region,
           mc.period_month       AS period_month,
           SUM(mc.net_revenue)   AS region_net_revenue
      FROM monthly_customer mc
      JOIN customer_base cb
        ON cb.cust_id = mc.cust_id
     GROUP BY cb.region, mc.period_month
),

-- ------------------------------------------------------------------------------------
-- 12. cat_tree - a RECURSIVE category hierarchy.
--
--     category_name is carried forward unchanged from the anchor, so every row at every
--     depth holds a value traceable to one stg_categories row. `depth` is the literal 1
--     and then its own previous value, and has NO base-table source at all.
-- ------------------------------------------------------------------------------------
cat_tree AS (
    SELECT c.category_id   AS category_id,
           c.category_name AS category_name,
           1               AS depth
      FROM stg_categories c
     WHERE c.parent_category_id IS NULL
    UNION ALL
    SELECT ch.category_id,
           ch.category_name,
           t.depth + 1
      FROM stg_categories ch
      JOIN cat_tree t
        ON t.category_id = ch.parent_category_id
),

-- ------------------------------------------------------------------------------------
-- 13. customer_top_category - a FIFTH grain, on a branch that rejoins at the very end.
-- ------------------------------------------------------------------------------------
customer_top_category AS (
    SELECT f.cust_id          AS cust_id,
           e.category_id      AS category_id,
           SUM(e.line_value)  AS category_value
      FROM order_facts f
      JOIN lines_enriched e
        ON e.order_id = f.order_id
     GROUP BY f.cust_id, e.category_id
),

-- ------------------------------------------------------------------------------------
-- 14. top_category_ranked - the recursive tree joined to the category spend, ranked.
--
--     `rn` is never projected to a target. It exists to be compared to 1 in a join
--     condition at the end, which is row selection by rank - the construct this project
--     has no convention for. The key flags it and labels nothing.
-- ------------------------------------------------------------------------------------
top_category_ranked AS (
    SELECT ctc.cust_id        AS cust_id,
           ct.category_name   AS category_name,
           ct.depth           AS category_depth,
           ROW_NUMBER() OVER (PARTITION BY ctc.cust_id
                                  ORDER BY ctc.category_value DESC) AS rn
      FROM customer_top_category ctc
      JOIN cat_tree ct
        ON ct.category_id = ctc.category_id
),

-- ------------------------------------------------------------------------------------
-- 15. segments - a UNION ALL whose arms differ ONLY in a literal.
--
--     Both arms read the same CTE with opposite predicates. `segment` is a literal in
--     both, so it has no upstream; the predicates are real dependencies of which segment
--     a customer-month lands in.
-- ------------------------------------------------------------------------------------
segments AS (
    SELECT mc.cust_id      AS cust_id,
           mc.period_month AS period_month,
           'HIGH'          AS segment
      FROM monthly_customer mc
     WHERE mc.net_revenue >= 1000
    UNION ALL
    SELECT mc.cust_id,
           mc.period_month,
           'LOW'
      FROM monthly_customer mc
     WHERE mc.net_revenue < 1000
),

-- ------------------------------------------------------------------------------------
-- 16. enriched - four window functions, three joins, and two partitions taken from a
--     DIFFERENT CTE than the one supplying the ordering.
--
--     region_rank partitions on customer_base.region and orders by monthly_customer's
--     net_revenue. cohort_rank partitions on a date_trunc computed in customer_base from
--     stg_customer.signup_date. Neither partition column is in the CTE the window's
--     argument comes from.
-- ------------------------------------------------------------------------------------
enriched AS (
    SELECT mc.cust_id             AS cust_id,
           cb.region              AS region,
           cb.cohort_month        AS cohort_month,
           mc.period_month        AS period_month,
           fo.first_order_date    AS first_order_date,
           mc.orders_placed       AS orders_placed,
           mc.gross_revenue       AS gross_revenue,
           mc.net_revenue         AS net_revenue,
           mc.refund_total        AS refund_total,
           mc.units_bought        AS units_bought,
           mc.distinct_categories AS distinct_categories,
           mc.avg_order_value     AS avg_order_value,
           mr.region_net_revenue  AS region_net_revenue,

           RANK() OVER (PARTITION BY cb.region, mc.period_month
                            ORDER BY mc.net_revenue DESC)          AS region_rank,

           DENSE_RANK() OVER (PARTITION BY cb.cohort_month
                                  ORDER BY mc.net_revenue DESC)    AS cohort_rank,

           SUM(mc.net_revenue) OVER (PARTITION BY mc.cust_id
                                         ORDER BY mc.period_month
                                         ROWS BETWEEN UNBOUNDED PRECEDING
                                                  AND CURRENT ROW) AS running_net_revenue,

           LAG(mc.net_revenue) OVER (PARTITION BY mc.cust_id
                                         ORDER BY mc.period_month) AS prev_month_net

      FROM monthly_customer mc
      JOIN customer_base cb
        ON cb.cust_id = mc.cust_id
      JOIN first_orders fo
        ON fo.cust_id = mc.cust_id
      LEFT JOIN monthly_region mr
        ON mr.region = cb.region
       AND mr.period_month = mc.period_month
)

-- ------------------------------------------------------------------------------------
-- THE FINAL SELECT.
--
-- Twenty projections. Three of them have no upstream at all and the key says so:
-- orders_placed is COUNT(*), category_depth is the recursive literal, segment is a
-- literal in both UNION arms. Two more - region_rank and cohort_rank - are window
-- functions that take no argument, so they have influence and no value.
--
-- revenue_share_of_region divides one aggregate by another aggregate computed at a
-- different grain, inside a CASE. Under the transform ladder `aggregated` outranks
-- `conditional`, so the CASE does not change the class.
-- ------------------------------------------------------------------------------------
SELECT e.cust_id,
       e.region,
       e.cohort_month,
       e.period_month,
       e.first_order_date,
       e.orders_placed,
       e.gross_revenue,
       e.net_revenue,
       e.refund_total,
       e.units_bought,
       e.distinct_categories,
       tcr.category_name,
       e.avg_order_value,
       e.region_rank,
       e.cohort_rank,
       e.running_net_revenue,
       e.prev_month_net,
       CASE WHEN e.region_net_revenue > 0
            THEN e.net_revenue / e.region_net_revenue
            ELSE 0
       END,
       tcr.category_depth,
       s.segment
  FROM enriched e
  LEFT JOIN top_category_ranked tcr
    ON tcr.cust_id = e.cust_id
   AND tcr.rn = 1
  LEFT JOIN segments s
    ON s.cust_id = e.cust_id
   AND s.period_month = e.period_month
 WHERE e.orders_placed >= 1
   AND e.region IN (SELECT rp.region
                      FROM ref_policy rp
                     WHERE rp.window_days > 0)
 ORDER BY e.region, e.period_month, e.region_rank;
