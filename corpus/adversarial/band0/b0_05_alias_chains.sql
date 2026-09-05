-- Band 0 / alias chains
-- Why it's hard: a column changes name four times between source and target.
-- Approach: rename tracking through the IR.
--
-- stg_orders.gross_amount -> gross -> amt -> net_amount, with a derivation applied
-- part-way. The transform class matters as much as the edge: this is a DERIVED edge,
-- not an identity copy, and an aggregation sits on top of it. An edge that is correct
-- but tagged as identity carries entirely the wrong meaning.

CREATE OR REPLACE PROCEDURE b0_alias_chains IS
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT customer,
           month_start,
           SUM(amt),
           COUNT(*)
      FROM (
            SELECT buyer            AS customer,
                   TRUNC(dt, 'MM')  AS month_start,
                   gross - disc     AS amt
              FROM (
                    SELECT cust_id       AS buyer,
                           order_date    AS dt,
                           gross_amount  AS gross,
                           discount_amt  AS disc
                      FROM stg_orders
                   )
           )
     GROUP BY customer, month_start;

    COMMIT;
END b0_alias_chains;
/
