-- =====================================================================================
-- Stress package 4: depth at scale.
--
-- WHAT THIS ONE IS FOR, AND HOW IT DIFFERS FROM THE OTHER THREE.
--
-- Stress 1 went wide on constructs. Stress 2 went deep on constructs. Stress 3 combined
-- them and found seven defects. This package introduces NO NEW CONSTRUCT AT ALL - every
-- keyword in it appears in stress 1, 2 or 3 - and instead asks whether the seven fixes
-- hold when the same constructs are stacked to the depth real ETL is written at.
--
-- That restriction is the design. A package that adds new constructs would find new
-- construct defects and tell us nothing about the ones already repaired. Here, anything
-- that breaks breaks because of DEPTH, SCALE or INTERACTION, which is the question.
--
-- Every unit therefore aims at a fix that has already landed:
--
--   S3-01  GROUP BY influence through ROLLUP/CUBE/GROUPING SETS
--   S3-02  window PARTITION BY / ORDER BY as influence, not value, through a CTE
--   S3-03  MERGE filter edges from the USING clause and from an arm
--   S3-06  a collection subscript is not a value source
--   S3-07  a MERGE's target is in scope for its own SET clause
--   S3-10  a correlated reference resolves outward, not to the nearest single source
--   S2-10  trigger edges arrive through the dictionary, not the file
--
-- ...and stacks it three to five levels deep, behind four or five joins, with aggregation
-- at two different depths in the same statement. The shape that broke `_grouping_influence`
-- was exactly this and passed every flat test first.
--
-- MULTI-LEVEL AGGREGATION IS THE SPINE OF THE PACKAGE. Several units aggregate, then
-- aggregate the aggregate at a different grain, then window over that. Each such statement
-- has two GROUP BY clauses in different scopes, which is the case `_grouping_influence`
-- returned early on until sq_02_cte_chain caught it.
--
-- The column mapping is kept traceable on purpose. The joins and CTE chains are deep, but
-- each output column resolves to a small, checkable set of base columns - depth where the
-- analyser is tested, not ambiguity where the KEY would be.
-- =====================================================================================


-- =====================================================================================
-- 1. s4_revenue_pipeline
--
--    Five CTE levels, three joins, and aggregation at levels 3 and 4 - a SUM over a SUM
--    computed at a different grain. A window at level 5 reads the second aggregate.
--
--    Aimed at S3-01 and S3-02 together: two GROUP BY clauses in different scopes, and a
--    window whose PARTITION BY sits three levels below the INSERT.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_revenue_pipeline IS
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    WITH order_scope AS (
        SELECT o.order_id                   AS order_id,
               o.cust_id                    AS cust_id,
               TRUNC(o.order_date, 'MM')    AS period_month,
               o.gross_amount               AS gross_amount,
               o.discount_amt               AS discount_amt
          FROM stg_orders o
         WHERE o.currency IN ('GBP', 'EUR')
           AND o.order_date IS NOT NULL
    ),
    line_scope AS (
        SELECT b.cust_id        AS cust_id,
               b.period_month   AS period_month,
               b.order_id       AS order_id,
               l.product_id     AS product_id,
               l.quantity       AS quantity,
               l.line_amount    AS line_amount,
               c.category_name  AS category_name
          FROM order_scope b
          JOIN stg_order_lines l
            ON l.order_id = b.order_id
          JOIN stg_products p
            ON p.product_id = l.product_id
          JOIN stg_categories c
            ON c.category_id = p.category_id
         WHERE p.status_code <> 'X'
    ),
    product_grain AS (
        SELECT s.cust_id                     AS cust_id,
               s.period_month                AS period_month,
               s.product_id                  AS product_id,
               SUM(s.line_amount)            AS product_total,
               COUNT(DISTINCT s.order_id)    AS product_orders
          FROM line_scope s
         GROUP BY s.cust_id, s.period_month, s.product_id
        HAVING SUM(s.line_amount) > 0
    ),
    customer_grain AS (
        SELECT a.cust_id                AS cust_id,
               a.period_month           AS period_month,
               SUM(a.product_total)     AS customer_total,
               SUM(a.product_orders)    AS customer_orders,
               COUNT(a.product_id)      AS product_variety
          FROM product_grain a
         GROUP BY a.cust_id, a.period_month
    ),
    trended AS (
        SELECT g.cust_id          AS cust_id,
               g.period_month     AS period_month,
               g.customer_total   AS customer_total,
               g.customer_orders  AS customer_orders,
               g.product_variety  AS product_variety,
               LAG(g.customer_total)
                   OVER (PARTITION BY g.cust_id ORDER BY g.period_month) AS previous_total
          FROM customer_grain g
    )
    SELECT z.cust_id,
           z.period_month,
           CASE WHEN z.previous_total IS NULL   THEN z.customer_total
                WHEN z.product_variety > 5      THEN z.customer_total - z.previous_total
                ELSE z.customer_total END,
           z.customer_orders
      FROM trended z;
END s4_revenue_pipeline;
/


-- =====================================================================================
-- 2. s4_product_margin
--
--    Four joins and a ROLLUP over an already-aggregated CTE, with LISTAGG, KEEP and
--    GROUPING_ID reading the second grain. The S3-01 fix has to reach a ROLLUP that sits
--    ABOVE another GROUP BY rather than beside it.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_product_margin IS
BEGIN
    INSERT INTO fct_product_sales
        (period_month, product_id, category_name, gross_sales, net_sales,
         units_sold, rank_in_month)
    WITH priced AS (
        SELECT TRUNC(o.order_date, 'MM')  AS period_month,
               l.product_id               AS product_id,
               l.quantity                 AS quantity,
               l.line_amount              AS line_amount,
               l.unit_price               AS unit_price,
               p.list_price               AS list_price,
               c.category_name            AS category_name
          FROM stg_orders o
          JOIN stg_order_lines l
            ON l.order_id = o.order_id
          JOIN stg_products p
            ON p.product_id = l.product_id
          JOIN stg_categories c
            ON c.category_id = p.category_id
         WHERE o.currency = 'GBP'
           AND p.status_code <> 'X'
    ),
    per_product AS (
        SELECT k.period_month           AS period_month,
               k.product_id             AS product_id,
               k.category_name          AS category_name,
               SUM(k.line_amount)       AS line_total,
               SUM(k.quantity)          AS unit_total,
               MAX(k.list_price)        AS top_list_price
          FROM priced k
         GROUP BY k.period_month, k.product_id, k.category_name
    )
    SELECT q.period_month,
           q.product_id,
           LISTAGG(q.category_name, ',') WITHIN GROUP (ORDER BY q.category_name),
           SUM(q.line_total),
           MAX(q.top_list_price) KEEP (DENSE_RANK FIRST ORDER BY q.unit_total DESC),
           SUM(q.unit_total),
           GROUPING_ID(q.product_id, q.category_name)
      FROM per_product q
     GROUP BY ROLLUP (q.period_month, q.product_id, q.category_name);
