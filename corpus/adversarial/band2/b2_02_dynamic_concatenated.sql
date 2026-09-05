-- Band 2 / dynamic SQL, concatenated at runtime
-- Why it's hard: the statement does not exist until runtime. Statically undecidable.
-- Approach: query log recovery - the database already executed and recorded it.
--
-- THIS IS THE HOP THE WHOLE ARCHITECTURE TURNS ON. It is invisible to every static
-- analyser, and it is the hop that actually writes the regulated column.
--
-- Two flavours here, and they are NOT equally recoverable:
--   * The target column varies (is_active vs lifetime_value) - the log shows which
--     one actually ran, but only for paths exercised in the retention window.
--   * The target TABLE name is built from a parameter - worst case; if that value
--     never occurred in the window, the edge is simply unobservable.
--
-- MODULE/ACTION are set explicitly here, which is the FAVOURABLE attribution case.
-- Real scheduler-driven code frequently sets neither, which is exactly the risk the
-- sub-spike has to measure rather than assume.

CREATE OR REPLACE PROCEDURE b2_dynamic_concatenated(
    p_region VARCHAR2,
    p_column VARCHAR2,
    p_suffix VARCHAR2
) IS
    v_sql VARCHAR2(1000);
BEGIN
    DBMS_APPLICATION_INFO.SET_MODULE('LINEAGE_CORPUS', 'b2_dynamic_concatenated');

    -- Column name chosen at runtime.
    v_sql := 'UPDATE dim_customer SET ' || p_column || ' = 1'
          || ' WHERE region = ''' || p_region || ''''
          || ' AND cust_id IN (SELECT cust_id FROM tmp_recent)';
    EXECUTE IMMEDIATE v_sql;

    -- Table name chosen at runtime - the genuinely dark case.
    v_sql := 'INSERT INTO tmp_' || p_suffix || ' (cust_id, last_login)'
          || ' SELECT cust_id, last_login FROM stg_customer';
    BEGIN
        EXECUTE IMMEDIATE v_sql;
    EXCEPTION
        WHEN OTHERS THEN NULL;  -- target may not exist; not the point of the case
    END;

    COMMIT;
END b2_dynamic_concatenated;
/
