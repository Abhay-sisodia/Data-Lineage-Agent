-- STRESS PG 4 / query complexity, and nothing else
--
-- No routines, no procedures, no DDL, no dollar-quoted anything. Three statements, and all
-- of the difficulty is inside the SELECT: multi-table joins, nested CTEs, correlated
-- subqueries, set operations, GROUPING SETS, aggregates, window functions over aggregates,
-- and predicates at every level of nesting.
--
-- WHY EACH QUERY IS WRAPPED IN AN `INSERT INTO ... SELECT`, WHICH IS THE ONE THING HERE
-- THAT IS NOT PURE SELECT.
--
-- A bare `SELECT` currently produces NOTHING AT ALL. Measured 2026-09-23:
--
--     SELECT o.cust_id, SUM(o.gross_amount) FROM stg_orders o JOIN stg_customer c
--       ON c.cust_id = o.cust_id WHERE o.currency = 'GBP' GROUP BY o.cust_id;
--
--     -> 0 edges, 0 refusals, 0 boundaries, statements_seen = 0
--
-- Not refused, not declared, not counted: SILENT, which is the one outcome this project
-- treats as never acceptable. It is filed as PG-09 and it is a real gap, because read-only
-- analytical SQL is exactly what a lineage tool gets pointed at. The reason is structural -
-- `band0.SUPPORTED` holds INSERT, MERGE, DELETE and CREATE, and lineage needs a TARGET to
-- name as the far end of an edge.
--
-- So the INSERT is the smallest possible wrapper that gives each query somewhere to land.
-- It contributes one line and nothing else: every construct being tested is in the SELECT
-- below it. When PG-09 is fixed these same three queries can be scored bare, against the
-- same key, by naming the projection instead of a column.
--
-- STILL ENTIRELY INSIDE docs/postgres_scope.md. No refusals are expected. The three
-- constructs the scope marks PARTIAL - LATERAL, unnest, jsonb_build_object - are
-- deliberately ABSENT: they are already measured in stress_pg_02, and including them here
-- would mix known gaps into a package whose purpose is to find new ones.
--
-- Ground truth was written from this source BEFORE the analyser was run over it.

-- ====================================================================================
-- Q1. SIX RELATIONS, THREE CTEs, TWO WINDOW FUNCTIONS, A HAVING AND AN OUTER FILTER
--     OVER AN AGGREGATE.
--
--     The grain changes twice. `line_facts` is one row per order line across four joined
--     tables; `agg` collapses it to month x product x category; the final SELECT joins
--     that to a SEPARATELY aggregated refunds CTE at a different grain, then ranks it.
--
--     A value entering at stg_order_lines.unit_price has to survive a multiplication, a
--     SUM, a subtraction against another aggregate, and a LAG - and still arrive as
--     `aggregated`.
-- ====================================================================================
INSERT INTO fct_product_sales (
    period_month, product_id, category_name, gross_sales, net_sales,
    refund_total, units_sold, rank_in_month, prior_month
)
WITH line_facts AS (
    SELECT date_trunc('month', o.order_date) AS period_month,
           l.product_id                      AS product_id,
           p.category_id                     AS category_id,
           l.quantity                        AS quantity,
           l.quantity * l.unit_price         AS line_value,
           o.cust_id                         AS cust_id,
           c.region                          AS region
      FROM stg_orders o
      JOIN stg_order_lines l
        ON l.order_id = o.order_id
      JOIN stg_products p
        ON p.product_id = l.product_id
      JOIN stg_customer c
        ON c.cust_id = o.cust_id
     WHERE o.currency = 'GBP'
       AND o.status_code <> 'CANCELLED'
       AND c.status_code = 'A'
),
refunds AS (
    SELECT date_trunc('month', r.returned_at) AS period_month,
           r.product_id                       AS product_id,
           SUM(r.refund_amount)               AS refund_total
      FROM stg_returns r
     WHERE r.status_code = 'DONE'
     GROUP BY date_trunc('month', r.returned_at), r.product_id
),
agg AS (
    SELECT f.period_month              AS period_month,
           f.product_id                AS product_id,
           f.category_id               AS category_id,
           SUM(f.line_value)           AS gross,
           SUM(f.quantity)             AS units,
           COUNT(DISTINCT f.cust_id)   AS buyers
      FROM line_facts f
     GROUP BY f.period_month, f.product_id, f.category_id
    HAVING SUM(f.line_value) > 1000
)
SELECT a.period_month,
       a.product_id,
       cat.category_name,
       a.gross,
       a.gross - COALESCE(rf.refund_total, 0),
       COALESCE(rf.refund_total, 0),
       a.units,
       DENSE_RANK() OVER (PARTITION BY a.period_month ORDER BY a.gross DESC),
       LAG(a.gross, 1) OVER (PARTITION BY a.product_id ORDER BY a.period_month)
  FROM agg a
  JOIN stg_categories cat
    ON cat.category_id = a.category_id
  LEFT JOIN refunds rf
    ON rf.product_id = a.product_id
   AND rf.period_month = a.period_month
 WHERE a.buyers >= 3;