END s4_product_margin;
/


-- =====================================================================================
-- 3. s4_customer_scoring
--
--    Correlated scalar aggregates at two different depths, one of them inside a CTE that
--    is itself joined twice. Aimed squarely at S3-10: the outer alias a correlation names
--    is two scopes up, not one.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_customer_scoring IS
BEGIN
    INSERT INTO dw_dim_customer_v2 (cust_id, region, is_active, email, lifetime_value)
    WITH base AS (
        SELECT c.cust_id      AS cust_id,
               c.region       AS region,
               c.email        AS email,
               c.status_code  AS status_code,
               c.signup_date  AS signup_date
          FROM stg_customer c
         WHERE c.signup_date IS NOT NULL
    ),
    enriched AS (
        SELECT b.cust_id   AS cust_id,
               b.region    AS region,
               b.email     AS email,
               b.status_code AS status_code,
               (SELECT SUM(r.net_amount)
                  FROM fct_revenue r
                 WHERE r.cust_id = b.cust_id)          AS revenue_total,
               (SELECT COUNT(t.refund_amount)
                  FROM stg_returns t
                 WHERE t.cust_id = b.cust_id
                   AND t.status_code = 'A')            AS refund_count
          FROM base b
    ),
    ranked AS (
        SELECT e.cust_id        AS cust_id,
               e.region         AS region,
               e.email          AS email,
               e.status_code    AS status_code,
               e.revenue_total  AS revenue_total,
               e.refund_count   AS refund_count,
               ROW_NUMBER() OVER (PARTITION BY e.region
                                      ORDER BY e.revenue_total DESC) AS region_rank
          FROM enriched e
    )
    SELECT k.cust_id,
           k.region,
           CASE WHEN k.status_code = 'A' AND k.region_rank <= 100 THEN 1
                ELSE 0 END,
           LOWER(k.email),
           NVL(k.revenue_total, 0) - NVL(k.refund_count, 0)
      FROM ranked k;
END s4_customer_scoring;
/


-- =====================================================================================
-- 4. s4_hierarchy_rollup
--
--    A three-level self-join over the customer hierarchy, joined back to two aggregates
--    at different grains. No CONNECT BY - it is refused, and this package introduces
--    nothing new; the depth is built out of ordinary joins instead.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_hierarchy_rollup IS
BEGIN
    INSERT INTO dim_customer_hier (cust_id, parent_cust_id, depth, root_name)
    WITH level_one AS (
        SELECT h.cust_id          AS cust_id,
               h.parent_cust_id   AS parent_cust_id,
               h.root_name        AS root_name
          FROM dim_customer_hier h
         WHERE h.parent_cust_id IS NOT NULL
    ),
    level_two AS (
        SELECT a.cust_id            AS cust_id,
               b.parent_cust_id     AS grandparent_id,
               a.root_name          AS root_name
          FROM level_one a
          JOIN dim_customer_hier b
            ON b.cust_id = a.parent_cust_id
    ),
    level_three AS (
        SELECT t.cust_id          AS cust_id,
               t.grandparent_id   AS grandparent_id,
               t.root_name        AS root_name,
               d.region           AS region
          FROM level_two t
          JOIN dim_customer d
            ON d.cust_id = t.cust_id
          JOIN stg_customer s
            ON s.cust_id = t.cust_id
         WHERE s.status_code <> 'D'
    ),
    revenue_grain AS (
        SELECT r.cust_id           AS cust_id,
               SUM(r.net_amount)   AS revenue_total,
               COUNT(r.period_month) AS active_months
          FROM fct_revenue r
         GROUP BY r.cust_id
    )
    SELECT x.cust_id,
           x.grandparent_id,
           NVL(g.active_months, 0),
           x.root_name
      FROM level_three x
      LEFT JOIN revenue_grain g
        ON g.cust_id = x.cust_id;
END s4_hierarchy_rollup;
/


-- =====================================================================================
-- 5. s4_merge_upsert
--
--    A MERGE whose USING is a four-level CTE chain carrying a correlated scalar
--    aggregate, with an accumulator in the SET clause and a predicate on each arm.
--    S3-03, S3-07 and S3-10 all land in this one statement, at depth.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_merge_upsert IS
BEGIN
    MERGE INTO dim_customer t
    USING (
        WITH recent AS (
            SELECT c.cust_id     AS cust_id,
                   c.region      AS region,
                   c.email       AS email,
                   c.last_login  AS last_login
              FROM stg_customer c
             WHERE c.last_login IS NOT NULL
        ),
        policy_scope AS (
            SELECT n.cust_id     AS cust_id,
                   n.region      AS region,
                   n.email       AS email,
                   n.last_login  AS last_login,
                   p.window_days AS window_days
              FROM recent n
              JOIN ref_policy p
                ON p.region = n.region
             WHERE p.window_days > 7
        ),
        revenue_scope AS (
            SELECT w.cust_id      AS cust_id,
                   w.region       AS region,
                   w.email        AS email,
                   w.window_days  AS window_days,
                   (SELECT SUM(r.net_amount)
                      FROM fct_revenue r
                     WHERE r.cust_id = w.cust_id) AS revenue_total
              FROM policy_scope w
        ),
        scored AS (
            SELECT v.cust_id                AS cust_id,
                   v.region                 AS region,
                   v.email                  AS email,
                   v.revenue_total          AS revenue_total,
                   RANK() OVER (PARTITION BY v.region
                                    ORDER BY v.revenue_total DESC) AS region_rank
              FROM revenue_scope v
        )
        SELECT s.cust_id        AS cust_id,
               s.region         AS region,
               s.email          AS email,
               s.revenue_total  AS revenue_total,
               s.region_rank    AS region_rank
          FROM scored s
    ) x
    ON (t.cust_id = x.cust_id)
    WHEN MATCHED THEN
        UPDATE SET t.region         = x.region,
                   t.email          = LOWER(x.email),
                   t.lifetime_value = NVL(t.lifetime_value, 0) + NVL(x.revenue_total, 0),
                   t.is_active      = CASE WHEN x.region_rank <= 50 THEN 1 ELSE 0 END
        WHERE x.revenue_total > 0
    WHEN NOT MATCHED THEN
        INSERT (cust_id, region, is_active, email, lifetime_value)
        VALUES (x.cust_id, x.region, 1, LOWER(x.email), NVL(x.revenue_total, 0));
END s4_merge_upsert;
/


