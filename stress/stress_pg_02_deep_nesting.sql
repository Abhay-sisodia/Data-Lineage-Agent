-- STRESS PG 2 / depth, not breadth
--
-- stress_pg_01 is wide and shallow: seven statements, each exercising one or two
-- constructs. This one is the opposite. Five statements, every one of them nested deeply
-- enough that a value has to survive several rewrites to arrive with the right transform
-- class and the right flow.
--
-- WHY DEPTH IS A DIFFERENT TEST. Almost every flow-classification finding in this project
-- was a rule that held at the top level and broke one level down - S3-02 (a window's
-- ordering leaks as VALUE through a CTE), S4-04 (a set operation's second arm read as value
-- when it sits in a CTE), S4-05 (a projection COMPUTED in a derived table had no source at
-- all). A rule that is right at depth 1 and wrong at depth 5 scores well on a wide package
-- and is useless on production code, where five-level CTE chains are ordinary.
--
-- STILL ENTIRELY INSIDE docs/postgres_scope.md. Every construct below appears in the EDGES
-- rows of the scope table. No refusals are expected.
--
-- THIS KEY IS LESS CERTAIN THAN STRESS_PG_01's, AND SAYS SO IN THREE PLACES. Three
-- constructs here have no precedent anywhere in this project - a recursive CTE's
-- self-reference, an inner ORDER BY ... LIMIT that selects rows, and an aggregate's
-- FILTER clause - and the key flags each rather than choosing the answer that would score
-- best. Where the analyser disagrees, the argument is settled against the convention as it
-- stands; where no convention exists, the disagreement is a CONVENTION finding, not a bug.
--
-- Ground truth was written from this source BEFORE the analyser was run over it.

