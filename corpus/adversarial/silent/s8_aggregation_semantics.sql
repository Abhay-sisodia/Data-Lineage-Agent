-- SILENT FAILURE 8 / aggregation semantics
--
-- Both procedures below produce a CORRECT edge:
--     stg_orders.gross_amount -> fct_revenue.net_amount
-- and the two edges mean completely different things. One copies a single row's
-- value. The other is a SUM over a whole customer-month. Same edge, same endpoints,
-- entirely different meaning to anyone reading the filing.
--
-- Defence: tag every edge with a transform class - identity, derived, aggregated,
-- conditional. Cheap to do now, impossible to backfill later, because reconstructing
-- it means re-analysing every statement ever recorded.
--
-- The register's phrasing is the test: an is_active derived from a SUM(...) is a
-- correct edge carrying entirely different meaning from a row-level copy.

-- IDENTITY: one row in, one row out, value copied unchanged.
CREATE OR REPLACE PROCEDURE s8_identity_copy IS
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT cust_id,
           order_date,
           gross_amount,   -- identity
           1
      FROM stg_orders;

    COMMIT;
END s8_identity_copy;
/

-- AGGREGATED: many rows collapse into one. Same endpoints, different fact.
CREATE OR REPLACE PROCEDURE s8_aggregated_copy IS
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT cust_id,
           TRUNC(order_date, 'MM'),
           SUM(gross_amount),   -- aggregated
           COUNT(*)
      FROM stg_orders
     GROUP BY cust_id, TRUNC(order_date, 'MM');

    COMMIT;
END s8_aggregated_copy;
/

-- CONDITIONAL: the value depends on a branch inside the expression.
CREATE OR REPLACE PROCEDURE s8_conditional_copy IS
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT cust_id,
           TRUNC(order_date, 'MM'),
           CASE WHEN currency = 'USD' THEN gross_amount * 0.92
                ELSE gross_amount
           END,                 -- conditional + derived
           1
      FROM stg_orders;

    COMMIT;
END s8_conditional_copy;
/