-- =====================================================================================
-- 6. s4_returns_reconciliation
--
--    Set operators over aggregated CTEs, each arm three levels deep, feeding a join back
--    to one of them. MINUS binds by POSITION, and its second arm contributes filter
--    rather than value - convention (a) from stress 2, now at depth.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_returns_reconciliation IS
BEGIN
    INSERT INTO gtt_stage (cust_id, period_month, amount)
    WITH sold AS (
        SELECT o.cust_id                  AS cust_id,
               TRUNC(o.order_date, 'MM')  AS period_month,
               l.line_amount              AS line_amount
          FROM stg_orders o
          JOIN stg_order_lines l
            ON l.order_id = o.order_id
         WHERE o.currency = 'GBP'
    ),
    sold_grain AS (
        SELECT s.cust_id            AS cust_id,
               s.period_month       AS period_month,
               SUM(s.line_amount)   AS sold_total
          FROM sold s
         GROUP BY s.cust_id, s.period_month
    ),
    returned AS (
        SELECT t.cust_id                  AS cust_id,
               TRUNC(o.order_date, 'MM')  AS period_month,
               t.refund_amount            AS refund_amount
          FROM stg_returns t
          JOIN stg_orders o
            ON o.order_id = t.order_id
         WHERE t.status_code = 'A'
    ),
    returned_grain AS (
        SELECT u.cust_id              AS cust_id,
               u.period_month         AS period_month,
               SUM(u.refund_amount)   AS refund_total
          FROM returned u
         GROUP BY u.cust_id, u.period_month
    ),
    net_scope AS (
        SELECT g.cust_id, g.period_month FROM sold_grain g
        MINUS
        SELECT h.cust_id, h.period_month FROM returned_grain h
    )
    SELECT n.cust_id,
           n.period_month,
           NVL(k.sold_total, 0)
      FROM net_scope n
      JOIN sold_grain k
        ON k.cust_id = n.cust_id
       AND k.period_month = n.period_month;
END s4_returns_reconciliation;
/


-- =====================================================================================
-- 7. s4_window_cascade
--
--    Three windows at three different CTE levels, each reading the one below, and each
--    partitioned differently. The S3-02 fix walks nested scopes for window influence;
--    this is the case where it has to walk three of them and keep the partitions apart.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_window_cascade IS
BEGIN
    INSERT INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
    WITH monthly AS (
        SELECT o.cust_id                   AS cust_id,
               TRUNC(o.order_date, 'MM')   AS period_month,
               o.gross_amount              AS gross_amount,
               o.discount_amt              AS discount_amt,
               o.currency                  AS currency
          FROM stg_orders o
         WHERE o.order_date >= ADD_MONTHS(SYSDATE, -36)
    ),
    smoothed AS (
        SELECT m.cust_id       AS cust_id,
               m.period_month  AS period_month,
               m.currency      AS currency,
               m.discount_amt  AS discount_amt,
               SUM(m.gross_amount)
                   OVER (PARTITION BY m.cust_id ORDER BY m.period_month) AS running_gross
          FROM monthly m
    ),
    shifted AS (
        SELECT s.cust_id        AS cust_id,
               s.period_month   AS period_month,
               s.running_gross  AS running_gross,
               LAG(s.discount_amt)
                   OVER (PARTITION BY s.currency ORDER BY s.period_month) AS prior_discount
          FROM smoothed s
    ),
    ordered AS (
        SELECT f.cust_id         AS cust_id,
               f.period_month    AS period_month,
               f.running_gross   AS running_gross,
               f.prior_discount  AS prior_discount,
               DENSE_RANK() OVER (PARTITION BY f.period_month
                                      ORDER BY f.running_gross DESC) AS month_rank
          FROM shifted f
    )
    SELECT d.cust_id,
           d.period_month,
           d.running_gross - NVL(d.prior_discount, 0),
           d.month_rank
      FROM ordered d;
END s4_window_cascade;
/


-- =====================================================================================
-- 8. s4_category_matrix
--
--    A four-table join feeding two INDEPENDENT aggregates that are then joined to each
--    other, with a ROLLUP over the join. Two GROUP BY clauses that are SIBLINGS rather
--    than nested - the arrangement `_grouping_influence` got right for the wrong reason
--    before sq_02_cte_chain, kept here so that stays true.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_category_matrix IS
BEGIN
    INSERT INTO fct_product_sales
        (period_month, product_id, category_name, gross_sales, refund_total, units_sold)
    WITH sales_side AS (
        SELECT TRUNC(o.order_date, 'MM')  AS period_month,
               p.product_id               AS product_id,
               c.category_name            AS category_name,
               l.line_amount              AS line_amount,
               l.quantity                 AS quantity
          FROM stg_orders o
          JOIN stg_order_lines l
            ON l.order_id = o.order_id
          JOIN stg_products p
            ON p.product_id = l.product_id
          JOIN stg_categories c
            ON c.category_id = p.category_id
         WHERE p.status_code <> 'X'
    ),
    sales_grain AS (
        SELECT v.period_month      AS period_month,
               v.product_id        AS product_id,
               v.category_name     AS category_name,
               SUM(v.line_amount)  AS sales_total,
               SUM(v.quantity)     AS units_total
          FROM sales_side v
         GROUP BY v.period_month, v.product_id, v.category_name
    ),
    refund_side AS (
        SELECT TRUNC(o.order_date, 'MM')  AS period_month,
               t.product_id               AS product_id,
               t.refund_amount            AS refund_amount
          FROM stg_returns t
          JOIN stg_orders o
            ON o.order_id = t.order_id
         WHERE t.status_code IN ('A', 'P')
    ),
    refund_grain AS (
        SELECT w.period_month         AS period_month,
               w.product_id           AS product_id,
               SUM(w.refund_amount)   AS refund_total
          FROM refund_side w
         GROUP BY w.period_month, w.product_id
    )
    SELECT a.period_month,
           a.product_id,
           a.category_name,
           SUM(a.sales_total),
           SUM(NVL(b.refund_total, 0)),
           SUM(a.units_total)
      FROM sales_grain a
      LEFT JOIN refund_grain b
        ON b.period_month = a.period_month
       AND b.product_id = a.product_id
     GROUP BY ROLLUP (a.period_month, a.product_id, a.category_name);
END s4_category_matrix;
/


