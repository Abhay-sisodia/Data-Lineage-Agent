-- Band 1 / exception handlers
-- Why it's hard: an error path may write somewhere the happy path never does.
-- Approach: treat the handler as an additional CFG edge, tagged exceptional.
--
-- The edge from the WHEN OTHERS block is real lineage: dim_customer.is_active gets
-- written from a hard-coded fallback when the policy lookup fails. A regulator asking
-- "where did this value come from" on a failure day needs that answer, and it exists
-- nowhere in the happy path.
--
-- Note also that the exceptional edge may be UNEXERCISED - present in code, never
-- observed running. That is a separate axis from tier, not a low tier.

CREATE OR REPLACE PROCEDURE b1_exception_handlers(p_region VARCHAR2) IS
    v_days NUMBER;
BEGIN
    SELECT window_days
      INTO v_days
      FROM ref_policy
     WHERE region = p_region;

    UPDATE dim_customer
       SET is_active = 1
     WHERE region = p_region
       AND cust_id IN (SELECT cust_id
                         FROM stg_customer
                        WHERE last_login > SYSDATE - v_days);

    COMMIT;

EXCEPTION
    WHEN NO_DATA_FOUND THEN
        -- Fallback path: writes the same column from a different source entirely.
        UPDATE dim_customer
           SET is_active = 0
         WHERE region = p_region;
        COMMIT;

    WHEN OTHERS THEN
        INSERT INTO tmp_recent (cust_id, last_login)
        VALUES (-1, SYSDATE);
        COMMIT;
        RAISE;
END b1_exception_handlers;
/