-- ====================================================================================
-- Q2. A SET OPERATION BETWEEN TWO CTEs, A CORRELATED EXISTS, GROUPING SETS, AND A WINDOW
--     FUNCTION OVER AN AGGREGATE.
--
--     `population` is active EXCEPT churned. Under convention (a) the second arm of an
--     EXCEPT decides WHICH ROWS SURVIVE and supplies none of the value, so churned's
--     columns are filter lineage and not value lineage - and both arms read the SAME
--     relation, which is what makes the distinction worth testing here.
--
--     `SUM(SUM(...)) OVER (...)` is a window function whose argument is itself an
--     aggregate of the same query. The inner SUM collapses the group; the outer one runs
--     across groups.
-- ====================================================================================
INSERT INTO fct_cohort (
    cohort_month, region, customers, revenue, rank_in_region, running_revenue
)
WITH active AS (
    SELECT c.cust_id                          AS cust_id,
           c.region                           AS region,
           date_trunc('month', c.signup_date) AS cohort_month
      FROM stg_customer c
     WHERE c.status_code = 'A'
       AND EXISTS (SELECT 1
                     FROM stg_orders o
                    WHERE o.cust_id = c.cust_id
                      AND o.order_date >= c.signup_date)
),
churned AS (
    SELECT c.cust_id                          AS cust_id,
           c.region                           AS region,
           date_trunc('month', c.signup_date) AS cohort_month
      FROM stg_customer c
     WHERE c.status_code = 'C'
),
population AS (
    SELECT a.cust_id, a.region, a.cohort_month FROM active a
    EXCEPT
    SELECT ch.cust_id, ch.region, ch.cohort_month FROM churned ch
),
revenue AS (
    SELECT o.cust_id                                         AS cust_id,
           SUM(o.gross_amount - COALESCE(o.discount_amt, 0)) AS net
      FROM stg_orders o
     WHERE o.currency IN ('GBP', 'EUR')
     GROUP BY o.cust_id
)
SELECT p.cohort_month,
       p.region,
       COUNT(*),
       SUM(COALESCE(r.net, 0)),
       RANK() OVER (PARTITION BY p.region ORDER BY SUM(COALESCE(r.net, 0)) DESC),
       SUM(SUM(COALESCE(r.net, 0))) OVER (PARTITION BY p.region
                                              ORDER BY p.cohort_month)
  FROM population p
  LEFT JOIN revenue r
    ON r.cust_id = p.cust_id
 GROUP BY GROUPING SETS ((p.cohort_month, p.region), (p.region))
HAVING SUM(COALESCE(r.net, 0)) > 0;

-- ====================================================================================
-- Q3. TWO CORRELATED SCALAR SUBQUERIES IN THE SELECT LIST, A FULL OUTER JOIN, AND
--     DISTINCT ON.
--
--     Every value in net_amount comes from a different place: one column, one column
--     minus a literal-defaulted column, and one CORRELATED AGGREGATE computed per outer
--     row. The transform ladder has to put `aggregated` on the result even though two of
--     the three contributions are ordinary arithmetic.
--
--     The second scalar subquery is COUNT(*) over a correlated predicate - no value
--     source at all, and a real filter dependency. Those two facts about one subquery are
--     easy to collapse into each other.
-- ====================================================================================
INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
SELECT DISTINCT ON (o.cust_id, date_trunc('month', o.order_date))
       o.cust_id,
       date_trunc('month', o.order_date),
       o.gross_amount
         - COALESCE(o.discount_amt, 0)
         - COALESCE((SELECT SUM(r.refund_amount)
                       FROM stg_returns r
                      WHERE r.order_id = o.order_id
                        AND r.status_code = 'DONE'), 0),
       (SELECT COUNT(*)
          FROM stg_order_lines l
         WHERE l.order_id = o.order_id)
  FROM stg_orders o
  FULL OUTER JOIN stg_customer c
    ON c.cust_id = o.cust_id
 WHERE COALESCE(c.region, 'UNKNOWN') <> 'XX'
   AND o.order_date >= CURRENT_DATE - INTERVAL '1 year'
 ORDER BY o.cust_id, date_trunc('month', o.order_date), o.order_date DESC;
