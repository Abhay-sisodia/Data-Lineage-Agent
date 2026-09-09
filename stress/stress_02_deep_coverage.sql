-- STRESS 2 / deep construct coverage
--
-- Twice the size of stress 1 and aimed at what stress 1 did NOT reach. Stress 1 stacked
-- ordinary constructs; this one goes after the parts of the language that are common in
-- real ETL and absent from the phase-0 corpus entirely: recursive CTEs, the analytic
-- suite beyond ROW_NUMBER, INTERSECT and MINUS, multi-table insert, collections, REF
-- CURSOR, autonomous transactions, nested blocks with their own handlers, and a call
-- chain three deep.
--
-- Same rules as stress 1. Binds against corpus/dictionary.json - inventing tables would
-- make every reference a dangling boundary and test nothing. Ground truth written from
-- this source BEFORE the analyser was run over it.
--
-- Several units below are EXPECTED to be refused. That is not a gap in the test - a
-- refusal is a deliverable, and a corpus with nothing refusable in it cannot tell an
-- honest abstention from a silent one.

-- ============================================================================
-- 1. Recursive CTE. The hierarchy walks itself; the anchor and the recursive
--    arm are two different feeds into the same output columns.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_recursive_cte IS
BEGIN
    INSERT INTO dim_customer_hier (cust_id, parent_cust_id, depth, root_name)
    WITH hier (cust_id, parent_cust_id, depth, root_name) AS (
        SELECT h.cust_id,
               h.parent_cust_id,
               1,
               h.root_name
          FROM dim_customer_hier h
         WHERE h.parent_cust_id IS NULL
        UNION ALL
        SELECT c.cust_id,
               c.parent_cust_id,
               p.depth + 1,
               p.root_name
          FROM dim_customer_hier c
          JOIN hier p
            ON p.cust_id = c.parent_cust_id
         WHERE p.depth < 10
    )
    SELECT hier.cust_id,
           hier.parent_cust_id,
           hier.depth,
           hier.root_name
      FROM hier;

    COMMIT;
END s2_recursive_cte;
/

-- ============================================================================
-- 2. The analytic suite. RANK, DENSE_RANK, NTILE, FIRST_VALUE, LAST_VALUE and
--    a windowed SUM with an explicit frame. Every PARTITION BY and ORDER BY
--    here is a trap: none of them supplies a value.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_analytics_suite IS
BEGIN
    INSERT INTO fct_product_sales (
        period_month, product_id, category_name, gross_sales,
        net_sales, refund_total, units_sold, rank_in_month, prior_month
    )
    SELECT TRUNC(o.order_date, 'MM'),
           l.product_id,
           FIRST_VALUE(p.product_name) OVER (
               PARTITION BY l.product_id ORDER BY o.order_date
           ),
           SUM(l.line_amount) OVER (
               PARTITION BY l.product_id
               ORDER BY o.order_date
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ),
           LAST_VALUE(l.unit_price) OVER (PARTITION BY l.product_id ORDER BY o.order_date),
           NTILE(4) OVER (ORDER BY l.line_amount),
           l.quantity,
           RANK() OVER (PARTITION BY TRUNC(o.order_date, 'MM') ORDER BY l.line_amount DESC),
           DENSE_RANK() OVER (ORDER BY o.order_date)
      FROM stg_orders o
      JOIN stg_order_lines l
        ON l.order_id = o.order_id
      JOIN stg_products p
        ON p.product_id = l.product_id
     WHERE o.currency = 'GBP';

    COMMIT;
END s2_analytics_suite;
/

-- ============================================================================
-- 3. INTERSECT and MINUS at the top level. Stress 1 found that a top-level
--    UNION ALL is refused as "INSERT ... VALUES"; these are the same shape and
--    should fail the same way. Included to see whether the failure is uniform
--    across set operators or specific to UNION.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_intersect_minus IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT s.cust_id, s.last_login
      FROM stg_customer s
     WHERE s.status_code = 'A'
    INTERSECT
    SELECT r.cust_id, SYSDATE
      FROM stg_returns r
     WHERE r.status_code = 'DONE';

    INSERT INTO gtt_stage (cust_id, period_month, amount)
    SELECT o.cust_id, TRUNC(o.order_date, 'MM'), o.gross_amount
      FROM stg_orders o
    MINUS
    SELECT g.cust_id, g.period_month, g.amount
      FROM gtt_stage g;

    COMMIT;