-- =====================================================================================
-- 9. s4_cursor_ladder
--
--    Band 1 at depth: a cursor whose query is itself a three-level CTE chain, driving a
--    loop that assigns through four variables with nested CASE before writing. Every
--    variable hop is a chance to lose the base column, and the cursor's own aggregation
--    is two scopes below the FOR loop.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_cursor_ladder IS
    CURSOR c_customer IS
        WITH scoped AS (
            SELECT o.cust_id                  AS cust_id,
                   TRUNC(o.order_date, 'MM')  AS period_month,
                   o.gross_amount             AS gross_amount,
                   o.discount_amt             AS discount_amt
              FROM stg_orders o
             WHERE o.currency = 'GBP'
        ),
        joined AS (
            SELECT s.cust_id       AS cust_id,
                   s.period_month  AS period_month,
                   s.gross_amount  AS gross_amount,
                   s.discount_amt  AS discount_amt,
                   d.region        AS region,
                   d.is_active     AS is_active
              FROM scoped s
              JOIN dim_customer d
                ON d.cust_id = s.cust_id
             WHERE d.is_active = 1
        ),
        summarised AS (
            SELECT j.cust_id             AS cust_id,
                   j.period_month        AS period_month,
                   j.region              AS region,
                   SUM(j.gross_amount)   AS gross_total,
                   SUM(j.discount_amt)   AS discount_total
              FROM joined j
             GROUP BY j.cust_id, j.period_month, j.region
            HAVING SUM(j.gross_amount) > 0
        )
        SELECT m.cust_id,
               m.period_month,
               m.region,
               m.gross_total,
               m.discount_total
          FROM summarised m;

    v_cust_id      NUMBER;
    v_period       DATE;
    v_gross        NUMBER;
    v_discount     NUMBER;
    v_net          NUMBER;
    v_band         NUMBER;
    v_order_count  NUMBER;
BEGIN
    FOR rec IN c_customer LOOP
        v_cust_id  := rec.cust_id;
        v_period   := rec.period_month;
        v_gross    := rec.gross_total;
        v_discount := NVL(rec.discount_total, 0);

        v_net := v_gross - v_discount;

        v_band := CASE WHEN v_net > 100000 THEN 3
                       WHEN v_net > 10000  THEN 2
                       ELSE 1 END;

        v_order_count := CASE WHEN rec.region = 'EU' THEN v_band * 2
                              ELSE v_band END;

        INSERT INTO fct_revenue_part (cust_id, period_month, net_amount, order_count)
        VALUES (v_cust_id, v_period, v_net, v_order_count);
    END LOOP;
END s4_cursor_ladder;
/


-- =====================================================================================
-- 10. s4_bulk_pipeline
--
--     Band 1: a collection loaded by BULK COLLECT and read back by subscript, which is
--     the S3-06 shape. The FETCH form is refused; the SELECT ... BULK COLLECT INTO form
--     is not, so both are here and the difference between them is the measurement.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_bulk_pipeline IS
    TYPE t_id_tab  IS TABLE OF NUMBER;
    TYPE t_amt_tab IS TABLE OF NUMBER;

    l_ids      t_id_tab;
    l_amounts  t_amt_tab;
    l_scaled   NUMBER;
    l_flag     NUMBER;
BEGIN
    SELECT g.cust_id, g.revenue_total
      BULK COLLECT INTO l_ids, l_amounts
      FROM (SELECT r.cust_id           AS cust_id,
                   SUM(r.net_amount)   AS revenue_total
              FROM fct_revenue r
             GROUP BY r.cust_id) g
     WHERE g.revenue_total > 0;

    FOR i IN 1 .. l_ids.COUNT LOOP
        l_scaled := l_amounts(i) * 1.05;
        l_flag   := CASE WHEN l_amounts(i) > 50000 THEN 1 ELSE 0 END;

        INSERT INTO fct_revenue_stage (cust_id, net_amount, order_count)
        VALUES (l_ids(i), l_scaled, l_flag);
    END LOOP;
END s4_bulk_pipeline;
/


-- =====================================================================================
-- 11. s4_audit_fanout
--
--     Band 2 at depth. The procedure writes tmp_recent, which carries trg_recent_audit,
--     so two of its edges land in dim_customer - a table this unit never names. The
--     source is a four-level chain behind a semi-join, so the inheritance has to survive
--     a statement that is not trivial (S2-10, T3.3).
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_audit_fanout IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    WITH eligible AS (
        SELECT c.cust_id      AS cust_id,
               c.region       AS region,
               c.last_login   AS last_login,
               c.signup_date  AS signup_date
          FROM stg_customer c
         WHERE c.status_code <> 'D'
    ),
    windowed AS (
        SELECT e.cust_id      AS cust_id,
               e.region       AS region,
               e.last_login   AS last_login,
               e.signup_date  AS signup_date,
               p.window_days  AS window_days
          FROM eligible e
          JOIN ref_policy p
            ON p.region = e.region
    ),
    recent_grain AS (
        SELECT w.cust_id                                  AS cust_id,
               GREATEST(w.last_login, w.signup_date)      AS effective_login,
               w.window_days                              AS window_days
          FROM windowed w
         WHERE w.window_days > 7
    )
    SELECT g.cust_id,
           g.effective_login
      FROM recent_grain g
     WHERE g.cust_id IN (SELECT d.cust_id
                           FROM dim_customer d
                          WHERE d.is_active = 1);
END s4_audit_fanout;
/


-- =====================================================================================
-- 12. s4_view_resolution
--
--     Reads through a three-deep view chain (v_cust_l3 -> v_cust_l2 -> v_cust_l1) and
--     joins it to an aggregate. Views are expanded rather than reported, so every edge
--     here must name the BASE table - a view appearing as a source would be a silent
--     substitution of the name the code wrote for the object it meant.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_view_resolution IS
BEGIN
    INSERT INTO dw_dim_customer_v2 (cust_id, region, is_active, email)
    WITH revenue_grain AS (
        SELECT r.cust_id            AS cust_id,
               SUM(r.net_amount)    AS revenue_total,
               COUNT(r.order_count) AS month_count
          FROM fct_revenue r
         GROUP BY r.cust_id
        HAVING SUM(r.net_amount) > 0
    ),
    joined AS (
        SELECT v.cust_id       AS cust_id,
               v.region        AS region,
               v.last_login    AS last_login,
               v.status_flag   AS status_flag,
               g.revenue_total AS revenue_total,
               g.month_count   AS month_count
          FROM v_cust_l3 v
          JOIN revenue_grain g
            ON g.cust_id = v.cust_id
    )
    SELECT j.cust_id,
           j.region,
           CASE WHEN j.month_count > 6 THEN 1 ELSE 0 END,
           LOWER(j.status_flag)
      FROM joined j
     WHERE j.revenue_total > 100;
END s4_view_resolution;
/


