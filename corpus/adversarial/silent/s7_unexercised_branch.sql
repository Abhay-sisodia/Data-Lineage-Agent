-- SILENT FAILURE 7 / unexercised branches
--
-- The EU path runs constantly and scores Tier B. The APAC path exists in the code,
-- has never run inside the log retention window, and therefore looks weak or absent -
-- despite being just as provably present in the source.
--
-- Defence: make "never observed executing" its own AXIS, not a low tier. An edge can
-- be Tier A - provably in the code - AND never observed running. That combination is
-- itself a finding, and nobody else reports it.
--
-- Two readings of the same fact, and they mean opposite things:
--   * dead code, safe to remove          (a migration finding)
--   * a year-end path that has not run yet (removing it would be a disaster)
-- The system must present the observation and let a human rule on which it is. This
-- is exactly the "state observations, never allegations" rule.
--
-- To exercise this case, run it with p_region = 'EU' only, and leave the other two
-- branches unexecuted in the log window.

CREATE OR REPLACE PROCEDURE s7_unexercised_branch(p_region VARCHAR2) IS
BEGIN
    DBMS_APPLICATION_INFO.SET_MODULE('LINEAGE_CORPUS', 's7_unexercised_branch');

    IF p_region = 'EU' THEN
        -- Exercised constantly.
        UPDATE dim_customer
           SET is_active = 1
         WHERE region = 'EU';

    ELSIF p_region = 'APAC' THEN
        -- Provably in the code. Never observed running.
        UPDATE dim_customer
           SET is_active = (SELECT COUNT(*)
                              FROM stg_orders o
                             WHERE o.cust_id = dim_customer.cust_id)
         WHERE region = 'APAC';

    ELSIF p_region = 'YEAR_END' THEN
        -- Runs once a year. Absent from an 18-month window only by bad luck.
        INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
        SELECT cust_id, TRUNC(order_date, 'YYYY'), SUM(gross_amount), COUNT(*)
          FROM stg_orders
         GROUP BY cust_id, TRUNC(order_date, 'YYYY');
    END IF;

    COMMIT;
END s7_unexercised_branch;
/