END s2_intersect_minus;
/

-- ============================================================================
-- 4. Multi-table INSERT. One SELECT feeding two different targets, with a
--    conditional arm. Nothing in the phase-0 corpus has this shape.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_multi_table_insert IS
BEGIN
    INSERT ALL
        WHEN gross_amount > 100 THEN
            INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
            VALUES (cust_id, period_month, gross_amount, 1)
        WHEN gross_amount <= 100 THEN
            INTO gtt_stage (cust_id, period_month, amount)
            VALUES (cust_id, period_month, gross_amount)
    SELECT o.cust_id            AS cust_id,
           TRUNC(o.order_date, 'MM') AS period_month,
           o.gross_amount       AS gross_amount
      FROM stg_orders o
     WHERE o.currency = 'GBP';

    COMMIT;
END s2_multi_table_insert;
/

-- ============================================================================
-- 5. MERGE with a DELETE arm. The delete does not write lineage, but it must
--    not be mistaken for one, and the update arm's WHERE is a real filter.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_merge_with_delete(p_cutoff DATE) IS
BEGIN
    MERGE INTO dim_customer d
    USING (
        SELECT s.cust_id,
               s.region,
               s.email,
               s.last_login
          FROM stg_customer s
         WHERE s.last_login >= p_cutoff
    ) src
       ON (d.cust_id = src.cust_id)
     WHEN MATCHED THEN
        UPDATE SET d.email  = src.email,
                   d.region = src.region
        WHERE src.last_login IS NOT NULL
        DELETE WHERE d.is_active = 0
     WHEN NOT MATCHED THEN
        INSERT (cust_id, region, email)
        VALUES (src.cust_id, src.region, src.email);

    COMMIT;
END s2_merge_with_delete;
/

-- ============================================================================
-- 6. Subquery forms. IN, NOT IN, ANY, ALL and a scalar subquery in the WHERE.
--    Each decides WHICH rows, none supplies a value - the error this project
--    has now found four separate times.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_subquery_forms IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT s.cust_id, s.last_login
      FROM stg_customer s
     WHERE s.cust_id IN (SELECT o.cust_id FROM stg_orders o WHERE o.currency = 'GBP')
       AND s.cust_id NOT IN (SELECT r.cust_id FROM stg_returns r WHERE r.status_code = 'DONE')
       AND s.signup_date > ANY (SELECT p.effective_from FROM ref_policy p)
       AND s.last_login >= ALL (SELECT g.period_month FROM gtt_stage g)
       AND s.status_code = (SELECT MAX(pr.status_code) FROM stg_products pr);

    COMMIT;
END s2_subquery_forms;
/

-- ============================================================================
-- 7. String and date functions. Every one of these is a DERIVED transform, and
--    calling any of them identity would be a miss under ADR-0001 4.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_scalar_functions IS
BEGIN
    INSERT INTO dw_dim_customer_v2 (cust_id, region, is_active, email, lifetime_value)
    SELECT s.cust_id,
           SUBSTR(s.region, 1, 2),
           DECODE(s.status_code, 'A', 1, 0),
           REPLACE(LOWER(s.email), ' ', ''),
           MONTHS_BETWEEN(SYSDATE, s.signup_date)
      FROM stg_customer s
     WHERE INSTR(s.email, '@') > 0
       AND EXTRACT(YEAR FROM s.signup_date) >= 2020
       AND s.last_login >= ADD_MONTHS(LAST_DAY(SYSDATE), -12);

    COMMIT;
END s2_scalar_functions;
/

-- ============================================================================
-- 8. Nested CASE inside COALESCE inside NULLIF. The transform class is the
--    STRONGEST on the path, so all of these are conditional, not derived.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_nested_expressions IS
BEGIN
    INSERT INTO dim_customer (cust_id, region, is_active, lifetime_value)
    SELECT s.cust_id,
           NVL(NULLIF(s.region, 'XX'), 'UNKNOWN'),
           CASE
               WHEN s.status_code = 'A' THEN
                   CASE WHEN s.last_login IS NULL THEN 0 ELSE 1 END
               WHEN s.status_code = 'P' THEN 0
               ELSE -1
           END,
           COALESCE(GREATEST(o.gross_amount, o.discount_amt), 0)
      FROM stg_customer s
      LEFT JOIN stg_orders o
        ON o.cust_id = s.cust_id;

    COMMIT;