-- ====================================================================================
-- A. A FIVE-LEVEL CTE CHAIN.  base -> spend -> joined -> rolled -> ranked -> INSERT.
--
--    The grain changes twice on the way: `spend` aggregates to one row per customer,
--    `rolled` re-aggregates to one row per cohort and region, and `ranked` puts two window
--    functions over the result - one with an explicit ROWS frame. A value entering at
--    stg_orders.gross_amount has to pass through two SUMs and a window SUM and still
--    arrive as `aggregated`.
-- ====================================================================================
INSERT INTO fct_cohort (
    cohort_month, region, customers, revenue, rank_in_region, running_revenue
)
WITH base AS (
    SELECT c.cust_id                           AS cust_id,
           c.region                            AS region,
           date_trunc('month', c.signup_date)  AS cohort_month
      FROM stg_customer c
     WHERE c.status_code = 'A'
),
spend AS (
    SELECT o.cust_id                                        AS cust_id,
           SUM(o.gross_amount - COALESCE(o.discount_amt, 0)) AS net
      FROM stg_orders o
     WHERE o.currency = 'GBP'
     GROUP BY o.cust_id
),
joined AS (
    SELECT b.cohort_month      AS cohort_month,
           b.region            AS region,
           b.cust_id           AS cust_id,
           COALESCE(s.net, 0)  AS net
      FROM base b
      LEFT JOIN spend s
        ON s.cust_id = b.cust_id
),
rolled AS (
    SELECT j.cohort_month     AS cohort_month,
           j.region           AS region,
           COUNT(j.cust_id)   AS customers,
           SUM(j.net)         AS revenue
      FROM joined j
     GROUP BY j.cohort_month, j.region
    HAVING SUM(j.net) > 100
),
ranked AS (
    SELECT r.cohort_month AS cohort_month,
           r.region       AS region,
           r.customers    AS customers,
           r.revenue      AS revenue,
           RANK() OVER (PARTITION BY r.region ORDER BY r.revenue DESC) AS rank_in_region,
           SUM(r.revenue) OVER (PARTITION BY r.region
                                    ORDER BY r.cohort_month
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                          AS running
      FROM rolled r
)
SELECT k.cohort_month, k.region, k.customers, k.revenue, k.rank_in_region, k.running
  FROM ranked k;

-- ====================================================================================
-- B. WITH RECURSIVE. A category tree walked to arbitrary depth.
--
--    The anchor arm reads real columns; the recursive arm reads the CTE ITSELF, which is a
--    fixpoint rather than a relation. `depth` and `root_name` are the interesting pair:
--    root_name is carried through unchanged from the anchor and IS traceable, while depth
--    starts at a literal and is only ever derived from its own previous value.
-- ====================================================================================
INSERT INTO dim_customer_hier (cust_id, parent_cust_id, depth, root_name)
WITH RECURSIVE tree AS (
    SELECT c.category_id        AS category_id,
           c.parent_category_id AS parent_category_id,
           1                    AS depth,
           c.category_name      AS root_name
      FROM stg_categories c
     WHERE c.parent_category_id IS NULL
    UNION ALL
    SELECT ch.category_id,
           ch.parent_category_id,
           t.depth + 1,
           t.root_name
      FROM stg_categories ch
      JOIN tree t
        ON t.category_id = ch.parent_category_id
)
SELECT t.category_id, t.parent_category_id, t.depth, t.root_name
  FROM tree t;

-- ====================================================================================
-- C. LATERAL, an aggregate FILTER clause, and string_agg with its own ORDER BY.
--
--    The LATERAL subquery is correlated to the outer row AND row-limiting: `ORDER BY
--    order_date DESC LIMIT 5` decides which five orders each customer contributes. The
--    correlation is ordinary filter lineage; the ORDER BY ... LIMIT is a construct this
--    project has no convention for, and the key says so instead of guessing.
-- ====================================================================================
INSERT INTO gtt_stage (cust_id, period_month, amount, note)
SELECT c.cust_id,
       date_trunc('month', c.signup_date),
       SUM(o.gross_amount) FILTER (WHERE o.currency = 'GBP'),
       string_agg(o.status_code, ',' ORDER BY o.order_date)
  FROM stg_customer c
  JOIN LATERAL (
        SELECT o2.cust_id      AS cust_id,
               o2.gross_amount AS gross_amount,
               o2.currency     AS currency,
               o2.status_code  AS status_code,
               o2.order_date   AS order_date
          FROM stg_orders o2
         WHERE o2.cust_id = c.cust_id
         ORDER BY o2.order_date DESC
         LIMIT 5
       ) o ON true
 GROUP BY c.cust_id, date_trunc('month', c.signup_date);

-- ====================================================================================
-- D. JSON, arrays, casts and interval arithmetic - the expression layer rather than the
--    query layer.
--
--    Four different ways for a column to reach a target without being copied: through a
--    JSON extraction and a cast, through unnest of an array, through interval arithmetic,
--    and through a function that BUILDS json out of two unrelated columns. All four are
--    `derived`: something happened to the value, and none of it is aggregation or a branch.
-- ====================================================================================
INSERT INTO audit_log (cust_id, changed_by, changed_at, detail)
SELECT (o.payload ->> 'cust_id')::int,
       t.tag,
       o.order_date + INTERVAL '1 day',
       jsonb_build_object('month', EXTRACT(MONTH FROM o.order_date), 'cur', o.currency)
  FROM stg_orders o
  JOIN stg_customer c
    ON c.cust_id = o.cust_id
  CROSS JOIN LATERAL unnest(c.tags) AS t(tag)
 WHERE o.order_date >= CURRENT_DATE - INTERVAL '30 days';

-- ====================================================================================
-- E. Three subquery predicates of three different shapes, over a ROLLUP.
--
--    `IN (subquery)`, `NOT EXISTS (correlated)`, and `IN` over a VALUES list that contains
--    nothing but literals. The last is there to be found EMPTY: a VALUES list has no
--    upstream, so the correct number of edges from it is zero, and an analyser that names
--    the outer column as its own filter source would look right.
-- ====================================================================================
INSERT INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
SELECT o.cust_id,
       date_trunc('month', o.order_date),
       SUM(o.gross_amount),
       COUNT(*)
  FROM stg_orders o
 WHERE o.cust_id IN (SELECT r.cust_id
                       FROM stg_returns r
                      WHERE r.status_code = 'OPEN')
   AND NOT EXISTS (SELECT 1
                     FROM stg_customer c
                    WHERE c.cust_id = o.cust_id
                      AND c.region = 'XX')
   AND o.currency IN (SELECT v.cur FROM (VALUES ('GBP'), ('EUR')) AS v(cur))
 GROUP BY ROLLUP (o.cust_id, date_trunc('month', o.order_date));
