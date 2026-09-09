-- STRESS 1 / wide construct coverage
--
-- Not part of the phase-0 corpus, and deliberately so: adding it under corpus/ would
-- change the corpus fingerprint and every number in the signed measurement. This lives in
-- stress/ with its own runner, so the phase-0 figures stay exactly where they were signed.
--
-- The point is BREADTH, not difficulty for its own sake. The phase-0 corpus was built one
-- construct per package, which is right for attributing a failure and wrong for finding
-- the ones that only appear when constructs are stacked: a window function inside a CTE
-- that feeds a MERGE, an exception handler around a cursor that writes a temp table that
-- fires a trigger.
--
-- Everything binds against the captured dictionary (corpus/dictionary.json). Inventing
-- tables would make every reference a dangling boundary and test nothing.
--
-- Ground truth for this file was written from the source BEFORE the analyser was run over
-- it, per the rule that has held since week 1. Where the analyser disagrees, the argument
-- is settled against the convention as it stood, not against whichever answer is nicer.

CREATE OR REPLACE PROCEDURE stress_cte_window IS
BEGIN
    -- CTE chain feeding a window function feeding an aggregate insert.
    -- Constructs: WITH x2, JOIN, GROUP BY, SUM, COUNT, ROW_NUMBER OVER PARTITION BY,
    -- LAG, TRUNC, NVL, a LEFT OUTER JOIN and a HAVING clause.
    INSERT INTO fct_product_sales (
        period_month, product_id, category_name, gross_sales,
        net_sales, refund_total, units_sold, rank_in_month, prior_month
    )
    WITH monthly AS (
        SELECT TRUNC(o.order_date, 'MM')      AS period_month,
               l.product_id                   AS product_id,
               SUM(l.line_amount)             AS gross,
               SUM(l.quantity)                AS units
          FROM stg_orders o
          JOIN stg_order_lines l
            ON l.order_id = o.order_id
         WHERE o.currency = 'GBP'
         GROUP BY TRUNC(o.order_date, 'MM'), l.product_id
        HAVING SUM(l.line_amount) > 0
    ),
    refunded AS (
        SELECT r.product_id            AS product_id,
               SUM(r.refund_amount)    AS refunds
          FROM stg_returns r
         WHERE r.status_code = 'DONE'
         GROUP BY r.product_id
    )
    SELECT m.period_month,
           m.product_id,
           c.category_name,
           m.gross,
           m.gross - NVL(f.refunds, 0),
           NVL(f.refunds, 0),
           m.units,
           ROW_NUMBER() OVER (PARTITION BY m.period_month ORDER BY m.gross DESC),
           LAG(m.gross) OVER (PARTITION BY m.product_id ORDER BY m.period_month)
      FROM monthly m
      LEFT JOIN refunded f
        ON f.product_id = m.product_id
      JOIN stg_products p
        ON p.product_id = m.product_id
      JOIN stg_categories c
        ON c.category_id = p.category_id;

    COMMIT;
END stress_cte_window;
/

CREATE OR REPLACE PROCEDURE stress_set_ops IS
BEGIN
    -- Set operations bind BY POSITION, not by name. The aliases below disagree with the
    -- target columns on purpose - the second arm's `cust_id` lands in period_month if the
    -- analyser reads names instead of positions.
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT o.cust_id, TRUNC(o.order_date, 'MM'), SUM(o.gross_amount), COUNT(*)
      FROM stg_orders o
     WHERE o.currency = 'GBP'
     GROUP BY o.cust_id, TRUNC(o.order_date, 'MM')
    UNION ALL
    SELECT r.cust_id, TRUNC(SYSDATE, 'MM'), -SUM(r.refund_amount), COUNT(*)
      FROM stg_returns r
     WHERE r.status_code = 'DONE'
     GROUP BY r.cust_id;

    COMMIT;
END stress_set_ops;
/

CREATE OR REPLACE PROCEDURE stress_merge_upsert(p_region VARCHAR2) IS
BEGIN
    -- MERGE: the two arms are different facts and must be analysed separately, then
    -- deduplicated where they agree.
    MERGE INTO dim_customer d
    USING (
        SELECT s.cust_id,
               s.region,
               s.email,
               CASE WHEN s.status_code = 'A' THEN 1 ELSE 0 END AS active_flag
          FROM stg_customer s
         WHERE s.region = p_region
    ) src
       ON (d.cust_id = src.cust_id)
     WHEN MATCHED THEN
        UPDATE SET d.email          = src.email,
                   d.is_active      = src.active_flag
     WHEN NOT MATCHED THEN
        INSERT (cust_id, region, email, is_active)
        VALUES (src.cust_id, src.region, src.email, src.active_flag);

    COMMIT;
END stress_merge_upsert;
/