END s2_nested_expressions;
/

-- ============================================================================
-- 9. UPDATE driven by a correlated subquery. The subquery supplies the VALUE;
--    its correlation predicate decides which row. Two different claims from
--    one construct, and conflating them is the b0_05 error.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_update_correlated IS
BEGIN
    UPDATE dim_customer d
       SET d.lifetime_value = (SELECT NVL(SUM(o.gross_amount - o.discount_amt), 0)
                                 FROM stg_orders o
                                WHERE o.cust_id = d.cust_id),
           d.is_active = 1
     WHERE EXISTS (SELECT 1
                     FROM stg_order_lines l
                     JOIN stg_orders o2 ON o2.order_id = l.order_id
                    WHERE o2.cust_id = d.cust_id);

    COMMIT;
END s2_update_correlated;
/

-- ============================================================================
-- 10. DELETE with a subquery. No lineage is written, but the predicate columns
--     are still a real dependency of the resulting table state.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_delete_with_subquery IS
BEGIN
    DELETE FROM fct_revenue_stage f
     WHERE f.cust_id IN (SELECT s.cust_id
                           FROM stg_customer s
                          WHERE s.status_code = 'X');

    COMMIT;
END s2_delete_with_subquery;
/

-- ============================================================================
-- 11. Collections. BULK COLLECT with LIMIT inside a loop, then FORALL with
--     SAVE EXCEPTIONS. The values cross into DML through a collection.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_bulk_limit IS
    TYPE t_row IS RECORD (
        cust_id    NUMBER,
        last_login DATE
    );
    TYPE t_rows IS TABLE OF t_row;
    v_batch t_rows;

    CURSOR c_src IS
        SELECT s.cust_id, s.last_login
          FROM stg_customer s
         WHERE s.status_code = 'A';
BEGIN
    OPEN c_src;
    LOOP
        FETCH c_src BULK COLLECT INTO v_batch LIMIT 100;
        EXIT WHEN v_batch.COUNT = 0;

        FORALL i IN 1 .. v_batch.COUNT SAVE EXCEPTIONS
            INSERT INTO tmp_recent (cust_id, last_login)
            VALUES (v_batch(i).cust_id, v_batch(i).last_login);

        COMMIT;
    END LOOP;
    CLOSE c_src;
END s2_bulk_limit;
/

-- ============================================================================
-- 12. REF CURSOR opened over dynamic text. The statement is constant here, so
--     it is recoverable - but the cursor handle carries no data and a def-use
--     edge through it would say nothing true.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_ref_cursor IS
    TYPE t_ref IS REF CURSOR;
    v_cur   t_ref;
    v_sql   VARCHAR2(4000);
    v_id    NUMBER;
    v_login DATE;
BEGIN
    v_sql := 'SELECT cust_id, last_login FROM stg_customer WHERE status_code = ''A''';

    OPEN v_cur FOR v_sql;
    LOOP
        FETCH v_cur INTO v_id, v_login;
        EXIT WHEN v_cur%NOTFOUND;

        UPDATE dim_customer
           SET is_active = 1
         WHERE cust_id = v_id;
    END LOOP;
    CLOSE v_cur;

    COMMIT;
END s2_ref_cursor;
/

-- ============================================================================
-- 13. Nested anonymous block with its own exception handler, and a RAISE that
--     re-throws to the outer one. Two handlers, two different writes, and only
--     one of them is on the happy path.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_nested_blocks(p_region VARCHAR2) IS
    v_days NUMBER;
BEGIN
    BEGIN
        SELECT r.window_days INTO v_days
          FROM ref_policy r
         WHERE r.region = p_region;
    EXCEPTION
        WHEN NO_DATA_FOUND THEN
            v_days := 30;

            INSERT INTO dw_audit_log (cust_id, changed_by, changed_at)
            VALUES (-1, USER, SYSDATE);
            RAISE;
    END;

    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT s.cust_id, s.last_login
      FROM stg_customer s
     WHERE s.last_login >= SYSDATE - v_days
       AND s.region = p_region;

    COMMIT;

