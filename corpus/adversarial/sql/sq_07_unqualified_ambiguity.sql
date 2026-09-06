-- COMPLEX SQL 7 / unqualified columns that are ambiguous ACROSS THE SCHEMA
--
-- FINDING, recorded while building this case: Oracle REJECTS a genuinely ambiguous
-- unqualified reference with ORA-00918. The first version of this file joined
-- stg_order_lines and stg_returns and referred to `product_id` unqualified; it would not
-- compile. So that particular silent failure does not exist in Oracle - the compiler
-- catches it, and any code in a real estate has already survived that check.
--
-- The real risk is narrower and this is it: a column that is UNAMBIGUOUS within the
-- statement's FROM scope but appears on several other tables in the schema.
--
--   cust_id      also on stg_customer, stg_returns, dim_customer, dim_customer_hier
--   status_code  also on stg_customer, stg_returns
--   quantity     only on stg_order_lines
--
-- None of these is qualified below, and Oracle resolves every one correctly because only
-- one table in THIS join has it. An analyser that resolves names against the whole
-- dictionary rather than against the statement's own FROM scope will bind some of them
-- to the wrong table - and the result parses, runs, and looks entirely normal.
--
-- Correct resolution:
--   cust_id     -> STG_ORDERS      (NOT stg_customer, which is not in this join)
--   status_code -> STG_PRODUCTS    (NOT stg_returns, which is not in this join)
--   quantity    -> STG_ORDER_LINES

CREATE OR REPLACE PROCEDURE sq_unqualified IS
BEGIN
    INSERT INTO fct_product_sales (period_month, product_id, gross_sales, units_sold)
    SELECT TRUNC(order_date, 'MM'),
           p.product_id,
           SUM(line_amount),
           SUM(quantity)
      FROM stg_orders o
      JOIN stg_order_lines l ON l.order_id = o.order_id
      JOIN stg_products p    ON p.product_id = l.product_id
     WHERE status_code = 1
       AND cust_id IS NOT NULL
     GROUP BY TRUNC(order_date, 'MM'), p.product_id;

    COMMIT;
END sq_unqualified;
/
