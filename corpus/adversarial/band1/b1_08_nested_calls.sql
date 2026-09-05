-- Band 1 / nested procedure and function calls
-- Why it's hard: logic hides one level down. A scalar UDF inside a SELECT is
-- particularly nasty - it looks like a column expression but contains a query.
-- Approach: summarise each callee once, inline the summary at every call site.
--
-- fn_net_amount reads stg_orders internally. Without inlining the summary, the edge
-- into fct_revenue.net_amount appears to come from nowhere - a function call with no
-- visible source. The call depth here is 3, comfortably inside the default cap of 6;
-- exceeding the cap must emit a boundary node, never a guess.

CREATE OR REPLACE FUNCTION fn_discount_rate(p_order_id NUMBER) RETURN NUMBER IS
    v_rate NUMBER;
BEGIN
    SELECT CASE WHEN gross_amount = 0 THEN 0
                ELSE discount_amt / gross_amount
           END
      INTO v_rate
      FROM stg_orders
     WHERE order_id = p_order_id;

    RETURN v_rate;
END fn_discount_rate;
/

CREATE OR REPLACE FUNCTION fn_net_amount(p_order_id NUMBER) RETURN NUMBER IS
    v_gross NUMBER;
BEGIN
    SELECT gross_amount
      INTO v_gross
      FROM stg_orders
     WHERE order_id = p_order_id;

    RETURN v_gross * (1 - fn_discount_rate(p_order_id));
END fn_net_amount;
/

CREATE OR REPLACE PROCEDURE b1_nested_calls IS
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT cust_id,
           TRUNC(order_date, 'MM'),
           fn_net_amount(order_id),
           1
      FROM stg_orders;

    COMMIT;
END b1_nested_calls;
/