EXCEPTION
    WHEN OTHERS THEN
        INSERT INTO dw_audit_log (cust_id, changed_by, changed_at)
        VALUES (-2, USER, SYSDATE);
        ROLLBACK;
        RAISE;
END s2_nested_blocks;
/

-- ============================================================================
-- 14. Autonomous transaction. The write commits independently of the caller,
--     so a rollback outside does not undo it - which changes what "this ran"
--     means for anything downstream.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_autonomous_log(p_cust_id NUMBER) IS
    PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
    INSERT INTO dw_audit_log (cust_id, changed_by, changed_at)
    VALUES (p_cust_id, USER, SYSDATE);

    COMMIT;
END s2_autonomous_log;
/

-- ============================================================================
-- 15. SELECT ... FOR UPDATE and an explicit LOCK TABLE. Neither writes, but a
--     FOR UPDATE cursor is where row-by-row writes usually follow.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_locking IS
    CURSOR c_lock IS
        SELECT d.cust_id, d.lifetime_value
          FROM dim_customer d
         WHERE d.is_active = 1
           FOR UPDATE OF d.lifetime_value;

    v_id  NUMBER;
    v_val NUMBER;
BEGIN
    LOCK TABLE dim_customer IN ROW SHARE MODE;

    OPEN c_lock;
    LOOP
        FETCH c_lock INTO v_id, v_val;
        EXIT WHEN c_lock%NOTFOUND;

        UPDATE dim_customer
           SET lifetime_value = v_val
         WHERE CURRENT OF c_lock;
    END LOOP;
    CLOSE c_lock;

    COMMIT;
END s2_locking;
/

-- ============================================================================
-- 16. PIVOT with a static column list. Stress 1 never reached PIVOT; u1 covers
--     the subquery form, which is refused. This is the decidable one.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_pivot_static IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, gross_sales, net_sales)
    SELECT period_month, product_id, gbp, usd
      FROM (
            SELECT TRUNC(o.order_date, 'MM') AS period_month,
                   l.product_id              AS product_id,
                   o.currency                AS currency,
                   l.line_amount             AS line_amount
              FROM stg_orders o
              JOIN stg_order_lines l
                ON l.order_id = o.order_id
           )
     PIVOT (SUM(line_amount) FOR currency IN ('GBP' AS gbp, 'USD' AS usd));

    COMMIT;
END s2_pivot_static;
/

-- ============================================================================
-- 17. Hierarchical query. CONNECT BY generates rows by walking a relationship,
--     so a column's value can come from a row that does not exist in any base
--     table. Expected to be refused, in a package rather than in isolation.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_hierarchical IS
BEGIN
    INSERT INTO dim_customer_hier (cust_id, parent_cust_id, depth, root_name)
    SELECT h.cust_id,
           h.parent_cust_id,
           LEVEL,
           CONNECT_BY_ROOT h.root_name
      FROM dim_customer_hier h
     START WITH h.parent_cust_id IS NULL
   CONNECT BY PRIOR h.cust_id = h.parent_cust_id;

    COMMIT;
END s2_hierarchical;
/

-- ============================================================================
-- 18. DB link and synonym in one statement. The link target is out of coverage
--     entirely; the synonym resolves to an object neither name mentions.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_remote_and_synonym IS
BEGIN
    INSERT INTO customer_target (cust_id, region, is_active, email, lifetime_value)
    SELECT r.cust_id,
           r.region,
           1,
           r.email,
           0
      FROM remote_customer@crm_link r
     WHERE r.region IS NOT NULL;

    COMMIT;
END s2_remote_and_synonym;
/

-- ============================================================================
-- 19. A package with overloaded procedures, package state, and a call chain
--     three deep. load -> stage -> publish, with state set at the top.
-- ============================================================================
CREATE OR REPLACE PACKAGE pkg_s2_pipeline AS
    PROCEDURE load(p_region VARCHAR2);
    PROCEDURE load(p_region VARCHAR2, p_cutoff DATE);
    PROCEDURE run_all(p_region VARCHAR2);
END pkg_s2_pipeline;
/