-- =====================================================================================
-- 13. s4_order_funnel
--
--     Five joins across four levels with aggregation at level 3 and again at level 5,
--     the second reading the first at a coarser grain. The deepest value path in the
--     package: line_amount -> line_total -> order_total -> funnel_total.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_order_funnel IS
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    WITH order_head AS (
        SELECT o.order_id                  AS order_id,
               o.cust_id                   AS cust_id,
               TRUNC(o.order_date, 'MM')   AS period_month,
               o.currency                  AS currency
          FROM stg_orders o
         WHERE o.gross_amount IS NOT NULL
    ),
    line_detail AS (
        SELECT h.order_id      AS order_id,
               h.cust_id       AS cust_id,
               h.period_month  AS period_month,
               l.line_id       AS line_id,
               l.line_amount   AS line_amount,
               p.category_id   AS category_id
          FROM order_head h
          JOIN stg_order_lines l
            ON l.order_id = h.order_id
          JOIN stg_products p
            ON p.product_id = l.product_id
         WHERE p.status_code <> 'X'
    ),
    order_grain AS (
        SELECT d.cust_id           AS cust_id,
               d.period_month      AS period_month,
               d.order_id          AS order_id,
               SUM(d.line_amount)  AS order_total,
               COUNT(d.line_id)    AS line_count
          FROM line_detail d
         GROUP BY d.cust_id, d.period_month, d.order_id
    ),
    qualified AS (
        SELECT g.cust_id       AS cust_id,
               g.period_month  AS period_month,
               g.order_id      AS order_id,
               g.order_total   AS order_total,
               g.line_count    AS line_count,
               k.region        AS region
          FROM order_grain g
          JOIN dim_customer k
            ON k.cust_id = g.cust_id
         WHERE k.is_active = 1
    ),
    funnel AS (
        SELECT q.cust_id            AS cust_id,
               q.period_month       AS period_month,
               SUM(q.order_total)   AS funnel_total,
               COUNT(q.order_id)    AS funnel_orders
          FROM qualified q
         GROUP BY q.cust_id, q.period_month
        HAVING SUM(q.order_total) > 0
    )
    SELECT f.cust_id,
           f.period_month,
           f.funnel_total,
           f.funnel_orders
      FROM funnel f;
END s4_order_funnel;
/


-- =====================================================================================
-- 14. s4_discount_analysis
--
--     A ROLLUP over a joined-and-renamed CTE. The S3-01 fix covers ROLLUP; this checks it
--     holds when the grouping sits above a join and a rename rather than above a base
--     table. GROUPING SETS and CUBE are deliberately absent - neither appears in stress
--     1, 2 or 3, and this package introduces no construct those three did not use.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_discount_analysis IS
BEGIN
    INSERT INTO fct_product_sales
        (period_month, product_id, gross_sales, net_sales, units_sold)
    WITH discounted AS (
        SELECT TRUNC(o.order_date, 'MM')   AS period_month,
               l.product_id                AS product_id,
               o.gross_amount              AS gross_amount,
               o.discount_amt              AS discount_amt,
               l.quantity                  AS quantity
          FROM stg_orders o
          JOIN stg_order_lines l
            ON l.order_id = o.order_id
         WHERE o.discount_amt IS NOT NULL
    ),
    rated AS (
        SELECT d.period_month    AS period_month,
               d.product_id      AS product_id,
               d.gross_amount    AS gross_amount,
               d.quantity        AS quantity,
               NULLIF(d.discount_amt, 0) AS discount_amt
          FROM discounted d
    )
    SELECT r.period_month,
           r.product_id,
           SUM(r.gross_amount),
           SUM(r.gross_amount) - SUM(NVL(r.discount_amt, 0)),
           SUM(r.quantity)
      FROM rated r
     GROUP BY ROLLUP (r.period_month, r.product_id);
END s4_discount_analysis;
/


-- =====================================================================================
-- 15. s4_customer_lifecycle
--
--     UNION ALL arms that are each three levels deep and aggregate differently, feeding
--     one target. Arms bind BY POSITION, so every arm feeds the same output column and
--     losing one is a silent under-report of a whole feed.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_customer_lifecycle IS
BEGIN
    INSERT INTO gtt_stage (cust_id, period_month, amount)
    WITH new_customers AS (
        SELECT c.cust_id                  AS cust_id,
               TRUNC(c.signup_date, 'MM') AS period_month,
               c.region                   AS region
          FROM stg_customer c
         WHERE c.signup_date IS NOT NULL
    ),
    new_grain AS (
        SELECT n.cust_id        AS cust_id,
               n.period_month   AS period_month,
               COUNT(n.region)  AS region_count
          FROM new_customers n
         GROUP BY n.cust_id, n.period_month
    ),
    returning_customers AS (
        SELECT o.cust_id                  AS cust_id,
               TRUNC(o.order_date, 'MM')  AS period_month,
               o.gross_amount             AS gross_amount
          FROM stg_orders o
          JOIN dim_customer d
            ON d.cust_id = o.cust_id
         WHERE d.is_active = 1
    ),
    returning_grain AS (
        SELECT t.cust_id             AS cust_id,
               t.period_month        AS period_month,
               SUM(t.gross_amount)   AS gross_total
          FROM returning_customers t
         GROUP BY t.cust_id, t.period_month
    )
    SELECT a.cust_id, a.period_month, a.region_count FROM new_grain a
    UNION ALL
    SELECT b.cust_id, b.period_month, b.gross_total FROM returning_grain b;
END s4_customer_lifecycle;
/


-- =====================================================================================
-- 16. s4_product_hierarchy
--
--     A category self-join three deep, joined to a product aggregate. Each level renames
--     the column it carries, so an alias chain four names long has to survive - the
--     b0_alias_chains shape with a join at every step.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_product_hierarchy IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, category_name, gross_sales)
    WITH leaf AS (
        SELECT c.category_id    AS leaf_id,
               c.category_name  AS leaf_name,
               c.parent_id      AS leaf_parent
          FROM stg_categories c
         WHERE c.parent_id IS NOT NULL
    ),
    mid AS (
        SELECT f.leaf_id        AS leaf_id,
               f.leaf_name      AS leaf_name,
               m.category_name  AS mid_name,
               m.parent_id      AS mid_parent
          FROM leaf f
          JOIN stg_categories m
            ON m.category_id = f.leaf_parent
    ),
    root AS (
        SELECT x.leaf_id     AS leaf_id,
               x.leaf_name   AS leaf_name,
               x.mid_name    AS mid_name,
               r.category_name AS root_name
          FROM mid x
          JOIN stg_categories r
            ON r.category_id = x.mid_parent
    ),
    product_grain AS (
        SELECT p.category_id              AS category_id,
               p.product_id               AS product_id,
               TRUNC(o.order_date, 'MM')  AS period_month,
               SUM(l.line_amount)         AS line_total
          FROM stg_products p
          JOIN stg_order_lines l
            ON l.product_id = p.product_id
          JOIN stg_orders o
            ON o.order_id = l.order_id
         WHERE p.status_code <> 'X'
         GROUP BY p.category_id, p.product_id, TRUNC(o.order_date, 'MM')
    )
    SELECT g.period_month,
           g.product_id,
           t.root_name,
           g.line_total
      FROM product_grain g
      JOIN root t
        ON t.leaf_id = g.category_id;