CREATE OR REPLACE PROCEDURE stress_view_and_synonym IS
BEGIN
    -- v_cust_l3 -> v_cust_l2 -> v_cust_l1 -> stg_customer, and customer_target is a
    -- synonym for dw_dim_customer_v2. Neither name that appears in this statement is the
    -- object the lineage should land on.
    INSERT INTO customer_target (cust_id, region, is_active, email, lifetime_value)
    SELECT v.cust_id,
           v.region,
           CASE WHEN v.status_flag = 'ACTIVE' THEN 1 ELSE 0 END,
           NULL,
           0
      FROM v_cust_l3 v
     WHERE v.last_login IS NOT NULL;

    COMMIT;
END stress_view_and_synonym;
/

CREATE OR REPLACE PROCEDURE stress_cursor_and_handler(p_region VARCHAR2) IS
    CURSOR c_cust IS
        SELECT s.cust_id, s.last_login, s.status_code
          FROM stg_customer s
         WHERE s.region = p_region;

    v_cust_id    NUMBER;
    v_last_login DATE;
    v_status     VARCHAR2(10);
    v_seen       NUMBER := 0;
BEGIN
    OPEN c_cust;
    LOOP
        FETCH c_cust INTO v_cust_id, v_last_login, v_status;
        EXIT WHEN c_cust%NOTFOUND;

        v_seen := v_seen + 1;

        -- Writing tmp_recent fires trg_recent_audit, which updates dim_customer.
        -- Nothing in this source mentions that.
        IF v_status = 'A' THEN
            INSERT INTO tmp_recent (cust_id, last_login)
            VALUES (v_cust_id, v_last_login);
        END IF;
    END LOOP;
    CLOSE c_cust;

    -- WHILE loop writing a temp table each pass.
    WHILE v_seen > 0 LOOP
        INSERT INTO gtt_stage (cust_id, period_month, amount)
        SELECT o.cust_id, TRUNC(o.order_date, 'MM'), SUM(o.gross_amount)
          FROM stg_orders o
         WHERE o.cust_id = v_cust_id
         GROUP BY o.cust_id, TRUNC(o.order_date, 'MM');

        v_seen := v_seen - 1;
    END LOOP;

    COMMIT;

EXCEPTION
    WHEN NO_DATA_FOUND THEN
        -- An error path that WRITES. This is lineage, and it appears nowhere on the
        -- happy path.
        INSERT INTO dw_audit_log (cust_id, changed_by, changed_at)
        VALUES (v_cust_id, USER, SYSDATE);
        ROLLBACK;
END stress_cursor_and_handler;
/

CREATE OR REPLACE PROCEDURE stress_cursor_for_loop IS
BEGIN
    -- Cursor FOR loop: rec is a RECORD whose shape is the query's select list, not a
    -- row of any table. `rec.email` is not a column of whatever happens to be in scope.
    FOR rec IN (
        SELECT s.cust_id   AS cust_id,
               s.email     AS email,
               s.region    AS region
          FROM stg_customer s
         WHERE s.status_code = 'A'
    ) LOOP
        UPDATE dim_customer d
           SET d.email = rec.email
         WHERE d.cust_id = rec.cust_id;
    END LOOP;

    COMMIT;
END stress_cursor_for_loop;
/

CREATE OR REPLACE PROCEDURE stress_self_join_and_scalar IS
BEGIN
    -- Self-join: both sides are the same relation and the IR has no row identity, so the
    -- distinction between the two aliases is not representable. Plus a scalar subquery in
    -- the select list and a correlated predicate that decides WHICH row, not what value.
    INSERT INTO dim_customer_hier (cust_id, parent_cust_id, depth, root_name)
    SELECT child.cust_id,
           parent.cust_id,
           1,
           (SELECT MAX(p.product_name)
              FROM stg_products p
             WHERE p.category_id = 1)
      FROM stg_customer child
      JOIN stg_customer parent
        ON parent.region = child.region
     WHERE child.signup_date > parent.signup_date
       AND EXISTS (SELECT 1
                     FROM stg_orders o
                    WHERE o.cust_id = child.cust_id);

    COMMIT;
END stress_self_join_and_scalar;
/

CREATE OR REPLACE PROCEDURE stress_transaction_control(p_strict NUMBER) IS
    v_count NUMBER;
BEGIN
    SAVEPOINT before_load;

    SELECT COUNT(*) INTO v_count FROM stg_orders;

    IF v_count = 0 THEN
        ROLLBACK TO before_load;
        RETURN;
    ELSIF p_strict = 1 THEN
        -- Guarded write: this edge only exists when p_strict = 1, and a regulator will
        -- ask about precisely that.
        INSERT INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
        SELECT o.cust_id, TRUNC(o.order_date, 'MM'),
               SUM(o.gross_amount - NVL(o.discount_amt, 0)), COUNT(*)
          FROM stg_orders o
         GROUP BY o.cust_id, TRUNC(o.order_date, 'MM');
    ELSE
        DELETE FROM fct_revenue_stage WHERE period_month < TRUNC(SYSDATE, 'YYYY');
    END IF;

    COMMIT;