CREATE OR REPLACE PACKAGE BODY pkg_s2_pipeline AS
    g_region  VARCHAR2(10);
    g_cutoff  DATE;
    g_rows    NUMBER := 0;

    -- Level 3: writes the final table from the staging table.
    PROCEDURE publish IS
    BEGIN
        INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
        SELECT g.cust_id, g.period_month, g.amount, 1
          FROM gtt_stage g
         WHERE g.period_month >= g_cutoff;
    END publish;

    -- Level 2: fills the staging table, then calls publish.
    PROCEDURE stage IS
    BEGIN
        INSERT INTO gtt_stage (cust_id, period_month, amount)
        SELECT o.cust_id,
               TRUNC(o.order_date, 'MM'),
               SUM(o.gross_amount - NVL(o.discount_amt, 0))
          FROM stg_orders o
          JOIN stg_customer s
            ON s.cust_id = o.cust_id
         WHERE s.region = g_region
         GROUP BY o.cust_id, TRUNC(o.order_date, 'MM');

        g_rows := SQL%ROWCOUNT;

        publish;
    END stage;

    -- Level 1, overload A: defaults the cutoff.
    PROCEDURE load(p_region VARCHAR2) IS
    BEGIN
        g_region := p_region;
        g_cutoff := TRUNC(SYSDATE, 'YYYY');
        stage;
    END load;

    -- Level 1, overload B: caller supplies the cutoff. Same name, different
    -- signature, and the two must not be summarised as one unit.
    PROCEDURE load(p_region VARCHAR2, p_cutoff DATE) IS
    BEGIN
        g_region := p_region;
        g_cutoff := p_cutoff;
        stage;
    END load;

    PROCEDURE run_all(p_region VARCHAR2) IS
    BEGIN
        load(p_region);
        COMMIT;
    END run_all;
END pkg_s2_pipeline;
/

-- ============================================================================
-- 20. A recursive procedure. Interprocedural analysis must terminate, and the
--     depth cap must be declared rather than silently truncating.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_recursive_walk(p_cust_id NUMBER, p_depth NUMBER) IS
    v_parent NUMBER;
BEGIN
    IF p_depth > 10 THEN
        RETURN;
    END IF;

    SELECT h.parent_cust_id INTO v_parent
      FROM dim_customer_hier h
     WHERE h.cust_id = p_cust_id;

    INSERT INTO dim_customer_hier (cust_id, parent_cust_id, depth)
    VALUES (p_cust_id, v_parent, p_depth);

    IF v_parent IS NOT NULL THEN
        s2_recursive_walk(v_parent, p_depth + 1);
    END IF;
END s2_recursive_walk;
/

-- ============================================================================
-- 21. Dynamic SQL assembled inside a loop. The value is constant on the first
--     pass and different on the second, so it is not constant on every path -
--     which is the MUST property, and it must be refused.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_dynamic_in_loop IS
    v_sql VARCHAR2(4000);
BEGIN
    FOR i IN 1 .. 3 LOOP
        v_sql := 'INSERT INTO gtt_stage (cust_id, period_month, amount) '
              || 'SELECT cust_id, TRUNC(order_date, ''MM''), gross_amount '
              || 'FROM stg_orders WHERE cust_id = ' || TO_CHAR(i);
        EXECUTE IMMEDIATE v_sql;
    END LOOP;

    COMMIT;
END s2_dynamic_in_loop;
/

-- ============================================================================
-- 22. A view read three ways in one statement: through the chain, through the
--     synonym, and through the base table directly. All three must land on the
--     same base columns or the resolver disagrees with itself.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_three_way_resolution IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT v.cust_id, v.last_login
      FROM v_cust_l3 v
      JOIN v_recent_raw w
        ON w.cust_id = v.cust_id
      JOIN stg_customer s
        ON s.cust_id = v.cust_id
     WHERE v.status_flag = 1
       AND w.last_login IS NOT NULL
       AND s.status_code = 'A';

    COMMIT;
END s2_three_way_resolution;
/

-- ============================================================================
-- 23. UNPIVOT and LISTAGG. UNPIVOT turns columns into rows, so one output
--     column is fed by several input columns at once - the reverse of every
--     other construct here, and the one most likely to be under-reported.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_unpivot_listagg IS
BEGIN
    INSERT INTO gtt_stage (cust_id, period_month, amount)
    SELECT u.cust_id, TRUNC(SYSDATE, 'MM'), u.amount
      FROM (
            SELECT f.cust_id,
                   f.net_amount   AS net_amount,
                   f.order_count  AS order_count
              FROM fct_revenue f
           )
    UNPIVOT (amount FOR measure IN (net_amount, order_count)) u;

    INSERT INTO dim_customer_hier (cust_id, parent_cust_id, depth, root_name)
    SELECT s.cust_id,
           NULL,
           0,
           LISTAGG(p.product_name, ',') WITHIN GROUP (ORDER BY p.product_name)
      FROM stg_customer s
      JOIN stg_orders o     ON o.cust_id = s.cust_id
      JOIN stg_order_lines l ON l.order_id = o.order_id
      JOIN stg_products p    ON p.product_id = l.product_id
     GROUP BY s.cust_id;

    COMMIT;