END s4_product_hierarchy;
/


-- =====================================================================================
-- 17. s4_revenue_variance
--
--     LAG and LEAD over the SAME aggregate at the same level, plus FIRST_VALUE over a
--     different partition. Three windows in one scope with two distinct partitions, so
--     the influence edges must not be pooled across them.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_revenue_variance IS
BEGIN
    INSERT INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
    WITH grain AS (
        SELECT r.cust_id            AS cust_id,
               r.period_month       AS period_month,
               SUM(r.net_amount)    AS net_total,
               SUM(r.order_count)   AS order_total
          FROM fct_revenue r
         GROUP BY r.cust_id, r.period_month
    ),
    joined AS (
        SELECT g.cust_id       AS cust_id,
               g.period_month  AS period_month,
               g.net_total     AS net_total,
               g.order_total   AS order_total,
               d.region        AS region
          FROM grain g
          JOIN dim_customer d
            ON d.cust_id = g.cust_id
    ),
    varied AS (
        SELECT j.cust_id       AS cust_id,
               j.period_month  AS period_month,
               j.net_total     AS net_total,
               j.order_total   AS order_total,
               LAG(j.net_total)
                   OVER (PARTITION BY j.cust_id ORDER BY j.period_month)  AS prior_net,
               LEAD(j.net_total)
                   OVER (PARTITION BY j.cust_id ORDER BY j.period_month)  AS next_net,
               FIRST_VALUE(j.order_total)
                   OVER (PARTITION BY j.region ORDER BY j.period_month)   AS region_first
          FROM joined j
    )
    SELECT v.cust_id,
           v.period_month,
           v.net_total - NVL(v.prior_net, 0) + NVL(v.next_net, 0),
           v.region_first
      FROM varied v;
END s4_revenue_variance;
/


-- =====================================================================================
-- 18. s4_return_rate
--
--     A ratio of two aggregates computed at different grains and joined, with the
--     divisor guarded by NULLIF. Two independent aggregating CTEs feeding one expression
--     means both groupings govern the same output column.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_return_rate IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, refund_total, net_sales)
    WITH sold_grain AS (
        SELECT TRUNC(o.order_date, 'MM')  AS period_month,
               l.product_id               AS product_id,
               SUM(l.line_amount)         AS sold_total
          FROM stg_orders o
          JOIN stg_order_lines l
            ON l.order_id = o.order_id
         WHERE o.currency = 'GBP'
         GROUP BY TRUNC(o.order_date, 'MM'), l.product_id
    ),
    refund_grain AS (
        SELECT TRUNC(o.order_date, 'MM')  AS period_month,
               t.product_id               AS product_id,
               SUM(t.refund_amount)       AS refund_total
          FROM stg_returns t
          JOIN stg_orders o
            ON o.order_id = t.order_id
         WHERE t.status_code = 'A'
         GROUP BY TRUNC(o.order_date, 'MM'), t.product_id
    ),
    rated AS (
        SELECT s.period_month   AS period_month,
               s.product_id     AS product_id,
               s.sold_total     AS sold_total,
               f.refund_total   AS refund_total
          FROM sold_grain s
          LEFT JOIN refund_grain f
            ON f.period_month = s.period_month
           AND f.product_id = s.product_id
    )
    SELECT k.period_month,
           k.product_id,
           NVL(k.refund_total, 0),
           k.sold_total - NVL(k.refund_total, 0)
      FROM rated k
     WHERE NULLIF(k.sold_total, 0) IS NOT NULL;
END s4_return_rate;
/


-- =====================================================================================
-- 19. s4_staged_upsert
--
--     A second MERGE, deliberately shaped differently from unit 5: the USING is a join
--     of two aggregates rather than a CTE chain, the accumulator reads the target twice,
--     and the arm predicate names the TARGET rather than the source.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_staged_upsert IS
BEGIN
    MERGE INTO fct_revenue_part t
    USING (
        SELECT a.cust_id          AS cust_id,
               a.period_month     AS period_month,
               a.revenue_total    AS revenue_total,
               b.refund_total     AS refund_total
          FROM (SELECT r.cust_id          AS cust_id,
                       r.period_month     AS period_month,
                       SUM(r.net_amount)  AS revenue_total
                  FROM fct_revenue r
                 GROUP BY r.cust_id, r.period_month) a
          LEFT JOIN (SELECT t2.cust_id             AS cust_id,
                            TRUNC(o.order_date, 'MM') AS period_month,
                            SUM(t2.refund_amount)  AS refund_total
                       FROM stg_returns t2
                       JOIN stg_orders o
                         ON o.order_id = t2.order_id
                      WHERE t2.status_code = 'A'
                      GROUP BY t2.cust_id, TRUNC(o.order_date, 'MM')) b
            ON b.cust_id = a.cust_id
           AND b.period_month = a.period_month
    ) x
    ON (t.cust_id = x.cust_id AND t.period_month = x.period_month)
    WHEN MATCHED THEN
        UPDATE SET t.net_amount  = NVL(t.net_amount, 0)
                                 + x.revenue_total
                                 - NVL(x.refund_total, 0),
                   t.order_count = NVL(t.order_count, 0) + 1
        WHERE t.net_amount < 1000000
    WHEN NOT MATCHED THEN
        INSERT (cust_id, period_month, net_amount, order_count)
        VALUES (x.cust_id, x.period_month, x.revenue_total, 1);
END s4_staged_upsert;
/


-- =====================================================================================
-- 20. s4_batch_writer
--
--     Band 1 with two collections and two write targets in one loop, plus a scalar
--     SELECT INTO feeding a variable that both writes read. The S3-06 subscript rule has
--     to hold for two different collections indexed by the same loop variable.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_batch_writer IS
    TYPE t_cust_tab   IS TABLE OF NUMBER;
    TYPE t_amount_tab IS TABLE OF NUMBER;

    l_customers  t_cust_tab;
    l_amounts    t_amount_tab;
    l_threshold  NUMBER;
    l_net        NUMBER;
    l_flag       NUMBER;
