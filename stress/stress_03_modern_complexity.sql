-- Stress package 3: modern ETL complexity.
--
-- Stress 1 went wide and stress 2 went deep on CONSTRUCTS. This one goes deep on
-- COMBINATIONS, because that is where this analyser's real defects have lived:
-- `_grouping_influence` returned at the first aggregating scope and passed every minimal
-- GROUP BY case, then dropped nested groupings the moment a CTE chain appeared. The
-- window-influence false positives were the same shape - correct in isolation, wrong once
-- traced through a CTE.
--
-- Every construct here was recorded as ACCEPTED by scripts/probe_parsers.py and as
-- producing edges by scripts/probe_analyser.py. That is the point: the triage found 48
-- constructs that emit edges NOBODY HAS EVER CHECKED AGAINST A KEY - the largest
-- unverified surface in the project, larger than either existing stress package. Parsing
-- is a floor. This package asks whether what comes out is true.
--
-- Weighted towards the transform ladder, because a wrong transform is a MISS ON BOTH
-- SIDES (ADR-0001 §4) and therefore costs double: LISTAGG, KEEP DENSE_RANK, GROUPING_ID,
-- nested CASE over aggregates, and a BARE-COLUMN LAG - the case S2-08 predicted would
-- behave differently and which the corpus cannot exercise, because every LAG in it is
-- LAG(SUM(...)) and the ladder keeps `aggregated` either way.
--
-- Runs against corpus/dictionary.json, like the other stress packages. Nothing is
-- executed; the key is source_read throughout and was written BEFORE the analyser saw it.

-- =====================================================================================
-- 1. A restatement run: three chained CTEs, grouping, HAVING, and a CASE over aggregates.
--    The shape that broke _grouping_influence, made harder: the GROUP BY is two scopes
--    below the INSERT, and one aggregate feeds another column's CASE condition.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s3_revenue_restatement IS
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    WITH order_base AS (
        SELECT o.cust_id                               AS cust_id,
               TRUNC(o.order_date, 'MM')               AS period_month,
               o.order_id                              AS order_id,
               o.gross_amount - NVL(o.discount_amt, 0) AS net_value
          FROM stg_orders o
         WHERE o.currency = 'GBP'
    ),
    line_detail AS (
        SELECT b.cust_id,
               b.period_month,
               b.order_id,
               l.line_amount,
               l.quantity
          FROM order_base b
          JOIN stg_order_lines l
            ON l.order_id = b.order_id
    ),
    rollup_stage AS (
        SELECT d.cust_id,
               d.period_month,
               SUM(d.line_amount)         AS line_total,
               COUNT(DISTINCT d.order_id) AS order_cnt
          FROM line_detail d
         GROUP BY d.cust_id, d.period_month
        HAVING SUM(d.line_amount) > 0
    )
    SELECT r.cust_id,
           r.period_month,
           CASE WHEN r.order_cnt > 10 THEN r.line_total * 0.95
                ELSE r.line_total END,
           r.order_cnt
      FROM rollup_stage r;
END s3_revenue_restatement;
/

-- =====================================================================================
-- 2. A product scorecard: ROLLUP, LISTAGG, KEEP DENSE_RANK and GROUPING_ID over a
--    four-table join. Three of these functions sit on the transform ladder and none of
--    them appears anywhere in the corpus.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s3_product_scorecard IS
BEGIN
    INSERT INTO fct_product_sales
        (period_month, product_id, category_name, gross_sales, net_sales,
         units_sold, rank_in_month)
    SELECT TRUNC(o.order_date, 'MM'),
           l.product_id,
           LISTAGG(c.category_name, ',') WITHIN GROUP (ORDER BY c.category_name),
           SUM(l.line_amount),
           MAX(p.list_price) KEEP (DENSE_RANK FIRST ORDER BY l.quantity DESC),
           SUM(l.quantity),
           GROUPING_ID(l.product_id, c.category_name)
      FROM stg_orders o
      JOIN stg_order_lines l ON l.order_id = o.order_id
      JOIN stg_products    p ON p.product_id = l.product_id
      JOIN stg_categories  c ON c.category_id = p.category_id
     WHERE p.status_code <> 'X'
     GROUP BY ROLLUP (TRUNC(o.order_date, 'MM'), l.product_id, c.category_name);
END s3_product_scorecard;
/