END s2_unpivot_listagg;
/

-- ============================================================================
-- 24. KEEP DENSE_RANK FIRST and a CASE inside GROUP BY. The aggregate picks a
--     value from a different row than the one the grouping names, which is a
--     shape no phase-0 package contains.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_keep_dense_rank IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, category_name, gross_sales)
    SELECT TRUNC(o.order_date, 'MM'),
           l.product_id,
           MAX(p.product_name) KEEP (DENSE_RANK FIRST ORDER BY l.line_amount DESC),
           SUM(l.line_amount)
      FROM stg_orders o
      JOIN stg_order_lines l ON l.order_id = o.order_id
      JOIN stg_products p    ON p.product_id = l.product_id
     GROUP BY TRUNC(o.order_date, 'MM'),
              l.product_id,
              CASE WHEN o.currency = 'GBP' THEN 1 ELSE 0 END;

    COMMIT;
END s2_keep_dense_rank;
/

-- ============================================================================
-- 25. ROWNUM and FETCH FIRST. Row limiting is a filter on the result set, not
--     on any column, so neither should invent a column-level dependency.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_row_limiting IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT s.cust_id, s.last_login
      FROM stg_customer s
     WHERE ROWNUM <= 100
       AND s.status_code = 'A';

    INSERT INTO gtt_stage (cust_id, period_month, amount)
    SELECT o.cust_id, TRUNC(o.order_date, 'MM'), o.gross_amount
      FROM stg_orders o
     ORDER BY o.gross_amount DESC
     FETCH FIRST 50 ROWS ONLY;

    COMMIT;
END s2_row_limiting;
/

-- ============================================================================
-- 26. TRUNCATE then reload. TRUNCATE is DDL and writes no lineage, but the
--     table's entire prior contents are gone - a fact about the table that a
--     DML-only reading never sees, and the s4 shape in a different form.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_truncate_reload IS
BEGIN
    EXECUTE IMMEDIATE 'TRUNCATE TABLE gtt_stage';

    INSERT INTO gtt_stage (cust_id, period_month, amount)
    SELECT o.cust_id, TRUNC(o.order_date, 'MM'), SUM(o.gross_amount)
      FROM stg_orders o
     GROUP BY o.cust_id, TRUNC(o.order_date, 'MM');

    COMMIT;
END s2_truncate_reload;
/

-- ============================================================================
-- 27. MODEL clause. Spreadsheet-style cell rules compute values from other
--     cells of the same result set, so a column's source can be a row that
--     does not exist in any base table. Expected to be refused.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_model_clause IS
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT cust_id, period_month, net_amount, 1
      FROM (
            SELECT o.cust_id, TRUNC(o.order_date, 'MM') AS period_month,
                   o.gross_amount AS net_amount
              FROM stg_orders o
           )
     MODEL
        PARTITION BY (cust_id)
        DIMENSION BY (period_month)
        MEASURES (net_amount)
        RULES (
            net_amount[ANY] = net_amount[CV()] * 1.1
        );

    COMMIT;
END s2_model_clause;
/

-- ============================================================================
-- 28. GOTO across a labelled block, and a variable assigned on both paths.
--     Control flow the CFG has to represent or the guard on the write below
--     is wrong rather than merely absent.
-- ============================================================================
CREATE OR REPLACE PROCEDURE s2_goto_flow(p_strict NUMBER) IS
    v_flag NUMBER;
BEGIN
    IF p_strict = 1 THEN
        v_flag := 1;
        GOTO do_write;
    END IF;

    v_flag := 0;

    <<do_write>>
    UPDATE dim_customer
       SET is_active = v_flag
     WHERE region = 'EU';

    COMMIT;
END s2_goto_flow;
/