BEGIN
    SELECT p.window_days
      INTO l_threshold
      FROM ref_policy p
     WHERE ROWNUM = 1;

    SELECT g.cust_id, g.revenue_total
      BULK COLLECT INTO l_customers, l_amounts
      FROM (SELECT r.cust_id           AS cust_id,
                   SUM(r.net_amount)   AS revenue_total
              FROM fct_revenue r
              JOIN dim_customer d
                ON d.cust_id = r.cust_id
             WHERE d.is_active = 1
             GROUP BY r.cust_id
            HAVING SUM(r.net_amount) > 0) g;

    FOR i IN 1 .. l_customers.COUNT LOOP
        l_net  := l_amounts(i) - l_threshold;
        l_flag := CASE WHEN l_amounts(i) > l_threshold THEN 1 ELSE 0 END;

        INSERT INTO fct_revenue_part (cust_id, net_amount, order_count)
        VALUES (l_customers(i), l_net, l_flag);

        INSERT INTO dw_audit_log (cust_id, changed_by, changed_at)
        VALUES (l_customers(i), USER, SYSDATE);
    END LOOP;
END s4_batch_writer;
/


-- =====================================================================================
-- 21. s4_regional_summary
--
--     Four joins, an aggregate, a ROLLUP over it, and a window over the ROLLUP. The
--     deepest single stack in the package: join -> GROUP BY -> ROLLUP -> window, each in
--     its own scope, with the S3-01 and S3-02 fixes both required to read it.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_regional_summary IS
BEGIN
    INSERT INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
    WITH spine AS (
        SELECT o.cust_id                   AS cust_id,
               TRUNC(o.order_date, 'MM')   AS period_month,
               l.line_amount               AS line_amount,
               d.region                    AS region,
               p.window_days               AS window_days
          FROM stg_orders o
          JOIN stg_order_lines l
            ON l.order_id = o.order_id
          JOIN dim_customer d
            ON d.cust_id = o.cust_id
          JOIN ref_policy p
            ON p.region = d.region
         WHERE d.is_active = 1
           AND p.window_days > 0
    ),
    cust_grain AS (
        SELECT s.cust_id            AS cust_id,
               s.period_month       AS period_month,
               s.region             AS region,
               SUM(s.line_amount)   AS cust_total
          FROM spine s
         GROUP BY s.cust_id, s.period_month, s.region
    ),
    region_grain AS (
        SELECT g.cust_id          AS cust_id,
               g.period_month     AS period_month,
               SUM(g.cust_total)  AS region_total,
               COUNT(g.region)    AS region_count
          FROM cust_grain g
         GROUP BY ROLLUP (g.cust_id, g.period_month)
    )
    SELECT z.cust_id,
           z.period_month,
           z.region_total,
           ROW_NUMBER() OVER (PARTITION BY z.cust_id ORDER BY z.region_total DESC)
      FROM region_grain z;
END s4_regional_summary;
/


-- =====================================================================================
-- 22. s4_order_velocity
--
--     NTILE and ROW_NUMBER over the same partition, with a DENSE_RANK over a different
--     one, all reading an aggregate two levels down. Rank functions take no argument, so
--     every source they have is influence and none of it is value.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_order_velocity IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, units_sold, rank_in_month)
    WITH velocity AS (
        SELECT TRUNC(o.order_date, 'MM')  AS period_month,
               l.product_id               AS product_id,
               l.quantity                 AS quantity,
               p.category_id              AS category_id
          FROM stg_orders o
          JOIN stg_order_lines l
            ON l.order_id = o.order_id
          JOIN stg_products p
            ON p.product_id = l.product_id
         WHERE l.quantity IS NOT NULL
    ),
    velocity_grain AS (
        SELECT v.period_month     AS period_month,
               v.product_id       AS product_id,
               v.category_id      AS category_id,
               SUM(v.quantity)    AS unit_total
          FROM velocity v
         GROUP BY v.period_month, v.product_id, v.category_id
    )
    SELECT k.period_month,
           k.product_id,
           k.unit_total,
           NTILE(4) OVER (PARTITION BY k.period_month ORDER BY k.unit_total DESC)
      FROM velocity_grain k;
END s4_order_velocity;
/


-- =====================================================================================
-- 23. s4_margin_bands
--
--     A CASE ladder over a DECODE over a COALESCE, each reading a different base column,
--     all inside an expression that also aggregates. The transform ladder has to survive
--     four nested classifiers - aggregated outranks conditional outranks derived.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_margin_bands IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, gross_sales, net_sales)
    WITH banded AS (
        SELECT TRUNC(o.order_date, 'MM')  AS period_month,
               l.product_id               AS product_id,
               l.line_amount              AS line_amount,
               l.unit_price               AS unit_price,
               p.list_price               AS list_price,
               p.status_code              AS status_code
          FROM stg_orders o
          JOIN stg_order_lines l
            ON l.order_id = o.order_id
          JOIN stg_products p
            ON p.product_id = l.product_id
         WHERE o.currency = 'GBP'
    )
    SELECT b.period_month,
           b.product_id,
           SUM(b.line_amount),
           SUM(CASE WHEN DECODE(b.status_code, 'A', 1, 'P', 1, 0) = 1
                    THEN COALESCE(b.unit_price, b.list_price)
                    ELSE 0 END)
      FROM banded b
     GROUP BY b.period_month, b.product_id;
END s4_margin_bands;
/


-- =====================================================================================
-- 24. s4_intersect_scope
--
--     INTERSECT between two aggregated arms, each two levels deep, then joined back for
--     the value. Convention (a) from stress 2: the second arm decides which rows survive
--     and supplies no value, so it contributes filter and not value.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_intersect_scope IS
BEGIN
    INSERT INTO gtt_stage (cust_id, period_month, amount)
    WITH revenue_side AS (
        SELECT r.cust_id           AS cust_id,
               r.period_month      AS period_month,
               SUM(r.net_amount)   AS revenue_total
          FROM fct_revenue r
         GROUP BY r.cust_id, r.period_month
    ),
    order_side AS (
        SELECT o.cust_id                   AS cust_id,
               TRUNC(o.order_date, 'MM')   AS period_month,
               SUM(o.gross_amount)         AS gross_total
          FROM stg_orders o
         WHERE o.currency = 'GBP'
         GROUP BY o.cust_id, TRUNC(o.order_date, 'MM')
    ),
    both_sides AS (
        SELECT a.cust_id, a.period_month FROM revenue_side a
        INTERSECT
        SELECT b.cust_id, b.period_month FROM order_side b
    )
    SELECT s.cust_id,
           s.period_month,
           v.revenue_total
      FROM both_sides s
      JOIN revenue_side v
        ON v.cust_id = s.cust_id
       AND v.period_month = s.period_month;
END s4_intersect_scope;
/


