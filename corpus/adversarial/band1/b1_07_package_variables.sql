-- Band 1 / package-level variables
-- Why it's hard: state survives ACROSS calls. This is the nastiest legitimate case
-- in the band. The value written into g_cutoff by one procedure is read by another,
-- with no parameter passing and nothing in either statement to connect them.
-- Approach: interprocedural analysis with an explicit, published depth cap.
--
-- The lineage that must be found:
--     ref_policy.window_days -> g_window_days -> g_cutoff -> tmp_recent row filter
-- spanning two separate procedure calls in a package body.

CREATE OR REPLACE PACKAGE pkg_policy_state AS
    PROCEDURE load_policy(p_region VARCHAR2);
    PROCEDURE apply_policy;
END pkg_policy_state;
/

CREATE OR REPLACE PACKAGE BODY pkg_policy_state AS

    -- Package state: written by one procedure, read by another.
    g_window_days NUMBER;
    g_cutoff      DATE;

    PROCEDURE load_policy(p_region VARCHAR2) IS
    BEGIN
        SELECT window_days
          INTO g_window_days
          FROM ref_policy
         WHERE region = p_region;

        g_cutoff := SYSDATE - g_window_days;
    END load_policy;

    PROCEDURE apply_policy IS
    BEGIN
        INSERT INTO tmp_recent (cust_id, last_login)
        SELECT cust_id, last_login
          FROM stg_customer
         WHERE last_login > g_cutoff;

        COMMIT;
    END apply_policy;

END pkg_policy_state;
/
