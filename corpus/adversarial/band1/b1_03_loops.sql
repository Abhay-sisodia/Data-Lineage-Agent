-- Band 1 / loops
-- Why it's hard: repeated writes, and the loop variable may select the target.
-- Approach: fixed-point iteration to a stable edge set, with a declared cap.
--
-- The nasty part is the second loop: the WRITE TARGET depends on the loop counter,
-- so the set of edges is not fixed by reading the statement. Analysis must converge
-- on the union of possible targets, or declare that it could not.

CREATE OR REPLACE PROCEDURE b1_loops IS
    v_total NUMBER := 0;
    v_col   VARCHAR2(30);
BEGIN
    -- Simple accumulation: value crosses iterations through a variable.
    FOR i IN 1 .. 12 LOOP
        SELECT NVL(SUM(gross_amount), 0) + v_total
          INTO v_total
          FROM stg_orders
         WHERE EXTRACT(MONTH FROM order_date) = i;
    END LOOP;

    UPDATE dim_customer
       SET lifetime_value = v_total
     WHERE cust_id = 1;

    -- WHILE loop whose body writes a table each pass.
    WHILE v_total > 100 LOOP
        INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
        SELECT cust_id, TRUNC(order_date, 'MM'), gross_amount - discount_amt, 1
          FROM stg_orders
         WHERE gross_amount < v_total;

        v_total := v_total / 2;
    END LOOP;

    COMMIT;
END b1_loops;
/
