-- Band 2 / DBMS_SQL
-- Why it's hard: the older API assembles a statement call by call. There is not even
-- a single string expression to inspect - the text is built across multiple lines
-- through an opaque handle.
-- Approach: log recovery only. No static route exists.
--
-- Expected behaviour from the analyser: detect DBMS_SQL usage, emit NO edge, and
-- declare the statement unresolvable. A guessed edge here would be worse than the
-- honest gap, and this construct is a good test of the abstention classifier because
-- it is easy to parse and impossible to resolve.

CREATE OR REPLACE PROCEDURE b2_dbms_sql(p_region VARCHAR2) IS
    v_cursor  INTEGER;
    v_rows    INTEGER;
    v_stmt    VARCHAR2(1000);
BEGIN
    DBMS_APPLICATION_INFO.SET_MODULE('LINEAGE_CORPUS', 'b2_dbms_sql');

    v_cursor := DBMS_SQL.OPEN_CURSOR;

    v_stmt := 'UPDATE dim_customer SET lifetime_value = ';
    v_stmt := v_stmt || '(SELECT NVL(SUM(gross_amount - discount_amt), 0) ';
    v_stmt := v_stmt || ' FROM stg_orders o WHERE o.cust_id = dim_customer.cust_id) ';
    v_stmt := v_stmt || ' WHERE region = :b_region';

    DBMS_SQL.PARSE(v_cursor, v_stmt, DBMS_SQL.NATIVE);
    DBMS_SQL.BIND_VARIABLE(v_cursor, ':b_region', p_region);
    v_rows := DBMS_SQL.EXECUTE(v_cursor);
    DBMS_SQL.CLOSE_CURSOR(v_cursor);

    COMMIT;
END b2_dbms_sql;
/
