-- Band 1 / local variables
-- Why it's hard: the value moves OUTSIDE any SQL statement. A SQL parser sees two
-- unrelated statements; the connection between them lives in a variable.
-- Approach: def-use chains over the CFG.
--
-- This is the worked example from the spike document, minus the dynamic hop.
-- The edge nobody else finds: ref_policy.window_days silently controls which rows
-- load into tmp_recent. A policy table is governing a filter, three statements away,
-- and no table-level tool would ever surface it.

CREATE OR REPLACE PROCEDURE b1_local_variables(p_region VARCHAR2) IS
    v_days   NUMBER;
    v_cutoff DATE;
BEGIN
    SELECT window_days
      INTO v_days
      FROM ref_policy
     WHERE region = p_region;

    v_cutoff := SYSDATE - v_days;

    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login
      FROM stg_customer
     WHERE last_login > v_cutoff;

    COMMIT;
END b1_local_variables;
/