-- =====================================================================================
-- 3. Customer signal: four analytic functions over one window, NO GROUP BY anywhere.
--    Deliberately arranged so that net_amount's ONLY path is a bare-column LAG - if
--    anything else on that path aggregated, the ladder would hide the answer, which is
--    exactly why the corpus cannot test it.
--
--    IT WRITES fct_revenue_stage RATHER THAN fct_revenue, AND THAT IS NOT COSMETIC.
--    Written against fct_revenue, six of this unit's edges collided with unit 1's on the
--    match key - same source column, same target column, same flow and transform, from a
--    different statement doing a different thing. Origin is not in the match key
--    (amendment 1b), so the key could not state both facts and would not load at all.
--    That is stress finding S1-05, live, in a package written today rather than argued
--    about in the abstract. Staging is the realistic shape anyway; the collision is
--    recorded in FINDINGS rather than papered over.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s3_customer_signal IS
BEGIN
    INSERT INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
    WITH ordered AS (
        SELECT o.cust_id,
               o.order_date,
               o.currency,
               LAG(o.gross_amount)
                   OVER (PARTITION BY o.cust_id ORDER BY o.order_date)   AS prev_gross,
               LEAD(o.discount_amt)
                   OVER (PARTITION BY o.cust_id ORDER BY o.order_date)   AS next_disc,
               FIRST_VALUE(o.currency IGNORE NULLS)
                   OVER (PARTITION BY o.cust_id ORDER BY o.order_date)   AS first_ccy,
               SUM(o.gross_amount)
                   OVER (PARTITION BY o.cust_id ORDER BY o.order_date
                         RANGE BETWEEN INTERVAL '30' DAY PRECEDING
                                   AND CURRENT ROW)                      AS rolling_30
          FROM stg_orders o
         WHERE o.order_date >= ADD_MONTHS(SYSDATE, -24)
    )
    SELECT s.cust_id,
           TRUNC(s.order_date, 'MM'),
           s.prev_gross,
           CASE WHEN s.first_ccy = 'GBP' THEN s.rolling_30
                ELSE s.rolling_30 - NVL(s.next_disc, 0) END
      FROM ordered s;
END s3_customer_signal;
/

-- =====================================================================================
-- 4. An incremental dimension load: MERGE whose USING carries a correlated scalar
--    aggregate, a conditional SET, a self-referencing accumulator, and an arm-level
--    WHERE. The standard slowly-changing-dimension shape, minus the DELETE arm that
--    SQLGlot cannot parse (S2-01).
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s3_incremental_merge IS
BEGIN
    MERGE INTO dw_dim_customer_v2 t
    USING (
        SELECT c.cust_id,
               c.region,
               c.email,
               c.status_code,
               (SELECT SUM(r.net_amount)
                  FROM fct_revenue r
                 WHERE r.cust_id = c.cust_id) AS revenue_total
          FROM stg_customer c
         WHERE c.signup_date < SYSDATE
    ) s
    ON (t.cust_id = s.cust_id)
    WHEN MATCHED THEN
        UPDATE SET t.region         = s.region,
                   t.email          = LOWER(s.email),
                   t.lifetime_value = NVL(t.lifetime_value, 0) + NVL(s.revenue_total, 0),
                   t.is_active      = CASE WHEN s.status_code = 'A' THEN 1 ELSE 0 END
        WHERE s.revenue_total > 0
    WHEN NOT MATCHED THEN
        INSERT (cust_id, region, is_active, email, lifetime_value)
        VALUES (s.cust_id, s.region, 1, LOWER(s.email), NVL(s.revenue_total, 0));
END s3_incremental_merge;
/

-- =====================================================================================
-- 5. Bulk reconciliation: BULK COLLECT into a collection of a CURSOR %ROWTYPE, read back
--    by index with field access, inside a nested loop. S2-04 was exactly this shape
--    yielding nothing SILENTLY; this is the harder version of it.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s3_bulk_reconcile IS
    CURSOR c_returns IS
        SELECT r.cust_id,
               r.product_id,
               r.refund_amount,
               r.status_code
          FROM stg_returns r
         WHERE r.status_code IN ('P', 'A');

    TYPE t_return_tab IS TABLE OF c_returns%ROWTYPE;

    l_batch    t_return_tab;
    l_adjusted NUMBER;
    l_flag     NUMBER;
BEGIN
    OPEN c_returns;
    LOOP
        FETCH c_returns BULK COLLECT INTO l_batch LIMIT 500;
        EXIT WHEN l_batch.COUNT = 0;

        FOR i IN 1 .. l_batch.COUNT LOOP
            l_adjusted := l_batch(i).refund_amount * 1.2;
            l_flag     := CASE WHEN l_batch(i).status_code = 'A' THEN 1 ELSE 0 END;

            INSERT INTO fct_product_sales (product_id, refund_total, units_sold)
            VALUES (l_batch(i).product_id, l_adjusted, l_flag);
        END LOOP;
    END LOOP;
    CLOSE c_returns;
END s3_bulk_reconcile;
/

-- =====================================================================================
-- 6. Audit fan-out: a write to tmp_recent, which carries trg_recent_audit. The procedure
--    never mentions dim_customer, and two of its edges land there. Combined here with a
--    semi-join and a conditional value so the band-2 inheritance has to survive a
--    statement that is not trivial.
-- =====================================================================================
CREATE OR REPLACE PROCEDURE s3_audit_fanout IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT c.cust_id,
           GREATEST(c.last_login, NVL(c.signup_date, c.last_login))
      FROM stg_customer c
     WHERE c.region IN (SELECT p.region
                          FROM ref_policy p
                         WHERE p.window_days > 7);
END s3_audit_fanout;
/