END stress_transaction_control;
/

CREATE OR REPLACE PROCEDURE stress_dynamic_mixed(p_column VARCHAR2) IS
    v_sql_ok  VARCHAR2(4000);
    v_sql_bad VARCHAR2(4000);
BEGIN
    -- Constant on every path: assembled from literals only, so constant propagation
    -- folds it and the recovered text is analysed as ordinary AST evidence - at band 2,
    -- because the path still runs through EXECUTE IMMEDIATE.
    v_sql_ok := 'INSERT INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count) ';
    v_sql_ok := v_sql_ok || 'SELECT cust_id, TRUNC(order_date, ''MM''), SUM(gross_amount), COUNT(*) ';
    v_sql_ok := v_sql_ok || 'FROM stg_orders GROUP BY cust_id, TRUNC(order_date, ''MM'')';
    EXECUTE IMMEDIATE v_sql_ok;

    -- Not constant: a parameter decides the target column. Folding the literal fragments
    -- and guessing the rest is the exact plausible-wrong-answer failure to refuse.
    v_sql_bad := 'UPDATE dim_customer SET ' || p_column || ' = 0 WHERE region = ''EU''';
    EXECUTE IMMEDIATE v_sql_bad;

    COMMIT;
END stress_dynamic_mixed;
/

CREATE OR REPLACE PROCEDURE stress_bulk_operations IS
    TYPE t_ids IS TABLE OF NUMBER;
    v_ids t_ids;
BEGIN
    -- BULK COLLECT and FORALL. The values cross into the DML through a collection, which
    -- is a memory location the def-use analysis does not model. Refusing is the correct
    -- answer; inventing a chain through the collection would not be.
    SELECT cust_id BULK COLLECT INTO v_ids
      FROM stg_customer
     WHERE status_code = 'A';

    FORALL i IN 1 .. v_ids.COUNT
        INSERT INTO tmp_recent (cust_id, last_login)
        VALUES (v_ids(i), SYSDATE);

    COMMIT;

EXCEPTION
    WHEN OTHERS THEN NULL;  -- swallows everything; absence of an edge proves nothing
END stress_bulk_operations;
/

CREATE OR REPLACE PACKAGE pkg_stress_state AS
    PROCEDURE set_window(p_region VARCHAR2);
    PROCEDURE apply_window;
END pkg_stress_state;
/

CREATE OR REPLACE PACKAGE BODY pkg_stress_state AS
    -- Package state: written by one call, read by another, with nothing in either
    -- statement connecting them. This is the case that makes b1_07 hard, restated with a
    -- second reader so the cross-unit boundary has to hold for both.
    g_cutoff  DATE;
    g_region  VARCHAR2(10);

    PROCEDURE set_window(p_region VARCHAR2) IS
        v_days NUMBER;
    BEGIN
        SELECT r.window_days INTO v_days
          FROM ref_policy r
         WHERE r.region = p_region;

        g_cutoff := SYSDATE - v_days;
        g_region := p_region;
    END set_window;

    PROCEDURE apply_window IS
    BEGIN
        INSERT INTO tmp_recent (cust_id, last_login)
        SELECT s.cust_id, s.last_login
          FROM stg_customer s
         WHERE s.last_login >= g_cutoff
           AND s.region = g_region;

        COMMIT;
    END apply_window;
END pkg_stress_state;
/

CREATE OR REPLACE FUNCTION fn_stress_net(p_order_id NUMBER) RETURN NUMBER IS
    v_net NUMBER;
BEGIN
    -- A scalar UDF used inside a SELECT below. Its return value depends on the columns
    -- it reads, not on the columns passed as arguments.
    SELECT NVL(SUM(l.line_amount), 0) - NVL(MAX(o.discount_amt), 0)
      INTO v_net
      FROM stg_orders o
      JOIN stg_order_lines l
        ON l.order_id = o.order_id
     WHERE o.order_id = p_order_id;

    RETURN v_net;
END fn_stress_net;
/

CREATE OR REPLACE PROCEDURE stress_udf_caller IS
BEGIN
    -- The interprocedural case: fn_stress_net contributes stg_order_lines.line_amount and
    -- stg_orders.discount_amt, NOT stg_orders.order_id, which is only its argument.
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT o.cust_id,
           TRUNC(o.order_date, 'MM'),
           fn_stress_net(o.order_id),
           1
      FROM stg_orders o
     WHERE o.currency = 'GBP';

    COMMIT;
END stress_udf_caller;
/
