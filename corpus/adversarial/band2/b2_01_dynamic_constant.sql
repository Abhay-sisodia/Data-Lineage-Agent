-- Band 2 / dynamic SQL from a constant string
-- Why it's hard: it isn't. It only LOOKS dynamic.
-- Approach: constant propagation through the CFG, then parse normally.
--
-- This is the cheap win, and it must be taken before reaching for the query log.
-- Any statement resolved here is an AST-mechanism edge - full static evidence - and
-- costs nothing. Treating it as unrecoverable would needlessly drop it a tier and
-- inflate the "unresolved dynamic SQL" count in the coverage statement.

CREATE OR REPLACE PROCEDURE b2_dynamic_constant IS
    v_sql VARCHAR2(400);
BEGIN
    -- Never concatenated with anything variable: fully knowable statically.
    v_sql := 'UPDATE dim_customer SET is_active = 1 WHERE region = ''EU''';
    EXECUTE IMMEDIATE v_sql;

    -- Constant assembled from constant parts - still statically resolvable.
    EXECUTE IMMEDIATE 'INSERT INTO tmp_recent (cust_id, last_login) '
                   || 'SELECT cust_id, last_login FROM stg_customer';

    COMMIT;
END b2_dynamic_constant;
/