-- =====================================================================================
-- 25. s4_nested_exists
--
--     EXISTS inside EXISTS, three relations deep, over an aggregate. Every correlation
--     here names a relation from an enclosing scope, which is the S3-10 fix at the depth
--     that fix was written for - and the join conditions inside them stay structural.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_nested_exists IS
BEGIN
    INSERT INTO dw_dim_customer_v2 (cust_id, region, email, lifetime_value)
    WITH candidate AS (
        SELECT c.cust_id   AS cust_id,
               c.region    AS region,
               c.email     AS email
          FROM stg_customer c
         WHERE c.status_code <> 'D'
    ),
    revenue_grain AS (
        SELECT r.cust_id           AS cust_id,
               SUM(r.net_amount)   AS revenue_total
          FROM fct_revenue r
         GROUP BY r.cust_id
    )
    SELECT k.cust_id,
           k.region,
           LOWER(k.email),
           g.revenue_total
      FROM candidate k
      JOIN revenue_grain g
        ON g.cust_id = k.cust_id
     WHERE EXISTS (SELECT 1
                     FROM stg_orders o
                    WHERE o.cust_id = k.cust_id
                      AND EXISTS (SELECT 1
                                    FROM stg_order_lines l
                                    JOIN stg_products p
                                      ON p.product_id = l.product_id
                                   WHERE l.order_id = o.order_id
                                     AND p.status_code <> 'X'));
END s4_nested_exists;
/


-- =====================================================================================
-- 26. s4_deep_alias_chain
--
--     Five renames of the same column with no aggregation anywhere, each level adding a
--     join that contributes nothing to the value. Pure traversal: if the chain breaks,
--     the edge either disappears or names an intermediate CTE instead of the base table,
--     and the second failure is the dangerous one because a CTE name looks like a table.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_deep_alias_chain IS
BEGIN
    INSERT INTO gtt_stage (cust_id, period_month, amount)
    WITH lvl1 AS (
        SELECT o.cust_id       AS c1,
               o.order_date    AS d1,
               o.gross_amount  AS a1
          FROM stg_orders o
         WHERE o.currency = 'GBP'
    ),
    lvl2 AS (
        SELECT x.c1  AS c2,
               x.d1  AS d2,
               x.a1  AS a2
          FROM lvl1 x
          JOIN dim_customer k
            ON k.cust_id = x.c1
         WHERE k.is_active = 1
    ),
    lvl3 AS (
        SELECT y.c2  AS c3,
               y.d2  AS d3,
               y.a2  AS a3
          FROM lvl2 y
          JOIN stg_customer s
            ON s.cust_id = y.c2
         WHERE s.status_code <> 'D'
    ),
    lvl4 AS (
        SELECT z.c3  AS c4,
               z.d3  AS d4,
               z.a3  AS a4
          FROM lvl3 z
          JOIN ref_policy p
            ON p.region = 'EU'
         WHERE p.window_days > 0
    ),
    lvl5 AS (
        SELECT w.c4                    AS c5,
               TRUNC(w.d4, 'MM')       AS d5,
               w.a4                    AS a5
          FROM lvl4 w
    )
    SELECT v.c5,
           v.d5,
           v.a5
      FROM lvl5 v;
END s4_deep_alias_chain;
/


-- =====================================================================================
-- 27. s4_multi_target_split
--
--     One shared CTE chain feeding THREE writes to three different targets in one unit.
--     Each statement is scored separately and carries its own origin, so a fan-out that
--     attributed all three to the first statement would still produce the right edges and
--     the wrong provenance.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_multi_target_split IS
BEGIN
    INSERT INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
    WITH shared_grain AS (
        SELECT o.cust_id                   AS cust_id,
               TRUNC(o.order_date, 'MM')   AS period_month,
               SUM(l.line_amount)          AS line_total,
               COUNT(l.line_id)            AS line_count
          FROM stg_orders o
          JOIN stg_order_lines l
            ON l.order_id = o.order_id
         WHERE o.currency = 'GBP'
         GROUP BY o.cust_id, TRUNC(o.order_date, 'MM')
    )
    SELECT g.cust_id, g.period_month, g.line_total, g.line_count
      FROM shared_grain g;

    INSERT INTO gtt_stage (cust_id, period_month, amount)
    WITH shared_grain AS (
        SELECT o.cust_id                   AS cust_id,
               TRUNC(o.order_date, 'MM')   AS period_month,
               SUM(o.discount_amt)         AS discount_total
          FROM stg_orders o
         WHERE o.discount_amt IS NOT NULL
         GROUP BY o.cust_id, TRUNC(o.order_date, 'MM')
    )
    SELECT g.cust_id, g.period_month, g.discount_total
      FROM shared_grain g;

    INSERT INTO dw_audit_log (cust_id, changed_by, changed_at)
    SELECT c.cust_id, c.email, c.last_login
      FROM stg_customer c
     WHERE c.last_login IS NOT NULL;
END s4_multi_target_split;
/


-- =====================================================================================
-- 28. s4_filter_cascade
--
--     A predicate at every one of five levels, each naming a different base column. All
--     five decide which rows reach the target, so all five are filter edges against it -
--     `_filter_edges` traverses every scope for exactly this reason, and a version that
--     read only the outermost would report one of five and look clean.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s4_filter_cascade IS
BEGIN
    INSERT INTO fct_revenue_part (cust_id, period_month, net_amount, order_count)
    WITH f1 AS (
        SELECT o.order_id, o.cust_id, o.order_date, o.gross_amount
          FROM stg_orders o
         WHERE o.currency = 'GBP'
    ),
    f2 AS (
        SELECT a.order_id, a.cust_id, a.order_date, a.gross_amount, l.line_amount
          FROM f1 a
          JOIN stg_order_lines l
            ON l.order_id = a.order_id
         WHERE l.quantity > 0
    ),
    f3 AS (
        SELECT b.order_id, b.cust_id, b.order_date, b.line_amount, p.category_id
          FROM f2 b
          JOIN stg_products p
            ON p.product_id = b.order_id
         WHERE p.status_code <> 'X'
    ),
    f4 AS (
        SELECT c.cust_id, c.order_date, c.line_amount, d.region
          FROM f3 c
          JOIN dim_customer d
            ON d.cust_id = c.cust_id
         WHERE d.is_active = 1
    ),
    f5 AS (
        SELECT e.cust_id                   AS cust_id,
               TRUNC(e.order_date, 'MM')   AS period_month,
               SUM(e.line_amount)          AS net_total,
               COUNT(e.region)             AS region_count
          FROM f4 e
         WHERE e.region IS NOT NULL
         GROUP BY e.cust_id, TRUNC(e.order_date, 'MM')
        HAVING SUM(e.line_amount) > 0
    )
    SELECT g.cust_id, g.period_month, g.net_total, g.region_count
      FROM f5 g;
END s4_filter_cascade;
/
